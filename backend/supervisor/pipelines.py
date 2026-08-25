"""Thinking-style orchestration: pipelines of stages, one composed model call.

Users pick a THINKING STYLE, not a skill. Skills are internal. Each style
runs a pipeline of stages (skill bodies + built-in orchestration stages)
composed into a single system prompt, so the model does all internal work
in one inference pass (the server runs a single slot at ~9-10 tok/s —
sequential per-stage calls would multiply latency).

Every pipeline ends with the mandatory Response Formatter stage: it hides
all internal process (stage names, skill names, workflow traces) and turns
the work into one clean, natural answer to the user's original question.

Thinking styles:

- ``fast``: General Assistant + Stop Slop + Response Formatter
- ``balanced``: General Assistant + Fact Checker + Response Formatter
- ``deep``: Superpower + Council + Stop Slop + Response Formatter
- ``research``: Research Analyst + Fact Checker + Council
  + Response Formatter
- ``build``: Requirement Interrogator (light) + Code Reviewer + Debugging
  + Security Review (if needed) + Response Formatter
- ``auto`` (default): zero-token rule-based classifier picks one of the
  five styles above.

Skills that belong to no fixed pipeline (prompt_engineer, aar, grill_me,
demo_product, performance_optimizer, architecture_review,
production_readiness) stay reachable through their activation rules: when
their triggers match the message, the best one is injected as one extra
stage before the formatter.
"""

from __future__ import annotations

import re

from backend.skills.loader import Skill, match_skill

MODES = (
    "auto",
    "fast",
    "balanced",
    "deep",
    "research",
    "build",
)
DEFAULT_MODE = "auto"

# The five user-facing thinking styles (everything except auto).
PIPELINE_MODES = (
    "fast",
    "balanced",
    "deep",
    "research",
    "build",
)

MODE_LABELS = {
    "fast": "Fast",
    "balanced": "Balanced",
    "deep": "Deep Think",
    "research": "Research",
    "build": "Build",
}

# Ordered stage ids per style. Stage ids resolve to a skill body when a
# skill with that id exists, otherwise to a built-in orchestration stage.
# The response formatter is mandatory and always last.
PIPELINES: dict[str, tuple[str, ...]] = {
    "fast": ("general", "stop_slop", "response_formatter"),
    "balanced": ("general", "fact_checker", "response_formatter"),
    "deep": ("superpower", "council", "stop_slop", "response_formatter"),
    "research": (
        "research_analyst",
        "fact_checker",
        "council",
        "response_formatter",
    ),
    "build": (
        "requirement_interrogator",
        "code_reviewer",
        "debugging",
        "security_review",
        "response_formatter",
    ),
}

# ---------------------------------------------------------------------------
# Built-in orchestration stages (prompt fragments, not skill files)
# ---------------------------------------------------------------------------

RESPONSE_FORMATTER = (
    "Turn all the internal work above into ONE clean, natural answer for the"
    " user.\n"
    "\n"
    "First detect the answer type, then follow its structure exactly. Use a"
    " short markdown heading (###) for each named section, and keep every"
    " section brief:\n"
    "- Comparison: a one-sentence short answer first, then a markdown"
    " comparison table, then a 'Recommendation' section with one clear"
    " pick.\n"
    "- Coding: an 'Approach' section of one line, the complete runnable"
    " code in a fenced code block, an 'Explanation' section of a few lines,"
    " then a 'Testing' section saying how to verify it works.\n"
    "- Explanation: the core concept in plain language, one concrete"
    " example, then a 'Key points' section with at most three bullets.\n"
    "- Debugging: a 'Problem' section restating it in one line, a 'Root"
    " cause' section with the actual cause, a 'Solution' section with the"
    " fix (code if relevant), and a 'Verification' section saying how to"
    " confirm it is fixed.\n"
    "- Research: a 'Summary' section of two or three sentences, an"
    " 'Evidence' section with the supporting facts as bullets or a table,"
    " a 'Tradeoffs' section with what each option costs, and a"
    " 'Recommendation' section with one clear pick.\n"
    "- Greeting or small talk: a plain one- or two-sentence reply with no"
    " headings at all.\n"
    "\n"
    "General rules:\n"
    "1. Direct answer first: one or two sentences that actually answer the"
    " original question.\n"
    "2. Do not make the answer longer than it needs to be: the structure"
    " organizes the answer, it does not add content. Skip a section only"
    " when it would be empty or pointless.\n"
    "3. Use a table or bullets only when they genuinely help.\n"
    "4. If a choice is involved, end with one specific recommendation.\n"
    "\n"
    "Never show the internal process:\n"
    "- Never mention stages, skills, pipelines, modes, thinking styles,"
    " workflows, or routing.\n"
    "- Never use headers or labels such as 'Requirement Analysis',"
    " 'Architecture Review', 'Council', 'Fact Check', 'Security Review',"
    " 'Response Formatter', or any other internal step.\n"
    "- Remove every section that does not directly serve the answer.\n"
    "- Do not ask clarifying questions; make reasonable assumptions and"
    " state them in one short line only when they change the answer.\n"
    "- Write like a helpful expert talking to a person, not a consultant"
    " writing a report.\n"
    "\n"
    "Final check before answering: (1) Did I answer the original question"
    " directly? (2) Is any internal process visible? If yes, remove it."
    " (3) Is the first line the actual answer, not a preamble? (4) Is the"
    " length proportional to the question? (5) Would a person actually talk"
    " like this? If not, rewrite it."
)

BUILTIN_STAGES: dict[str, tuple[str, str]] = {
    "fact_checker": (
        "Fact Checker",
        "Fact-check the claims you are about to make. Keep only what you are"
        " confident about; do not invent names, numbers, versions, or"
        " benchmarks. If something is uncertain, say so in one short line"
        " instead of guessing.",
    ),
    "code_reviewer": (
        "Code Reviewer",
        "Review the code you produce for correctness, edge cases, and"
        " obvious bugs before showing it. Fix problems silently in the final"
        " code; do not narrate the review.",
    ),
    "response_formatter": ("Response Formatter", RESPONSE_FORMATTER),
}

# Fallback bodies for skill-backed stages when a skill is missing, so a
# pipeline always composes.
_STAGE_FALLBACK_BODIES = {
    "general": (
        "Answer the user's request directly with practical, accurate help."
    ),
    "requirement_interrogator": (
        "Clarify the task internally: goal, users, constraints, success"
        " criteria, and the questions that must be settled before building."
    ),
    "debugging": (
        "Diagnose the problem: reproduce it, isolate the cause, fix it, and"
        " say how to verify the fix."
    ),
    "architecture_review": (
        "Evaluate the design: components, data flow, trade-offs, and simpler"
        " alternatives worth considering."
    ),
    "security_review": (
        "Check for security issues: input validation, authentication,"
        " secrets handling, injection, and data exposure."
    ),
    "production_readiness": (
        "Assess operational readiness: monitoring, backups, failure modes,"
        " and rollout strategy."
    ),
    "research_analyst": (
        "Analyze the options: criteria, evidence, trade-offs, and a"
        " recommendation."
    ),
    "council": (
        "Consider two or three expert perspectives that disagree where"
        " useful, then settle on the recommended position and why."
    ),
    "stop_slop": (
        "Cut filler: remove generic advice, repeated points, and padded"
        " sections."
    ),
    "superpower": (
        "Find the fastest, most effective approach first, including any"
        " shortcut or leverage worth taking."
    ),
}

# Per-(style, stage) wrappers that adapt a shared skill to its pipeline role.
_BUILD_INTERROGATOR_NOTE = (
    "Light requirement check only. Identify the task, target environment,"
    " and constraints silently. Do NOT ask the user questions — make"
    " sensible assumptions and proceed."
)
_CONDITIONAL_SECURITY_NOTE = (
    "Apply this stage only if the work involves user input, authentication,"
    " secrets, network access, or persistence. Otherwise skip it silently."
)

_STAGE_VARIANTS: dict[tuple[str, str], str] = {
    ("build", "requirement_interrogator"): _BUILD_INTERROGATOR_NOTE,
    ("build", "security_review"): _CONDITIONAL_SECURITY_NOTE,
}

# Each skill stage body is truncated so a multi-stage pipeline still leaves
# room for history inside an 8k server context.
_STAGE_BODY_LIMIT = 1200

MODE_NOTES = {
    "fast": (
        "Keep it short: one or two sentences unless the user clearly needs"
        " more. Never ask questions."
    ),
    "balanced": (
        "A normal helpful answer. Go deeper only when the question needs it."
    ),
    "deep": (
        "The user wants a thorough examination: cover trade-offs, risks,"
        " and second-order effects, still as one clean answer."
    ),
    "research": (
        "The user wants analysis with a decision. Compare real options and"
        " end with one clear recommendation."
    ),
    "build": (
        "The user wants working software or a fix. Provide complete,"
        " runnable code; keep prose minimal."
    ),
}

_PIPELINE_HEADER = (
    "You are PocketAI. Work through the stages below in order as your"
    " internal process, then produce ONE clean final answer for the user."
    " Every stage except the Response Formatter is internal: never mention"
    " stage names, skills, pipelines, or your internal process in the"
    " answer."
)

# ---------------------------------------------------------------------------
# Intent classifier (auto): zero-token, rule-based
# ---------------------------------------------------------------------------

# Debugging/fixing signals route to Build immediately.
_BUILD_DEBUG_SIGNALS = (
    "traceback",
    "stack trace",
    "error",
    "exception",
    "bug",
    "debug",
    "segfault",
    "crash",
    "compile",
    "syntax error",
    "fix this",
    "fix my",
    "refactor",
    "lint",
    "unit test",
    "code review",
    "broken",
)
# "build/create/... <artifact>" routes to Build.
_BUILD_VERB_RE = re.compile(
    r"\b(?:build|create|make|develop|write|implement|code)\b"
    r"[\w\s,.'\-]{0,40}"
    r"\b(?:app|application|page|site|website|dashboard|script|program|form|"
    r"api|endpoint|component|ui|interface|game|bot|tool|plugin|extension|"
    r"cli|widget|html|css|javascript|python|function|class|database|schema|"
    r"backend|frontend|server|parser|scraper|landing page|crud)\b"
)
_DEEP_SIGNALS = (
    "design",
    "architect",
    "distributed",
    "infrastructure",
    "microservice",
    "scalab",
    "production",
    "system design",
    "high availability",
    "load balancer",
    "kubernetes",
    "deployment",
    "tech stack",
    "database schema",
    "data pipeline",
    "trade-off",
    "tradeoff",
    "trade offs",
    "thorough",
    "deep dive",
    "in depth",
    "in-depth",
    "comprehensive",
    "second opinion",
    "council",
    "grill",
    "postmortem",
    "post-mortem",
)
_RESEARCH_SIGNALS = (
    "research",
    "investigate",
    "survey",
    "benchmark",
    "state of the art",
    "top 5",
    "top 10",
    "alternatives",
    "pros and cons",
)
# Comparison requests route to Research even when short ("HTML vs React").
_COMPARISON_RE = re.compile(
    r"\b(?:compare|compared|comparing|comparison|versus|vs|"
    r"difference between|differences between)\b"
)


def _signals_re(signals: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        r"\b(?:" + "|".join(re.escape(s) for s in signals) + r")\b"
    )


_BUILD_DEBUG_RE = _signals_re(_BUILD_DEBUG_SIGNALS)
_DEEP_RE = _signals_re(_DEEP_SIGNALS)
_RESEARCH_RE = _signals_re(_RESEARCH_SIGNALS)
_BEST_FOR_RE = re.compile(r"\bbest [\w -]{0,30} for\b")
_WHICH_BEST_RE = re.compile(
    r"\bwhich [\w -]{0,30} (?:should|is better|is best)\b"
)
_GREETING_RE = re.compile(
    r"^(?:hi|hiya|hey|hello|yo|sup|howdy|greetings|"
    r"good (?:morning|afternoon|evening))\b"
)

# A signal-free message at or under this length is a fast answer; anything
# longer with no signals is balanced.
_FAST_MAX_LEN = 20
# Deep needs 2 signal hits, or 1 hit in a longer message, so a short
# conceptual question ("what is production?") is not escalated.
_GUARDED_MIN_LEN = 40


def classify_intent(message: str) -> str:
    """Rule-based intent classifier for auto mode.

    Returns one of the five thinking styles. Order matters: build beats
    deep beats research. Deep signals are guarded so one hit in a short
    message does not escalate. Greetings and very short messages are fast;
    everything else is balanced. Zero tokens spent — a small 4B model
    should not burn inference on routing.
    """
    text = message.strip()
    lowered = text.lower()
    if not lowered:
        return "fast"
    if _BUILD_VERB_RE.search(lowered) or _BUILD_DEBUG_RE.search(lowered):
        return "build"
    deep_hits = len(_DEEP_RE.findall(lowered))
    if deep_hits >= 2 or (deep_hits == 1 and len(text) >= _GUARDED_MIN_LEN):
        return "deep"
    if (
        _RESEARCH_RE.search(lowered)
        or _BEST_FOR_RE.search(lowered)
        or _WHICH_BEST_RE.search(lowered)
        or _COMPARISON_RE.search(lowered)
    ):
        return "research"
    if _GREETING_RE.match(lowered) or len(text) <= _FAST_MAX_LEN:
        return "fast"
    return "balanced"


def resolve_mode(requested: str | None, message: str) -> str:
    """Map a requested thinking style to a concrete style (auto classifies)."""
    mode = (requested or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r}")
    if mode != "auto":
        return mode
    return classify_intent(message)


# ---------------------------------------------------------------------------
# Pipeline composition
# ---------------------------------------------------------------------------


def _stage_content(
    stage_id: str, mode: str, registry: dict[str, Skill]
) -> tuple[str, str]:
    """Resolve a stage id to (display_name, body) for the given style."""
    if stage_id in BUILTIN_STAGES:
        name, body = BUILTIN_STAGES[stage_id]
        return name, body
    skill = registry.get(stage_id)
    if skill is not None:
        name, body = skill.name, skill.body[:_STAGE_BODY_LIMIT]
    else:
        name = stage_id.replace("_", " ").title()
        body = _STAGE_FALLBACK_BODIES.get(stage_id, "")
    note = _STAGE_VARIANTS.get((mode, stage_id))
    if note:
        body = f"{note}\n\n{body}" if body else note
    return name, body


def activation_injection(
    mode: str, registry: dict[str, Skill], message: str
) -> str | None:
    """Pick one non-pipeline skill whose activation triggers match.

    Keeps skills that belong to no fixed pipeline (prompt_engineer, aar,
    grill_me, demo_product, performance_optimizer, architecture_review,
    production_readiness) reachable. Candidates are ordered by priority
    so trigger-count ties go to the higher-priority skill. At most one
    stage is injected.
    """
    if mode not in PIPELINES or not message.strip():
        return None
    pipeline_ids = set(PIPELINES[mode])
    candidates = {
        skill_id: skill
        for skill_id, skill in registry.items()
        if skill_id not in pipeline_ids and skill_id not in BUILTIN_STAGES
    }
    if not candidates:
        return None
    ordered = dict(
        sorted(candidates.items(), key=lambda kv: (kv[1].priority, kv[0]))
    )
    match = match_skill(ordered, message)
    return match.id if match else None


def build_pipeline_prompt(
    mode: str, registry: dict[str, Skill], message: str = ""
) -> tuple[str, list[str]]:
    """Compose the style pipeline into ONE system prompt.

    Returns (system_prompt, stage_display_names). The Response Formatter
    is always the final stage; an activation-matched non-pipeline skill
    is inserted just before it.
    """
    if mode not in PIPELINES:
        raise ValueError(f"no pipeline for mode: {mode!r}")
    stage_ids = list(PIPELINES[mode])
    extra = activation_injection(mode, registry, message)
    if extra:
        stage_ids.insert(len(stage_ids) - 1, extra)

    parts = [
        _PIPELINE_HEADER,
        f"Thinking style: {MODE_LABELS[mode]}\n{MODE_NOTES[mode]}",
    ]
    display_names: list[str] = []
    for index, stage_id in enumerate(stage_ids, 1):
        name, body = _stage_content(stage_id, mode, registry)
        display_names.append(name)
        if stage_id == "response_formatter":
            parts.append(f"### Final Stage: {name}\n{body}")
        else:
            parts.append(f"### Stage {index}: {name} (INTERNAL)\n{body}")
    return "\n\n".join(parts), display_names


# ---------------------------------------------------------------------------
# Clarification gate (build and deep only)
# ---------------------------------------------------------------------------

# Build and Deep Think may ask one clarifying round for very vague messages;
# Fast, Balanced and Research always answer directly.
GATED_MODES = ("build", "deep")
MIN_CLEAR_LENGTH = 12
VAGUE_MESSAGES = {
    "help",
    "help me",
    "hi",
    "hello",
    "hey",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "?",
    "...",
    "start",
    "begin",
    "do something",
    "idk",
    "i don't know",
    "test",
}


def needs_clarification(message: str) -> bool:
    """Rule-based vague gate: zero tokens, no model call."""
    stripped = message.strip().lower()
    if not stripped:
        return True
    if len(stripped) < MIN_CLEAR_LENGTH:
        return True
    return stripped in VAGUE_MESSAGES
