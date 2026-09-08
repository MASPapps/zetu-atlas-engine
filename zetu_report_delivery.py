#!/usr/bin/env python3
"""
Zetu Atlas — report email delivery (additive; does not modify
zetu_production_engine_v2.py or zetu_closed_book_writer.py).

Flow: INTELLIGENCE PACK -> CLOSED-BOOK ATLAS REPORT (zetu_closed_book_writer.py,
unchanged) -> VALIDATION (this module, reusing the writer's own
accepted/unsupportedClaimCount fields, never re-deciding safety itself)
-> SAVE REPORT (local JSON, mirroring the writer's own output convention)
-> EMAIL REPORT (smtplib, only reached if validation passed) -> RECORD
DELIVERY RESULT (a local JSON ledger, keyed by a deterministic report id,
to prevent duplicate sends).

Audited before writing this file (see
ZETU_DATA_LOOP... email audit -- no separate doc, reported inline to the
user): zero existing email/notification/SMTP code anywhere in this repo.
SUBSTACK_EMAIL in .env is the Substack *login* used by
zetu_production_engine_v2.py's publish_to_substack() -- not a
notification recipient. EMAIL_FROM/EMAIL_PASSWORD exist in .env but were
never referenced by any code before this file. No ATLAS_REPORT_EMAIL (or
equivalent recipient) variable exists anywhere. No cron/launchd/systemd
schedule exists for any Atlas script (the one crontab entry on this
machine is an unrelated Zetumap job, sync:careerjet-jobs).

Never prints, logs, or stores any credential value. The SMTP host is
derived from EMAIL_FROM's domain via a small known-provider map, read
internally and never displayed.
"""

import os
import json
import hashlib
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

LEDGER_PATH = "atlas_report_delivery_ledger.json"

KNOWN_SMTP_HOSTS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
}


class DeliveryBlocked(Exception):
    """Raised when a report must NOT be emailed. .reason is a short, safe
    category string -- never a secret, never raw report content."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def validate_report_for_delivery(closed_book_result):
    """Reuses the closed-book writer's OWN safety decision -- does not
    re-implement or second-guess claim auditing here. Returns nothing on
    success; raises DeliveryBlocked with a specific reason otherwise."""
    briefing = (closed_book_result or {}).get("briefing")
    if not briefing or not briefing.strip():
        raise DeliveryBlocked("EMPTY_OR_CORRUPT_REPORT")
    if briefing.strip() == "INSUFFICIENT_EVIDENCE":
        raise DeliveryBlocked("INSUFFICIENT_EVIDENCE")
    if closed_book_result.get("accepted") is not True:
        raise DeliveryBlocked("UNSUPPORTED_CLAIMS_DETECTED")
    if closed_book_result.get("unsupportedClaimCount", 1) != 0:
        raise DeliveryBlocked("UNSUPPORTED_CLAIMS_DETECTED")
    return True


def compute_report_id(closed_book_result):
    """Deterministic id from country + pack version + the exact accepted
    briefing text -- the SAME report (same pack, same generated text)
    always produces the same id, which is what duplicate-send protection
    keys on. A different regeneration (even of the same pack) naturally
    gets a different id, since the prose itself differs -- this is
    intentional: it is a NEW report, not a resend of an old one."""
    material = f"{closed_book_result.get('packCountry')}|{closed_book_result.get('packAssembledAt')}|{closed_book_result.get('briefing')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_report_email(closed_book_result, pack):
    country = pack.get("country", {}).get("name", closed_book_result.get("packCountry", "Unknown"))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"Zetu Atlas Report — {country} — {date_str}"

    eb = pack.get("economicBaseline", {})
    stale_fields = [k for k, v in (eb.get("indicators") or {}).items() if "STALE" in (v.get("flags") or [])] if eb.get("available") else []
    supported_evidence_count = len(pack.get("signals", [])) + len([o for o in pack.get("opportunities", []) if not o.get("flags")])
    limitations = set()
    for o in pack.get("opportunities", []):
        limitations.update(o.get("mayNotClaim", [])[:2])  # a couple of representative limitations, not a full dump

    lines = [
        "ZETU ATLAS",
        "",
        f"{country} Economic Briefing",
        "",
        closed_book_result["briefing"],
        "",
        "---",
        "",
        "Evidence summary:",
        f"- Intelligence Pack: {country}, assembled {pack.get('assembledAt', 'unknown time')}",
        f"- Supported evidence items: {supported_evidence_count}",
    ]
    if stale_fields:
        lines.append(f"- Stale-data warning: the following indicators are from an earlier year than the pack's most recent data — {', '.join(stale_fields)}")
    if limitations:
        lines.append("- Important limitations:")
        for lim in list(limitations)[:3]:
            lines.append(f"    • {lim}")

    body = "\n".join(lines)
    return subject, body


def get_recipient():
    recipient = os.environ.get("ATLAS_REPORT_EMAIL")
    if not recipient or not recipient.strip():
        raise DeliveryBlocked("RECIPIENT_NOT_CONFIGURED")
    return recipient.strip()


def get_sender_config():
    sender = os.environ.get("EMAIL_FROM")
    password = os.environ.get("EMAIL_PASSWORD")
    if not sender or not password:
        raise DeliveryBlocked("SENDER_CREDENTIALS_NOT_CONFIGURED")

    # An explicit SMTP_HOST (+ optional SMTP_PORT, default 587) always
    # wins -- required for a custom domain (e.g. EMAIL_FROM's real domain
    # here is not one of the well-known consumer providers below), since
    # guessing a wrong host for a real mailbox is worse than failing
    # closed and asking for one explicit value.
    explicit_host = os.environ.get("SMTP_HOST")
    if explicit_host:
        port = int(os.environ.get("SMTP_PORT", "587"))
        return {"sender": sender, "password": password, "host": explicit_host, "port": port}

    domain = sender.split("@")[-1].lower() if "@" in sender else ""
    host_port = KNOWN_SMTP_HOSTS.get(domain)
    if not host_port:
        raise DeliveryBlocked("UNKNOWN_SMTP_PROVIDER_FOR_SENDER_DOMAIN")
    host, port = host_port
    return {"sender": sender, "password": password, "host": host, "port": port}


def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return []
    with open(LEDGER_PATH, "r") as f:
        return json.load(f)


def save_ledger(entries):
    with open(LEDGER_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def find_existing_delivery(ledger, report_id):
    for entry in ledger:
        if entry.get("reportId") == report_id and entry.get("deliveryStatus") in ("SENT", "ACCEPTED"):
            return entry
    return None


def record_delivery(report_id, recipient_ref, status, error_category=None, provider_message_id=None, sent_at=None):
    ledger = load_ledger()
    entry = {
        "reportId": report_id,
        "recipientConfigRef": "ATLAS_REPORT_EMAIL",  # reference to the env var name, never the address itself
        "recipientDomain": recipient_ref.split("@")[-1] if recipient_ref and "@" in recipient_ref else None,
        "deliveryStatus": status,  # BLOCKED | ACCEPTED | SENT | FAILED
        "errorCategory": error_category,
        "providerMessageId": provider_message_id,
        "attemptedAt": datetime.now(timezone.utc).isoformat(),
        "sentAt": sent_at,
    }
    ledger.append(entry)
    save_ledger(ledger)
    return entry


def send_email_via_smtp(subject, body, recipient, sender_config, dry_run=True):
    """dry_run=True (default) never opens a real network connection --
    used by every safety test. Only the one controlled real test in this
    task sets dry_run=False."""
    if dry_run:
        return {"accepted": True, "providerMessageId": None, "dryRun": True}

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender_config["sender"]
    msg["To"] = recipient

    context = ssl.create_default_context()
    with smtplib.SMTP(sender_config["host"], sender_config["port"]) as server:
        server.starttls(context=context)
        server.login(sender_config["sender"], sender_config["password"])
        result = server.sendmail(sender_config["sender"], [recipient], msg.as_string())
        # smtplib.sendmail returns {} on full success (all recipients accepted);
        # a non-empty dict means SOME recipients were rejected by the server.
        accepted = result == {}
        return {"accepted": accepted, "providerMessageId": None, "dryRun": False, "smtpRefusals": result}


def deliver_report(closed_book_result, pack, dry_run=True, force_resend=False):
    """Full pipeline: validate -> dedupe -> send -> record. Returns the
    ledger entry. Raises DeliveryBlocked (never sends) if any gate fails."""
    validate_report_for_delivery(closed_book_result)
    report_id = compute_report_id(closed_book_result)

    ledger = load_ledger()
    if not force_resend:
        existing = find_existing_delivery(ledger, report_id)
        if existing:
            raise DeliveryBlocked("DUPLICATE_SEND_BLOCKED")

    recipient = get_recipient()
    sender_config = get_sender_config()
    subject, body = build_report_email(closed_book_result, pack)

    try:
        result = send_email_via_smtp(subject, body, recipient, sender_config, dry_run=dry_run)
    except Exception as e:
        record_delivery(report_id, recipient, "FAILED", error_category=type(e).__name__)
        raise

    status = "SENT" if (result["accepted"] and not dry_run) else ("ACCEPTED" if result["accepted"] else "FAILED")
    entry = record_delivery(
        report_id, recipient, status,
        provider_message_id=result.get("providerMessageId"),
        sent_at=datetime.now(timezone.utc).isoformat() if status == "SENT" else None,
    )
    return entry
