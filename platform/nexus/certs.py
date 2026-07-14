"""
Nexus City OS — Certificate / Compliance Engine (ADR-002).

Per-plan HMAC-SHA256-signed safety certificates appended into the existing
hash-chained audit trail: the signature is embedded in the entry body BEFORE
the chain hash is computed, so a certificate is protected twice (its own HMAC
plus the tamper-evident chain). Rules run, ruleset version (file hash of
safety.py), canonical input snapshot hashes, and verdict are all inside the
signed body.

Key management: the certificate signing key is DISTINCT from the auth
signing key (auth.py) and never falls back to it. Resolution order:
``NEXUS_CERT_KEY`` env → Store kv ("cert_signing_key", generated once via
``secrets.token_hex`` and persisted). Retired keys stay in the Store
(verification only) so rotation never invalidates issued certificates.

Three print-optimized HTML report templates (index.html-style string
substitution, no build step): ops after-action, NIST AI RMF
critical-infrastructure conformity summary, investor safety case. Every
template states the HMAC (symmetric) issuer-verifiable limitation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from . import safety as _safety
from .audit import AuditTrail
from .models import ActionPlan, new_id, now_ts
from .safety import SafetyGate
from .store import Store

CERT_ACTION = "safety_certificate"          # audit entry action (ADR-002)
CERT_KEY_KV = "cert_signing_key"            # Store kv — NEVER the auth key
CERT_RETIRED_KV = "cert_signing_key_retired"  # retired keys, verify-only
RULE_IDS = ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
            "H1", "H2", "H3", "H4")         # MUTCD R + hallucination H rules
HMAC_DISCLOSURE = ("HMAC (symmetric) — issuer-verifiable; third-party "
                   "asymmetric signatures on roadmap")


def _canonical(obj: Any) -> str:
    # IDENTICAL serialization to the audit chain hash (audit.py:79-81).
    return json.dumps(obj, sort_keys=True, default=str)


def _snapshot_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _key_bytes(text: str) -> bytes:
    try:
        return bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")


class CertificateEngine:
    """Issues + verifies HMAC-signed safety certificates over the audit trail."""

    def __init__(self, store: Store, audit: AuditTrail) -> None:
        self._lock = threading.RLock()
        self._store = store
        self._audit = audit
        env = os.environ.get("NEXUS_CERT_KEY", "").strip()
        if env:
            self._key = _key_bytes(env)
        else:
            existing = store.get_kv(CERT_KEY_KV)
            if existing:
                self._key = bytes.fromhex(existing)
            else:
                fresh = secrets.token_hex(32)
                store.set_kv(CERT_KEY_KV, fresh)
                self._key = bytes.fromhex(fresh)
        # ruleset_version: sha256 of the safety module's file bytes — the
        # stable seam across the rulepack refactor (never rulepack internals).
        self._ruleset_version = hashlib.sha256(
            Path(_safety.__file__).read_bytes()).hexdigest()

    @staticmethod
    def _key_id(key: bytes) -> str:
        # One-way identifier: names the key without disclosing material.
        return "ck-" + hashlib.sha256(key).hexdigest()[:16]

    # -- key rotation ------------------------------------------------------

    def rotate_key(self) -> None:
        with self._lock:
            retired: List[str] = self._store.get_kv(CERT_RETIRED_KV, [])
            retired.append(self._key.hex())
            self._store.set_kv(CERT_RETIRED_KV, retired)
            fresh = secrets.token_hex(32)
            self._store.set_kv(CERT_KEY_KV, fresh)
            self._key = bytes.fromhex(fresh)

    # -- issuance ----------------------------------------------------------

    def issue(self, plan: ActionPlan, gate: SafetyGate) -> Dict[str, Any]:
        rules_run = self._rules_run(plan, gate)
        with self._lock:
            key, key_id = self._key, self._key_id(self._key)
        body = {
            "cert_id": new_id("CERT"),
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash(),
            "rules_run": rules_run,
            "ruleset_version": self._ruleset_version,
            "input_snapshot_hashes": {
                "plan": _snapshot_hash(plan.to_dict()),
                "provenance": _snapshot_hash({
                    "entities": plan.provenance.entities,
                    "data_sources": plan.provenance.data_sources,
                    "weather": plan.provenance.weather,
                    "rationale": plan.provenance.rationale}),
            },
            "verdict": plan.status.value,
            "issued_at": now_ts(),
            "key_id": key_id,
        }
        signature = hmac.new(key, _canonical(body).encode("utf-8"),
                             hashlib.sha256).hexdigest()
        # A NORMAL audit entry: the HMAC lives inside after_state, so the
        # chain hash covers it — never mutated post-hoc.
        return self._audit.record(
            actor="certificate_engine", action=CERT_ACTION,
            targets=list(plan.targets),
            after_state={"certificate": body, "signature": signature},
            detail=f"certificate {body['cert_id']} for plan {plan.plan_id} "
                   f"(verdict={body['verdict']})")

    @staticmethod
    def _rules_run(plan: ActionPlan,
                   gate: SafetyGate) -> List[Dict[str, Any]]:
        # Re-run the pure checks to capture per-rule detail (the gate's
        # verifier/monitor are read-only; engine.approve already re-verifies
        # the same way).
        violations = (gate.monitor.check(plan).violations
                      + gate.verifier.verify(plan).violations)
        by_rule: Dict[str, List[str]] = {}
        for v in violations:
            by_rule.setdefault(v.rule_id, []).append(f"[{v.rule_id}] {v.message}")
        return [{"rule_id": rid,
                 "passed": rid not in by_rule,
                 "detail": "; ".join(by_rule.get(rid, []))}
                for rid in RULE_IDS]

    # -- verification ------------------------------------------------------

    def verify_certificate(self, cert: Dict[str, Any]) -> bool:
        payload = cert.get("after_state", cert) if isinstance(cert, dict) else {}
        body = payload.get("certificate")
        signature = payload.get("signature")
        if not isinstance(body, dict) or not isinstance(signature, str):
            return False
        message = _canonical(body).encode("utf-8")
        with self._lock:
            keys = [self._key]
        keys += [bytes.fromhex(h)
                 for h in self._store.get_kv(CERT_RETIRED_KV, [])]
        for key in keys:
            expected = hmac.new(key, message, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                return True
        return False

    # -- HTML rendering (evidence.py-style substitution, print CSS) --------

    def render_after_action(self, cert: Dict[str, Any]) -> str:
        return self._render(
            cert, title="Operations After-Action Report",
            subtitle="Per-decision safety certificate — operator record",
            body=_AFTER_ACTION_BODY)

    def render_nist_conformity(self, cert: Dict[str, Any]) -> str:
        return self._render(
            cert, title="NIST AI RMF Conformity Summary",
            subtitle="Critical-infrastructure profile mapping "
                     "(Govern / Map / Measure / Manage)",
            body=_NIST_BODY)

    def render_investor_case(self, cert: Dict[str, Any]) -> str:
        return self._render(
            cert, title="Investor Safety Case",
            subtitle="Verified-decision evidence for diligence",
            body=_INVESTOR_BODY)

    def _render(self, cert: Dict[str, Any], title: str,
                subtitle: str, body: str) -> str:
        payload = cert.get("after_state", cert)
        cb = payload["certificate"]
        rows = "\n".join(
            f"<tr><td>{r['rule_id']}</td>"
            f"<td>{'PASS' if r['passed'] else 'FAIL'}</td>"
            f"<td>{r['detail'] or '—'}</td></tr>"
            for r in cb["rules_run"])
        tokens = {
            "__TITLE__": title,
            "__SUBTITLE__": subtitle,
            "__CERT_ID__": cb["cert_id"],
            "__PLAN_ID__": cb["plan_id"],
            "__PLAN_HASH__": cb["plan_hash"],
            "__VERDICT__": cb["verdict"],
            "__RULESET__": cb["ruleset_version"],
            "__KEY_ID__": cb["key_id"],
            "__ISSUED__": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(cb["issued_at"])),
            "__SIGNATURE__": payload["signature"],
            "__RULE_ROWS__": rows,
            "__HMAC_NOTE__": HMAC_DISCLOSURE,
        }
        html = _PAGE.replace("__BODY__", body)
        for key, value in tokens.items():
            html = html.replace(key, value)
        return html


# ---------------------------------------------------------------------------
# Templates — print-optimized HTML, no operator identities, no key material.
# ---------------------------------------------------------------------------

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — Nexus City OS</title>
<style>
  body { font: 14px/1.5 Georgia, 'Times New Roman', serif; color: #1a1a2e;
         max-width: 800px; margin: 40px auto; padding: 0 24px; }
  h1 { font-size: 26px; margin-bottom: 2px; }
  .sub { color: #555; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; margin: 18px 0; }
  th, td { border: 1px solid #bbb; padding: 6px 10px; text-align: left; }
  th { background: #f0f0f5; }
  .verdict { font-size: 20px; font-weight: bold; }
  .proof { font-family: monospace; font-size: 12px; word-break: break-all; }
  .note { background: #f7f7fa; border-left: 4px solid #4a4a8a;
          padding: 10px 14px; margin: 16px 0; font-size: 13px; }
  footer { margin-top: 32px; font-size: 12px; color: #777;
           border-top: 1px solid #ccc; padding-top: 10px; }
  @media print {
    body { margin: 0; max-width: none; font-size: 12px; }
    h1 { page-break-after: avoid; }
    table { page-break-inside: avoid; }
  }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__ · Nexus City OS · issued __ISSUED__</p>
<table>
<tr><th>Certificate</th><td class="proof">__CERT_ID__</td></tr>
<tr><th>Plan</th><td class="proof">__PLAN_ID__</td></tr>
<tr><th>Plan hash</th><td class="proof">__PLAN_HASH__</td></tr>
<tr><th>Verdict</th><td class="verdict">__VERDICT__</td></tr>
<tr><th>Ruleset version</th><td class="proof">__RULESET__</td></tr>
<tr><th>Signing key id</th><td class="proof">__KEY_ID__</td></tr>
</table>
__BODY__
<h2>Rules run</h2>
<table>
<tr><th>Rule</th><th>Result</th><th>Detail</th></tr>
__RULE_ROWS__
</table>
<p class="proof">HMAC-SHA256 signature: __SIGNATURE__</p>
<div class="note">__HMAC_NOTE__. The certificate is additionally embedded
in the platform's hash-chained, tamper-evident audit trail (PRD &sect;11.3):
altering any field breaks both the HMAC and the chain.</div>
<footer>Contains no operator identities and no key material — the acting
principal appears in the audit trail as a role/user-id reference only.</footer>
</body>
</html>
"""

_AFTER_ACTION_BODY = """<p>This after-action report certifies the exact
safety evaluation the platform performed on the plan above before any
operator saw it. Every MUTCD guardrail (R1&ndash;R7) and hallucination check
(H1&ndash;H4) result is listed below with its detail; the verdict is the
gate's final disposition. The ruleset version pins the exact verifier code
that produced this result.</p>
"""

_NIST_BODY = """<p>Mapping of this certificate's fields onto the
<strong>NIST AI RMF</strong> critical-infrastructure profile functions:</p>
<table>
<tr><th>Function</th><th>Certificate evidence</th></tr>
<tr><td><strong>Govern</strong></td><td>Human-in-the-loop approval is
constant; signing key id __KEY_ID__ and ruleset version pin accountability
for this exact decision.</td></tr>
<tr><td><strong>Map</strong></td><td>Input snapshot hashes bind the decision
to the exact plan and provenance data evaluated.</td></tr>
<tr><td><strong>Measure</strong></td><td>Per-rule pass/fail results
(R1&ndash;R7 MUTCD, H1&ndash;H4 hallucination) with recorded detail.</td></tr>
<tr><td><strong>Manage</strong></td><td>Verdict __VERDICT__ enforced before
operator exposure; refusals and abstentions are first-class outcomes in the
audit chain.</td></tr>
</table>
<p class="note">Roadmap flag: EU AI Act Annex IV technical-documentation
export is a planned output of this same certificate schema (not built).</p>
"""

_INVESTOR_BODY = """<p>Every AI-influenced decision on this platform emits
a signed certificate like this one: the rules that ran, the exact verifier
version, the input data hashes, and the verdict — appended to a
tamper-evident audit chain. No competitor markets a signed per-decision
conformity artifact (ADR-002). This is the verification moat: the platform
can <em>prove</em> what it checked, what it refused, and when it chose to
stay quiet.</p>
"""
