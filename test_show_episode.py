#!/usr/bin/env python3
"""
ZETU_SHOW_EPISODE (the full 9-chapter investigative documentary format)
regression tests -- roadmap Phase E, the bridge between content:show's
chapter structure and the evidence-safe pipeline. Structurally different
from every other format: some chapters conditionally SKIP when the
story has no real material for them (mirrors content-planner.mjs's own
buildZetuShowEpisodeBrief design), rather than the 5-part structure's
"never skip, always disclose" rule -- deliberately, since forcing an
"investigation" chapter with nothing to investigate is a narrative
defect for a documentary, not a compliance gap. Pure/offline: no OpenAI
or Anthropic call anywhere in this file.

Usage: python3 test_show_episode.py
"""
from zetu_closed_book_writer import (
    ZETU_SHOW_EPISODE_CHAPTERS, _show_episode_chapter_plan,
    _show_episode_structure_block, TASK_FRAMING, _MAX_TOKENS_BY_FORMAT,
    _AUDIT_MAX_TOKENS_BY_FORMAT, _OUTPUT_SUFFIX, _FIVE_PART_STRUCTURE_FORMATS,
    _SHORT_FORM_STRUCTURE_FORMATS, build_closed_book_prompt, build_story_pack,
    _ANTI_DRIFT_RULE,
)

failures = []
def ok(label, fn):
    try:
        fn()
        print(f"  ok {label}")
    except AssertionError as e:
        failures.append(label)
        print(f"  FAIL {label}: {e}")


def assert_(cond, msg=""):
    if not cond:
        raise AssertionError(msg)


def _sr(**overrides):
    fields = ["SIGNAL", "OBJECT", "QUESTION", "MONEY", "CHAIN", "GAP", "PROOF", "BUILDER", "OPPORTUNITY", "OBSTACLE", "ACTION", "OPEN_THREAD"]
    base = {k: {"value": None, "pack_source": None, "status": "unsupported"} for k in fields}
    base.update(overrides)
    return base


# ============================================================
# 1. Format registration
# ============================================================
print("=== FORMAT CONFIG ===")

ok("ZETU_SHOW_EPISODE is registered in TASK_FRAMING", lambda: assert_("ZETU_SHOW_EPISODE" in TASK_FRAMING))
ok("ZETU_SHOW_EPISODE has the largest token ceiling of any format -- it's the longest by design", lambda: (
    assert_(_MAX_TOKENS_BY_FORMAT["ZETU_SHOW_EPISODE"] > max(v for k, v in _MAX_TOKENS_BY_FORMAT.items() if k != "ZETU_SHOW_EPISODE"))
))
ok("ZETU_SHOW_EPISODE has its own audit ceiling and output suffix, no collision", lambda: (
    assert_("ZETU_SHOW_EPISODE" in _AUDIT_MAX_TOKENS_BY_FORMAT),
    assert_(_OUTPUT_SUFFIX["ZETU_SHOW_EPISODE"] not in [v for k, v in _OUTPUT_SUFFIX.items() if k != "ZETU_SHOW_EPISODE"]),
))
ok("ZETU_SHOW_EPISODE is not accidentally in the 5-part or short-form structure sets -- it has its own", lambda: (
    assert_("ZETU_SHOW_EPISODE" not in _FIVE_PART_STRUCTURE_FORMATS),
    assert_("ZETU_SHOW_EPISODE" not in _SHORT_FORM_STRUCTURE_FORMATS),
))

# ============================================================
# 2. Chapter plan -- deterministic, computed from real Story Record
# fields, never left to model judgment
# ============================================================
print("\n=== CHAPTER PLAN (deterministic, from real Story Record fields) ===")

ok("all 9 chapters are defined, matching content-planner.mjs's own set", lambda: (
    assert_(len(ZETU_SHOW_EPISODE_CHAPTERS) == 9),
    assert_([c for c, _ in ZETU_SHOW_EPISODE_CHAPTERS] == [
        "THE QUESTION", "THE STORY", "THE EVIDENCE", "THE INVESTIGATION", "THE OPPORTUNITY",
        "THE BUILDERS", "THE OBSTACLE", "THE MISSION", "CONTINUE ON ZETU",
    ]),
))

ok("THE QUESTION, THE MISSION, and CONTINUE ON ZETU are ALWAYS included, even on a fully empty story", lambda: (
    lambda plan=dict(_show_episode_chapter_plan(_sr())): (
        assert_(plan["THE QUESTION"] is True),
        assert_(plan["THE MISSION"] is True),
        assert_(plan["CONTINUE ON ZETU"] is True),
    )
)())

ok("THE STORY and THE INVESTIGATION both depend on SIGNAL -- both included together, both skipped together", lambda: (
    lambda supported=dict(_show_episode_chapter_plan(_sr(SIGNAL={"value": "x", "pack_source": "y", "status": "supported"}))),
           unsupported=dict(_show_episode_chapter_plan(_sr())): (
        assert_(supported["THE STORY"] is True and supported["THE INVESTIGATION"] is True),
        assert_(unsupported["THE STORY"] is False and unsupported["THE INVESTIGATION"] is False),
    )
)())

ok("THE OPPORTUNITY depends on OPPORTUNITY, THE BUILDERS on BUILDER, THE OBSTACLE on OBSTACLE -- independently", lambda: (
    lambda plan=dict(_show_episode_chapter_plan(_sr(
        OPPORTUNITY={"value": "x", "pack_source": "y", "status": "supported"},
    ))): (
        assert_(plan["THE OPPORTUNITY"] is True),
        assert_(plan["THE BUILDERS"] is False),
        assert_(plan["THE OBSTACLE"] is False),
    )
)())

ok("a fully thin story still gets a valid plan: only the 3 always-on chapters included, everything else skipped -- never crashes, never forces empty chapters", lambda: (
    lambda plan=_show_episode_chapter_plan(_sr()): (
        assert_(sum(1 for _, ok_ in plan if ok_) == 3),
    )
)())

ok("a fully rich story includes all 9 chapters", lambda: (
    lambda rich=_sr(**{k: {"value": "x", "pack_source": "y", "status": "supported"} for k in ["SIGNAL", "PROOF", "OPPORTUNITY", "BUILDER", "OBSTACLE"]}): (
        assert_(sum(1 for _, ok_ in _show_episode_chapter_plan(rich) if ok_) == 9),
    )
)())

# ============================================================
# 3. Real prompt integration -- the chapter plan actually reaches the
# model, and only for ZETU_SHOW_EPISODE with a real story-pack
# ============================================================
print("\n=== PROMPT-LEVEL CHAPTER PLAN INJECTION ===")

THIN_STORY_RESULT = {
    "packCountry": "Kenya", "packCountryCode": "KE", "packAssembledAt": "2026-09-19T00:00:00.000Z",
    "storyRecord": _sr(QUESTION={"value": "Why?", "pack_source": "[ECONOMIC:gdp_usd]", "status": "supported"}),
}

ok("a thin story-pack's real prompt tells the model to SKIP the unsupported chapters explicitly, by name", lambda: (
    lambda prompt=build_closed_book_prompt(build_story_pack(THIN_STORY_RESULT), "ZETU_SHOW_EPISODE"): (
        assert_(prompt is not None),
        assert_("SKIP entirely" in prompt),
        assert_("THE STORY" in prompt and "THE BUILDERS" in prompt),
        assert_("INCLUDE, in this order: THE QUESTION, THE MISSION, CONTINUE ON ZETU" in prompt),
    )
)())

RICH_STORY_RESULT = {
    "packCountry": "Kenya", "packCountryCode": "KE", "packAssembledAt": "2026-09-19T00:00:00.000Z",
    "storyRecord": _sr(**{k: {"value": "x", "pack_source": "[ECONOMIC:gdp_usd]", "status": "supported"} for k in ["SIGNAL", "PROOF", "OPPORTUNITY", "BUILDER", "OBSTACLE"]}),
}

ok("a rich story-pack's real prompt says every chapter has real material, no skip list", lambda: (
    lambda prompt=build_closed_book_prompt(build_story_pack(RICH_STORY_RESULT), "ZETU_SHOW_EPISODE"): (
        assert_("Every chapter has real material this time" in prompt),
        assert_("SKIP entirely" not in prompt),
    )
)())

ok("fix after a real run stopped partway through (ended on THE OPPORTUNITY, never reached THE MISSION/CTA): the prompt now forcefully requires reaching CONTINUE ON ZETU, on both thin and rich stories", lambda: (
    lambda thin_prompt=build_closed_book_prompt(build_story_pack(THIN_STORY_RESULT), "ZETU_SHOW_EPISODE"),
           rich_prompt=build_closed_book_prompt(build_story_pack(RICH_STORY_RESULT), "ZETU_SHOW_EPISODE"): (
        assert_("MUST ACTUALLY WRITE ALL THE WAY THROUGH" in thin_prompt),
        assert_("CONTINUE ON ZETU" in thin_prompt and "MUST ACTUALLY WRITE ALL THE WAY THROUGH" in rich_prompt),
        assert_("INCOMPLETE and unusable" in thin_prompt),
    )
)())

ok("a NORMAL (non-story) pack never gets a chapter plan injected -- only applies to an approved Story Record", lambda: (
    lambda normal_pack={"country": {"name": "Kenya"}, "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 1, "year": 2025, "flags": []}}}}: (
        lambda prompt=build_closed_book_prompt(normal_pack, "ZETU_SHOW_EPISODE"): (
            assert_(prompt is not None),
            assert_("CHAPTER PLAN FOR THIS EPISODE" not in prompt),
        )
    )()
)())

ok("the chapter-plan note never leaks into an unrelated format (e.g. LINKEDIN_ARTICLE) even on the same story-pack", lambda: (
    lambda prompt=build_closed_book_prompt(build_story_pack(THIN_STORY_RESULT), "LINKEDIN_ARTICLE"): (
        assert_("CHAPTER PLAN FOR THIS EPISODE" not in prompt),
    )
)())

# ============================================================
# 4. Shared machinery reused unchanged
# ============================================================
print("\n=== SHARED MACHINERY REUSED UNCHANGED ===")

ok("ZETU_SHOW_EPISODE's framing carries the shared anti-drift rule", lambda: (
    assert_(_ANTI_DRIFT_RULE in TASK_FRAMING["ZETU_SHOW_EPISODE"]("Kenya", 30))
))
ok("the structure block explains what each of the 9 chapters IS, for the model's own reference", lambda: (
    lambda block=_show_episode_structure_block(): (
        assert_("THE BUILDERS" in block),
        assert_("OBSERVED (never VERIFIED)" in block, "must preserve the evidence-tier honesty rule even here"),
    )
)())

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
