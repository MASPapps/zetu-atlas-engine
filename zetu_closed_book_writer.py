#!/usr/bin/env python3
"""
Zetu Atlas — CLOSED-BOOK writer (Phase 7, Zetu Data Loop roadmap).

Additive. Does NOT modify zetu_production_engine_v2.py or its existing
sources -> OpenAI-claims -> Anthropic-verify -> briefing -> Substack
pipeline, which remains fully intact for whatever workflow already relies
on it.

This module is a SEPARATE writer path with a single, hard rule: the
writer may use ONLY facts present in a supplied Zetu Intelligence Pack
(see Zetumap's lib/intelligence-pack.mjs -- the pack is generated there,
serialized to JSON, and read here as the writer's entire factual
universe). It may explain, structure, compare, narrate, and simplify
facts already in the pack. It may NOT introduce outside facts, fill
missing numbers, invent comparisons/causation, invent business success,
demand, policy, or upgrade any evidence tier (OBSERVED->VERIFIED,
USER_DECLARED->SOURCE_VERIFIED, funding->procurement, opportunity->
guaranteed demand).

Root cause of the original hallucination (documented in this roadmap's
Phase 4-6 report and the user's own account): zetu_production_engine_v2.py's
generate_briefing() prompt forces "600-800 words. Hook with number. Show
pattern." -- and voice_guide.txt separately forces the same 600-800 word
count PLUS "Include at least one person or company doing it right." Both
of those specific mandates are structurally hallucination-forcing when
the underlying evidence is thin: a model told to hit a word count and
include a company story, with insufficient real material, will invent
one. This module reuses voice_guide.txt for STYLE only (short sentences,
active voice, tone markers, the closing line) and explicitly overrides
both hallucination-forcing mandates with "as long as the evidence
supports, no minimum" and "include a real business/person example ONLY
if the pack actually contains one."

Two-call pattern, mirroring the existing engine's own OpenAI-writes /
Anthropic-verifies structure (extract_claims / verify_claim), just
retargeted from "sources" to "the pack":
  1. generate_closed_book_briefing() -- OpenAI writes, closed-book.
  2. audit_briefing_claims() -- Anthropic breaks the output into individual
     substantive statements and classifies each against the pack:
     SUPPORTED / QUALIFIED / UNKNOWN. An UNKNOWN statement means the
     writer said something the pack does not support -- this is the
     signal used by the hallucination regression test and by the "zero
     unsupported factual claims" requirement.

Zero writes to Supabase, zero Substack/email/social calls. Every output
of this module is a local file -- a pending/test artifact, never
published.
"""

import os
import json
import re
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from anthropic import Anthropic

load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


def load_voice_guide_style_only():
    """Reuses voice_guide.txt for STYLE markers only -- explicitly strips
    the two mandates that force hallucination on thin evidence (600-800
    word count, mandatory person/company story)."""
    path = "voice_guide.txt"
    if not os.path.exists(path):
        return ""
    with open(path, "r") as f:
        raw = f.read()
    # Drop the LENGTH section and the "storytelling" mandate line entirely
    # -- everything else (tone markers, short-sentence rule, active voice,
    # sign-off) is genuine STYLE, not a factual-completeness requirement,
    # and is safe to keep.
    raw = re.sub(r"LENGTH\s*\n=+\s*\n600-800 words\..*?\n", "", raw, flags=re.DOTALL)
    raw = raw.replace(
        "Include at least one person or company doing it right.\nUse their story to illustrate the mechanism.",
        "Include a real business/person example ONLY if one genuinely exists in the supplied facts. If none exists, omit this beat entirely -- do not invent one.",
    )
    return raw


VOICE_STYLE = load_voice_guide_style_only()


def load_intelligence_pack(path):
    with open(path, "r") as f:
        return json.load(f)


def _pack_fact_lines(pack):
    """Flattens the pack into explicit, citable fact lines -- exactly what
    the writer is allowed to draw on. Each line carries its own
    provenance inline so the model sees the boundary attached to the
    fact, not as a separate abstract rule."""
    lines = []

    eb = pack.get("economicBaseline", {})
    if eb.get("available"):
        for field, ind in eb.get("indicators", {}).items():
            if ind.get("value") is None:
                continue
            stale = "STALE — " if "STALE" in (ind.get("flags") or []) else ""
            lines.append(f"[ECONOMIC:{field}] {stale}value={ind['value']} (year={ind.get('year')}, source={ind.get('source')})")

    for s in pack.get("signals", []):
        lines.append(f"[SIGNAL:{s['id']}] \"{s['title']}\" (trustState={s.get('trustState')}) — MAY_CLAIM: {s.get('claimBoundaries', {}).get('mayClaim', 'n/a')} — MAY_NOT_CLAIM: {s.get('claimBoundaries', {}).get('mayNotClaim', 'n/a')}")

    for o in pack.get("opportunities", []):
        state_note = " [RETIRED/INACTIVE — do not present as currently open]" if o.get("opportunityState") in ("RETIRED", "REJECTED") else ""
        lines.append(f"[OPPORTUNITY:{o['id']}] \"{o['title']}\" state={o.get('opportunityState')}{state_note} — MAY_CLAIM: {'; '.join(o.get('mayClaim', []))} — MAY_NOT_CLAIM: {'; '.join(o.get('mayNotClaim', []))}")

    for b in pack.get("businesses", []):
        if not b.get("hasAnyCapabilityEvidence"):
            continue
        caps = "; ".join(f"{c['contextKey']} ({c['evidenceTier'].upper()} — never VERIFIED)" for c in b.get("capabilities", []))
        lines.append(f"[BUSINESS:{b['businessId']}] \"{b['name']}\" ({b.get('category')}) — capabilities: {caps}")

    for t in pack.get("matchesActionsOutcomes", []):
        for e in t.get("events", []):
            lines.append(f"[LIFECYCLE:{e['stage']}] business={e['businessId']}, opportunity={e['opportunityId']}, tier={e['tier']} (tier={e['tier']} means: {'a real fact the platform itself observed' if e['tier']=='SYSTEM_OBSERVED' else 'a self-declared claim, NOT independently verified' if e['tier']=='USER_DECLARED' else 'independently confirmed'})")

    for n in pack.get("newsDevelopments", []):
        lines.append(f"[NEWS:{n['id']}] \"{n['title']}\" ({n.get('sourceUrl')}) — {n.get('claim')}")

    return lines


TASK_FRAMING = {
    "ATLAS_BRIEFING": lambda country: f"Write a briefing for The Zetu Atlas about {country}, in Michael's voice. This is a written/newsletter piece.",
    "ZETU_SHOW_COLD_OPEN": lambda country: (
        f"Write a short SPOKEN cold-open for a Zetu Show episode about {country}, in Michael's voice -- the first ~30-45 seconds "
        f"a viewer hears before the show cuts to investigation. This is spoken narration, not a newsletter -- even shorter, "
        f"punchier sentences, and it should end on a genuine open question the facts raise (not a resolved conclusion), "
        f"inviting the viewer to keep watching. Do NOT use the Atlas sign-off line -- end on the open question instead."
    ),
}


def build_closed_book_prompt(pack, content_format="ATLAS_BRIEFING"):
    facts = _pack_fact_lines(pack)
    country = pack.get("country", {}).get("name", "Unknown")

    if not facts:
        # No point calling the model at all -- there is nothing to write about.
        return None

    facts_block = "\n".join(f"- {f}" for f in facts)
    framing = TASK_FRAMING.get(content_format, TASK_FRAMING["ATLAS_BRIEFING"])(country)

    return f"""{framing}

VOICE (style only -- follow the tone, sentence rhythm; ignore any length target elsewhere):
{VOICE_STYLE}

=== THE ONLY FACTS YOU MAY USE ===
{facts_block}
=== END OF FACTS ===

HARD RULES -- THESE OVERRIDE ANY STYLE INSTRUCTION ABOVE:
1. You may explain, structure, compare, and narrate ONLY the facts listed above. Nothing else exists for this briefing.
2. Do NOT introduce any fact, number, name, percentage, ranking, policy, or company that is not explicitly listed above.
3. Do NOT fill in a missing number, invent a comparison, or infer a cause-and-effect relationship the facts don't state.
4. If a fact is marked STALE, you may use it but must say it is from an earlier year, not "current."
5. If a fact is marked [RETIRED/INACTIVE], you must NOT present it as a currently open opportunity.
6. A capability tier of OBSERVED or CLAIMED must never be described as "verified" or "confirmed."
7. A USER_DECLARED lifecycle event (self-reported) must never be described as verified, proven, or confirmed by Zetu -- say it is self-reported / not independently verified.
8. Do NOT claim any business will win, profit from, or succeed at anything. Do NOT claim proven demand, market gaps, or investment-readiness unless a fact explicitly states it (none of the facts above do).
9. There is NO required word count, NO required numerical hook, and NO required company story.
10. Prefer connecting and comparing the facts above over adding new generic economic-reasoning sentences (e.g. "high inflation affects purchasing power" is analysis you invented, not one of the facts). A little of this is fine if it helps the piece read naturally -- an automatic audit will flag anything that goes too far, and you'll get one chance to fix only those specific flagged sentences afterward, so it is not fatal if a sentence or two drifts this way.

You have {len(facts)} real, usable facts above -- that is enough for a real, short, honest briefing. Write it. Only if you genuinely have nothing at all to say (this is not that case) would you instead respond with exactly: INSUFFICIENT_EVIDENCE

Begin:"""


def generate_closed_book_briefing(openai_client, pack, content_format="ATLAS_BRIEFING"):
    prompt = build_closed_book_prompt(pack, content_format)
    if prompt is None:
        return "INSUFFICIENT_EVIDENCE", {"reason": "pack contained zero usable facts"}
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1500,
    )
    text = response.choices[0].message.content.strip()
    return text, {"promptFactCount": len(_pack_fact_lines(pack))}


def audit_briefing_claims(anthropic_client, pack, briefing_text):
    """Breaks the generated briefing into individual substantive
    statements and classifies each against the pack's own facts.
    Mirrors the existing engine's verify_claim() pattern, retargeted from
    'sources' to 'the pack'."""
    if briefing_text.strip() == "INSUFFICIENT_EVIDENCE":
        return []

    facts_block = "\n".join(f"- {f}" for f in _pack_fact_lines(pack))
    prompt = f"""You are auditing a briefing for factual accuracy against a fixed set of allowed facts.

ALLOWED FACTS (the ONLY things the briefing is permitted to state):
{facts_block}

BRIEFING TO AUDIT:
{briefing_text}

Break the briefing into individual substantive factual statements (ignore pure narrative/stylistic sentences with no factual content). For EACH substantive statement, return a JSON object with:
  statement: the exact sentence or clause
  pack_source: which allowed fact (by its [TAG:id] prefix) supports it, or null if none
  claim_status: "SUPPORTED" (directly stated in the allowed facts), "QUALIFIED" (a fair restatement/simplification of an allowed fact, e.g. rounding a number or paraphrasing a claim boundary), or "UNKNOWN" (not traceable to any allowed fact -- this includes any invented number, name, comparison, or causal claim)
  notes: brief reason

Return ONLY a JSON array, no markdown, no commentary."""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [{"statement": "AUDIT_PARSE_FAILED", "pack_source": None, "claim_status": "UNKNOWN", "notes": text[:300]}]


def repair_briefing(openai_client, pack, briefing_text, unsupported):
    """Feeds back the exact flagged sentences and asks the model to
    remove or ground ONLY those -- never to add anything new. Used when
    the audit finds UNKNOWN statements after the first draft."""
    flagged = "\n".join(f'- "{u["statement"]}" ({u.get("notes", "")})' for u in unsupported)
    facts_block = "\n".join(f"- {f}" for f in _pack_fact_lines(pack))
    prompt = f"""Here is a briefing you wrote, and a list of specific sentences in it that an independent audit found NOT traceable to the allowed facts.

ALLOWED FACTS (unchanged):
{facts_block}

BRIEFING:
{briefing_text}

SENTENCES FLAGGED AS UNSUPPORTED:
{flagged}

Rewrite the briefing. For EACH flagged sentence: either delete it, or replace it with a direct restatement of an allowed fact. Leave every OTHER sentence in the briefing exactly as it is -- do not shorten, rewrite, or remove anything that wasn't flagged, and do not add anything new. The rest of the briefing was fine; only the flagged sentences are the problem. Deleting a handful of sentences from an otherwise-good briefing is normal editing, not a reason to discard the whole piece -- only respond with INSUFFICIENT_EVIDENCE if EVERY SINGLE sentence in the briefing was flagged (which is not the case here).

Return only the revised briefing, nothing else."""
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1500,
    )
    return response.choices[0].message.content.strip()


def run_closed_book_pipeline(pack_path, out_dir=".", max_repair_attempts=2, content_format="ATLAS_BRIEFING"):
    pack = load_intelligence_pack(pack_path)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

    briefing, meta = generate_closed_book_briefing(openai_client, pack, content_format)
    audit = audit_briefing_claims(anthropic_client, pack, briefing)
    unsupported = [a for a in audit if a.get("claim_status") == "UNKNOWN"]

    attempts = 0
    while unsupported and attempts < max_repair_attempts and briefing.strip() != "INSUFFICIENT_EVIDENCE":
        attempts += 1
        briefing = repair_briefing(openai_client, pack, briefing, unsupported)
        audit = audit_briefing_claims(anthropic_client, pack, briefing)
        unsupported = [a for a in audit if a.get("claim_status") == "UNKNOWN"]

    result = {
        "generatedAt": datetime.now().isoformat(),
        "contentFormat": content_format,
        "packCountry": pack.get("country", {}).get("name"),
        "packAssembledAt": pack.get("assembledAt"),
        "meta": meta,
        "repairAttempts": attempts,
        "briefing": briefing,
        "claimAudit": audit,
        "unsupportedClaimCount": len(unsupported),
        "unsupportedClaims": unsupported,
        "accepted": len(unsupported) == 0,
    }

    suffix = "atlas" if content_format == "ATLAS_BRIEFING" else "show_cold_open"
    out_path = os.path.join(out_dir, f"closed_book_preview_{pack.get('country', {}).get('code', 'XX')}_{suffix}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    return result, out_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 zetu_closed_book_writer.py <intelligence_pack.json> [out_dir] [ATLAS_BRIEFING|ZETU_SHOW_COLD_OPEN]")
        sys.exit(1)
    fmt = sys.argv[3] if len(sys.argv) > 3 else "ATLAS_BRIEFING"
    result, out_path = run_closed_book_pipeline(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ".", content_format=fmt)
    print(f"[{fmt}] Briefing ({len(result['briefing'].split())} words), accepted={result['accepted']}, repairAttempts={result['repairAttempts']}:\n")
    print(result["briefing"])
    print(f"\nUnsupported claims: {result['unsupportedClaimCount']}")
    print(f"Saved to: {out_path}")
