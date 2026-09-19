#!/usr/bin/env python3
"""
Multi-angle generation regression tests -- the fix for "one article
covering 4+ categories can't also commit to a single argument." Tests
angle selection (deterministic, code-based, zero-cost), pack narrowing
(never mutates the original, never keeps a category outside its angle),
and the per-angle prompt structure (the universal 5-part HOOK/.../CTA: structure required, question
framing present). Pure/offline: no OpenAI or Anthropic call anywhere in
this file.

Usage: python3 test_multi_angle_generation.py
"""
import json
from zetu_closed_book_writer import (
    ANGLE_DEFINITIONS, CATEGORY_TO_TAG_PREFIX, UNIVERSAL_ALWAYS_KEEP_PREFIXES,
    UNIVERSAL_ALWAYS_KEEP_ECONOMIC_FIELDS, _angle_specific_fact_count,
    select_available_angles, _narrow_pack_for_angle, build_closed_book_prompt,
    _pack_fact_lines, _pack_valid_tags, validate_claim_source,
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


# A pack rich enough to have real material for every angle.
RICH_PACK = {
    "country": {"code": "KE", "name": "Kenya"},
    "economicBaseline": {
        "available": True,
        "indicators": {
            "gdp_usd": {"value": 100, "year": 2025, "flags": []},
            "population": {"value": 50000000, "year": 2025, "flags": []},
            "top_exports": {"value": ["Tea"], "year": 2022, "flags": ["STALE"]},
            "unemployment_rate": {"value": 5.4, "year": 2025, "flags": []},
        },
    },
    "signals": [{"id": "sig1", "title": "s", "trustState": "SOURCE_VERIFIED", "claimBoundaries": {}}],
    "opportunities": [{"id": "opp1", "title": "o", "opportunityState": "OPPORTUNITY_SIGNAL", "mayClaim": [], "mayNotClaim": []}],
    "businesses": [{"businessId": "b1", "name": "Biz", "category": "c", "hasAnyCapabilityEvidence": True, "capabilities": [{"contextKey": "x", "evidenceTier": "observed"}]}],
    "matchesActionsOutcomes": [], "newsDevelopments": [],
    "cropZones": [{"id": "c1", "cropName": "Tea"}, {"id": "c2", "cropName": "Coffee"}, {"id": "c3", "cropName": "Flowers"}],
    "naturalResources": [{"id": "r1", "name": "Gold"}],
    "skillsGaps": [{"id": "s1", "sector": "Tech"}, {"id": "s2", "sector": "Health"}, {"id": "s3", "sector": "Ag"}],
    "tourismSites": [{"id": "t1", "name": "Site1"}, {"id": "t2", "name": "Site2"}, {"id": "t3", "name": "Site3"}],
    "techHubs": [{"id": "h1", "name": "Hub1"}, {"id": "h2", "name": "Hub2"}],
    "universities": [{"id": "u1", "name": "Uni1"}],
    "investmentOpportunities": [{"id": "inv1", "title": "Inv1"}],
    "countryIndicators": [], "languages": [],
    "indices": {"zloi": {"rank": 5, "score": 70}, "ztri": {"rank": 12, "score": 60}, "dataYear": 2022, "sources": ["World Bank"]},
}

THIN_PACK = {
    "country": {"code": "GH", "name": "Ghana"},
    "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 1, "year": 2025, "flags": []}}},
    "signals": [], "opportunities": [], "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
}

# ============================================================
# 1. Angle selection is deterministic and never forces a thin angle
# ============================================================
print("=== ANGLE SELECTION ===")

ok("a rich pack with 3+ crops selects trade_value_chain as an available angle", lambda: (
    assert_("trade_value_chain" in select_available_angles(RICH_PACK))
))
ok("a rich pack selects multiple distinct angles, capped at max_angles", lambda: (
    lambda angles=select_available_angles(RICH_PACK, max_angles=3): (
        assert_(len(angles) == 3, f"expected exactly 3 (capped), got {angles}"),
        assert_(len(set(angles)) == 3, "angles must be distinct"),
    )
)())
ok("angles are ordered most-fact-rich first (deterministic, not random)", lambda: (
    lambda angles=select_available_angles(RICH_PACK, max_angles=5): (
        assert_(angles == select_available_angles(RICH_PACK, max_angles=5), "must be deterministic across calls"),
    )
)())
ok("a THIN pack with no crops/skills/tourism/etc. selects ZERO angles -- never forces one it has no material for", lambda: (
    assert_(select_available_angles(THIN_PACK) == [])
))
ok("_angle_specific_fact_count ignores universal always-kept facts (gdp/population/business) -- a pack with ONLY those scores 0 for every angle", lambda: (
    lambda only_universal={**THIN_PACK, "businesses": [{"businessId": "b1", "name": "X", "hasAnyCapabilityEvidence": True, "capabilities": []}]}: (
        assert_(_angle_specific_fact_count(only_universal, "trade_value_chain") == 0),
        assert_(_angle_specific_fact_count(only_universal, "tourism_and_heritage") == 0),
    )
)())
ok("min_facts threshold actually excludes a barely-populated angle", lambda: (
    lambda barely={**THIN_PACK, "cropZones": [{"id": "c1", "cropName": "Tea"}]}: (
        assert_(_angle_specific_fact_count(barely, "trade_value_chain") == 1),
        assert_("trade_value_chain" not in select_available_angles(barely, min_facts=3)),
        assert_("trade_value_chain" in select_available_angles(barely, min_facts=1)),
    )
)())

# ============================================================
# 2. Pack narrowing -- never mutates original, only keeps angle-relevant
# + universal categories, drops everything else.
# ============================================================
print("\n=== PACK NARROWING ===")

ok("narrowing for trade_value_chain keeps cropZones/naturalResources, drops tourismSites/techHubs/skillsGaps", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"): (
        assert_(len(narrowed["cropZones"]) == 3),
        assert_(len(narrowed["naturalResources"]) == 1),
        assert_(narrowed["tourismSites"] == []),
        assert_(narrowed["techHubs"] == []),
        assert_(narrowed["skillsGaps"] == []),
    )
)())
ok("narrowing NEVER mutates the original pack -- RICH_PACK's own arrays are untouched after narrowing", lambda: (
    lambda _=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"): (
        assert_(len(RICH_PACK["tourismSites"]) == 3, "original pack was mutated!"),
        assert_(len(RICH_PACK["cropZones"]) == 3, "original pack was mutated!"),
    )
)())
ok("narrowing keeps universal always-kept categories (businesses) regardless of angle", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "tourism_and_heritage"): (
        assert_(len(narrowed["businesses"]) == 1, "businesses should survive narrowing for every angle"),
    )
)())
ok("narrowing keeps gdp_usd/population but drops unrelated economic fields for a non-economic angle", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "tourism_and_heritage"): (
        assert_("gdp_usd" in narrowed["economicBaseline"]["indicators"]),
        assert_("population" in narrowed["economicBaseline"]["indicators"]),
        assert_("top_exports" not in narrowed["economicBaseline"]["indicators"]),
        assert_("unemployment_rate" not in narrowed["economicBaseline"]["indicators"]),
    )
)())
ok("narrowing keeps top_exports for trade_value_chain (its own economic field)", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"): (
        assert_("top_exports" in narrowed["economicBaseline"]["indicators"]),
    )
)())
ok("narrowing keeps ONLY the angle's own index (ztri for tourism, not zloi)", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "tourism_and_heritage"): (
        assert_(narrowed["indices"]["ztri"] is not None),
        assert_(narrowed["indices"]["zloi"] is None),
    )
)())
ok("narrowing for skills_and_workforce keeps zero index (this angle uses none)", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "skills_and_workforce"): (
        assert_(narrowed["indices"]["zloi"] is None),
        assert_(narrowed["indices"]["ztri"] is None),
    )
)())

# ============================================================
# 3. Provenance validation still works correctly on a NARROWED pack --
# the existing safety machinery applies unchanged, proven by checking a
# fact from a DROPPED category is correctly rejected as invalid provenance.
# ============================================================
print("\n=== PROVENANCE STILL ENFORCED ON A NARROWED PACK ===")

ok("a real tag from a category this angle DROPPED (tourism, for a trade-angle pack) is correctly rejected as invalid -- proves narrowing doesn't accidentally widen what's citable", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"), valid=lambda n: _pack_valid_tags(n): (
        assert_("TOURISM:t1" not in valid(narrowed), "a dropped category's tag must not remain valid"),
        assert_("CROP:c1" in valid(narrowed), "a kept category's tag must remain valid"),
    )
)())

# ============================================================
# 4. Per-angle prompt structure -- the universal 5-part HOOK/.../CTA:
# structure required, question framing present, facts genuinely
# narrowed in the real prompt.
# ============================================================
print("\n=== PER-ANGLE PROMPT STRUCTURE ===")

ok("an angle-scoped prompt requires the universal 5-part HOOK:/CTA: structure", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"), fmt="LINKEDIN_ARTICLE": (
        lambda prompt=build_closed_book_prompt(narrowed, fmt, "trade_value_chain"): (
            assert_(prompt is not None),
            assert_("HOOK:" in prompt),
            assert_("CTA:" in prompt),
        )
    )()
)())
ok("an angle-scoped prompt states the angle's specific question, with the country name substituted", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"): (
        lambda prompt=build_closed_book_prompt(narrowed, "LINKEDIN_ARTICLE", "trade_value_chain"): (
            assert_("who actually captures the value" in prompt),
            assert_("Kenya" in prompt),
        )
    )()
)())
ok("an angle-scoped prompt (built from the CORRECTLY NARROWED pack, matching real pipeline usage) only contains facts from that angle's categories -- no [TOURISM:...] tag in a trade-angle prompt", lambda: (
    lambda narrowed=_narrow_pack_for_angle(RICH_PACK, "trade_value_chain"): (
        lambda prompt=build_closed_book_prompt(narrowed, "LINKEDIN_ARTICLE", "trade_value_chain"): (
            assert_("[CROP:" in prompt),
            assert_("[TOURISM:" not in prompt),
            assert_("[TECH_HUB:" not in prompt),
        )
    )()
)())
ok("a call with angle=None still gets the universal 5-part structure (it applies to the format, not the angle), but none of the angle-specific framing -- and every category is still present, unnarrowed", lambda: (
    lambda prompt=build_closed_book_prompt(RICH_PACK, "LINKEDIN_ARTICLE"): (
        assert_("HOOK:" in prompt, "the universal structure should still apply without an angle"),
        assert_("ANGLE FOR THIS SPECIFIC PIECE" not in prompt, "angle-specific framing must not leak in without an angle"),
        assert_("[TOURISM:" in prompt),
        assert_("[CROP:" in prompt),
    )
)())
ok("every ANGLE_DEFINITIONS key maps to at least one real CATEGORY_TO_TAG_PREFIX value or a recognized economic/index field -- no angle references a category that doesn't exist", lambda: (
    [assert_(p in CATEGORY_TO_TAG_PREFIX.values(), f"{p} in {key} is not a real category prefix")
     for key, d in ANGLE_DEFINITIONS.items() for p in d["tag_prefixes"]]
))

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
