#!/usr/bin/env python3
"""
Safety tests for zetu_report_delivery.py. NO real email is ever sent by
this file (send_email_via_smtp defaults to dry_run=True, which never
opens a network connection). Uses an isolated ledger file, never the real
atlas_report_delivery_ledger.json, and never reads/prints any real
credential value.

Usage: python3 test_report_delivery.py
"""
import os
import json
import importlib

TEST_LEDGER_PATH = "test_atlas_report_delivery_ledger.json"
failures = []


def ok(label, fn):
    try:
        fn()
        print(f"  ok {label}")
    except AssertionError as e:
        failures.append(label)
        print(f"  FAIL {label}: {e}")


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)


def fresh_module(env_overrides=None, clear_keys=None):
    """Reloads the delivery module with a controlled environment and an
    isolated ledger path -- so each test starts from a clean, known state
    and never touches the real ledger or real env."""
    if os.path.exists(TEST_LEDGER_PATH):
        os.remove(TEST_LEDGER_PATH)
    saved = dict(os.environ)
    if clear_keys:
        for k in clear_keys:
            os.environ.pop(k, None)
    if env_overrides:
        os.environ.update(env_overrides)
    import zetu_report_delivery as m
    importlib.reload(m)
    m.LEDGER_PATH = TEST_LEDGER_PATH
    os.environ.clear()
    os.environ.update(saved)
    if clear_keys:
        for k in clear_keys:
            os.environ.pop(k, None)
    if env_overrides:
        os.environ.update(env_overrides)
    return m


def raises_blocked(m, report, expected_reason):
    try:
        m.validate_report_for_delivery(report)
        return False
    except m.DeliveryBlocked as e:
        return e.reason == expected_reason


def expect_blocked(fn, expected_reason):
    try:
        fn()
        return False
    except Exception as e:
        return getattr(e, "reason", None) == expected_reason


ACCEPTED_REPORT = {
    "packCountry": "Ghana", "packAssembledAt": "2026-09-08T00:00:00Z",
    "briefing": "Ghana's GDP is $114 billion in 2025.",
    "accepted": True, "unsupportedClaimCount": 0,
}
UNSUPPORTED_REPORT = {**ACCEPTED_REPORT, "accepted": False, "unsupportedClaimCount": 3}
INSUFFICIENT_REPORT = {**ACCEPTED_REPORT, "briefing": "INSUFFICIENT_EVIDENCE", "accepted": True, "unsupportedClaimCount": 0}
EMPTY_REPORT = {**ACCEPTED_REPORT, "briefing": ""}
PACK = {"country": {"name": "Ghana", "code": "GH"}, "assembledAt": "2026-09-08T00:00:00Z", "economicBaseline": {"available": False, "indicators": {}}, "signals": [], "opportunities": []}


def check_duplicate_blocked():
    m = fresh_module({"ATLAS_REPORT_EMAIL": "someone@example.com", "EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "x"})
    first = m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=True)
    assert_(first["deliveryStatus"] == "ACCEPTED", f"first delivery should be ACCEPTED, got {first['deliveryStatus']}")
    try:
        m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=True)
        raise AssertionError("second delivery of the identical report should have been blocked")
    except m.DeliveryBlocked as e:
        assert_(e.reason == "DUPLICATE_SEND_BLOCKED", f"wrong block reason: {e.reason}")


def check_force_resend():
    m = fresh_module({"ATLAS_REPORT_EMAIL": "someone@example.com", "EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "x"})
    m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=True)
    second = m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=True, force_resend=True)
    assert_(second["deliveryStatus"] == "ACCEPTED", "explicit resend should succeed")


def check_provider_failure():
    m = fresh_module({"ATLAS_REPORT_EMAIL": "someone@example.com", "EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "x"})

    def _boom(*a, **k):
        raise ConnectionError("simulated SMTP failure")
    m.send_email_via_smtp = _boom
    try:
        m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=False)
        raise AssertionError("expected the simulated failure to propagate")
    except ConnectionError:
        pass
    ledger = m.load_ledger()
    assert_(len(ledger) == 1 and ledger[0]["deliveryStatus"] == "FAILED", "failure must be recorded as FAILED")
    assert_(ledger[0]["errorCategory"] == "ConnectionError", "error category should be the exception class name, not its message")
    dumped = json.dumps(ledger)
    assert_("simulated SMTP failure" not in dumped, "raw exception message must not be persisted verbatim")


def check_ledger_no_secrets():
    m = fresh_module({"ATLAS_REPORT_EMAIL": "realaddress@example.com", "EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "supersecretpassword123"})
    m.deliver_report(ACCEPTED_REPORT, PACK, dry_run=True)
    dumped = json.dumps(m.load_ledger())
    assert_("realaddress" not in dumped, "the actual recipient address leaked into the ledger")
    assert_("supersecretpassword123" not in dumped, "the sender password leaked into the ledger")


def check_email_no_ids():
    m = fresh_module()
    pack_with_ids = {**PACK, "opportunities": [{"id": "8641e5f261bc734822cf3e62048a0934", "title": "Real Opp", "flags": [], "mayClaim": ["x"], "mayNotClaim": ["y"]}]}
    subject, body = m.build_report_email(ACCEPTED_REPORT, pack_with_ids)
    assert_("8641e5f261bc734822cf3e62048a0934" not in body, "raw opportunity id leaked into the email body")
    assert_("Zetu Atlas Report" in subject and "Ghana" in subject, "subject format wrong")
    assert_(ACCEPTED_REPORT["briefing"] in body, "the approved briefing text must be included verbatim")


print("=== ELIGIBILITY GATES (no email sent by any of these) ===")

ok("A successful, accepted report passes validation", lambda: fresh_module(
    {"ATLAS_REPORT_EMAIL": "test@example.com", "EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "x"},
).validate_report_for_delivery(ACCEPTED_REPORT))

ok("A report with unsupported claims is blocked (never sent)", lambda: assert_(
    raises_blocked(fresh_module(), UNSUPPORTED_REPORT, "UNSUPPORTED_CLAIMS_DETECTED"), "expected DeliveryBlocked"))

ok("An INSUFFICIENT_EVIDENCE report is blocked (never sent)", lambda: assert_(
    raises_blocked(fresh_module(), INSUFFICIENT_REPORT, "INSUFFICIENT_EVIDENCE"), "expected DeliveryBlocked"))

ok("An empty/corrupt report is blocked (never sent)", lambda: assert_(
    raises_blocked(fresh_module(), EMPTY_REPORT, "EMPTY_OR_CORRUPT_REPORT"), "expected DeliveryBlocked"))

print("\n=== RECIPIENT / SENDER CONFIGURATION ===")

ok("Missing ATLAS_REPORT_EMAIL fails safely with a clear reason, never guesses an address", lambda: assert_(
    expect_blocked(fresh_module(clear_keys=["ATLAS_REPORT_EMAIL"]).get_recipient, "RECIPIENT_NOT_CONFIGURED"), "expected RECIPIENT_NOT_CONFIGURED"))

ok("A configured ATLAS_REPORT_EMAIL is read correctly", lambda: assert_(
    fresh_module({"ATLAS_REPORT_EMAIL": "someone@example.com"}).get_recipient() == "someone@example.com", "recipient mismatch"))

ok("Missing EMAIL_FROM/EMAIL_PASSWORD fails safely", lambda: assert_(
    expect_blocked(fresh_module(clear_keys=["EMAIL_FROM", "EMAIL_PASSWORD"]).get_sender_config, "SENDER_CREDENTIALS_NOT_CONFIGURED"), "expected SENDER_CREDENTIALS_NOT_CONFIGURED"))

ok("An unrecognized sender domain fails safely rather than guessing an SMTP host", lambda: assert_(
    expect_blocked(fresh_module({"EMAIL_FROM": "sender@some-unknown-provider.example", "EMAIL_PASSWORD": "x"}).get_sender_config, "UNKNOWN_SMTP_PROVIDER_FOR_SENDER_DOMAIN"), "expected UNKNOWN_SMTP_PROVIDER_FOR_SENDER_DOMAIN"))

ok("A recognized sender domain (gmail.com) resolves to a known SMTP host", lambda: assert_(
    fresh_module({"EMAIL_FROM": "sender@gmail.com", "EMAIL_PASSWORD": "x"}).get_sender_config()["host"] == "smtp.gmail.com", "wrong host"))

ok("An explicit SMTP_HOST always overrides the domain guess -- required for a custom-domain sender like the real EMAIL_FROM here", lambda: assert_(
    fresh_module({"EMAIL_FROM": "sender@a-custom-domain.example", "EMAIL_PASSWORD": "x", "SMTP_HOST": "smtp.example-custom.com", "SMTP_PORT": "465"}).get_sender_config() == {"sender": "sender@a-custom-domain.example", "password": "x", "host": "smtp.example-custom.com", "port": 465}, "explicit SMTP_HOST/SMTP_PORT not honored"))

print("\n=== DUPLICATE SEND PROTECTION ===")

ok("The same accepted report cannot be delivered twice without force_resend", check_duplicate_blocked)
ok("force_resend=True explicitly bypasses duplicate protection (manual resend path)", check_force_resend)

print("\n=== FAILURE RECORDING ===")

ok("A provider-side failure (simulated) is recorded in the ledger with a safe error category, no secrets", check_provider_failure)

print("\n=== NO SECRETS IN LEDGER OR OUTPUT ===")

ok("The ledger never stores the recipient's full address or any credential, only a domain reference", check_ledger_no_secrets)

print("\n=== EMAIL CONTENT ===")

ok("The built email never dumps internal database IDs into the body", check_email_no_ids)

if os.path.exists(TEST_LEDGER_PATH):
    os.remove(TEST_LEDGER_PATH)

print(f"\n{'PASS' if not failures else f'FAIL ({len(failures)})'}")
if failures:
    exit(1)
