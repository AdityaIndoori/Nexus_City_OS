"""Verify the dockerized stack: local healthz + both public URLs."""
import time
import urllib.error
import urllib.request

CHECKS = [
    ("http://127.0.0.1:8757/healthz", 200),
    ("https://nexuscity.aindoori.com/", 200),
    ("https://nexuscity.aindoori.com/landing-assets/console.png", 200),
]


def code_of(url):
    req = urllib.request.Request(url, method="GET")
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}", 0


def main():
    # Wait for the platform to finish booting (live topology fetch).
    for attempt in range(12):
        code, _ = code_of("http://127.0.0.1:8757/healthz")
        if code == 200:
            break
        time.sleep(10)
    lines = []
    ok = True
    for url, expect in CHECKS:
        code, size = code_of(url)
        status = "PASS" if code == expect else "FAIL"
        if code != expect:
            ok = False
        lines.append(f"{status} {url} -> {code} ({size} bytes)")
    # Console must redirect to Access (302) — urllib follows redirects, so
    # a final 200 at cloudflareaccess.com login is also acceptable; check
    # non-follow with a manual opener.
    import urllib.request as ur

    class NoRedirect(ur.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = ur.build_opener(NoRedirect)
    try:
        r = opener.open("https://nexus.aindoori.com/", timeout=20)
        code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    status = "PASS" if code == 302 else "FAIL"
    if code != 302:
        ok = False
    lines.append(f"{status} https://nexus.aindoori.com/ -> {code} "
                 f"(expect 302 to Access)")
    lines.append("ALL OK" if ok else "FAILURES")
    out = "\n".join(lines)
    open("docker-switch-check.txt", "w").write(out)
    print(out)


if __name__ == "__main__":
    main()