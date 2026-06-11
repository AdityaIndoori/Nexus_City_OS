"""Nexus City OS launcher.

Usage:
    python platform/run.py                       # REAL Seattle data (default)
    python platform/run.py --city tacoma         # REAL Tacoma / Pierce Transit
    python platform/run.py --sim                 # offline deterministic mode
    python platform/run.py --port 9000           # custom port
    python platform/run.py --host 0.0.0.0        # bind all interfaces (Docker)
    python platform/run.py --no-vision           # disable the AI vision sweep
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nexus.server import build_arg_parser, serve  # noqa: E402

if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    serve(host=args.host, port=args.port, live=not args.sim,
          city=args.city, enable_vision=not args.no_vision)