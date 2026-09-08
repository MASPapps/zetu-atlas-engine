#!/usr/bin/env python3
"""
Structural regression tests for zetu_closed_book_writer.py. Pure/offline
-- no API calls, no network. Tests the prompt-construction and
fact-extraction logic that determines closed-book compliance BEFORE any
model call is made. The actual generation/audit behavior (which requires
real API calls) was separately proven live against the real Ghana pack
and a deliberately thin pack -- see
ZETU_DATA_LOOP_PHASE_7_9_CONTENT_COMPLETION.md for those results.

Usage: python3 test_closed_book_writer.py
"""
import json
from zetu_closed_book_writer import (
    build_closed_book_prompt, _pack_fact_lines, load_voice_guide_style_only,
)

failures = []


def ok(label, fn):
    try:
        fn()
        print(f"  ok {label}")
    except AssertionError as e:
        failures.append(label)
        print(f"  FAIL {label}: {e}")


RICH_PACK = {
    "country": {"code": "GH", "name": "Ghana"},
    "economicBaseline": {"available": True, "indicators": {
        "gdp_usd": {"value": 100, "year": 2025, "source": "World Bank", "flags": []},
        "top_exports": {"value": ["Gold"], "year": 2022, "source": "World Bank", "flags": ["STALE"]},
    }},
    "signals": [{"id": "s1", "title": "Sig1", "trustState": "SOURCE_VERIFIED", "claimBoundaries": {"mayClaim": "x", "mayNotClaim": "y"}}],
    "opportunities": [
        {"id": "o1", "title": "Active Opp", "opportunityState": "OPPORTUNITY_SIGNAL", "mayClaim": ["a"], "mayNotClaim": ["b"]},
        {"id": "o2", "title": "Retired Opp", "opportunityState": "RETIRED", "mayClaim": ["a"], "mayNotClaim": ["b"]},
    ],
    "businesses": [
        {"businessId": "b1", "name": "Evidenced Biz", "category": "Retail", "hasAnyCapabilityEvidence": True, "capabilities": [{"contextKey": "x", "evidenceTier": "observed"}]},
        {"businessId": "b2", "name": "No Evidence Biz", "category": "Retail", "hasAnyCapabilityEvidence": False, "capabilities": []},
    ],
    "matchesActionsOutcomes": [{"events": [{"stage": "PURSUED", "businessId": "b1", "opportunityId": "o1", "tier": "USER_DECLARED"}]}],
    "newsDevelopments": [],
}

EMPTY_PACK = {
    "country": {"code": "ZZ", "name": "Nowhere"},
    "economicBaseline": {"available": False, "indicators": {}},
    "signals": [], "opportunities": [], "businesses": [], "matchesActionsOutcomes": [], "newsDevelopments": [],
}

print("=== FACT LINE EXTRACTION ===")

def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)


def check_business_evidence_filter():
    lines = _pack_fact_lines(RICH_PACK)
    assert_(any("BUSINESS:b1" in l for l in lines), "no BUSINESS:b1 line")
    assert_(not any("No Evidence Biz" in l for l in lines), "unevidenced business leaked into fact lines")


ok("Only the business WITH capability evidence produces a fact line", check_business_evidence_filter)


ok("A RETIRED opportunity's fact line is explicitly marked inactive", lambda: assert_(
    any("Retired Opp" in l and "RETIRED/INACTIVE" in l for l in _pack_fact_lines(RICH_PACK)),
    "RETIRED opportunity missing the inactive marker",
))

ok("A capability evidence tier is always shown with 'never VERIFIED' attached", lambda: assert_(
    any("OBSERVED" in l and "never VERIFIED" in l for l in _pack_fact_lines(RICH_PACK)),
    "capability tier line missing the never-VERIFIED note",
))

ok("A USER_DECLARED lifecycle event's fact line explicitly says self-declared, not independently verified", lambda: assert_(
    any("USER_DECLARED" in l and "NOT independently verified" in l for l in _pack_fact_lines(RICH_PACK)),
    "lifecycle fact line missing the self-declared caveat",
))

ok("An empty pack produces zero fact lines", lambda: assert_(
    len(_pack_fact_lines(EMPTY_PACK)) == 0, "empty pack produced fact lines out of nowhere",
))

print("\n=== PROMPT CONSTRUCTION -- NO HALLUCINATION-FORCING MANDATES ===")

ok("The closed-book prompt contains NO mandatory word count (the original bug)", lambda: assert_(
    "600-800" not in build_closed_book_prompt(RICH_PACK), "the 600-800 word mandate leaked into the closed-book prompt",
))

ok("The closed-book prompt does not force a mandatory company/person story", lambda: assert_(
    "Include at least one person or company doing it right." not in build_closed_book_prompt(RICH_PACK),
    "the original mandatory-story instruction leaked into the closed-book prompt",
))

ok("The closed-book prompt explicitly permits INSUFFICIENT_EVIDENCE as a valid response", lambda: assert_(
    "INSUFFICIENT_EVIDENCE" in build_closed_book_prompt(RICH_PACK), "no INSUFFICIENT_EVIDENCE escape hatch in the prompt",
))

ok("An empty pack (zero facts) returns None -- no API call is even attempted", lambda: assert_(
    build_closed_book_prompt(EMPTY_PACK) is None, "empty pack should short-circuit to None, not a hollow prompt",
))

ok("The prompt explicitly forbids introducing outside facts/names/percentages/policy", lambda: assert_(
    "Do NOT introduce any fact, number, name, percentage, ranking, policy, or company" in build_closed_book_prompt(RICH_PACK),
    "missing the explicit no-outside-facts rule",
))

ok("The prompt explicitly forbids claiming guaranteed business success/demand", lambda: assert_(
    "Do NOT claim any business will win, profit from, or succeed" in build_closed_book_prompt(RICH_PACK),
    "missing the explicit no-guaranteed-success rule",
))

print("\n=== VOICE GUIDE OVERRIDE ===")

ok("The style-only voice guide has the 600-800 word LENGTH section stripped", lambda: assert_(
    "600-800 words" not in load_voice_guide_style_only(), "LENGTH mandate was not stripped from the style guide",
))

ok("The style-only voice guide replaces the mandatory-story line with an optional one", lambda: assert_(
    "ONLY if one genuinely exists" in load_voice_guide_style_only(), "mandatory-story override text missing",
))

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)})'}")
if failures:
    exit(1)
