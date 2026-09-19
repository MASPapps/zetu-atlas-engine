#!/usr/bin/env python3
"""
Claim-to-source provenance hardening tests (narrow addition to the
existing closed-book writer). Pure/offline: no OpenAI or Anthropic call
anywhere in this file -- annotate_provenance()/validate_claim_source() are
tested directly against synthetic audit arrays and synthetic packs, plus
the real, already-accepted Ghana and Kenya artifacts, exactly the way
test_closed_book_writer.py already tests other pure functions.

Usage: python3 test_claim_provenance.py
"""
import json
from zetu_closed_book_writer import (
    annotate_provenance, validate_claim_source, _pack_valid_tags, _unresolved,
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
    "country": {"code": "GH", "name": "Ghana"},
    "economicBaseline": {
        "available": True,
        "indicators": {
            "gdp_usd": {"value": 100, "year": 2025, "source": "World Bank", "flags": []},
        },
    },
    "signals": [],
    "opportunities": [
        {"id": "real-opp-1", "title": "Real Opportunity", "opportunityState": "OPPORTUNITY_SIGNAL", "mayClaim": ["x"], "mayNotClaim": ["y"]},
    ],
    "businesses": [],
    "matchesActionsOutcomes": [],
    "newsDevelopments": [],
}

# ============================================================
# 1. Valid real pack_source -> accepted
# ============================================================
ok("1: a SUPPORTED statement with a real pack_source tag is provenance_valid, not unresolved", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": "[ECONOMIC:gdp_usd]", "notes": ""}], PACK): (
        assert_(audit[0]["provenance_valid"] is True),
        assert_(_unresolved(audit) == []),
    )
)())

# ============================================================
# 2. Null source -> rejected
# ============================================================
ok("2: a SUPPORTED statement with pack_source=None is rejected (unresolved), even though claim_status says SUPPORTED", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": None, "notes": ""}], PACK): (
        assert_(audit[0]["provenance_valid"] is False),
        assert_(len(_unresolved(audit)) == 1),
        assert_(audit[0]["claim_status"] == "SUPPORTED", "claim_status itself must be preserved, never rewritten"),
    )
)())

# ============================================================
# 3. Missing source (key absent entirely) -> rejected
# ============================================================
ok("3: a QUALIFIED statement with the pack_source key entirely missing is rejected", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "QUALIFIED", "notes": ""}], PACK): (
        assert_(audit[0]["provenance_valid"] is False),
        assert_(len(_unresolved(audit)) == 1),
    )
)())

# ============================================================
# 4. Fabricated/nonexistent pack source -> rejected
# ============================================================
ok("4: a plausible-looking but nonexistent pack_source (e.g. a fabricated economic field) is rejected, not trusted", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": "[ECONOMIC:fdi_growth_rate]", "notes": ""}], PACK): (
        assert_(audit[0]["provenance_valid"] is False),
        assert_(len(_unresolved(audit)) == 1),
        assert_("PROVENANCE CHECK FAILED" in audit[0]["notes"]),
    )
)())

ok("4b: a fabricated opportunity id in pack_source is rejected even though a real opportunity id exists in the pack", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": "[OPPORTUNITY:not-a-real-id]", "notes": ""}], PACK): (
        assert_(audit[0]["provenance_valid"] is False),
    )
)())

# ============================================================
# 5. Valid QUALIFIED statement with valid source -> allowed
#    (QUALIFIED must never be treated as failure merely for being QUALIFIED)
# ============================================================
ok("5: a QUALIFIED statement with a real pack_source is allowed -- QUALIFIED is not itself a failure", lambda: (
    lambda audit=annotate_provenance([{"statement": "x", "claim_status": "QUALIFIED", "pack_source": "[OPPORTUNITY:real-opp-1]", "notes": "rounded"}], PACK): (
        assert_(audit[0]["provenance_valid"] is True),
        assert_(_unresolved(audit) == []),
        assert_(audit[0]["claim_status"] == "QUALIFIED"),
    )
)())

# ============================================================
# 6. Mixed statements: one has invalid provenance -> final result cannot
# be accepted (the pipeline's own accepted = len(unsupported) == 0 uses
# this same _unresolved() list, so this directly proves the gating)
# ============================================================
ok("6: one invalid-provenance statement among several valid ones still makes the whole batch unresolved", lambda: (
    lambda audit=annotate_provenance([
        {"statement": "a", "claim_status": "SUPPORTED", "pack_source": "[ECONOMIC:gdp_usd]", "notes": ""},
        {"statement": "b", "claim_status": "QUALIFIED", "pack_source": "[OPPORTUNITY:real-opp-1]", "notes": ""},
        {"statement": "c", "claim_status": "SUPPORTED", "pack_source": "[ECONOMIC:invented_field]", "notes": ""},
    ], PACK): (
        assert_(len(_unresolved(audit)) == 1),
        assert_(_unresolved(audit)[0]["statement"] == "c"),
    )
)())

# ============================================================
# 7. Repair produces a valid source mapping -> can subsequently pass
# (simulates the two audit passes the real repair loop performs)
# ============================================================
ok("7: a statement unresolved on the first audit becomes resolved once a repaired re-audit gives it a real pack_source", lambda: (
    lambda first=annotate_provenance([{"statement": "x", "claim_status": "SUPPORTED", "pack_source": None, "notes": ""}], PACK),
           second=annotate_provenance([{"statement": "x (repaired)", "claim_status": "SUPPORTED", "pack_source": "[ECONOMIC:gdp_usd]", "notes": ""}], PACK): (
        assert_(len(_unresolved(first)) == 1),
        assert_(_unresolved(second) == []),
    )
)())

# ============================================================
# 8. No regression to existing Ghana/Kenya evidence-safe semantics --
# real, already-accepted artifacts re-checked against the stricter rule.
# ============================================================
def _check_real_artifact(path, pack_tags_hint):
    with open(path) as f:
        result = json.load(f)
    # Build a minimal pack shape carrying exactly the tags these real
    # opportunities/economic fields are known to produce (see the live
    # inspection notes below each test) -- this proves the VALIDATION
    # LOGIC accepts real tags; it does not re-run the writer or call any API.
    pack = {
        "economicBaseline": {"available": True, "indicators": {k: {"value": 1, "year": 2025, "source": "test", "flags": []} for k in pack_tags_hint["economic"]}},
        "opportunities": [{"id": i, "title": "t", "opportunityState": "OPPORTUNITY_SIGNAL", "mayClaim": [], "mayNotClaim": []} for i in pack_tags_hint["opportunities"]],
        "signals": [{"id": i, "title": "t", "trustState": "SOURCE_VERIFIED", "claimBoundaries": {}} for i in pack_tags_hint["signals"]],
        "businesses": [{"businessId": i, "name": "b", "category": "c", "hasAnyCapabilityEvidence": True, "capabilities": [{"contextKey": "x", "evidenceTier": "observed"}]} for i in pack_tags_hint["businesses"]],
        "matchesActionsOutcomes": [], "newsDevelopments": [],
    }
    audit = annotate_provenance(result["claimAudit"], pack)
    unresolved = _unresolved(audit)
    return result, unresolved


ok("8a: the real, accepted Ghana artifact's claimAudit is still fully resolved under the stricter rule (all real tags)", lambda: (
    lambda check=_check_real_artifact(
        "/Users/michaelsarpong/Zetumap/phase9-atlas-briefing-preview.json",
        {"economic": ["gdp_usd", "gdp_per_capita_usd", "unemployment_rate", "inflation_rate", "top_exports", "top_imports", "main_trading_partners"],
         "opportunities": ["8641e5f261bc734822cf3e62048a0934"], "signals": [], "businesses": []},
    ): (
        assert_(check[0]["accepted"] is True, "sanity: this artifact was originally accepted"),
        assert_(len(check[1]) == 0, f"expected no regression, got unresolved: {check[1]}"),
    )
)())

ok("8b: the real, accepted Kenya artifact's claimAudit is still fully resolved under the stricter rule (all real tags)", lambda: (
    lambda check=_check_real_artifact(
        "/Users/michaelsarpong/zetu-atlas-engine/closed_book_preview_KE_atlas.json",
        # NOTE: this hint list must match whatever closed_book_preview_KE_atlas.json
        # currently on disk actually cites -- that file was regenerated by a
        # later real Kenya proof run (gdp_per_capita_usd + a second business,
        # the hospitality cluster) after this test was first written, which
        # silently desynced the fixture from the artifact it checks. Fixed
        # here; this is a real-artifact regression check, not a synthetic
        # fixture, so it must be re-verified whenever that file changes.
        {"economic": ["gdp_usd", "population", "unemployment_rate", "inflation_rate", "top_exports", "top_imports", "gdp_per_capita_usd"],
         "opportunities": ["eb70cfb87b5a9980f95197a98dfeb8ca", "28f52e9aa42425a34b6568aa07a15633"],
         "signals": ["a6345674-93f7-446a-a3a3-52a3278e20df"],
         "businesses": ["ae3137b1-79c4-4187-94ac-49c7554f341e", "016ce96a-c2d7-47bb-be2f-5ed8d462d126"]},
    ): (
        assert_(check[0]["accepted"] is True, "sanity: this artifact was originally accepted"),
        assert_(len(check[1]) == 0, f"expected no regression, got unresolved: {check[1]}"),
    )
)())

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)}): ' + ', '.join(failures)}")
import sys
sys.exit(0 if not failures else 1)
