#!/usr/bin/env python3
"""
SHORT_FORM_VIDEO (TikTok/Reels/Shorts) format regression tests --
roadmap Phase D. Deliberately does NOT use the 5-part structure (a
45-90 second clip can't fit 5 real sections); uses its own 3-beat
structure (HOOK / THE POINT / CTA) instead. Reuses everything else
(provenance, anti-drift rule, tense rule, real CTA logic) completely
unchanged. Pure/offline: no OpenAI or Anthropic call anywhere in this
file.

Usage: python3 test_short_form_video.py
"""
from zetu_closed_book_writer import (
    TASK_FRAMING, _MAX_TOKENS_BY_FORMAT, _AUDIT_MAX_TOKENS_BY_FORMAT,
    _OUTPUT_SUFFIX, _FIVE_PART_STRUCTURE_FORMATS, _SHORT_FORM_STRUCTURE_FORMATS,
    _short_form_structure_block, build_closed_book_prompt, _ANTI_DRIFT_RULE,
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


RICH_PACK = {
    "country": {"code": "KE", "name": "Kenya"},
    "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 100, "year": 2025, "flags": [], "trustState": "VERIFIED", "trustReason": "fresh"}}},
    "signals": [], "opportunities": [], "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
}

# ============================================================
# 1. Format registration -- present everywhere a real format needs to be
# ============================================================
print("=== FORMAT CONFIG ===")

ok("SHORT_FORM_VIDEO is registered in TASK_FRAMING", lambda: assert_("SHORT_FORM_VIDEO" in TASK_FRAMING))
ok("SHORT_FORM_VIDEO has its own (small) generation token ceiling, not silently sharing another format's", lambda: (
    assert_("SHORT_FORM_VIDEO" in _MAX_TOKENS_BY_FORMAT),
    assert_(_MAX_TOKENS_BY_FORMAT["SHORT_FORM_VIDEO"] <= 500, "should be small -- this is a 100-150 word format"),
))
ok("SHORT_FORM_VIDEO has its own audit token ceiling", lambda: assert_("SHORT_FORM_VIDEO" in _AUDIT_MAX_TOKENS_BY_FORMAT))
ok("SHORT_FORM_VIDEO has its own, unique output suffix", lambda: (
    assert_("SHORT_FORM_VIDEO" in _OUTPUT_SUFFIX),
    assert_(_OUTPUT_SUFFIX["SHORT_FORM_VIDEO"] not in [v for k, v in _OUTPUT_SUFFIX.items() if k != "SHORT_FORM_VIDEO"], "must not collide with another format's output filename"),
))

# ============================================================
# 2. Structure -- 3-beat, NOT the 5-part structure
# ============================================================
print("\n=== STRUCTURE (3 beats, not 5) ===")

ok("SHORT_FORM_VIDEO is explicitly NOT one of the 5-part-structure formats", lambda: (
    assert_("SHORT_FORM_VIDEO" not in _FIVE_PART_STRUCTURE_FORMATS)
))
ok("SHORT_FORM_VIDEO IS one of the short-form-structure formats", lambda: (
    assert_("SHORT_FORM_VIDEO" in _SHORT_FORM_STRUCTURE_FORMATS)
))
ok("the short-form structure requires exactly HOOK / THE POINT / CTA, and forbids padding", lambda: (
    lambda block=_short_form_structure_block(): (
        assert_("HOOK" in block),
        assert_("THE POINT" in block),
        assert_("CTA" in block),
        assert_("NEVER pad" in block),
    )
)())
ok("the real prompt for SHORT_FORM_VIDEO carries the 3-beat structure, not the 5-part one", lambda: (
    lambda prompt=build_closed_book_prompt(RICH_PACK, "SHORT_FORM_VIDEO"): (
        assert_(prompt is not None),
        assert_("THE POINT" in prompt),
        assert_("WHO'S AFFECTED:" not in prompt, "the 5-part structure's own labels must not leak into short-form"),
        assert_("EVIDENCE QUALITY:" not in prompt),
    )
)())

# ============================================================
# 3. Reuses everything else unchanged -- anti-drift rule, tense rule,
# hard rules, provenance -- all universal, none reinvented for this format
# ============================================================
print("\n=== SHARED MACHINERY REUSED UNCHANGED ===")

ok("SHORT_FORM_VIDEO's framing carries the shared anti-drift rule", lambda: (
    assert_(_ANTI_DRIFT_RULE in TASK_FRAMING["SHORT_FORM_VIDEO"]("Kenya", 20))
))
ok("the real prompt still carries the universal HARD RULES and tense rule, unaffected by the new format", lambda: (
    lambda prompt=build_closed_book_prompt(RICH_PACK, "SHORT_FORM_VIDEO"): (
        assert_("HARD RULES" in prompt),
        assert_("HARD RULE 11" not in prompt or "already happened: state it as a recorded fact" in prompt),
    )
)())
ok("a story-pack's real prompt for SHORT_FORM_VIDEO still gets the CTA override note when a suggestedCTA is present -- the real CTA logic is completely format-agnostic", lambda: (
    lambda story_pack={"country": {"code": "KE", "name": "Kenya"}, "storyRecord": {"SIGNAL": {"value": "x", "pack_source": "[ECONOMIC:gdp_usd]", "status": "supported"}}, "suggestedCTA": "Claim your business listing on Zetu"}: (
        lambda prompt=build_closed_book_prompt(story_pack, "SHORT_FORM_VIDEO"): (
            assert_("THE REQUIRED CALL TO ACTION" in prompt),
            assert_("Claim your business listing on Zetu" in prompt),
        )
    )()
)())

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
