"""Thinking-style orchestration tests: auto classifier, pipelines, gate.

Users pick a thinking style (fast / balanced / deep / research / build),
or leave it on auto and let a zero-token rule-based classifier decide.
Skills are internal pipeline stages and are never user-facing.
"""

from __future__ import annotations

import typing
from pathlib import Path

import pytest

from backend.schemas import ResponseMode
from backend.skills.loader import load_skills
from backend.supervisor.pipelines import (
    BUILTIN_STAGES,
    GATED_MODES,
    MODES,
    PIPELINE_MODES,
    PIPELINES,
    build_pipeline_prompt,
    classify_intent,
    needs_clarification,
    resolve_mode,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    return load_skills(ROOT / "skills")


# ---------------- auto classification ----------------


def test_classifier_spec_validation_inputs():
    # The validation inputs from the thinking-style spec.
    assert classify_intent("hello") == "fast"
    assert classify_intent("best processor for gaming") == "research"
    assert classify_intent("design distributed banking architecture") == "deep"
    assert classify_intent("build a FastAPI application") == "build"


def test_classifier_comparison_requests_route_to_research():
    # Comparisons get analysis plus a recommendation, even when short.
    assert classify_intent("compare React and Vue") == "research"
    assert classify_intent("HTML vs React") == "research"
    assert classify_intent("difference between REST and GraphQL") == "research"


def test_classifier_debug_and_error_signals_route_to_build():
    assert classify_intent("Explain this traceback: ValueError: bad literal") == "build"
    assert classify_intent("my script is broken") == "build"
    assert classify_intent("fix my deployment script") == "build"


def test_classifier_deep_signals_are_guarded():
    # One deep signal in a short message must not escalate.
    assert classify_intent("what is production?") == "fast"
    # Two hits escalate.
    assert classify_intent("deep dive and thorough analysis") == "deep"
    # One hit in a longer message escalates.
    long_message = "I would like to understand how production systems are run"
    assert len(long_message) >= 40
    assert classify_intent(long_message) == "deep"


def test_classifier_length_fallbacks():
    assert classify_intent("hey") == "fast"
    assert classify_intent("Explain quantum entanglement") == "balanced"
    assert classify_intent("x" * 130) == "balanced"


def test_resolve_mode_mapping():
    assert resolve_mode(None, "hey") == "fast"
    assert resolve_mode("auto", "design distributed banking architecture") == "deep"
    # Explicit styles pass through unchanged (case-insensitive).
    assert resolve_mode("fast", "x") == "fast"
    assert resolve_mode("balanced", "x") == "balanced"
    assert resolve_mode("deep", "x") == "deep"
    assert resolve_mode("research", "x") == "research"
    assert resolve_mode("BUILD", "x") == "build"
    with pytest.raises(ValueError):
        resolve_mode("turbo", "x")


def test_modes_constant_matches_schema():
    assert MODES[0] == "auto"
    assert set(PIPELINE_MODES) | {"auto"} == set(MODES)
    # The API literal must expose exactly the same styles.
    assert set(typing.get_args(ResponseMode)) == set(MODES)


# ---------------- pipeline composition ----------------


def test_style_pipelines_match_spec():
    # The internal mapping behind each thinking style.
    assert PIPELINES["fast"] == ("general", "stop_slop", "response_formatter")
    assert PIPELINES["balanced"] == ("general", "fact_checker", "response_formatter")
    assert PIPELINES["deep"] == (
        "superpower",
        "council",
        "stop_slop",
        "response_formatter",
    )
    assert PIPELINES["research"] == (
        "research_analyst",
        "fact_checker",
        "council",
        "response_formatter",
    )
    assert PIPELINES["build"] == (
        "requirement_interrogator",
        "code_reviewer",
        "debugging",
        "security_review",
        "response_formatter",
    )


def test_every_pipeline_ends_with_response_formatter():
    for mode in PIPELINE_MODES:
        assert PIPELINES[mode][-1] == "response_formatter"
        assert PIPELINES[mode].count("response_formatter") == 1


def test_build_pipeline_prompt_structure(registry):
    prompt, stages = build_pipeline_prompt(
        "build", registry, "build a FastAPI application"
    )
    assert stages == [
        "Requirement Interrogator",
        "Code Reviewer",
        "Debugging",
        "Security Review",
        "Response Formatter",
    ]
    assert "Thinking style: Build" in prompt
    assert "### Stage 1: Requirement Interrogator (INTERNAL)" in prompt
    assert "### Final Stage: Response Formatter" in prompt
    # Real skill bodies are embedded, not just the fallbacks.
    assert registry["debugging"].body[:60] in prompt
    assert registry["requirement_interrogator"].body[:60] in prompt


def test_deep_pipeline_prompt_structure(registry):
    prompt, stages = build_pipeline_prompt(
        "deep", registry, "What is photosynthesis?"
    )
    assert stages == ["Superpower", "Council", "Stop Slop", "Response Formatter"]
    assert "Thinking style: Deep Think" in prompt
    assert registry["council"].body[:60] in prompt
    assert registry["superpower"].body[:60] in prompt


def test_pipeline_prompt_hides_internal_process(registry):
    prompt, _ = build_pipeline_prompt("fast", registry, "What is photosynthesis?")
    assert "Never show the internal process" in prompt
    assert "Direct answer first" in prompt
    assert "Final check before answering" in prompt


def test_build_stage_variants(registry):
    prompt, _ = build_pipeline_prompt("build", registry, "fix my script")
    # Light interrogator: never asks the user questions mid-pipeline.
    assert "Do NOT ask the user questions" in prompt
    # Security review is conditional in the build pipeline.
    assert "Apply this stage only if" in prompt


def test_pipeline_prompt_survives_empty_registry():
    for mode in PIPELINE_MODES:
        prompt, stages = build_pipeline_prompt(mode, {})
        assert len(stages) == len(PIPELINES[mode])
        assert stages[-1] == "Response Formatter"
        assert "### Final Stage: Response Formatter" in prompt


# ---------------- activation injection ----------------


def test_activation_injection_prompt_engineer(registry):
    prompt, stages = build_pipeline_prompt(
        "fast", registry, "Improve this prompt: You are a tutor."
    )
    assert "Prompt Engineer" in stages
    # Injected just before the mandatory formatter.
    assert stages[-1] == "Response Formatter"
    assert stages[-2] == "Prompt Engineer"
    assert "### Stage" in prompt


def test_no_injection_without_matching_trigger(registry):
    _, stages = build_pipeline_prompt("fast", registry, "What is photosynthesis?")
    assert stages == ["General Assistant", "Stop Slop", "Response Formatter"]


def test_activation_injection_architecture_review(registry):
    # A design question pulls the Architecture Review skill into the deep
    # pipeline, directly before the mandatory formatter.
    _, stages = build_pipeline_prompt(
        "deep", registry, "design distributed banking architecture"
    )
    assert stages[-1] == "Response Formatter"
    assert stages[-2] == "Architecture Review"


# ---------------- skill metadata ----------------


def test_all_skills_have_orchestration_metadata(registry):
    assert len(registry) == 15
    for skill_id, skill in registry.items():
        assert skill.category, skill_id
        assert isinstance(skill.priority, int) and skill.priority >= 1, skill_id
        assert skill.modes, f"{skill_id} has no supported styles"
        assert set(skill.modes) <= set(PIPELINE_MODES), skill_id
        assert skill.activation, skill_id


def test_pipeline_skill_modes_are_consistent(registry):
    for mode, stage_ids in PIPELINES.items():
        for stage_id in stage_ids:
            if stage_id in BUILTIN_STAGES or stage_id not in registry:
                continue
            assert mode in registry[stage_id].modes, (mode, stage_id)


# ---------------- /chat endpoint ----------------


def test_fast_greeting_answered_directly(client, mock_llama):
    resp = client.post("/chat", json={"message": "hey"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["clarification"] is False
    assert body["mode"] == "fast"
    assert body["workflow"] == ["General Assistant", "Stop Slop", "Response Formatter"]
    assert "skill" not in body
    assert mock_llama.chat_calls == 1


@pytest.mark.parametrize("mode", PIPELINE_MODES)
def test_every_style_reports_workflow_ending_in_formatter(client, mock_llama, mode):
    body = client.post(
        "/chat", json={"message": "Help me with this task please", "mode": mode}
    ).json()
    assert body["mode"] == mode
    assert body["workflow"] is not None
    assert body["workflow"][-1] == "Response Formatter"
    system = mock_llama.last_payload["messages"][0]["content"]
    assert "### Final Stage: Response Formatter" in system


def test_build_pipeline_composes_in_request(client, mock_llama, registry):
    body = client.post(
        "/chat",
        json={"message": "build a FastAPI application", "mode": "build"},
    ).json()
    assert body["mode"] == "build"
    assert body["workflow"] == [
        "Requirement Interrogator",
        "Code Reviewer",
        "Debugging",
        "Security Review",
        "Response Formatter",
    ]
    system = mock_llama.last_payload["messages"][0]["content"]
    assert registry["debugging"].body[:60] in system


def test_auto_comparison_gets_research_style(client, mock_llama):
    body = client.post("/chat", json={"message": "HTML vs React"}).json()
    assert body["mode"] == "research"
    assert body["workflow"][0] == "Research Analyst"
    assert body["workflow"][-1] == "Response Formatter"


# ---------------- clarification gate ----------------


@pytest.mark.parametrize("mode", ["build", "deep"])
def test_vague_gate_clarifies_without_model_call(client, mock_llama, mode):
    body = client.post("/chat", json={"message": "help", "mode": mode}).json()
    assert body["clarification"] is True
    assert body["mode"] == mode
    assert "skill" not in body
    assert "Task" in body["answer"]
    assert mock_llama.chat_calls == 0


def test_vague_gate_deep_with_vague_word(client, mock_llama):
    body = client.post("/chat", json={"message": "start", "mode": "deep"}).json()
    assert body["clarification"] is True
    assert body["mode"] == "deep"
    assert mock_llama.chat_calls == 0


@pytest.mark.parametrize("mode", ["fast", "balanced", "research"])
def test_vague_gate_never_fires_in_ungated_styles(client, mock_llama, mode):
    # Fast, Balanced and Research answer short vague messages directly.
    body = client.post("/chat", json={"message": "help", "mode": mode}).json()
    assert body["clarification"] is False
    assert body["mode"] == mode
    assert mock_llama.chat_calls == 1


def test_gated_modes_constant():
    assert GATED_MODES == ("build", "deep")


def test_clear_message_in_gated_style_is_not_interrogated(client, mock_llama):
    # A concrete build request skips the gate entirely.
    body = client.post(
        "/chat",
        json={"message": "build a FastAPI application", "mode": "build"},
    ).json()
    assert body["clarification"] is False
    assert mock_llama.chat_calls == 1


def test_needs_clarification_rules():
    assert needs_clarification("help")
    assert needs_clarification("ok")
    assert needs_clarification("   ")
    assert not needs_clarification("build a FastAPI application")


def test_invalid_mode_rejected(client, mock_llama):
    resp = client.post("/chat", json={"message": "hello there", "mode": "turbo"})
    assert resp.status_code == 422
    assert mock_llama.chat_calls == 0
