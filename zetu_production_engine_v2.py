#!/usr/bin/env python3
"""
Zetu Atlas Production Engine v2 - WITH SUBSTACK AUTO-PUBLISH
Sources → Claims → Verification → KPIs → Briefing → Substack
"""

import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

from openai import OpenAI
from anthropic import Anthropic
import supabase
from substack import Api as SubstackApi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('zetu_engine.log'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUBSTACK_EMAIL = os.environ.get("SUBSTACK_EMAIL")
SUBSTACK_PASSWORD = os.environ.get("SUBSTACK_PASSWORD")
SUBSTACK_PUBLICATION_URL = os.environ.get("SUBSTACK_PUBLICATION_URL")

def load_voice_guide():
    if os.path.exists("voice_guide.txt"):
        with open("voice_guide.txt", 'r') as f:
            return f.read()
    return ""

VOICE_GUIDE = load_voice_guide()

def validate_config():
    required = {'OPENAI_API_KEY': OPENAI_API_KEY, 'ANTHROPIC_API_KEY': ANTHROPIC_API_KEY, 'SUPABASE_URL': SUPABASE_URL, 'SUPABASE_KEY': SUPABASE_KEY}
    missing = [k for k, v in required.items() if not v or v.startswith('your-')]
    if missing:
        raise ValueError(f"Missing: {', '.join(missing)}")
    return True

def init_clients():
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    supabase_client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)
    return openai_client, anthropic_client, supabase_client

def fetch_sources(supabase_client, country_list):
    logger.info(f"📚 Fetching sources")
    try:
        response = supabase_client.table("sources").select("*").in_("country", country_list).eq("status", "active").execute()
        sources = response.data if response.data else []
        return sources
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return []

def extract_claims(openai_client, source):
    logger.info(f"🔍 Extracting from: {source['title'][:40]}")
    prompt = f"Extract claims. Return JSON only.\n\nSource: {source['title']}\nContent: {source['content'][:1500]}\n\nReturn: [{{'claim': 'text', 'reporting_period': 'date', 'source_extract': 'quote'}}]\nReturn [] if none."
    try:
        response = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=1000)
        text = response.choices[0].message.content
        if "```" in text:
            text = text.split("```")[1].strip("json").strip()
        claims = json.loads(text)
        return claims, source['id']
    except Exception as e:
        return [], source['id']

def verify_claim(anthropic_client, claim, source):
    prompt = f"Does source support claim? Return JSON.\nCLAIM: {claim.get('claim')}\nSOURCE: {claim.get('source_extract')}\n\n{{'status': 'pass|fail', 'reason': 'text', 'permitted_wording': 'text'}}"
    try:
        response = anthropic_client.messages.create(model="claude-sonnet-4-6", max_tokens=500, messages=[{"role": "user", "content": prompt}])
        text = response.content[0].text
        if "```" in text:
            text = text.split("```")[1].strip("json").strip()
        result = json.loads(text)
        return result.get('status') == 'pass', result.get('permitted_wording')
    except Exception as e:
        print(f"ANTHROPIC ERROR: {e}")
        return False, None

def fetch_kpis(supabase_client, week_of):
    try:
        response = supabase_client.table("kpis").select("*").eq("week_of", week_of).execute()
        return response.data if response.data else []
    except Exception as e:
        return []

def format_kpi_table(kpis):
    if not kpis:
        return ""
    table = "| Indicator | Value | Trend | Unit |\n|-----------|-------|-------|------|\n"
    for kpi in kpis:
        table += f"| {kpi.get('indicator_name')} | {kpi.get('current_value')} | {kpi.get('trend')} | {kpi.get('unit')} |\n"
    return table

def generate_briefing(openai_client, passed_claims, kpi_table, week_of):
    logger.info("✍️  Generating briefing")
    claims_text = "\n".join([f"- {c.get('permitted_wording', c.get('claim'))}" for c in passed_claims])
    prompt = f"""Write briefing for The Zetu Atlas in MICHAEL'S VOICE.

VOICE GUIDE:
{VOICE_GUIDE}

CLAIMS:
{claims_text}

KPIs:
{kpi_table}

Write 600-800 words. Hook with number. Show pattern. Include KPIs. Short sentences. Active voice.
End: "This is The Zetu Atlas. One question. One economy. Together."

Begin:"""
    try:
        response = openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.7, max_tokens=1500)
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return ""

def save_script(supabase_client, briefing, claims_used, claims_rejected, week_of):
    logger.info("💾 Saving")
    try:
        response = supabase_client.table("scripts").insert({"week_of": week_of, "briefing_title": f"Zetu Economic Briefing: {week_of}", "briefing_content": briefing, "claims_used": claims_used, "claims_rejected": claims_rejected, "approval_status": "pending_approval", "word_count": len(briefing.split()), "reading_time_minutes": len(briefing.split()) // 200}).execute()
        script_id = response.data[0]['id'] if response.data else None
        return script_id
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return None

def publish_to_substack(briefing_title, briefing_content):
    """Publish briefing to Substack"""
    logger.info("📤 Publishing to Substack")
    try:
        if not SUBSTACK_EMAIL or not SUBSTACK_PASSWORD or not SUBSTACK_PUBLICATION_URL:
            logger.error("❌ Substack credentials missing")
            return False
        
        api = SubstackApi(email=SUBSTACK_EMAIL, password=SUBSTACK_PASSWORD, publication_url=SUBSTACK_PUBLICATION_URL)
        draft = api.post_draft({"title": briefing_title, "body": briefing_content, "subtitle": "Africa's value, its builders, and its opportunities"})
        published = api.publish_draft(draft, send=True)
        logger.info(f"✅ Published: {published.get('url')}")
        return True
    except Exception as e:
        logger.error(f"❌ Publish failed: {e}")
        return False

def run_pipeline():
    print("""
╔════════════════════════════════════════════════════════╗
║  ZETU ATLAS ENGINE v2 - WITH SUBSTACK AUTO-PUBLISH    ║
║  Sources → Claims → Verification → KPIs → Briefing    ║
╚════════════════════════════════════════════════════════╝
""")
    
    try:
        validate_config()
        print("✅ Configuration valid\n")
        
        openai_client, anthropic_client, supabase_client = init_clients()
        print("✅ API clients initialized")
        
        if VOICE_GUIDE:
            print("✅ Voice guide loaded\n")
        
        country_list = ["Nigeria", "Kenya", "Ethiopia", "South Africa", "Ghana"]
        week_of = datetime.now().strftime("%Y-%m-%d")
        
        logger.info(f"🚀 Started for {week_of}")
        
        sources = fetch_sources(supabase_client, country_list)
        if not sources:
            print("⚠️  No sources in Supabase")
            return
        
        all_claims = []
        source_map = {}
        for source in sources:
            claims, source_id = extract_claims(openai_client, source)
            all_claims.extend(claims)
            source_map[source_id] = source
        
        if not all_claims:
            print("⚠️  No claims extracted")
            return
        
        passed_claims = []
        failed_claims = []
        for i, claim in enumerate(all_claims):
            source = source_map.get(claim.get('source_id'), {})
            passed, wording = verify_claim(anthropic_client, claim, source)
            if passed:
                claim['permitted_wording'] = wording
                passed_claims.append(claim)
                print(f"✅ Claim {i+1}: PASS")
            else:
                failed_claims.append(claim)
                print(f"❌ Claim {i+1}: FAIL")
        
        if not passed_claims:
            print("⚠️  No claims passed")
            return
        
        print(f"\n📊 {len(passed_claims)} passed, {len(failed_claims)} failed\n")
        
        kpis = fetch_kpis(supabase_client, week_of)
        kpi_table = format_kpi_table(kpis)
        
        briefing = generate_briefing(openai_client, passed_claims, kpi_table, week_of)
        if not briefing:
            print("❌ Briefing failed")
            return
        
        script_id = save_script(supabase_client, briefing, [c.get('id') for c in passed_claims], [c.get('id') for c in failed_claims], week_of)
        
        print(f"""
╔════════════════════════════════════════════════════════╗
║  ✅ BRIEFING READY FOR APPROVAL                        ║
╚════════════════════════════════════════════════════════╝

📊 Results:
  Sources: {len(sources)}
  Claims: {len(passed_claims)} passed
  KPIs: {len(kpis)}
  Script ID: {script_id}

📍 Workflow:
  1. Review in Supabase > scripts table
  2. Approve (set approval_status = 'approved')
  3. After approval, publish the approved script to Substack
  4. Briefing auto-publishes to Substack

✅ System ready
""")
        logger.info("✅ Complete")
        
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    run_pipeline()
