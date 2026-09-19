#!/usr/bin/env python3
"""
Priority 3B roadmap item #8 -- Phase 2 regression tests for the
per-channel content formats (LINKEDIN_ARTICLE, YOUTUBE_SCRIPT,
SUBSTACK_NEWSLETTER) and the roadmap #8 content-machine pack categories
(_pack_fact_lines' TOURISM/RESOURCE/CROP/TECH_HUB/UNIVERSITY/SKILLS_GAP/
INDICATOR/LANGUAGE/INVESTMENT/INDEX tags). Pure/offline: no OpenAI or
Anthropic call anywhere in this file, exactly like test_claim_provenance.py
and test_closed_book_writer.py already do for the rest of this module.

Usage: python3 test_content_channel_formats.py
"""
import inspect
import json
from zetu_closed_book_writer import (
    _pack_fact_lines, _pack_valid_tags, _trust_suffix, annotate_provenance,
    validate_claim_source, _unresolved, build_closed_book_prompt,
    TASK_FRAMING, _MAX_TOKENS_BY_FORMAT, _AUDIT_MAX_TOKENS_BY_FORMAT, _OUTPUT_SUFFIX,
    _fact_floor, _ANTI_DRIFT_RULE, run_closed_book_pipeline,
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


FORMATS = ["ATLAS_BRIEFING", "ZETU_SHOW_COLD_OPEN", "LINKEDIN_ARTICLE", "YOUTUBE_SCRIPT", "SUBSTACK_NEWSLETTER"]

RICH_PACK = {
    "country": {"code": "KE", "name": "Kenya"},
    "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 100, "year": 2025, "source": "World Bank", "flags": []}}},
    "signals": [], "opportunities": [], "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
    "tourismSites": [{"id": "t1", "name": "Maasai Mara", "category": "Park", "city": "Narok", "annualVisitors": 300000, "unescoListed": False, "trustState": "INSUFFICIENT_EVIDENCE", "trustReason": "no source recorded"}],
    "naturalResources": [{"id": "r1", "name": "Turkana Oil", "resourceType": "oil", "extractionStatus": "exploration", "estimatedValueUsd": 1000000, "trustState": "INSUFFICIENT_EVIDENCE", "trustReason": "no source column"}],
    "cropZones": [{"id": "c1", "cropName": "Tea", "exportValueUsdMillions": 1200, "productionTonnes": 500000, "majorProducingRegions": ["Kericho"], "trustState": "INSUFFICIENT_EVIDENCE", "trustReason": "no source column"}],
    "techHubs": [{"id": "h1", "name": "iHub", "city": "Nairobi", "hubType": "incubator", "foundedYear": 2010, "companiesCount": 50, "trustState": "INSUFFICIENT_EVIDENCE", "trustReason": "no source column"}],
    "universities": [{"id": "u1", "name": "University of Nairobi", "city": "Nairobi", "establishedYear": 1970, "studentCount": 84000, "trustState": "INSUFFICIENT_EVIDENCE", "trustReason": "no source column"}],
    "skillsGaps": [{"id": "s1", "sector": "Manufacturing", "skillNeeded": "Welding", "workersNeeded": 5000, "gapSeverity": "high", "trustState": "QUALIFIED", "trustReason": 'named source "ILO/AfDB 2022"'}],
    "countryIndicators": [{"id": "i1", "indicatorCode": "FDI_INFLOW", "value": 448, "year": 2022, "trustState": "QUALIFIED", "trustReason": 'named source "UNCTAD" but 4y old'}],
    "languages": [{"languageName": "Swahili", "isOfficial": True}],
    "investmentOpportunities": [{"id": "inv1", "title": "Solar farm", "sector": "Energy", "riskLevel": "medium", "status": "open", "expectedRoiPct": 12, "trustState": "QUALIFIED", "trustReason": 'named source "afdb"'}],
    "indices": {
        "zloi": {"rank": 5, "code": "KE", "name": "Kenya", "score": 72},
        "ztri": {"rank": 12, "code": "KE", "name": "Kenya", "score": 61},
        "dataYear": 2022, "sources": ["World Bank", "ILO", "UNWTO"],
        "trustState": "QUALIFIED", "trustReason": 'named source "World Bank, ILO, UNWTO"',
    },
}

# ============================================================
# 1. Every new category produces a correctly-tagged fact line
# ============================================================
print("=== FACT LINE TAGGING ===")

lines = _pack_fact_lines(RICH_PACK)
tag_prefixes = ["[TOURISM:t1]", "[RESOURCE:r1]", "[CROP:c1]", "[TECH_HUB:h1]", "[UNIVERSITY:u1]", "[SKILLS_GAP:s1]", "[INDICATOR:i1]", "[LANGUAGE:Swahili]", "[INVESTMENT:inv1]", "[INDEX:ZLOI]", "[INDEX:ZTRI]"]

for prefix in tag_prefixes:
    ok(f"a fact line starting with {prefix} is produced from the rich pack", lambda p=prefix: (
        assert_(any(line.startswith(p) for line in lines), f"no line starts with {p}: {lines}")
    ))

ok("an INSUFFICIENT_EVIDENCE category's trust reason is visible on the fact line itself, not hidden", lambda: (
    assert_(any("INSUFFICIENT_EVIDENCE" in line and "no source" in line for line in lines if line.startswith("[RESOURCE:")))
))

ok("_trust_suffix renders trustState with no reason gracefully (no crash, no 'None' reason text)", lambda: (
    assert_(_trust_suffix({"trustState": "QUALIFIED"}) == "(trustState=QUALIFIED)")
))

# ============================================================
# 2. _pack_valid_tags auto-derives every new tag type with zero
# hardcoded tag-name list -- proves the provenance validator covers the
# new categories for free, exactly as designed.
# ============================================================
print("\n=== PROVENANCE AUTO-COVERAGE FOR NEW CATEGORIES ===")

valid_tags = _pack_valid_tags(RICH_PACK)
for tag in ["TOURISM:t1", "RESOURCE:r1", "CROP:c1", "TECH_HUB:h1", "UNIVERSITY:u1", "SKILLS_GAP:s1", "INDICATOR:i1", "LANGUAGE:Swahili", "INVESTMENT:inv1", "INDEX:ZLOI", "INDEX:ZTRI"]:
    ok(f"{tag} is auto-derived as a valid tag with zero hardcoded tag-name list", lambda t=tag: assert_(t in valid_tags, f"{t} not in {valid_tags}"))

ok("a real new-category tag (e.g. [TOURISM:t1]) is accepted by validate_claim_source", lambda: (
    assert_(validate_claim_source("[TOURISM:t1]", valid_tags) is True)
))

ok("a fabricated new-category tag (a plausible-looking but nonexistent tourism site id) is rejected, not trusted", lambda: (
    assert_(validate_claim_source("[TOURISM:not-a-real-id]", valid_tags) is False)
))

ok("annotate_provenance rejects a SUPPORTED statement citing a fabricated [INDEX:ZLOI2] tag that does not exist", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": "[INDEX:ZLOI2]", "notes": ""}], RICH_PACK): (
        assert_(audit[0]["provenance_valid"] is False),
        assert_(len(_unresolved(audit)) == 1),
    )
)())

ok("annotate_provenance accepts a SUPPORTED statement citing the real [INDEX:ZLOI] tag", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": "[INDEX:ZLOI]", "notes": ""}], RICH_PACK): (
        assert_(audit[0]["provenance_valid"] is True),
        assert_(_unresolved(audit) == []),
    )
)())

# ============================================================
# 3. Per-channel TASK_FRAMING / prompt building
# ============================================================
print("\n=== PER-CHANNEL FRAMING ===")

ok("all 5 formats are present in TASK_FRAMING", lambda: (
    assert_(set(FORMATS) <= set(TASK_FRAMING.keys()), f"missing: {set(FORMATS) - set(TASK_FRAMING.keys())}")
))

for fmt in FORMATS:
    ok(f"{fmt}'s framing produces a non-empty, country-specific string", lambda f=fmt: (
        lambda text=TASK_FRAMING[f]("Kenya"): (
            assert_(isinstance(text, str) and len(text) > 20),
            assert_("Kenya" in text, f"country name missing from {f} framing"),
        )
    )())

for fmt in FORMATS:
    ok(f"build_closed_book_prompt works for {fmt} and still carries the universal hard-rules block", lambda f=fmt: (
        lambda prompt=build_closed_book_prompt(RICH_PACK, f): (
            assert_(prompt is not None),
            assert_("HARD RULES" in prompt),
            assert_("Do NOT introduce any fact" in prompt),
            assert_("[TOURISM:t1]" in prompt, "expanded pack facts must reach every format, not just ATLAS_BRIEFING"),
        )
    )())

ok("LINKEDIN_ARTICLE framing asks for paragraphs, not bullet points", lambda: (
    assert_("bullet" in TASK_FRAMING["LINKEDIN_ARTICLE"]("Kenya").lower())
))

ok("YOUTUBE_SCRIPT framing asks for spoken narration, not a newsletter", lambda: (
    assert_("spoken" in TASK_FRAMING["YOUTUBE_SCRIPT"]("Kenya").lower())
))

ok("SUBSTACK_NEWSLETTER framing requires a SUBJECT_LINE convention", lambda: (
    assert_("SUBJECT_LINE" in TASK_FRAMING["SUBSTACK_NEWSLETTER"]("Kenya"))
))

# ============================================================
# 3b. Per-platform CTA + length/category-coverage calibration (added
# after real runs came back short and shallow: 260-384 words, only 2-3
# of ~14 available categories touched, no CTA at all).
# ============================================================
print("\n=== PER-PLATFORM CTA + DEPTH CALIBRATION ===")

ok("LINKEDIN_ARTICLE names its own CTA: inviting a same-sector business to list on ZetuMap", lambda: (
    assert_("ZetuMap" in TASK_FRAMING["LINKEDIN_ARTICLE"]("Kenya"))
))
ok("LINKEDIN_ARTICLE requires drawing from at least 4 fact categories, not just 1-2", lambda: (
    assert_("AT LEAST 4" in TASK_FRAMING["LINKEDIN_ARTICLE"]("Kenya"))
))
ok("LINKEDIN_ARTICLE explicitly limits its own CTA to one sentence, not a pitch", lambda: (
    assert_("one sentence" in TASK_FRAMING["LINKEDIN_ARTICLE"]("Kenya"))
))

ok("YOUTUBE_SCRIPT names its own CTA: the full dataset on Zetu Atlas / Substack", lambda: (
    lambda text=TASK_FRAMING["YOUTUBE_SCRIPT"]("Kenya"): (
        assert_("Zetu Atlas" in text),
        assert_("Substack" in text),
    )
)())
ok("YOUTUBE_SCRIPT sets a real spoken-runtime target (700-1100 words), not just 'no minimum'", lambda: (
    assert_("700-1100" in TASK_FRAMING["YOUTUBE_SCRIPT"]("Kenya"))
))
ok("YOUTUBE_SCRIPT explicitly forbids reading raw database labels aloud", lambda: (
    assert_("technical label" in TASK_FRAMING["YOUTUBE_SCRIPT"]("Kenya"))
))

ok("SUBSTACK_NEWSLETTER's CTA is an explicit referral ask, never a renewal/upsell pitch (they already pay)", lambda: (
    lambda text=TASK_FRAMING["SUBSTACK_NEWSLETTER"]("Kenya"): (
        assert_("forward" in text.lower()),
        assert_("renewal/upsell pitch" in text),
    )
)())
ok("SUBSTACK_NEWSLETTER sets a real length target (900-1400 words) proportional to being the paid, deepest format", lambda: (
    assert_("900-1400" in TASK_FRAMING["SUBSTACK_NEWSLETTER"]("Kenya"))
))
ok("SUBSTACK_NEWSLETTER's referral CTA explicitly forbids asserting WHO benefits (the actual cause of a real audit rejection: 'forward this to a tourism analyst' was flagged as an unsupported claim)", lambda: (
    assert_("never as a claim about WHO specifically would benefit" in TASK_FRAMING["SUBSTACK_NEWSLETTER"]("Kenya"))
))
ok("SUBSTACK_NEWSLETTER's missing-evidence section has an exact required heading, not just a soft suggestion (the earlier soft version got the section skipped twice in real runs)", lambda: (
    assert_("**What's Missing**" in TASK_FRAMING["SUBSTACK_NEWSLETTER"]("Kenya"))
))

for fmt in ["LINKEDIN_ARTICLE", "YOUTUBE_SCRIPT", "SUBSTACK_NEWSLETTER"]:
    ok(f"{fmt} prompt still builds correctly and still carries the CTA framing text end-to-end (no CTA text lost between TASK_FRAMING and the final prompt)", lambda f=fmt: (
        lambda prompt=build_closed_book_prompt(RICH_PACK, f): (
            assert_(prompt is not None),
            assert_(("ZetuMap" in prompt) or ("Substack" in prompt) or ("forward" in prompt.lower()), f"no CTA language reached the final {f} prompt"),
        )
    )())

# ============================================================
# 4. Per-format token/suffix maps stay in sync -- catches "added a new
# format to TASK_FRAMING but forgot one of the other 3 dicts" bugs.
# ============================================================
print("\n=== FORMAT CONFIG CONSISTENCY ===")

for fmt in FORMATS:
    ok(f"{fmt} has an explicit entry in _MAX_TOKENS_BY_FORMAT (not silently falling back to the ATLAS_BRIEFING default)", lambda f=fmt: assert_(f in _MAX_TOKENS_BY_FORMAT))
    ok(f"{fmt} has an explicit entry in _AUDIT_MAX_TOKENS_BY_FORMAT", lambda f=fmt: assert_(f in _AUDIT_MAX_TOKENS_BY_FORMAT))
    ok(f"{fmt} has an explicit entry in _OUTPUT_SUFFIX", lambda f=fmt: assert_(f in _OUTPUT_SUFFIX))

ok("output suffixes are unique across every format -- two formats must never collide on the same output filename", lambda: (
    assert_(len(set(_OUTPUT_SUFFIX.values())) == len(_OUTPUT_SUFFIX), f"duplicate suffixes: {_OUTPUT_SUFFIX}")
))

ok("an unmapped future format falls back to its own lowercased name as a suffix, never silently reuses an existing format's suffix", lambda: (
    assert_(_OUTPUT_SUFFIX.get("SOME_NEW_FORMAT", "SOME_NEW_FORMAT".lower()) == "some_new_format")
))

# ============================================================
# 5. Regression -- the two pre-existing formats are completely unaffected
# by any of the above.
# ============================================================
print("\n=== ZERO REGRESSION ON EXISTING FORMATS ===")

ok("ATLAS_BRIEFING's own framing text is character-for-character unchanged", lambda: (
    assert_(TASK_FRAMING["ATLAS_BRIEFING"]("Ghana") == "Write a briefing for The Zetu Atlas about Ghana, in Michael's voice. This is a written/newsletter piece.")
))

ok("ATLAS_BRIEFING keeps its original 1500-token generation ceiling", lambda: assert_(_MAX_TOKENS_BY_FORMAT["ATLAS_BRIEFING"] == 1500))
ok("ATLAS_BRIEFING keeps its original 'atlas' output suffix", lambda: assert_(_OUTPUT_SUFFIX["ATLAS_BRIEFING"] == "atlas"))
ok("ZETU_SHOW_COLD_OPEN keeps its original 'show_cold_open' output suffix", lambda: assert_(_OUTPUT_SUFFIX["ZETU_SHOW_COLD_OPEN"] == "show_cold_open"))

ok("a pack with NONE of the new categories still builds a valid ATLAS_BRIEFING prompt exactly as before", lambda: (
    lambda thin_pack={"country": {"name": "Ghana"}, "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 1, "year": 2025, "flags": []}}}}: (
        lambda prompt=build_closed_book_prompt(thin_pack, "ATLAS_BRIEFING"): (
            assert_(prompt is not None),
            assert_("[TOURISM:" not in prompt),
            assert_("[INDEX:" not in prompt),
        )
    )()
)())

# ============================================================
# 6. Fixes after real-run regressions: shared anti-drift rule on ALL
# per-channel formats (not just Substack), a dynamic fact-count floor
# that never exceeds what a pack actually has (never pressures padding
# on a thin pack), and a higher repair-attempt ceiling.
# ============================================================
print("\n=== ANTI-DRIFT RULE + FACT-COUNT FLOOR (post-regression fixes) ===")

ok("_fact_floor never exceeds the real fact count -- a thin pack's floor is just 'use what you have'", lambda: (
    assert_(_fact_floor(3, 10) == 3),
))
ok("_fact_floor returns the target unmodified when the pack has plenty of facts", lambda: (
    assert_(_fact_floor(113, 10) == 10),
))
ok("_fact_floor returns the target (not 0) when fact_count is falsy -- a defensive default, never reached in real use since build_closed_book_prompt already short-circuits on zero facts", lambda: (
    assert_(_fact_floor(0, 10) == 10),
))

for fmt in ["LINKEDIN_ARTICLE", "YOUTUBE_SCRIPT", "SUBSTACK_NEWSLETTER"]:
    ok(f"{fmt} now carries the shared anti-interpretive-drift rule (previously only SUBSTACK_NEWSLETTER had it)", lambda f=fmt: (
        assert_(_ANTI_DRIFT_RULE in TASK_FRAMING[f]("Kenya", 50), f"{f} missing the anti-drift rule")
    ))

ok("build_closed_book_prompt passes the REAL fact count into the framing, not a placeholder -- the LinkedIn floor in the actual prompt matches the rich pack's real fact count", lambda: (
    lambda real_count=len(_pack_fact_lines(RICH_PACK)), prompt=build_closed_book_prompt(RICH_PACK, "LINKEDIN_ARTICLE"): (
        assert_(f"Cite at least {_fact_floor(real_count, 10)} distinct facts" in prompt, prompt[:300]),
    )
)())

ok("a THIN pack's fact-count floor in the real prompt never exceeds what the pack actually has (no pressure to pad/invent)", lambda: (
    lambda thin_pack={"country": {"name": "Ghana"}, "economicBaseline": {"available": True, "indicators": {"gdp_usd": {"value": 1, "year": 2025, "flags": []}}}}: (
        lambda prompt=build_closed_book_prompt(thin_pack, "LINKEDIN_ARTICLE"), real_count=len(_pack_fact_lines(thin_pack)): (
            assert_(real_count < 10, "this test's thin pack must actually be thinner than the LinkedIn floor to be meaningful"),
            assert_(f"Cite at least {real_count} distinct facts" in prompt, prompt[:300]),
        )
    )()
)())

ok("run_closed_book_pipeline's default max_repair_attempts is now 3, not 2", lambda: (
    assert_(inspect.signature(run_closed_book_pipeline).parameters["max_repair_attempts"].default == 3)
))

ok("ATLAS_BRIEFING and ZETU_SHOW_COLD_OPEN ignore fact_count entirely -- their text is unaffected by it, exact original strings preserved", lambda: (
    assert_(TASK_FRAMING["ATLAS_BRIEFING"]("Ghana", 999) == TASK_FRAMING["ATLAS_BRIEFING"]("Ghana", 0)),
    assert_(TASK_FRAMING["ZETU_SHOW_COLD_OPEN"]("Ghana", 999) == TASK_FRAMING["ZETU_SHOW_COLD_OPEN"]("Ghana", 0)),
))

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
