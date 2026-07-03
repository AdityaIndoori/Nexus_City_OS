"""Post-deploy smoke check: boot routes on the local sim server (8899)."""
import sys
import time
import urllib.error
import urllib.request

CHECKS = [("/healthz", 200), ("/landing", 200),
          ("/landing-assets/console.png", 200), ("/", 200),
          ("/landing-assets/notthere.png", 404)]


def main() -> int:
    time.sleep(2)
    ok = True
    lines = []
    for path, expect in CHECKS:
        try:
            r = urllib.request.urlopen("http://127.0.0.1:8899" + path,
                                       timeout=15)
            code, size = r.status, len(r.read())
        except urllib.error.HTTPError as e:
            code, size = e.code, 0
        except Exception as e:  # noqa: BLE001
            code, size = f"ERR {e}", 0
        status = "PASS" if code == expect else "FAIL"
        if code != expect:
            ok = False
        lines.append(f"{status} {path} -> {code} "
                     f"(expect {expect}, {size} bytes)")
    lines.append("ALL OK" if ok else "FAILURES")
    open("smoke-check.txt", "w").write("\n".join(lines))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())