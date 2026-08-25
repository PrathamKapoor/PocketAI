"""Skill loader tests (run against the real skills/ directory)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.skills.loader import load_skills, match_skill

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SKILLS = {
    "requirement_interrogator",
    "general",
    "prompt_engineer",
    "debugging",
    "architecture_review",
    # Phase 8 advanced skills
    "security_review",
    "production_readiness",
    "grill_me",
    "stop_slop",
    "aar",
    "council",
    "superpower",
    "research_analyst",
    "performance_optimizer",
    "demo_product",
}


def test_loads_all_shipped_skills():
    registry = load_skills(ROOT / "skills")
    assert set(registry) == EXPECTED_SKILLS
    for skill in registry.values():
        assert skill.name
        assert skill.body.strip()
        assert skill.id == skill.path.parent.name


def test_keyword_routing():
    registry = load_skills(ROOT / "skills")
    assert match_skill(registry, "I get a traceback when running my script").id == "debugging"
    assert match_skill(registry, "Please write a prompt for a tutor bot").id == "prompt_engineer"
    assert match_skill(registry, "Run an architecture review of my design").id == "architecture_review"
    assert match_skill(registry, "What is photosynthesis?") is None


def test_keyword_routing_uses_word_boundaries():
    registry = load_skills(ROOT / "skills")
    # 'aar' must not fire inside an unrelated word (substring matching would).
    assert match_skill(registry, "Meet me at the bazaar at noon") is None


# Phase 8 spec: lightweight routing must land these phrases on the right skill.
@pytest.mark.parametrize(
    "message,expected",
    [
        ("Review my architecture", "architecture_review"),
        ("Find security issues", "security_review"),
        ("Be critical", "grill_me"),
        ("Is this production ready?", "production_readiness"),
        ("This paragraph is too verbose, tighten it", "stop_slop"),
        ("Let's do an after action review of the outage", "aar"),
        ("Give me a second opinion, council", "council"),
        ("What's the fastest way to do this, any shortcut?", "superpower"),
        ("Analyze these options and compare them", "research_analyst"),
        ("Why is this endpoint so slow, optimize it", "performance_optimizer"),
        ("Help me pitch and demo my project", "demo_product"),
    ],
)
def test_phase8_skill_routing(message, expected):
    registry = load_skills(ROOT / "skills")
    matched = match_skill(registry, message)
    assert matched is not None, f"no skill matched: {message!r}"
    assert matched.id == expected


def test_phase8_skills_have_full_structure():
    """Every Phase 8 skill must carry the required operating-mode sections."""
    registry = load_skills(ROOT / "skills")
    required = ("## Purpose", "## Activation", "## Workflow", "## Output format", "## Quality checks")
    # Phase 8 scope: the 10 new skills plus architecture_review (upgraded).
    phase8 = EXPECTED_SKILLS - {
        "requirement_interrogator",
        "general",
        "prompt_engineer",
        "debugging",
    }
    for skill_id in phase8:
        body = registry[skill_id].body
        for section in required:
            assert section in body, f"{skill_id} missing {section!r}"
        assert registry[skill_id].triggers, f"{skill_id} has no routing triggers"


def test_malformed_skill_skipped(tmp_path):
    bad = tmp_path / "bad_skill"
    bad.mkdir()
    (bad / "skill.md").write_text("no frontmatter here", encoding="utf-8")
    good = tmp_path / "good_skill"
    good.mkdir()
    (good / "skill.md").write_text(
        "---\nname: Good\ndescription: ok\ntriggers: foo\n---\nBody text.",
        encoding="utf-8",
    )
    registry = load_skills(tmp_path)
    assert set(registry) == {"good_skill"}
    assert registry["good_skill"].triggers == ("foo",)


def test_non_skill_entries_skipped(tmp_path):
    (tmp_path / "stray_file.txt").write_text("hi", encoding="utf-8")
    empty = tmp_path / "no_markdown"
    empty.mkdir()
    bad_name = tmp_path / "Bad-Name"
    bad_name.mkdir()
    (bad_name / "skill.md").write_text(
        "---\nname: X\n---\nBody.", encoding="utf-8"
    )
    assert load_skills(tmp_path) == {}


def test_symlink_escape_skipped(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    real = outside / "skill.md"
    real.write_text("---\nname: Evil\n---\nBody.", encoding="utf-8")

    skills_dir = tmp_path / "skills"
    linked = skills_dir / "linked_skill"
    linked.mkdir(parents=True)
    target = linked / "skill.md"
    try:
        os.symlink(real, target)
    except OSError:
        pytest.skip("symlinks unavailable on this machine")
    assert load_skills(skills_dir) == {}


def test_missing_dir_returns_empty(tmp_path):
    assert load_skills(tmp_path / "does_not_exist") == {}
