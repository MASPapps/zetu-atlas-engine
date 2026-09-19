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
            lines.append(f"[ECONOMIC:{field}] {stale}value={ind['value']} (year={ind.get('year')}, source={ind.get('source')}) {_trust_suffix(ind)}")

    for s in pack.get("signals", []):
        lines.append(f"[SIGNAL:{s['id']}] \"{s['title']}\" (trustState={s.get('trustState')}, intelligenceTrustState={s.get('intelligenceTrustState')}) — MAY_CLAIM: {s.get('claimBoundaries', {}).get('mayClaim', 'n/a')} — MAY_NOT_CLAIM: {s.get('claimBoundaries', {}).get('mayNotClaim', 'n/a')}")

    for o in pack.get("opportunities", []):
        state_note = " [RETIRED/INACTIVE — do not present as currently open]" if o.get("opportunityState") in ("RETIRED", "REJECTED") else ""
        lines.append(f"[OPPORTUNITY:{o['id']}] \"{o['title']}\" state={o.get('opportunityState')}{state_note} (intelligenceTrustState={o.get('intelligenceTrustState')}) — MAY_CLAIM: {'; '.join(o.get('mayClaim', []))} — MAY_NOT_CLAIM: {'; '.join(o.get('mayNotClaim', []))}")

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

    # Priority 3B roadmap item #8 content-machine expansion -- categories
    # added to the Intelligence Pack (see Zetumap's
    # lib/intelligence-pack.mjs) so LinkedIn/YouTube/Substack formats have
    # real depth to draw on, not just the original 6 categories above.
    # Every one of these carries its own trustState/trustReason inline,
    # exactly like the categories above -- most of these tables have no
    # affirmative provenance column at all, so the model is told that
    # explicitly rather than left to assume a listed fact is verified.
    for t in pack.get("tourismSites", []):
        lines.append(f"[TOURISM:{t['id']}] \"{t['name']}\" ({t.get('category')}, {t.get('city')}) — annualVisitors={t.get('annualVisitors')}, unescoListed={t.get('unescoListed')} {_trust_suffix(t)}")

    for r in pack.get("naturalResources", []):
        lines.append(f"[RESOURCE:{r['id']}] \"{r['name']}\" (type={r.get('resourceType')}, status={r.get('extractionStatus')}) — estimatedValueUsd={r.get('estimatedValueUsd')} {_trust_suffix(r)}")

    for c in pack.get("cropZones", []):
        lines.append(f"[CROP:{c['id']}] \"{c['cropName']}\" — exportValueUsdMillions={c.get('exportValueUsdMillions')}, productionTonnes={c.get('productionTonnes')}, regions={c.get('majorProducingRegions')} {_trust_suffix(c)}")

    for h in pack.get("techHubs", []):
        lines.append(f"[TECH_HUB:{h['id']}] \"{h['name']}\" ({h.get('city')}, type={h.get('hubType')}) — foundedYear={h.get('foundedYear')}, companiesCount={h.get('companiesCount')} {_trust_suffix(h)}")

    for u in pack.get("universities", []):
        lines.append(f"[UNIVERSITY:{u['id']}] \"{u['name']}\" ({u.get('city')}) — establishedYear={u.get('establishedYear')}, studentCount={u.get('studentCount')} {_trust_suffix(u)}")

    for g in pack.get("skillsGaps", []):
        lines.append(f"[SKILLS_GAP:{g['id']}] {g.get('sector')} sector needs \"{g.get('skillNeeded')}\" — severity={g.get('gapSeverity')}, workersNeeded={g.get('workersNeeded')} {_trust_suffix(g)}")

    for ind in pack.get("countryIndicators", []):
        lines.append(f"[INDICATOR:{ind['id']}] {ind.get('indicatorCode')}={ind.get('value')} (year={ind.get('year')}) {_trust_suffix(ind)}")

    for lang in pack.get("languages", []):
        lang_id = re.sub(r"[^A-Za-z0-9_-]", "_", lang.get("languageName") or "unknown")
        lines.append(f"[LANGUAGE:{lang_id}] \"{lang['languageName']}\" — official={lang.get('isOfficial')}")

    for inv in pack.get("investmentOpportunities", []):
        lines.append(f"[INVESTMENT:{inv['id']}] \"{inv['title']}\" (sector={inv.get('sector')}, risk={inv.get('riskLevel')}, status={inv.get('status')}) — expectedRoiPct={inv.get('expectedRoiPct')} {_trust_suffix(inv)}")

    # ZLOI/ZTRI: a single precomputed cross-country ranking, not a list --
    # ids are fixed (ZLOI/ZTRI), never per-row.
    indices = pack.get("indices")
    if indices:
        if indices.get("zloi"):
            z = indices["zloi"]
            lines.append(f"[INDEX:ZLOI] Zetu Labour Opportunity Index rank #{z['rank']} (score={z['score']}, methodology year={indices.get('dataYear')}, sources={indices.get('sources')}) {_trust_suffix(indices)}")
        if indices.get("ztri"):
            z = indices["ztri"]
            lines.append(f"[INDEX:ZTRI] Zetu Tourism Readiness Index rank #{z['rank']} (score={z['score']}, methodology year={indices.get('dataYear')}, sources={indices.get('sources')}) {_trust_suffix(indices)}")

    return lines


def _trust_suffix(item):
    """Renders an item's trustState/trustReason inline, the same shape
    for every one of the roadmap #8 categories above -- most of them can
    never exceed QUALIFIED or land at INSUFFICIENT_EVIDENCE (see
    lib/intelligence-trust-state.mjs), and the model needs that visible
    on the fact itself, not as a separate rule it might not apply."""
    state = item.get("trustState")
    reason = item.get("trustReason")
    return f"(trustState={state} — {reason})" if reason else f"(trustState={state})"


# Shared anti-interpretive-drift rule -- proven on SUBSTACK_NEWSLETTER
# (2 real rejections without it, clean pass with it), now applied to
# every per-channel format, not just the one that happened to get it
# first. This is the actual, most common failure mode observed across
# real runs: a summary/connective sentence after a fact, characterizing
# what it implies, that no fact actually states.
_ANTI_DRIFT_RULE = (
    "CRITICAL, most common mistake to avoid: after stating a fact, number, or ranking, do NOT add a sentence "
    "characterizing what it implies or means (e.g. 'this suggests room for growth', 'this is a gap, but also an "
    "opportunity', 'Kenya is growing, diversifying, and innovating') unless a fact explicitly states that "
    "implication. A skills gap is a skills gap, not evidence of an opportunity for anyone, unless a fact says so. "
    "State the fact and move to the next one; let the reader draw their own conclusion."
)


def _fact_floor(fact_count, target):
    """Never asks for more facts than actually exist -- a thin pack's
    floor is just 'use what you have', never pressure toward padding or
    invention on a country with little real evidence."""
    return min(target, fact_count) if fact_count else target


TASK_FRAMING = {
    "ATLAS_BRIEFING": lambda country, fact_count=0: f"Write a briefing for The Zetu Atlas about {country}, in Michael's voice. This is a written/newsletter piece.",
    "ZETU_SHOW_COLD_OPEN": lambda country, fact_count=0: (
        f"Write a short SPOKEN cold-open for a Zetu Show episode about {country}, in Michael's voice -- the first ~30-45 seconds "
        f"a viewer hears before the show cuts to investigation. This is spoken narration, not a newsletter -- even shorter, "
        f"punchier sentences, and it should end on a genuine open question the facts raise (not a resolved conclusion), "
        f"inviting the viewer to keep watching. Do NOT use the Atlas sign-off line -- end on the open question instead."
    ),
    # Priority 3B roadmap item #8 -- per-channel formats. Same facts, same
    # hard rules (appended below regardless of format); what differs is
    # depth and structure, matching what each real platform actually
    # needs, per the explicit correction that a single short summary
    # cannot serve LinkedIn, YouTube, and Substack at once.
    "LINKEDIN_ARTICLE": lambda country, fact_count=0: (
        f"Write a LinkedIn article about {country}, in Michael's voice, for an audience of investors, founders, "
        f"SME owners, corporate buyers, and diaspora professionals. This is a professional long-form post, TARGET "
        f"500-800 words -- with this many real facts available, treat that range as something to reach, not a "
        f"ceiling to avoid; never pad with invented filler to get there, but do not stop after 1-2 categories "
        f"either. Cite at least {_fact_floor(fact_count, 10)} distinct facts, drawn from AT LEAST 4 different "
        f"categories (e.g. economy, trade, a named business, tourism, tech/skills, an index rank) -- a reader "
        f"deciding where to look for opportunity needs texture, not a single statistic. {_ANTI_DRIFT_RULE} Open "
        f"with the single most concrete, specific fact available (a real number, a named business, a named "
        f"institution) -- never a generic \"Africa is rising\" framing. Write connected paragraphs, not bullet "
        f"points. The value of this piece must stand on its own as real intelligence -- end with exactly ONE "
        f"low-key, specific line: if a named business or sector was mentioned above, invite a reader running a "
        f"business in that sector to list it on ZetuMap so buyers and investors like the ones reading this can "
        f"find them. This is an invitation, not a sales pitch -- one sentence, not a paragraph."
    ),
    "YOUTUBE_SCRIPT": lambda country, fact_count=0: (
        f"Write a spoken video/podcast script about {country}, in Michael's voice, for a YouTube investigation "
        f"episode aimed at the broad African diaspora, global travelers, students, and anyone curious about African "
        f"economic realities -- this is entertainment and discovery first, not a business memo. TARGET 700-1100 "
        f"words (roughly 5-7 minutes read aloud at a natural pace) -- cite at least {_fact_floor(fact_count, 14)} "
        f"distinct facts across your segments, don't stop after covering just one or two categories when more real "
        f"evidence exists. {_ANTI_DRIFT_RULE} This is narration meant to be read aloud on camera -- short "
        f"sentences, natural spoken rhythm, no bullet points, no markdown tables. Structure it as a sequence of "
        f"clearly separated spoken segments, each opening with a plain question the following sentences answer "
        f"using only the facts available for that segment (for example: who lives here, how the economy makes "
        f"money, what's holding it back, who's already working on it, what opportunity exists). Do not write a "
        f"segment for a question the facts don't support -- skip it rather than pad it. If a data field is a bare "
        f"technical label (a database tag, an internal status string) with nothing natural to say about it, leave "
        f"it out rather than reading the label aloud. End with an open question the facts raise, immediately "
        f"followed by exactly one spoken line inviting the viewer to the full dataset behind the story on Zetu "
        f"Atlas (Substack) -- phrase it as a natural next step for someone who just got curious, not an ad read."
    ),
    "SUBSTACK_NEWSLETTER": lambda country, fact_count=0: (
        f"Write a paid Substack newsletter issue about {country}, in Michael's voice, for subscribers ALREADY "
        f"paying specifically for Zetu Atlas intelligence -- they have already bought in; this issue's job is to "
        f"prove that was worth it, not to sell them again. TARGET 900-1400 words -- this is the longest, most "
        f"analytical format; cite at least {_fact_floor(fact_count, 18)} distinct facts across your sections, "
        f"touching most of the categories that actually have material (economy, trade, opportunity signals, "
        f"business activity, tourism, tech/skills gaps, index ranks) rather than 2-3 of them. Organize into a few "
        f"clearly-headed sections a reader can scan. It should read like something worth paying for: connect facts "
        f"across categories rather than listing them. {_ANTI_DRIFT_RULE} Add a section titled exactly '**What's "
        f"Missing**' and under that exact heading write ONE SPECIFIC sentence naming a fact or category that is "
        f"thin, stale, or missing for {country} (e.g. 'visitor numbers for these tourism sites are not available') "
        f"-- this section must never be skipped or left without that sentence; do not let the piece sound more "
        f"complete than it is. Immediately after that sentence, add exactly ONE generic referral line phrased as a "
        f"direct instruction to the reader, such as 'If this was useful, forward it to one person who'd use it' -- "
        f"phrase it ONLY as an instruction to act, never as a claim about WHO specifically would benefit or WHY "
        f"(e.g. never 'forward this to a tourism analyst' -- asserting who benefits is itself an unsupported claim "
        f"about the piece, not an instruction). Never a renewal/upsell pitch, since they already pay. You MUST end "
        f"the newsletter with a line starting exactly with 'SUBJECT_LINE:' followed by one real, specific subject "
        f"line for this exact issue "
        f"(not a generic template) -- this is a required part of the output, not optional."
    ),
}

# Per-format generation/repair token ceiling -- a fixed 1500 (the original
# ATLAS_BRIEFING-only value) silently truncated a longer format's output
# or repair. Auditing a longer piece also needs more room for the JSON
# breakdown, hence the separate, larger audit ceiling.
_MAX_TOKENS_BY_FORMAT = {
    "ATLAS_BRIEFING": 1500,
    "ZETU_SHOW_COLD_OPEN": 400,
    "LINKEDIN_ARTICLE": 1800,
    "YOUTUBE_SCRIPT": 3000,
    "SUBSTACK_NEWSLETTER": 2500,
}
_AUDIT_MAX_TOKENS_BY_FORMAT = {
    "ATLAS_BRIEFING": 2000,
    "ZETU_SHOW_COLD_OPEN": 1000,
    # Raised after a real run: a genuine ~530-word LinkedIn article's full
    # per-statement JSON breakdown (statement + pack_source + claim_status
    # + notes, ~25-35 statements) truncated at 2500, producing
    # AUDIT_PARSE_FAILED -- a token-budget bug, not a real hallucination.
    "LINKEDIN_ARTICLE": 4000,
    "YOUTUBE_SCRIPT": 6000,
    "SUBSTACK_NEWSLETTER": 6000,
}
_OUTPUT_SUFFIX = {
    "ATLAS_BRIEFING": "atlas",
    "ZETU_SHOW_COLD_OPEN": "show_cold_open",
    "LINKEDIN_ARTICLE": "linkedin",
    "YOUTUBE_SCRIPT": "youtube_script",
    "SUBSTACK_NEWSLETTER": "substack_newsletter",
}


# ============================================================
# Multi-angle generation (real-founder correction): one article trying to
# touch 4+ categories at once cannot also commit to a single argument --
# it either goes deep on one thing or lists many things, not both. The
# fix is not a better single-article prompt; it's generating SEVERAL
# focused pieces per pack, each narrowed to one angle's facts and forced
# to build one argument around them, rather than one piece trying to
# cover everything shallowly.
#
# Angle selection is deterministic and code-based (no LLM call, no
# extra cost, reproducible) -- it only asks "does this pack actually
# have enough real material for this angle", never invents an angle a
# thin pack can't support.
# ============================================================

CATEGORY_TO_TAG_PREFIX = {
    "cropZones": "CROP",
    "naturalResources": "RESOURCE",
    "skillsGaps": "SKILLS_GAP",
    "tourismSites": "TOURISM",
    "techHubs": "TECH_HUB",
    "universities": "UNIVERSITY",
    "opportunities": "OPPORTUNITY",
    "investmentOpportunities": "INVESTMENT",
    "signals": "SIGNAL",
    "countryIndicators": "INDICATOR",
}
# Always preserved regardless of angle -- a named, evidence-tiered
# business or a real lifecycle event grounds ANY argument, and neither
# is itself specific to one angle the way tourism/crops/skills are.
UNIVERSAL_ALWAYS_KEEP_PREFIXES = ("BUSINESS", "LIFECYCLE")
UNIVERSAL_ALWAYS_KEEP_ECONOMIC_FIELDS = ("gdp_usd", "population")

ANGLE_DEFINITIONS = {
    "trade_value_chain": {
        "label": "Trade & Value Chain",
        "tag_prefixes": ("CROP", "RESOURCE"),
        "economic_fields": ("top_exports", "top_imports", "main_trading_partners"),
        "index_keys": (),
        "question": "who actually captures the value in what {country} produces and trades, based on the real export/import/production facts available",
    },
    "skills_and_workforce": {
        "label": "Skills & Workforce",
        "tag_prefixes": ("SKILLS_GAP",),
        "economic_fields": ("unemployment_rate", "employment_informal_pct"),
        "index_keys": (),
        "question": "what {country}'s specific, named workforce gaps mean for anyone hiring, training, or building a workforce there",
    },
    "tourism_and_heritage": {
        "label": "Tourism & Heritage",
        "tag_prefixes": ("TOURISM",),
        "economic_fields": (),
        "index_keys": ("ztri",),
        "question": "what {country}'s real tourism assets and readiness ranking actually tell you, and what's still unproven",
    },
    "tech_and_innovation": {
        "label": "Tech & Innovation",
        "tag_prefixes": ("TECH_HUB", "UNIVERSITY"),
        "economic_fields": (),
        "index_keys": ("zloi",),
        "question": "what {country}'s named tech hubs and institutions actually have to show for themselves right now, not the 'Silicon X' narrative",
    },
    "investment_signals": {
        "label": "Investment Signals",
        "tag_prefixes": ("OPPORTUNITY", "INVESTMENT", "SIGNAL"),
        "economic_fields": (),
        "index_keys": (),
        "question": "which specific, real signals in {country} are worth an investor's attention right now, and which explicitly aren't yet",
    },
}


def _angle_specific_fact_count(pack, angle_key):
    """Counts ONLY this angle's own material (never the universal
    always-kept business/lifecycle/gdp/population facts) -- so an angle
    with zero real specific evidence is never selected just because
    every pack has a GDP figure."""
    definition = ANGLE_DEFINITIONS[angle_key]
    count = 0
    for category, prefix in CATEGORY_TO_TAG_PREFIX.items():
        if prefix in definition["tag_prefixes"]:
            count += len(pack.get(category) or [])
    eb = pack.get("economicBaseline", {})
    if eb.get("available"):
        for field in definition["economic_fields"]:
            if eb.get("indicators", {}).get(field, {}).get("value") is not None:
                count += 1
    indices = pack.get("indices")
    if indices:
        for key in definition["index_keys"]:
            if indices.get(key):
                count += 1
    return count


def select_available_angles(pack, max_angles=3, min_facts=3):
    """Deterministic, code-based, zero-cost -- picks the angles this
    SPECIFIC pack actually has enough real material for, most fact-rich
    first. A thin pack may return fewer than max_angles, or zero (never
    forces an angle that doesn't have min_facts real, specific facts)."""
    scored = [(key, _angle_specific_fact_count(pack, key)) for key in ANGLE_DEFINITIONS]
    scored = [(key, n) for key, n in scored if n >= min_facts]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return [key for key, _ in scored[:max_angles]]


def _narrow_pack_for_angle(pack, angle_key):
    """Returns a NEW pack (never mutates the original) containing only
    this angle's specific categories plus the universal always-kept
    ones. Every other function in this file (fact extraction, provenance
    validation, audit) operates on `pack` as an opaque dict, so handing
    them this narrowed pack makes the ENTIRE existing safety pipeline
    apply unchanged -- no new gate, no new validator, just a smaller
    input."""
    definition = ANGLE_DEFINITIONS[angle_key]
    narrowed = dict(pack)

    eb = pack.get("economicBaseline", {})
    if eb.get("available"):
        keep_fields = set(definition["economic_fields"]) | set(UNIVERSAL_ALWAYS_KEEP_ECONOMIC_FIELDS)
        narrowed["economicBaseline"] = {
            **eb,
            "indicators": {k: v for k, v in eb.get("indicators", {}).items() if k in keep_fields},
        }

    for category, prefix in CATEGORY_TO_TAG_PREFIX.items():
        if prefix not in definition["tag_prefixes"] and category in narrowed:
            narrowed[category] = []

    if narrowed.get("indices"):
        idx = narrowed["indices"]
        narrowed["indices"] = {
            **idx,
            "zloi": idx.get("zloi") if "zloi" in definition["index_keys"] else None,
            "ztri": idx.get("ztri") if "ztri" in definition["index_keys"] else None,
        }

    return narrowed


# ============================================================
# Real founder-designed 5-part structure: HOOK -> Evidence Chain
# (what's happening / why it matters / who's affected) -> THE
# OPPORTUNITY (named, honest about whether a business match exists) ->
# EVIDENCE QUALITY (confidence, gaps) -> CTA. Applies to every full
# briefing/article format. Deliberately excludes ZETU_SHOW_COLD_OPEN --
# a 30-45s teaser feeding into a separate full episode, not a
# standalone briefing, and structurally incompatible with 5 real
# sections. YOUTUBE_SCRIPT gets the same 5 beats without literal
# labels, since this is read aloud, not displayed as text.
# ============================================================

_FIVE_PART_STRUCTURE_FORMATS = ("ATLAS_BRIEFING", "LINKEDIN_ARTICLE", "SUBSTACK_NEWSLETTER")


def _five_part_structure_block(spoken=False):
    if spoken:
        return (
            "REQUIRED STRUCTURE (5 beats, in this order -- narrate them naturally as part of the script; do NOT "
            "say these labels out loud, they are for you only, not the viewer): "
            "(1) HOOK -- open with the single most concrete fact or question. "
            "(2) WHAT'S HAPPENING / WHY IT MATTERS / WHO'S AFFECTED -- the real facts, connected. "
            "(3) THE OPPORTUNITY -- name ONE specific, real opportunity from the facts (never a vague sector). If "
            "a fact links a specific business to it, say so; if NO fact links any business to it, say plainly that "
            "no business match is recorded yet -- never imply one that isn't in the facts. "
            "(4) EVIDENCE QUALITY -- one honest beat naming what's solid vs. thin or unverified, using the trust "
            "states shown on the facts (VERIFIED/QUALIFIED/INSUFFICIENT_EVIDENCE) -- never treat every fact as "
            "equally certain. "
            "(5) CTA -- the SPECIFIC invitation already described above (e.g. list on ZetuMap, subscribe to Zetu "
            "Atlas). The voice guide's generic sign-off ('This is The Zetu Atlas. One question. One economy. "
            "Together.') is NOT this CTA -- if you use that line at all, say it AFTER the specific CTA as a "
            "closing flourish, never instead of it."
        )
    return (
        "REQUIRED STRUCTURE -- use these exact section labels so the piece is legible at a glance:\n"
        "HOOK: <one sentence -- the single most concrete fact or a genuine open question>\n"
        "WHAT'S HAPPENING: <the core facts, in your own words>\n"
        "WHY IT MATTERS: <what those facts mean, using only inferences the facts themselves support>\n"
        "WHO'S AFFECTED: <which sectors, businesses, or geographies, per the facts>\n"
        "THE OPPORTUNITY: <name ONE specific, real opportunity from the facts -- never a vague sector. If a fact "
        "links a specific business to it, name them together; if NO fact links any business to it, state plainly "
        "that no business match is recorded yet for this opportunity -- never imply one that isn't in the facts. "
        "Include a timeline/deadline ONLY if a fact states one.>\n"
        "EVIDENCE QUALITY: <one honest paragraph naming what's solid vs. thin or unverified here, using the trust "
        "states shown on the facts (VERIFIED/QUALIFIED/INSUFFICIENT_EVIDENCE) -- never treat every fact as equally "
        "certain, and never omit this section.>\n"
        "CTA: <the SPECIFIC invitation already described above (e.g. list on ZetuMap, subscribe to Zetu Atlas). "
        "The voice guide's generic sign-off ('This is The Zetu Atlas. One question. One economy. Together.') is "
        "NOT this CTA -- if you use that line at all, put it AFTER this specific CTA as a closing flourish, never "
        "instead of it.>"
    )


def build_closed_book_prompt(pack, content_format="ATLAS_BRIEFING", angle=None):
    """CONTRACT: when `angle` is given, `pack` MUST already be the
    angle-narrowed pack (_narrow_pack_for_angle's output) -- this
    function does not narrow it itself. `angle` only adds the
    question/HEADLINE-CTA framing; narrowing must happen once, upstream,
    on the SAME pack object also handed to audit_briefing_claims() and
    repair_briefing(), or the audit would validate against facts this
    piece was never supposed to cite. run_multi_angle_pipeline() is the
    one correct caller for angle-scoped generation."""
    facts = _pack_fact_lines(pack)
    country = pack.get("country", {}).get("name", "Unknown")

    if not facts:
        # No point calling the model at all -- there is nothing to write about.
        return None

    facts_block = "\n".join(f"- {f}" for f in facts)
    base_framing = TASK_FRAMING.get(content_format, TASK_FRAMING["ATLAS_BRIEFING"])(country, len(facts))
    if angle:
        definition = ANGLE_DEFINITIONS[angle]
        question = definition["question"].format(country=country)
        framing = (
            f"{base_framing}\n\nANGLE FOR THIS SPECIFIC PIECE: {definition['label']} -- the facts below have "
            f"already been narrowed to just this angle; there is no other topic to cover in this piece. Build the "
            f"ENTIRE piece around answering one question: {question}. Do not attempt to also cover unrelated "
            f"categories not represented in the facts below. The HOOK and THE OPPORTUNITY sections in the required "
            f"structure below must both be specific claims about {country} answering that exact question, never a "
            f"generic topic label like 'Trade in {country}'."
        )
    else:
        framing = base_framing

    structure_block = ""
    if content_format in _FIVE_PART_STRUCTURE_FORMATS:
        structure_block = "\n\n" + _five_part_structure_block(spoken=False)
    elif content_format == "YOUTUBE_SCRIPT":
        structure_block = "\n\n" + _five_part_structure_block(spoken=True)

    today = datetime.now().strftime("%Y-%m-%d")

    return f"""{framing}{structure_block}

TODAY'S DATE IS {today}. Use this to judge tense correctly for every dated fact below -- see HARD RULE 11.

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
11. A fact's year label tells you whether it is PAST or FUTURE relative to {today} -- use that to pick correct tense. A year at or before {today} already happened: state it as a recorded fact ("Kenya's GDP reached $135.9 billion in 2025"), never as a projection ("is projected to reach... by 2025", "is forecasted to be... in 2025") -- that year is not still coming. Only a year genuinely after {today} may be described as a projection or forecast.

You have {len(facts)} real, usable facts above -- that is enough for a real, short, honest briefing. Write it. Only if you genuinely have nothing at all to say (this is not that case) would you instead respond with exactly: INSUFFICIENT_EVIDENCE

Begin:"""


def generate_closed_book_briefing(openai_client, pack, content_format="ATLAS_BRIEFING", angle=None):
    prompt = build_closed_book_prompt(pack, content_format, angle)
    if prompt is None:
        return "INSUFFICIENT_EVIDENCE", {"reason": "pack contained zero usable facts"}
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=_MAX_TOKENS_BY_FORMAT.get(content_format, 1500),
    )
    text = response.choices[0].message.content.strip()
    return text, {"promptFactCount": len(_pack_fact_lines(pack))}


def audit_briefing_claims(anthropic_client, pack, briefing_text, content_format="ATLAS_BRIEFING"):
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
        max_tokens=_AUDIT_MAX_TOKENS_BY_FORMAT.get(content_format, 2000),
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [{"statement": "AUDIT_PARSE_FAILED", "pack_source": None, "claim_status": "UNKNOWN", "notes": text[:300]}]


def repair_briefing(openai_client, pack, briefing_text, unsupported, content_format="ATLAS_BRIEFING"):
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
        max_tokens=_MAX_TOKENS_BY_FORMAT.get(content_format, 1500),
    )
    return response.choices[0].message.content.strip()


# --- Claim-to-source provenance hardening (narrow addition, this pipeline
# stage only) ---
#
# audit_briefing_claims() already asks Claude for a `pack_source` per
# statement (the writer's own docstring: "which allowed fact (by its
# [TAG:id] prefix) supports it, or null if none"), but nothing previously
# checked whether that string was REAL -- a plausible-looking but invented
# tag (or a null one) on an otherwise SUPPORTED/QUALIFIED statement was
# accepted exactly as if it were genuinely traceable. This closes that gap
# without changing what SUPPORTED/QUALIFIED/UNKNOWN mean, without touching
# generation, and without adding any new API call: pure, local,
# post-processing of the audit response Claude already returns, using the
# exact same fact tags the writer already put in the prompt.

_TAG_PATTERN = re.compile(r"([A-Z][A-Z_]*):([A-Za-z0-9_-]+)")


def _pack_valid_tags(pack):
    """The exact set of [TAG:id] identifiers the model was actually shown
    -- derived from _pack_fact_lines(pack) itself (the same function that
    built the prompt), never a second, parallel list that could drift out
    of sync with it."""
    tags = set()
    for line in _pack_fact_lines(pack):
        m = re.match(r"^\[([A-Z][A-Z_]*:[A-Za-z0-9_-]+)\]", line)
        if m:
            tags.add(m.group(1))
    return tags


def _extract_source_tags(pack_source):
    """Pulls every TAG:id token out of a pack_source string, tolerant of
    brackets, missing brackets, or more than one tag joined by "and"/","
    (all forms already observed in real audit output) -- but this is
    tolerant PARSING only. Whether an extracted tag is real is a separate,
    strict check (validate_claim_source)."""
    if not pack_source:
        return []
    return [f"{m.group(1)}:{m.group(2)}" for m in _TAG_PATTERN.finditer(str(pack_source))]


def validate_claim_source(pack_source, valid_tags):
    """True only if pack_source names at least one tag, and EVERY tag it
    names is a real fact the pack actually contains. A null/missing
    pack_source, or one naming even a single invented/nonexistent tag
    (alone or mixed with a real one), is never valid -- this is the "do
    not allow Claude to invent a plausible-looking source identifier"
    requirement, enforced locally rather than trusted from the model."""
    tags = _extract_source_tags(pack_source)
    if not tags:
        return False
    return all(t in valid_tags for t in tags)


def annotate_provenance(audit, pack):
    """Adds a `provenance_valid` field to each audit entry -- never
    rewrites `claim_status` itself, so the SUPPORTED/QUALIFIED/UNKNOWN
    distinction Claude made is fully preserved and still visible. A
    statement that was SUPPORTED or QUALIFIED but fails provenance gets an
    explanatory note appended, so a human reading the saved artifact can
    see exactly why it was later treated as unresolved."""
    valid_tags = _pack_valid_tags(pack)
    annotated = []
    for entry in audit:
        entry = dict(entry)
        is_valid = validate_claim_source(entry.get("pack_source"), valid_tags)
        entry["provenance_valid"] = is_valid
        if not is_valid and entry.get("claim_status") in ("SUPPORTED", "QUALIFIED"):
            existing_note = (entry.get("notes") or "").strip()
            flag = "[PROVENANCE CHECK FAILED: pack_source does not resolve to an allowed pack fact]"
            entry["notes"] = f"{existing_note} {flag}".strip() if existing_note else flag
        annotated.append(entry)
    return annotated


def _unresolved(audit):
    """The gating definition, tightened: UNKNOWN (as before) OR a real
    provenance failure on an otherwise SUPPORTED/QUALIFIED statement.
    QUALIFIED itself is never treated as a failure -- only the specific
    combination of "claims to be supported/qualified" + "cannot actually
    be traced to a real pack fact" is unresolved."""
    return [a for a in audit if a.get("claim_status") == "UNKNOWN" or not a.get("provenance_valid", True)]


def _generate_audit_repair_loop(openai_client, anthropic_client, pack, content_format, max_repair_attempts, angle=None):
    """Shared by run_closed_book_pipeline() and run_multi_angle_pipeline()
    -- identical generate -> audit -> repair cycle either way; only the
    pack (full vs angle-narrowed) and angle differ. Extracted so there is
    exactly one place this loop is implemented, not two copies that could
    drift out of sync."""
    briefing, meta = generate_closed_book_briefing(openai_client, pack, content_format, angle)
    audit = annotate_provenance(audit_briefing_claims(anthropic_client, pack, briefing, content_format), pack)
    unsupported = _unresolved(audit)

    attempts = 0
    while unsupported and attempts < max_repair_attempts and briefing.strip() != "INSUFFICIENT_EVIDENCE":
        attempts += 1
        briefing = repair_briefing(openai_client, pack, briefing, unsupported, content_format)
        audit = annotate_provenance(audit_briefing_claims(anthropic_client, pack, briefing, content_format), pack)
        unsupported = _unresolved(audit)

    return briefing, meta, audit, unsupported, attempts


def run_closed_book_pipeline(pack_path, out_dir=".", max_repair_attempts=3, content_format="ATLAS_BRIEFING"):
    pack = load_intelligence_pack(pack_path)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

    briefing, meta, audit, unsupported, attempts = _generate_audit_repair_loop(
        openai_client, anthropic_client, pack, content_format, max_repair_attempts,
    )

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

    # NOTE: was a two-way ternary ("atlas" vs "show_cold_open") that would
    # have mislabeled every new format's output file as "show_cold_open" --
    # a real bug that only ever mattered once a 3rd format existed.
    suffix = _OUTPUT_SUFFIX.get(content_format, content_format.lower())
    out_path = os.path.join(out_dir, f"closed_book_preview_{pack.get('country', {}).get('code', 'XX')}_{suffix}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    return result, out_path


def run_multi_angle_pipeline(pack_path, out_dir=".", content_format="LINKEDIN_ARTICLE", max_angles=3, min_facts=3, max_repair_attempts=3):
    """Generates SEVERAL focused pieces from one pack instead of one piece
    trying to cover everything -- each angle gets its own narrowed pack
    (see _narrow_pack_for_angle), its own generate/audit/repair cycle, and
    is independently accepted/rejected. An angle this pack doesn't have
    enough real material for is never generated (select_available_angles
    only returns angles meeting min_facts) -- never forced, never padded.
    Returns a list of result dicts (same shape as run_closed_book_pipeline's
    single result, plus `angle`/`angleLabel`), one file written per angle.
    """
    pack = load_intelligence_pack(pack_path)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

    angles = select_available_angles(pack, max_angles=max_angles, min_facts=min_facts)
    country_code = pack.get("country", {}).get("code", "XX")
    suffix = _OUTPUT_SUFFIX.get(content_format, content_format.lower())

    results = []
    for angle_key in angles:
        narrowed_pack = _narrow_pack_for_angle(pack, angle_key)
        briefing, meta, audit, unsupported, attempts = _generate_audit_repair_loop(
            openai_client, anthropic_client, narrowed_pack, content_format, max_repair_attempts, angle_key,
        )
        result = {
            "generatedAt": datetime.now().isoformat(),
            "contentFormat": content_format,
            "angle": angle_key,
            "angleLabel": ANGLE_DEFINITIONS[angle_key]["label"],
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
        out_path = os.path.join(out_dir, f"closed_book_preview_{country_code}_{suffix}_{angle_key}.json")
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        result["_outPath"] = out_path
        results.append(result)

    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 zetu_closed_book_writer.py <intelligence_pack.json> [out_dir] [FORMAT] [--multi-angle]")
        print("       FORMAT: ATLAS_BRIEFING|ZETU_SHOW_COLD_OPEN|LINKEDIN_ARTICLE|YOUTUBE_SCRIPT|SUBSTACK_NEWSLETTER")
        sys.exit(1)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[1] if len(args) > 1 else "."
    fmt = args[2] if len(args) > 2 else "ATLAS_BRIEFING"

    if "--multi-angle" in sys.argv:
        results = run_multi_angle_pipeline(args[0], out_dir, content_format=fmt)
        if not results:
            print(f"[{fmt}/multi-angle] No angle had enough real material for this pack -- nothing generated.")
        for result in results:
            print(f"[{fmt}/{result['angle']}] {result['angleLabel']} -- {len(result['briefing'].split())} words, accepted={result['accepted']}, repairAttempts={result['repairAttempts']}:\n")
            print(result["briefing"])
            print(f"\nUnsupported claims: {result['unsupportedClaimCount']}")
            print(f"Saved to: {result['_outPath']}\n{'-' * 60}\n")
    else:
        result, out_path = run_closed_book_pipeline(args[0], out_dir, content_format=fmt)
        print(f"[{fmt}] Briefing ({len(result['briefing'].split())} words), accepted={result['accepted']}, repairAttempts={result['repairAttempts']}:\n")
        print(result["briefing"])
        print(f"\nUnsupported claims: {result['unsupportedClaimCount']}")
        print(f"Saved to: {out_path}")
