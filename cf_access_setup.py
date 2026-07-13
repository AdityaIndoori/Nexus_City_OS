"""Re-create the Nexus City OS Cloudflare Access application + policies.

Uses the Cloudflare API (token from CF_API_TOKEN env var):
  1. Verify token + locate the account.
  2. Ensure the One-time PIN IdP exists.
  3. Create the self-hosted Access app on nexus.aindoori.com.
  4. Policy 1: Allow admin email instantly.
  5. Policy 2: Allow Everyone WITH approval (approver = admin email).
  6. Print the app AUD tag.

Output: cf-access-result.txt (never prints the token).
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
API = "https://api.cloudflare.com/client/v4"
ADMIN_EMAIL = "indooriaditya@gmail.com"
APP_DOMAIN = "nexus.aindoori.com"
LOG = []

# The provided credential is a Global API Key (legacy) -> X-Auth headers.
AUTH_HEADERS = {"X-Auth-Email": ADMIN_EMAIL, "X-Auth-Key": TOKEN}


def log(msg):
    LOG.append(str(msg))
    print(msg)


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={**AUTH_HEADERS, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def main():
    if not TOKEN:
        log("ERROR: CF_API_TOKEN not set")
        return 1

    # Find account id
    acc = call("GET", "/accounts")
    if not acc.get("success") or not acc.get("result"):
        log("ERROR listing accounts: " + json.dumps(acc.get("errors")))
        return 1
    account_id = acc["result"][0]["id"]
    log(f"account: {acc['result'][0]['name']} ({account_id})")

    base = f"/accounts/{account_id}/access"

    # 2. Identity providers — ensure One-time PIN exists
    idps = call("GET", f"{base}/identity_providers")
    otp_id = None
    for idp in idps.get("result") or []:
        log(f"idp: {idp.get('type')} {idp.get('name')} ({idp.get('id')})")
        if idp.get("type") == "onetimepin":
            otp_id = idp["id"]
    if otp_id is None:
        r = call("POST", f"{base}/identity_providers", {
            "name": "One-time PIN", "type": "onetimepin", "config": {}})
        if r.get("success"):
            otp_id = r["result"]["id"]
            log(f"created One-time PIN idp ({otp_id})")
        else:
            log("ERROR creating OTP idp: " + json.dumps(r.get("errors")))
            return 1

    # 3. Check for an existing app on the domain
    apps = call("GET", f"{base}/apps")
    app = None
    for a in apps.get("result") or []:
        if a.get("domain") == APP_DOMAIN:
            app = a
            log(f"existing app found: {a['name']} aud={a['aud']}")
    if app is None:
        r = call("POST", f"{base}/apps", {
            "name": "Nexus City OS",
            "domain": APP_DOMAIN,
            "type": "self_hosted",
            "session_duration": "24h",
            "allowed_idps": [otp_id],
            "auto_redirect_to_identity": True,
            "app_launcher_visible": False,
        })
        if not r.get("success"):
            log("ERROR creating app: " + json.dumps(r.get("errors")))
            return 1
        app = r["result"]
        log(f"created app: {app['name']} aud={app['aud']}")
    app_id = app["id"]

    # 4+5. Policies (app-scoped)
    pols = call("GET", f"{base}/apps/{app_id}/policies")
    existing = [p.get("name") for p in (pols.get("result") or [])]
    log(f"existing policies: {existing}")

    if "Admins" not in existing:
        r = call("POST", f"{base}/apps/{app_id}/policies", {
            "name": "Admins",
            "decision": "allow",
            "precedence": 1,
            "include": [{"email": {"email": ADMIN_EMAIL}}],
        })
        log("Admins policy: " + ("OK" if r.get("success")
            else json.dumps(r.get("errors"))))

    if "Signups (approval required)" not in existing:
        r = call("POST", f"{base}/apps/{app_id}/policies", {
            "name": "Signups (approval required)",
            "decision": "allow",
            "precedence": 2,
            "include": [{"everyone": {}}],
            "purpose_justification_required": True,
            "purpose_justification_prompt":
                "Tell us who you are and why you want access to the "
                "Nexus City OS console.",
            "approval_required": True,
            "approval_groups": [{
                "email_addresses": [ADMIN_EMAIL],
                "approvals_needed": 1,
            }],
        })
        log("Signups policy: " + ("OK" if r.get("success")
            else json.dumps(r.get("errors"))))

    # Final: re-read app for the AUD
    final = call("GET", f"{base}/apps/{app_id}")
    aud = (final.get("result") or {}).get("aud", "")
    log(f"FINAL AUD: {aud}")
    return 0


if __name__ == "__main__":
    rc = main()
    open("cf-access-result.txt", "w").write("\n".join(LOG))
    sys.exit(rc)