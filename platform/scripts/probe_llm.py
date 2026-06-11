"""Probe the OpenAI-compatible proxy: text + vision capability checks."""
import base64
import json
import sys
import urllib.request

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nexus.llm import LLM_API_KEY, LLM_BASE_URL  # noqa: E402

BASE = LLM_BASE_URL
KEY = LLM_API_KEY


def chat(model, msgs, max_tokens=200):
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({"model": model, "messages": msgs,
                         "max_tokens": max_tokens}).encode(),
        headers={"Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        return r["choices"][0]["message"]["content"][:200]
    except Exception as e:  # noqa: BLE001
        body = ""
        if hasattr(e, "read"):
            try:
                body = e.read().decode()[:150]
            except Exception:  # noqa: BLE001
                pass
        return f"ERR: {e} {body}"


def main():
    print("text models:")
    for m in ["us.anthropic.claude-sonnet-4-5-20250929-v1:0",
              "us.anthropic.claude-haiku-4-5-20251001-v1:0",
              "us.amazon.nova-lite-v1:0"]:
        print(" ", m, "->", chat(m, [{"role": "user",
                                      "content": "Reply with exactly: OK"}]))

    # vision probe: fetch a real SDOT camera frame, send as base64
    print("vision probe:")
    img_req = urllib.request.Request(
        "https://www.seattle.gov/trafficcams/images/2_Battery_NS.jpg",
        headers={"User-Agent": "NexusCityOS/1.0"})
    img = urllib.request.urlopen(img_req, timeout=20).read()
    b64 = base64.b64encode(img).decode()
    vision_msgs = [{"role": "user", "content": [
        {"type": "text",
         "text": "One sentence: what do you see in this traffic camera image?"},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}]
    for m in ["us.anthropic.claude-haiku-4-5-20251001-v1:0",
              "us.amazon.nova-lite-v1:0"]:
        print(" ", m, "->", chat(m, vision_msgs))


if __name__ == "__main__":
    main()