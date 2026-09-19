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
    fact, not as a separate abstract rule.

    A pack carrying a "storyRecord" key (an approved Zetu Story Record,
    see build_story_pack()) is a DIFFERENT input shape -- its fact lines
    come ONLY from the story's own supported fields, never the raw pack
    categories below. This is what makes every downstream function
    (_pack_valid_tags, annotate_provenance, build_closed_book_prompt)
    work UNCHANGED against a story record: they all just call this
    function and never know or care which shape produced the lines."""
    if "storyRecord" in pack:
        return _story_record_fact_lines(pack["storyRecord"])
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
        f"across categories rather than listing them. {_ANTI_DRIFT_RULE} Immediately after the required EVIDENCE "
        f"QUALITY section below (be SPECIFIC there about what's thin, stale, or missing for {country}, e.g. "
        f"'visitor numbers for these tourism sites are not available' -- do not let the piece sound more complete "
        f"than it is), add exactly ONE generic referral line phrased as a "
        f"direct instruction to the reader, such as 'If this was useful, forward it to one person who'd use it' -- "
        f"phrase it ONLY as an instruction to act, never as a claim about WHO specifically would benefit or WHY "
        f"(e.g. never 'forward this to a tourism analyst' -- asserting who benefits is itself an unsupported claim "
        f"about the piece, not an instruction). Never a renewal/upsell pitch, since they already pay. You MUST end "
        f"the newsletter with a line starting exactly with 'SUBJECT_LINE:' followed by one real, specific subject "
        f"line for this exact issue "
        f"(not a generic template) -- this is a required part of the output, not optional."
    ),
    "SHORT_FORM_VIDEO": lambda country, fact_count=0: (
        f"Write a short-form video script about {country}, in Michael's voice, for TikTok/Reels/YouTube Shorts "
        f"viewers -- young, digital-native, scrolling fast, zero patience for a slow start. TARGET 100-150 words "
        f"(45-90 seconds spoken) -- this is the shortest, most compressed format; pick the SINGLE most striking "
        f"real fact from the story and build the whole clip around it, rather than trying to summarize "
        f"everything the story contains. {_ANTI_DRIFT_RULE} Short sentences. No bullet points, no markdown, no "
        f"throat-clearing context the viewer doesn't need in the first 2 seconds. This is entertainment/discovery "
        f"first, not a business memo."
    ),
    "ZETU_SHOW_EPISODE": lambda country, fact_count=0: (
        f"Write a full spoken video script about {country}, in Michael's voice, for a Zetu Show investigative "
        f"episode -- the long-form documentary format, following the show's own investigation formula: 'Where is "
        f"the opportunity?' This is narration meant to be read aloud on camera across a full episode -- short "
        f"sentences, natural spoken rhythm, no bullet points, no markdown tables. TARGET 1200-1800 words (roughly "
        f"8-12 minutes read aloud) -- cite at least {_fact_floor(fact_count, 20)} distinct facts across the "
        f"chapters you're told to include below. {_ANTI_DRIFT_RULE} Structure the episode as a sequence of "
        f"clearly separated spoken chapters, following the chapter plan given below EXACTLY -- do not add a "
        f"chapter that isn't listed as included, and do not skip one that is. End on the CONTINUE ON ZETU "
        f"chapter with the required CTA."
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
    "SHORT_FORM_VIDEO": 400,
    "ZETU_SHOW_EPISODE": 4500,
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
    "SHORT_FORM_VIDEO": 1200,
    "ZETU_SHOW_EPISODE": 8000,
}
_OUTPUT_SUFFIX = {
    "ATLAS_BRIEFING": "atlas",
    "ZETU_SHOW_COLD_OPEN": "show_cold_open",
    "LINKEDIN_ARTICLE": "linkedin",
    "YOUTUBE_SCRIPT": "youtube_script",
    "SUBSTACK_NEWSLETTER": "substack_newsletter",
    "SHORT_FORM_VIDEO": "short_form_video",
    "ZETU_SHOW_EPISODE": "show_episode",
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
            "REQUIRED STRUCTURE -- ALL 5 beats below MUST appear in the script, every time, in this order, with no "
            "exceptions -- never silently drop a beat just because it's thin (narrate them naturally; do NOT say "
            "these labels out loud, they are for you only, not the viewer): "
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
            "closing flourish, never instead of it. "
            "NEVER skip a beat entirely on a thin story. If the facts genuinely don't support a real answer for "
            "one (e.g. no real opportunity or affected business exists), say that plainly in one honest sentence "
            "(e.g. 'no specific opportunity is identified in the available evidence for this story') -- an honest "
            "'nothing here yet' beat is correct; a silently skipped one is not."
        )
    return (
        "REQUIRED STRUCTURE -- ALL 7 labels below MUST appear in your output, every time, in this exact order, "
        "with no exceptions -- never drop a label entirely just because a section is thin. If a section has "
        "nothing real to say, the label still appears, followed by one honest sentence saying there's nothing "
        "there (see THE OPPORTUNITY's own instruction below for the exact pattern). A missing label is a "
        "structural error, not a stylistic choice:\n"
        "HOOK: <one sentence -- the single most concrete fact or a genuine open question>\n"
        "WHAT'S HAPPENING: <the core facts, in your own words>\n"
        "WHY IT MATTERS: <what those facts mean, using only inferences the facts themselves support>\n"
        "WHO'S AFFECTED: <which sectors, businesses, or geographies, per the facts. If nothing in the facts names "
        "a specific sector, business, or geography beyond the country itself, say that plainly -- do not drop "
        "this label.>\n"
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
        "instead of it.>\n"
        "NEVER leave a section's content empty on a thin story. If the facts genuinely don't support a real "
        "answer for a section (e.g. no real opportunity or affected business exists), write ONE honest sentence "
        "saying so plainly (e.g. 'No specific opportunity is identified in the available evidence for this "
        "story.') -- an honest 'nothing here yet' sentence under the label is correct; leaving the label with "
        "nothing under it is not."
    )


# Short-form video (TikTok/Reels/Shorts) deliberately does NOT use the
# 5-part structure -- a 45-90 second clip cannot fit 5 distinct
# sections with real content each (Phase C just proved even a full
# article-length piece can run out of material for 5 sections on a
# thin story; forcing that shape into 100-150 words would be worse).
# 3 beats instead: HOOK, THE POINT (one real fact, not a summary), CTA.
_SHORT_FORM_STRUCTURE_FORMATS = ("SHORT_FORM_VIDEO",)


def _short_form_structure_block():
    return (
        "REQUIRED STRUCTURE -- exactly 3 beats, narrated naturally as spoken narration for a 45-90 second video "
        "(do NOT say these labels out loud, they are for you only): "
        "(1) HOOK -- the single most concrete, striking fact or question, in the first 1-2 sentences. This has to "
        "earn the next few seconds of attention immediately -- no warm-up, no context-setting first. "
        "(2) THE POINT -- ONE real insight or fact from the story, explained in plain, fast, spoken language. Do "
        "not try to cover more than one idea -- a short video that tries to say five things says nothing. "
        "(3) CTA -- the SPECIFIC invitation already described above. The voice guide's generic sign-off is NOT "
        "this CTA -- if used at all, it comes after, never instead of it. "
        "NEVER pad to fill time -- if the story only genuinely supports 15 real seconds of content, write a "
        "15-second script, not a padded 90-second one."
    )


# ============================================================
# The full Zetu Show episode -- the 9-chapter investigative documentary
# format (real founder correction: the actual bridge between
# content:show's chapter structure and the evidence-safe pipeline).
# Structurally DIFFERENT from every other format here: some chapters
# are only real if the story supports the field they depend on --
# content-planner.mjs's own design (buildZetuShowEpisodeBrief's
# narrativeSections) treats an "investigation" chapter with nothing to
# investigate as a narrative defect, not something to fill with an
# honest "nothing here" sentence (contrast with the 5-part structure's
# own "never skip, always disclose" rule, which is right for a short
# article but wrong for a 9-chapter documentary arc). This mirrors that
# real, tested logic exactly, computed from the SAME Story Record
# fields already extracted and audited -- never left to model judgment.
# ============================================================

ZETU_SHOW_EPISODE_CHAPTERS = (
    ("THE QUESTION", None),
    ("THE STORY", "SIGNAL"),
    ("THE EVIDENCE", "PROOF"),
    ("THE INVESTIGATION", "SIGNAL"),
    ("THE OPPORTUNITY", "OPPORTUNITY"),
    ("THE BUILDERS", "BUILDER"),
    ("THE OBSTACLE", "OBSTACLE"),
    ("THE MISSION", None),
    ("CONTINUE ON ZETU", None),
)


def _show_episode_chapter_plan(story_record):
    """(chapter, included) pairs -- a chapter with field=None is always
    included (THE QUESTION/THE MISSION/CONTINUE ON ZETU); every other
    chapter is included only if its dependent Story Record field is
    genuinely supported. Pure, deterministic, no model judgment."""
    return [
        (chapter, True if field is None else story_record.get(field, {}).get("status") == "supported")
        for chapter, field in ZETU_SHOW_EPISODE_CHAPTERS
    ]


def _show_episode_structure_block():
    return (
        "THE ZETU SHOW 9-CHAPTER FORMAT (only the chapters marked INCLUDE in the chapter plan given separately "
        "below actually appear in your script -- this just describes what each chapter IS):\n"
        "THE QUESTION: the central investigative question this episode is chasing (always included).\n"
        "THE STORY: the real signal or development that anchors this investigation.\n"
        "THE EVIDENCE: what real evidence backs the story so far.\n"
        "THE INVESTIGATION: digging into that evidence on camera -- what it reveals, what it doesn't yet answer.\n"
        "THE OPPORTUNITY: name ONE specific, real opportunity from the facts -- never a vague sector, and never "
        "imply a business match the facts don't state.\n"
        "THE BUILDERS: a real, named business or person already acting, with their OBSERVED (never VERIFIED) "
        "capability.\n"
        "THE OBSTACLE: what's blocking it, per a real stated limitation or retired opportunity.\n"
        "THE MISSION: Zetu's own framing of why this investigation matters to the platform's mission (always "
        "included -- this is Zetu's own voice, not a factual claim about the country).\n"
        "CONTINUE ON ZETU: the required CTA (always included, see below)."
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
    elif content_format in _SHORT_FORM_STRUCTURE_FORMATS:
        structure_block = "\n\n" + _short_form_structure_block()
    elif content_format == "ZETU_SHOW_EPISODE":
        structure_block = "\n\n" + _show_episode_structure_block()

    story_note = ""
    if "storyRecord" in pack:
        story_note = (
            "\n\nNOTE: the facts below are NOT raw pack data -- they are an already-approved, curated Zetu "
            "Story Record (a shared editorial interpretation already vetted for this specific story, one shared "
            "across every platform's version of it). Present them for this platform; do not reinterpret or "
            "second-guess the story, and a field that isn't listed below was deliberately marked unsupported -- "
            "it is not something to infer or fill in yourself."
        )

    cta_override = ""
    if pack.get("suggestedCTA"):
        cta_override = (
            f"\n\nTHE REQUIRED CALL TO ACTION FOR THIS PIECE IS: \"{pack['suggestedCTA']}\" -- this is a real, "
            f"pre-selected Zetu action, chosen because of what THIS SPECIFIC story actually supports (a real "
            f"named business, a real open opportunity, or neither), not a generic default. This OVERRIDES any "
            f"other call-to-action example given above or in the required structure below -- adapt its wording "
            f"naturally to this platform's voice and length, but the underlying ask must be this exact one. Do "
            f"NOT invent a different call to action, and do not let the voice guide's generic sign-off line "
            f"substitute for it."
        )

    chapter_plan_note = ""
    if content_format == "ZETU_SHOW_EPISODE" and "storyRecord" in pack:
        plan = _show_episode_chapter_plan(pack["storyRecord"])
        included = [c for c, ok in plan if ok]
        skipped = [c for c, ok in plan if not ok]
        chapter_plan_note = (
            f"\n\nCHAPTER PLAN FOR THIS EPISODE (computed from what THIS story actually supports -- follow it "
            f"exactly, it is not optional): INCLUDE, in this order: {', '.join(included)}."
        )
        if skipped:
            chapter_plan_note += (
                f" SKIP entirely (no real material exists for these this time -- do not force them, do not "
                f"apologize for or mention skipping them on camera, just move directly from one included "
                f"chapter to the next): {', '.join(skipped)}."
            )
        else:
            chapter_plan_note += " Every chapter has real material this time -- include all of them."
        chapter_plan_note += (
            " YOU MUST ACTUALLY WRITE ALL THE WAY THROUGH TO THE LAST INCLUDED CHAPTER, ENDING ON 'CONTINUE ON "
            "ZETU' WITH THE REQUIRED CTA -- a script that stops partway through the plan above (e.g. ending on "
            "THE OPPORTUNITY without ever reaching THE MISSION or CONTINUE ON ZETU) is INCOMPLETE and unusable, "
            "even if every sentence written so far is accurate. If you need to keep earlier chapters shorter to "
            "make room, do that -- but you must reach the end of the plan. This is as non-negotiable as the "
            "SUBJECT_LINE requirement is for a newsletter."
        )

    today = datetime.now().strftime("%Y-%m-%d")

    return f"""{framing}{structure_block}{story_note}{cta_override}{chapter_plan_note}

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

ONE exception to "do not add anything new": if this briefing uses the required section-label structure (HOOK:, WHAT'S HAPPENING:, etc.) and deleting a flagged sentence would leave a required label with nothing under it, add exactly ONE short honest sentence there instead (e.g. "No specific opportunity is identified in the available evidence for this story.") -- a label with an honest "nothing here" sentence is required by the format, not new content; a label left with nothing at all under it is a structural error.

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


# ============================================================
# Zetu Story Record -- the shared editorial interpretation layer between
# the Intelligence Pack and any platform generator (real founder
# correction: "Intelligence Pack -> Zetu Story Record -> platform-
# specific content", never Pack -> platform directly). A field is never
# invented to complete the template -- unsupported fields are expected
# and correct. Reuses annotate_provenance() and _pack_valid_tags()
# COMPLETELY UNCHANGED (see _story_record_to_audit_entries below); the
# only new logic is the extraction prompt, the semantic claim audit
# (mirrors audit_briefing_claims()'s own shape), and the repair loop
# for rejected fields.
# ============================================================

STORY_RECORD_FIELD_DEFINITIONS = {
    "SIGNAL": "what happened or what was discovered, per the facts",
    "OBJECT": "a tangible object, product, or service that can carry the story -- ONLY if the facts name one",
    "QUESTION": "the central investigative question this story raises -- must come from a real tension between facts, never a generic question",
    "MONEY": "how or where money/value moves, per the facts",
    "CHAIN": "the relevant value-chain step(s) shown in the facts",
    "GAP": "where value or opportunity is being missed or underdeveloped, per the facts",
    "PROOF": "the specific evidence that supports this story",
    "BUILDER": "a named person, business, or organisation already acting -- ONLY if the facts name one",
    "OPPORTUNITY": "a specific participation/business/investment opportunity the facts actually support -- never a vague sector",
    "OBSTACLE": "what prevents or limits it -- ONLY if the facts state one",
    "ACTION": "a useful, concrete audience action grounded in the facts",
    "OPEN_THREAD": "an unresolved question worth following, per the facts",
}
# PLACE is deliberately NOT in this dict -- it's a structural fact
# (country/economy) known before any interpretation, never an LLM
# extraction or an audited claim. See build_story_pack()/CLI output,
# which surface it directly from pack["country"].


def _story_record_fact_lines(story_record):
    """Fact lines for an approved Story Record -- the ONLY facts a
    platform generator may draw from once a story exists, per "must not
    independently interpret the raw Intelligence Pack". A field with no
    supported value produces NO line at all -- nothing to cite means
    nothing is available to the platform generator, exactly matching
    "mark it unsupported/not available", never fabricated to look
    complete."""
    lines = []
    for field, data in story_record.items():
        if data.get("status") != "supported" or data.get("value") is None:
            continue
        # NOTE: deliberately does NOT print the field's original underlying
        # pack_source (e.g. "[ECONOMIC:gdp_usd]") inside the visible text --
        # a real run showed the audit model citing THAT embedded tag as its
        # source instead of the actual, valid [STORY:field] tag, since it
        # looks exactly like a citation. The original tag was already
        # verified once during story extraction; it has no further role
        # once the story is approved -- [STORY:field] is the only citation
        # that should exist from here on.
        lines.append(f"[STORY:{field}] {data['value']} (a fact from the approved Story Record, already independently verified)")
    return lines


def _story_record_prompt(pack):
    facts = _pack_fact_lines(pack)
    facts_block = "\n".join(f"- {f}" for f in facts)
    fields_block = "\n".join(f"- {name}: {desc}" for name, desc in STORY_RECORD_FIELD_DEFINITIONS.items())
    return f"""You are extracting a structured Zetu Story Record from a fixed set of allowed facts. This is a
shared editorial interpretation layer -- platform writers (LinkedIn, YouTube, Substack) will build their
pieces ONLY from what you fill in here, so honesty about gaps matters more than completeness.

=== THE ONLY FACTS YOU MAY USE ===
{facts_block}
=== END OF FACTS ===

For EACH of the following fields, either fill it with a value DIRECTLY traceable to one or more of the facts
above (citing the exact [TAG:id] that supports it), or mark it unsupported. DO NOT invent a value just to
complete the template -- an incomplete Story Record, with several fields marked unsupported, is expected and
correct when the facts don't cover every field. Never upgrade a signal/opportunity's evidence tier, never
imply a business-opportunity match the facts don't state, never invent a builder, object, or obstacle.

FIELDS:
{fields_block}

Return ONLY a JSON object, no markdown, no commentary, in exactly this shape:
{{"FIELD_NAME": {{"value": "the extracted value" or null, "pack_source": "[TAG:id]" or null}}, ...}}
One entry per field above, using the exact field names given.

Begin:"""


def extract_story_record(openai_client, pack):
    prompt = _story_record_prompt(pack)
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    text = response.choices[0].message.content.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = {}
    # Defensive: every defined field is present in the result even if the
    # model omitted one -- a missing field is treated as unsupported,
    # never silently dropped from the record.
    return {
        name: {"value": (raw.get(name) or {}).get("value"), "pack_source": (raw.get(name) or {}).get("pack_source")}
        for name in STORY_RECORD_FIELD_DEFINITIONS
    }


def audit_story_record_claims(anthropic_client, pack, story_record):
    """Mirrors audit_briefing_claims()'s own shape exactly, applied to
    story fields instead of briefing sentences -- an independent
    SEMANTIC check that a field's VALUE genuinely matches what its
    cited fact says, not just that the cited tag exists (that
    structural check is _story_record_to_audit_entries + the unchanged
    annotate_provenance(), a separate, deterministic layer)."""
    filled = {k: v for k, v in story_record.items() if v.get("value") is not None}
    if not filled:
        return {}

    facts_block = "\n".join(f"- {f}" for f in _pack_fact_lines(pack))
    fields_block = "\n".join(f'- {k}: "{v["value"]}" (cites {v.get("pack_source")})' for k, v in filled.items())
    prompt = f"""You are auditing a structured Story Record for factual accuracy against a fixed set of allowed facts.

ALLOWED FACTS (the ONLY things any field is permitted to state):
{facts_block}

STORY RECORD FIELDS TO AUDIT:
{fields_block}

For EACH field above, return a JSON object with:
  field: the field name
  claim_status: "SUPPORTED" (the value directly matches its cited fact), "QUALIFIED" (a fair restatement/simplification), or "UNKNOWN" (the value is not traceable to its cited fact, misstates it, or the citation doesn't support it)
  notes: brief reason

Return ONLY a JSON array, no markdown, no commentary."""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        return {k: "UNKNOWN" for k in filled}
    return {r.get("field"): r.get("claim_status", "UNKNOWN") for r in results if isinstance(r, dict) and r.get("field")}


def _story_record_to_audit_entries(story_record):
    """Adapts filled story fields into the exact {statement, pack_source,
    claim_status} shape annotate_provenance() already expects -- reuses
    that function COMPLETELY UNCHANGED rather than reimplementing
    provenance checking for a second data shape."""
    return [
        {"field": field, "statement": data["value"], "pack_source": data.get("pack_source"), "claim_status": "SUPPORTED"}
        for field, data in story_record.items() if data.get("value") is not None
    ]


def annotate_story_record(story_record, pack, semantic_status=None):
    """Combines the deterministic provenance check (annotate_provenance,
    UNCHANGED) with the semantic claim_status check (audit_story_record_claims)
    -- a field is only 'supported' if BOTH agree; either one failing
    forces the field to unsupported, never partially trusted."""
    entries = _story_record_to_audit_entries(story_record)
    annotated = annotate_provenance(entries, pack)
    by_field = {e["field"]: e for e in annotated}
    semantic_status = semantic_status or {}

    result = {}
    for field, data in story_record.items():
        has_value = data.get("value") is not None
        entry = by_field.get(field)
        prov_ok = bool(entry and entry.get("provenance_valid"))
        sem_ok = semantic_status.get(field) in ("SUPPORTED", "QUALIFIED")
        if has_value and prov_ok and sem_ok:
            result[field] = {"value": data["value"], "pack_source": data["pack_source"], "status": "supported"}
        else:
            result[field] = {"value": None, "pack_source": None, "status": "unsupported"}
    return result


def _unresolved_story_fields(story_record_before, annotated):
    """Fields that HAD a value proposed but got forced to unsupported --
    the actual repair target. A field the model itself already marked
    unsupported is not 'unresolved', it's just honestly incomplete."""
    return [f for f, before in story_record_before.items()
            if before.get("value") is not None and annotated[f]["status"] == "unsupported"]


def repair_story_record(openai_client, pack, story_record, unresolved_fields):
    """Mirrors repair_briefing()'s pattern: feed back ONLY the specific
    fields that failed verification, ask for a corrected value with a
    real citation or an honest 'unsupported' -- never touches a field
    that wasn't flagged."""
    facts_block = "\n".join(f"- {f}" for f in _pack_fact_lines(pack))
    flagged = "\n".join(
        f'- {f}: was "{story_record[f]["value"]}" (citing {story_record[f].get("pack_source")}) -- rejected, not genuinely traceable'
        for f in unresolved_fields
    )
    fields_block = "\n".join(f"- {name}: {STORY_RECORD_FIELD_DEFINITIONS[name]}" for name in unresolved_fields)
    prompt = f"""Here is a Story Record extraction, and a list of specific fields an independent audit
rejected as not genuinely traceable to the allowed facts.

ALLOWED FACTS (unchanged):
{facts_block}

FIELDS REJECTED:
{flagged}

FIELD DEFINITIONS (for the rejected fields only):
{fields_block}

For EACH rejected field: either provide a corrected value with a real [TAG:id] citation from the allowed
facts, or mark it unsupported (value: null, pack_source: null) if no real fact actually supports it. Do not
touch any other field.

Return ONLY a JSON object for the rejected fields, in the same shape: {{"FIELD_NAME": {{"value": ... or null, "pack_source": ... or null}}}}."""
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1000,
    )
    text = response.choices[0].message.content.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip()
    try:
        fixes = json.loads(text)
    except json.JSONDecodeError:
        fixes = {}
    updated = dict(story_record)
    for field in unresolved_fields:
        fix = fixes.get(field) or {"value": None, "pack_source": None}
        updated[field] = {"value": fix.get("value"), "pack_source": fix.get("pack_source")}
    return updated


def _generate_story_record_with_repair(openai_client, anthropic_client, pack, max_repair_attempts=3):
    story_record = extract_story_record(openai_client, pack)
    semantic_status = audit_story_record_claims(anthropic_client, pack, story_record)
    annotated = annotate_story_record(story_record, pack, semantic_status)
    unresolved = _unresolved_story_fields(story_record, annotated)

    attempts = 0
    while unresolved and attempts < max_repair_attempts:
        attempts += 1
        story_record = repair_story_record(openai_client, pack, story_record, unresolved)
        semantic_status = audit_story_record_claims(anthropic_client, pack, story_record)
        annotated = annotate_story_record(story_record, pack, semantic_status)
        unresolved = _unresolved_story_fields(story_record, annotated)

    return annotated, attempts


# ============================================================
# Real founder correction: the CTA in every platform piece was a fixed,
# invented string ("if you run a business in X, list it on ZetuMap"),
# never actually tied to a real ZetuMap feature. A real, tested,
# already-proven-on-Ghana-data CTA vocabulary already existed in
# Zetumap's lib/content-planner.mjs (Phase 8) and was never connected
# to anything. This is a literal port of that vocabulary (the exact
# same 7 strings) -- lib/content-planner.mjs remains the canonical
# source; if it changes, this must be updated to match.
# ============================================================

SAFE_CTAS = {
    "DISCOVER_BUSINESSES": "Discover businesses on Zetu",
    "CLAIM_YOUR_BUSINESS": "Claim your business listing on Zetu",
    "REGISTER_YOUR_BUSINESS": "Register your business on Zetu",
    "EXPLORE_OPPORTUNITY": "Explore this opportunity signal on Zetu",
    "PURSUE_VIA_OFFICIAL_SOURCE": "Pursue this opportunity through its official source, cited on Zetu",
    "DECLARE_CAPABILITY": "Declare or update your business capability on Zetu",
    "SUBMIT_FOR_CONSIDERATION": "Submit a relevant business or development for consideration on Zetu",
}


def select_safe_cta(story_record, pack):
    """Generalizes content-planner.mjs's own per-brief CTA rules
    (buildBusinessSpotlightBrief / buildOpportunitySpotlightBrief /
    buildZetuShowEpisodeBrief) across whatever a Story Record actually
    supports, since a story can carry EITHER a BUILDER or an OPPORTUNITY
    (or neither) -- content-planner.mjs never had to choose between the
    two in one brief, so this priority order (a named business first,
    since it's the most specific, then a real opportunity, else the
    always-safe default) is this function's own synthesis on top of the
    real vocabulary, not a direct copy of a single JS function. Must be
    called with the SAME pack the story was extracted/narrowed from --
    this is where opportunity active/retired state is still resolvable,
    which a downstream story-pack (build_story_pack()'s output) no
    longer carries."""
    builder = story_record.get("BUILDER", {})
    if builder.get("status") == "supported":
        return SAFE_CTAS["CLAIM_YOUR_BUSINESS"]

    opportunity = story_record.get("OPPORTUNITY", {})
    if opportunity.get("status") == "supported":
        opp_ids = [t.split(":", 1)[1] for t in _extract_source_tags(opportunity.get("pack_source")) if t.startswith("OPPORTUNITY:")]
        # A real run showed OPPORTUNITY can be "supported" while citing a
        # non-opportunity tag (e.g. an index ranking) -- the extraction
        # correctly traced it to a real fact, but there is no actual
        # canonical_opportunities record, so no real "official source" to
        # send anyone to. PURSUE_VIA_OFFICIAL_SOURCE would be misleading
        # in that case; only use it when a real opportunity id is present.
        if opp_ids:
            opportunities_by_id = {o["id"]: o for o in pack.get("opportunities", [])}
            is_retired = any("MAY_NOT_CLAIM" in (opportunities_by_id.get(oid, {}).get("flags") or []) for oid in opp_ids)
            return SAFE_CTAS["DISCOVER_BUSINESSES"] if is_retired else SAFE_CTAS["PURSUE_VIA_OFFICIAL_SOURCE"]

    return SAFE_CTAS["DISCOVER_BUSINESSES"]


def run_story_record_pipeline(pack_path, out_dir=".", angle=None, max_repair_attempts=3):
    """Pack -> ONE approved Story Record for ONE story (angle). Never
    forces an angle the pack lacks material for -- that's
    select_available_angles()'s job, called by run_weekly_story_pipeline
    below, not this function (angle=None here means "use the full,
    unnarrowed pack as one story", a valid choice for a caller who
    already knows what they want)."""
    pack = load_intelligence_pack(pack_path)
    if angle:
        pack = _narrow_pack_for_angle(pack, angle)
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

    annotated, attempts = _generate_story_record_with_repair(openai_client, anthropic_client, pack, max_repair_attempts)
    supported_count = sum(1 for v in annotated.values() if v["status"] == "supported")
    country_code = pack.get("country", {}).get("code", "XX")
    # Computed HERE, not at platform-generation time -- this is the only
    # point where the real (angle-narrowed) pack is still in scope to
    # resolve an opportunity's active/retired state. build_story_pack()
    # carries the result forward as plain data.
    suggested_cta = select_safe_cta(annotated, pack)

    result = {
        "generatedAt": datetime.now().isoformat(),
        "packCountry": pack.get("country", {}).get("name"),
        "packCountryCode": country_code,
        "packAssembledAt": pack.get("assembledAt"),
        "angle": angle,
        "angleLabel": ANGLE_DEFINITIONS[angle]["label"] if angle else None,
        "repairAttempts": attempts,
        "storyRecord": annotated,
        "supportedFieldCount": supported_count,
        "totalFieldCount": len(annotated),
        "suggestedCTA": suggested_cta,
    }

    suffix = angle or "full"
    out_path = os.path.join(out_dir, f"story_record_{country_code}_{suffix}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    return result, out_path


def _select_best_story_candidate(candidates):
    """Picks the candidate result with the highest supportedFieldCount;
    a tie breaks toward the earlier-ranked candidate (lower index,
    matching select_available_angles' own deterministic ranking).
    `candidates` is a list of run_story_record_pipeline() result dicts.
    Pure, synchronous, no I/O -- the only piece of the empirical
    selection below that can be tested without a real API call."""
    return max(range(len(candidates)), key=lambda i: (candidates[i]["supportedFieldCount"], -i))


def run_weekly_story_pipeline(pack_path, out_dir=".", max_repair_attempts=3, candidate_angles=2):
    """The '1 story/week' policy entry point.

    Real evidence from two live Kenya runs showed neither raw fact
    count, nor category-type diversity, nor an angle's own trust-state
    reliably predicts which angle will produce a fuller story once
    extracted: tech_and_innovation (100% INSUFFICIENT_EVIDENCE own
    material) outperformed investment_signals (100% QUALIFIED/VERIFIED
    own material) 8/12 supported fields to 4/12. A code-only heuristic
    trying to predict this from raw pack shape would be a guess dressed
    up as a formula.

    Instead: extract full Story Records for the top `candidate_angles`
    angles (select_available_angles' own ranking) and empirically keep
    whichever ACTUALLY produced more supported fields -- selection by
    real outcome, not prediction. Real cost: `candidate_angles`
    extraction+audit(+repair) cycles instead of 1, but platform
    generation (the expensive part) still only ever runs once, against
    the winner. Returns (None, None) if the pack has no angle with
    enough real material for a story this week -- never forces one,
    and never runs more candidates than angles actually available."""
    pack = load_intelligence_pack(pack_path)
    angles = select_available_angles(pack, max_angles=candidate_angles)
    if not angles:
        return None, None

    candidates = [run_story_record_pipeline(pack_path, out_dir, angle=angle, max_repair_attempts=max_repair_attempts) for angle in angles]
    best_index = _select_best_story_candidate([result for result, _ in candidates])
    winner_result, winner_path = candidates[best_index]

    # Self-documenting: the persisted artifact records what else was
    # considered and why this one won, so a human reading it later
    # doesn't have to guess or re-run anything to see the comparison.
    winner_result["candidatesConsidered"] = [
        {"angle": result["angle"], "angleLabel": result["angleLabel"], "supportedFieldCount": result["supportedFieldCount"]}
        for result, _ in candidates
    ]
    with open(winner_path, "w") as f:
        json.dump(winner_result, f, indent=2)

    return winner_result, winner_path


def build_story_pack(story_result):
    """Wraps an approved Story Record into a 'pack'-shaped object the
    EXISTING generation pipeline (build_closed_book_prompt,
    generate_closed_book_briefing, audit_briefing_claims,
    repair_briefing, run_closed_book_pipeline) consumes COMPLETELY
    UNCHANGED -- see _pack_fact_lines' storyRecord branch above. This is
    the one function that turns "an approved story" into something the
    rest of this file already knows how to read."""
    return {
        "country": {"code": story_result.get("packCountryCode"), "name": story_result.get("packCountry")},
        "assembledAt": story_result.get("packAssembledAt"),
        "storyRecord": story_result["storyRecord"],
        "suggestedCTA": story_result.get("suggestedCTA"),
    }


def run_platform_from_story(story_result, content_format, out_dir=".", max_repair_attempts=3):
    """Story -> ONE platform piece, by building a story-pack and handing
    it to the EXISTING, unmodified run_closed_book_pipeline(). Adds no
    new generation/audit logic of its own -- it only prepares the input
    the existing, already-proven pipeline reads."""
    story_pack = build_story_pack(story_result)
    tmp_path = os.path.join(out_dir, f"_story_pack_{story_result.get('packCountryCode', 'XX')}_{story_result.get('angle') or 'full'}.json")
    with open(tmp_path, "w") as f:
        json.dump(story_pack, f, indent=2)
    return run_closed_book_pipeline(tmp_path, out_dir, max_repair_attempts=max_repair_attempts, content_format=content_format)


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
        print("       python3 zetu_closed_book_writer.py <intelligence_pack.json> [out_dir] --story")
        print("       python3 zetu_closed_book_writer.py <story_record.json> [out_dir] <FORMAT> --from-story")
        print("       FORMAT: ATLAS_BRIEFING|ZETU_SHOW_COLD_OPEN|LINKEDIN_ARTICLE|YOUTUBE_SCRIPT|SUBSTACK_NEWSLETTER|SHORT_FORM_VIDEO|ZETU_SHOW_EPISODE")
        sys.exit(1)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[1] if len(args) > 1 else "."
    fmt = args[2] if len(args) > 2 else "ATLAS_BRIEFING"

    if "--story" in sys.argv:
        result, out_path = run_weekly_story_pipeline(args[0], out_dir)
        if result is None:
            print("No angle in this pack has enough real material for a story this week -- nothing generated.")
        else:
            if len(result.get("candidatesConsidered", [])) > 1:
                print("Candidates considered:")
                for c in result["candidatesConsidered"]:
                    mark = " <- chosen" if c["angle"] == result["angle"] else ""
                    print(f"  {c['angleLabel']}: {c['supportedFieldCount']} fields supported{mark}")
                print()
            print(f"Story Record for {result['packCountry']} ({result['angleLabel']}) -- {result['supportedFieldCount']}/{result['totalFieldCount']} fields supported, repairAttempts={result['repairAttempts']}\n")
            for field, data in result["storyRecord"].items():
                mark = "OK" if data["status"] == "supported" else "--"
                print(f"  [{mark}] {field}: {data['value'] if data['value'] else '(unsupported)'}")
            print(f"\nSaved to: {out_path}")
    elif "--from-story" in sys.argv:
        with open(args[0]) as f:
            story_result = json.load(f)
        result, out_path = run_platform_from_story(story_result, fmt, out_dir)
        print(f"[{fmt} from story] ({len(result['briefing'].split())} words), accepted={result['accepted']}, repairAttempts={result['repairAttempts']}:\n")
        print(result["briefing"])
        print(f"\nUnsupported claims: {result['unsupportedClaimCount']}")
        print(f"Saved to: {out_path}")
    elif "--multi-angle" in sys.argv:
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
