#!/usr/bin/env python3
"""
Real founder-designed 5-part structure regression tests: HOOK -> Evidence
Chain (what's happening/why it matters/who's affected) -> THE OPPORTUNITY
(named, honest about business-match absence) -> EVIDENCE QUALITY ->
CTA. Also covers the trust-state visibility fix (_pack_fact_lines now
shows trustState on ECONOMIC/SIGNAL/OPPORTUNITY lines, matching what the
roadmap #8 categories already showed). Pure/offline: no OpenAI or
Anthropic call anywhere in this file.

Usage: python3 test_five_part_structure.py
"""
from zetu_closed_book_writer import (
    build_closed_book_prompt, _pack_fact_lines, _FIVE_PART_STRUCTURE_FORMATS,
    _five_part_structure_block,
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
    "country": {"code": "GH", "name": "Ghana"},
    "economicBaseline": {
        "available": True,
        "indicators": {"gdp_usd": {"value": 100, "year": 2025, "source": "World Bank", "flags": [], "trustState": "VERIFIED", "trustReason": "live-ingested, fresh"}},
    },
    "signals": [{"id": "sig1", "title": "s", "trustState": "SOURCE_VERIFIED", "intelligenceTrustState": "QUALIFIED", "claimBoundaries": {}}],
    "opportunities": [{"id": "opp1", "title": "o", "opportunityState": "OPPORTUNITY_SIGNAL", "intelligenceTrustState": "INSUFFICIENT_EVIDENCE", "mayClaim": [], "mayNotClaim": []}],
    "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
}

# ============================================================
# 1. Format coverage -- exactly the right formats get the structure
# ============================================================
print("=== FORMAT COVERAGE ===")

ok("ATLAS_BRIEFING, LINKEDIN_ARTICLE, SUBSTACK_NEWSLETTER are the written 5-part-structure formats", lambda: (
    assert_(set(_FIVE_PART_STRUCTURE_FORMATS) == {"ATLAS_BRIEFING", "LINKEDIN_ARTICLE", "SUBSTACK_NEWSLETTER"})
))

for fmt in ["ATLAS_BRIEFING", "LINKEDIN_ARTICLE", "SUBSTACK_NEWSLETTER"]:
    ok(f"{fmt}'s real prompt requires the full labeled 5-part structure", lambda f=fmt: (
        lambda prompt=build_closed_book_prompt(RICH_PACK, f): (
            assert_(prompt is not None),
            assert_("HOOK:" in prompt),
            assert_("WHAT'S HAPPENING:" in prompt),
            assert_("WHY IT MATTERS:" in prompt),
            assert_("WHO'S AFFECTED:" in prompt),
            assert_("THE OPPORTUNITY:" in prompt),
            assert_("EVIDENCE QUALITY:" in prompt),
            assert_("CTA:" in prompt),
        )
    )())

ok("YOUTUBE_SCRIPT gets the same 5 beats but WITHOUT literal on-screen labels (spoken variant)", lambda: (
    lambda prompt=build_closed_book_prompt(RICH_PACK, "YOUTUBE_SCRIPT"): (
        assert_(prompt is not None),
        assert_("do NOT say these labels out loud" in prompt),
        assert_("WHAT'S HAPPENING:" not in prompt, "the spoken variant must not use literal written-format labels"),
    )
)())

ok("ZETU_SHOW_COLD_OPEN gets NEITHER structure variant -- it's a 30-45s teaser, structurally incompatible with 5 sections", lambda: (
    lambda prompt=build_closed_book_prompt(RICH_PACK, "ZETU_SHOW_COLD_OPEN"): (
        assert_(prompt is not None),
        assert_("REQUIRED STRUCTURE" not in prompt),
        assert_("HOOK:" not in prompt),
    )
)())

# ============================================================
# 2. THE OPPORTUNITY section's honesty requirement -- must report the
# ABSENCE of a business match rather than let the model imply one.
# ============================================================
print("\n=== HONEST BUSINESS-MATCH REPORTING ===")

ok("THE OPPORTUNITY section explicitly requires reporting when NO business match exists, rather than staying silent (which could read as an implied match)", lambda: (
    assert_("no business match is recorded yet" in _five_part_structure_block(spoken=False))
))
ok("the spoken (YouTube) variant carries the identical honesty requirement", lambda: (
    assert_("no business match is recorded yet" in _five_part_structure_block(spoken=True))
))
ok("EVIDENCE QUALITY section explicitly requires using real trust-state vocabulary, not an invented confidence claim", lambda: (
    assert_("VERIFIED/QUALIFIED/INSUFFICIENT_EVIDENCE" in _five_part_structure_block(spoken=False))
))

# ============================================================
# 2b. CTA vs. voice_guide.txt's generic sign-off -- fix after a real run
# dropped the specific per-format CTA entirely and used only the
# voice guide's hardcoded "End every briefing with..." sign-off instead.
# ============================================================
ok("the CTA section explicitly disambiguates the specific CTA from the voice guide's generic sign-off (written variant)", lambda: (
    lambda block=_five_part_structure_block(spoken=False): (
        assert_("is NOT this CTA" in block),
        assert_("never instead of it" in block),
    )
)())
ok("the CTA section explicitly disambiguates the specific CTA from the voice guide's generic sign-off (spoken variant)", lambda: (
    lambda block=_five_part_structure_block(spoken=True): (
        assert_("is NOT this CTA" in block),
        assert_("never instead of it" in block),
    )
)())

# ============================================================
# 3. Trust-state visibility fix -- ECONOMIC/SIGNAL/OPPORTUNITY fact
# lines now show trust state, matching what roadmap #8 categories
# already showed (previously inconsistent: only the new categories
# surfaced this).
# ============================================================
print("\n=== TRUST-STATE VISIBILITY (fix for uneven EVIDENCE QUALITY reporting) ===")

lines = _pack_fact_lines(RICH_PACK)

ok("an ECONOMIC fact line now shows trustState/trustReason (previously silently dropped even though the pack computes it)", lambda: (
    lambda line=next(l for l in lines if l.startswith("[ECONOMIC:")): (
        assert_("trustState=VERIFIED" in line, line),
    )
)())
ok("a SIGNAL fact line now shows BOTH the raw trust_state and the computed intelligenceTrustState", lambda: (
    lambda line=next(l for l in lines if l.startswith("[SIGNAL:")): (
        assert_("trustState=SOURCE_VERIFIED" in line, line),
        assert_("intelligenceTrustState=QUALIFIED" in line, line),
    )
)())
ok("an OPPORTUNITY fact line now shows intelligenceTrustState", lambda: (
    lambda line=next(l for l in lines if l.startswith("[OPPORTUNITY:")): (
        assert_("intelligenceTrustState=INSUFFICIENT_EVIDENCE" in line, line),
    )
)())

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
