#!/usr/bin/env python3
"""
Zetu Story Record regression tests -- the shared editorial interpretation
layer between the Intelligence Pack and any platform generator
(Pack -> Story Record -> platform content, never Pack -> platform
directly). Pure/offline: no OpenAI or Anthropic call anywhere in this
file. Tests the field-shape contract, the reuse of annotate_provenance()
unchanged, the story-pack fact-line branch in _pack_fact_lines(), and
that build_closed_book_prompt() correctly treats a story-pack as
already-approved (no re-interpretation instruction leaks into a normal
pack's prompt).

Usage: python3 test_story_record.py
"""
from zetu_closed_book_writer import (
    STORY_RECORD_FIELD_DEFINITIONS, _story_record_fact_lines,
    _story_record_to_audit_entries, annotate_story_record,
    _unresolved_story_fields, build_story_pack, _pack_fact_lines,
    _pack_valid_tags, validate_claim_source, build_closed_book_prompt,
    _select_best_story_candidate,
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


PACK = {
    "country": {"code": "KE", "name": "Kenya"},
    "economicBaseline": {
        "available": True,
        "indicators": {"gdp_usd": {"value": 100, "year": 2025, "source": "World Bank", "flags": [], "trustState": "VERIFIED", "trustReason": "fresh"}},
    },
    "signals": [], "opportunities": [
        {"id": "opp1", "title": "Real Opportunity", "opportunityState": "OPPORTUNITY_SIGNAL", "intelligenceTrustState": "QUALIFIED", "mayClaim": [], "mayNotClaim": ["guaranteed return"]},
    ],
    "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
}

# ============================================================
# 1. Field shape / structural contract
# ============================================================
print("=== FIELD DEFINITIONS ===")

ok("all 12 LLM-extracted fields from the spec are present, PLACE is deliberately excluded (structural, not extracted)", lambda: (
    assert_(set(STORY_RECORD_FIELD_DEFINITIONS.keys()) == {
        "SIGNAL", "OBJECT", "QUESTION", "MONEY", "CHAIN", "GAP", "PROOF",
        "BUILDER", "OPPORTUNITY", "OBSTACLE", "ACTION", "OPEN_THREAD",
    }),
    assert_("PLACE" not in STORY_RECORD_FIELD_DEFINITIONS, "PLACE is structural, not an LLM-extracted/audited field"),
))

# ============================================================
# 2. Reuse of annotate_provenance() COMPLETELY UNCHANGED, via the
# adapter -- proves a fabricated citation is rejected the same way a
# fabricated briefing claim is, with zero new provenance logic.
# ============================================================
print("\n=== PROVENANCE REUSE (annotate_provenance UNCHANGED) ===")

ok("a story field citing a REAL tag and passing semantic audit is marked supported", lambda: (
    lambda sr={"SIGNAL": {"value": "Kenya's GDP is $100", "pack_source": "[ECONOMIC:gdp_usd]"},
               **{k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS if k != "SIGNAL"}}: (
        lambda annotated=annotate_story_record(sr, PACK, {"SIGNAL": "SUPPORTED"}): (
            assert_(annotated["SIGNAL"]["status"] == "supported"),
            assert_(annotated["SIGNAL"]["value"] == "Kenya's GDP is $100"),
        )
    )()
)())

ok("a story field citing a FABRICATED tag is forced to unsupported -- same rejection annotate_provenance already proved on briefing claims", lambda: (
    lambda sr={"SIGNAL": {"value": "x", "pack_source": "[ECONOMIC:invented_field]"},
               **{k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS if k != "SIGNAL"}}: (
        lambda annotated=annotate_story_record(sr, PACK, {"SIGNAL": "SUPPORTED"}): (
            assert_(annotated["SIGNAL"]["status"] == "unsupported"),
            assert_(annotated["SIGNAL"]["value"] is None),
        )
    )()
)())

ok("a field with a REAL tag but a FAILED semantic audit (misstates the fact) is still forced to unsupported", lambda: (
    lambda sr={"SIGNAL": {"value": "wildly wrong restatement", "pack_source": "[ECONOMIC:gdp_usd]"},
               **{k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS if k != "SIGNAL"}}: (
        lambda annotated=annotate_story_record(sr, PACK, {"SIGNAL": "UNKNOWN"}): (
            assert_(annotated["SIGNAL"]["status"] == "unsupported", "a real tag alone must not be sufficient -- the value must also genuinely match"),
        )
    )()
)())

ok("a field the model already marked unsupported (value=None) stays unsupported, and is never flagged as 'rejected' by _unresolved_story_fields (it wasn't a claim to begin with)", lambda: (
    lambda sr={k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS}: (
        lambda annotated=annotate_story_record(sr, PACK, {}): (
            assert_(all(v["status"] == "unsupported" for v in annotated.values())),
            assert_(_unresolved_story_fields(sr, annotated) == [], "an already-honest null field is not a repair target"),
        )
    )()
)())

ok("QUALIFIED semantic status is accepted, not treated as a failure (mirrors the existing claim_status convention)", lambda: (
    lambda sr={"OPPORTUNITY": {"value": "Real Opportunity", "pack_source": "[OPPORTUNITY:opp1]"},
               **{k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS if k != "OPPORTUNITY"}}: (
        lambda annotated=annotate_story_record(sr, PACK, {"OPPORTUNITY": "QUALIFIED"}): (
            assert_(annotated["OPPORTUNITY"]["status"] == "supported"),
        )
    )()
)())

# ============================================================
# 3. _unresolved_story_fields correctly identifies ONLY fields that
# HAD a value proposed but got rejected -- the real repair target.
# ============================================================
print("\n=== REPAIR TARGETING ===")

ok("a field proposed with a value that got rejected IS the repair target", lambda: (
    lambda before={"SIGNAL": {"value": "x", "pack_source": "[ECONOMIC:fake]"}, **{k: {"value": None, "pack_source": None} for k in STORY_RECORD_FIELD_DEFINITIONS if k != "SIGNAL"}}: (
        lambda annotated=annotate_story_record(before, PACK, {"SIGNAL": "SUPPORTED"}): (
            assert_(_unresolved_story_fields(before, annotated) == ["SIGNAL"]),
        )
    )()
)())

# ============================================================
# 4. Story-pack fact lines + provenance auto-coverage -- proves
# _pack_valid_tags/validate_claim_source work UNCHANGED against a
# story-pack, exactly as they do against a raw pack.
# ============================================================
print("\n=== STORY-PACK FACT LINES + PROVENANCE ===")

STORY_RESULT = {
    "packCountry": "Kenya", "packCountryCode": "KE", "packAssembledAt": "2026-09-19T00:00:00.000Z",
    "angle": "trade_value_chain", "storyRecord": {
        "SIGNAL": {"value": "Kenya's GDP is $100", "pack_source": "[ECONOMIC:gdp_usd]", "status": "supported"},
        "OPPORTUNITY": {"value": "Real Opportunity", "pack_source": "[OPPORTUNITY:opp1]", "status": "supported"},
        **{k: {"value": None, "pack_source": None, "status": "unsupported"} for k in STORY_RECORD_FIELD_DEFINITIONS if k not in ("SIGNAL", "OPPORTUNITY")},
    },
}

ok("build_story_pack produces a pack _pack_fact_lines() recognizes via the storyRecord branch", lambda: (
    lambda story_pack=build_story_pack(STORY_RESULT): (
        assert_("storyRecord" in story_pack),
        assert_(story_pack["country"]["code"] == "KE"),
    )
)())

ok("only SUPPORTED story fields produce a fact line -- unsupported fields produce NONE (nothing to cite)", lambda: (
    lambda lines=_pack_fact_lines(build_story_pack(STORY_RESULT)): (
        assert_(any(l.startswith("[STORY:SIGNAL]") for l in lines)),
        assert_(any(l.startswith("[STORY:OPPORTUNITY]") for l in lines)),
        assert_(not any(l.startswith("[STORY:BUILDER]") for l in lines), "BUILDER was unsupported, must produce no line"),
        assert_(len(lines) == 2, f"expected exactly 2 lines (the 2 supported fields), got {len(lines)}"),
    )
)())

ok("_pack_valid_tags works UNCHANGED against a story-pack -- only the 2 real [STORY:...] tags are valid", lambda: (
    lambda tags=_pack_valid_tags(build_story_pack(STORY_RESULT)): (
        assert_("STORY:SIGNAL" in tags),
        assert_("STORY:OPPORTUNITY" in tags),
        assert_("STORY:BUILDER" not in tags),
        assert_(validate_claim_source("[STORY:SIGNAL]", tags) is True),
        assert_(validate_claim_source("[STORY:BUILDER]", tags) is False, "a field that was never approved must not validate just because its NAME is plausible"),
    )
)())

# ============================================================
# 5. build_closed_book_prompt() correctly distinguishes a story-pack
# from a normal pack -- the "already approved, don't reinterpret" note
# only appears for a story-pack, never leaks into normal generation.
# ============================================================
print("\n=== PROMPT-LEVEL STORY AWARENESS ===")

ok("a story-pack's prompt includes the 'already-approved, do not reinterpret' note", lambda: (
    lambda prompt=build_closed_book_prompt(build_story_pack(STORY_RESULT), "LINKEDIN_ARTICLE"): (
        assert_(prompt is not None),
        assert_("already-approved" in prompt),
        assert_("do not reinterpret" in prompt),
    )
)())

ok("a NORMAL pack's prompt never carries the story-approval note -- it only fires for a real story-pack", lambda: (
    lambda prompt=build_closed_book_prompt(PACK, "LINKEDIN_ARTICLE"): (
        assert_(prompt is not None),
        assert_("already-approved" not in prompt),
    )
)())

ok("a story-pack's prompt still carries the universal 5-part structure and hard rules, completely unaffected", lambda: (
    lambda prompt=build_closed_book_prompt(build_story_pack(STORY_RESULT), "LINKEDIN_ARTICLE"): (
        assert_("HOOK:" in prompt),
        assert_("HARD RULES" in prompt),
        assert_("[STORY:SIGNAL]" in prompt),
    )
)())

# ============================================================
# 6. Empirical best-candidate selection -- fix after real evidence
# showed no code-only heuristic (raw fact count, category diversity,
# trust-state) reliably predicts which angle produces a fuller story.
# _select_best_story_candidate() replaces prediction with picking
# whichever candidate ACTUALLY produced more supported fields.
# ============================================================
print("\n=== EMPIRICAL BEST-CANDIDATE SELECTION ===")

ok("the candidate with more supported fields wins, regardless of order", lambda: (
    assert_(_select_best_story_candidate([{"supportedFieldCount": 4}, {"supportedFieldCount": 8}]) == 1),
    assert_(_select_best_story_candidate([{"supportedFieldCount": 8}, {"supportedFieldCount": 4}]) == 0),
))

ok("a tie breaks toward the earlier-ranked (lower-index) candidate, matching select_available_angles' own ranking", lambda: (
    assert_(_select_best_story_candidate([{"supportedFieldCount": 5}, {"supportedFieldCount": 5}]) == 0),
    assert_(_select_best_story_candidate([{"supportedFieldCount": 3}, {"supportedFieldCount": 5}, {"supportedFieldCount": 5}]) == 1, "of the two tied candidates, the earlier one (index 1) must win over index 2"),
))

ok("a single candidate is trivially selected (the max_angles=1 / thin-pack case)", lambda: (
    assert_(_select_best_story_candidate([{"supportedFieldCount": 0}]) == 0),
))

ok("reproduces the actual real-world finding: a lower-trust angle (tech_and_innovation, 8 fields) correctly beats a higher-trust angle (investment_signals, 4 fields) that happens to be ranked first", lambda: (
    assert_(_select_best_story_candidate([{"supportedFieldCount": 4}, {"supportedFieldCount": 8}]) == 1)
))

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
