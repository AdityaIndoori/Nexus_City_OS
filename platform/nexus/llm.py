"""
Nexus City OS — Real AI layer (production pillar 3, AIP mirror).

Connects the platform to production LLMs through an OpenAI-compatible
gateway, using only the stdlib. Three use-cases, each mapped to the most
appropriate model:

  PLANNER  → Claude Sonnet 4.5   deep traffic-engineering reasoning for
                                  ActionPlan generation (highest stakes,
                                  quality over latency)
  VISION   → Claude Haiku 4.5    live SDOT camera-frame incident
                                  verification (fast multimodal triage)
  CHAT     → Claude Haiku 4.5    operator natural-language queries
                                  (low latency, grounded context)

ARCHITECTURAL INVARIANT (PRD §4): the LLM is *never trusted*. It can only
propose structured operations that are schema-validated here, and every
plan still passes the independent SafetyGate (MUTCD constraint verifier,
hallucination monitor, provenance check, confidence abstention) before any
operator sees it. On any LLM failure (network, malformed output, timeout)
the platform degrades to the deterministic expert system — availability of
the mission thread never depends on the model.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_llm_config() -> tuple:
    """LLM gateway credentials: env vars first, then a gitignored
    llm_config.json at the repo root ({"base_url": ..., "api_key": ...}).
    Empty values → the platform runs with the deterministic expert system
    only (every LLM call degrades gracefully)."""
    base = os.environ.get("NEXUS_LLM_BASE_URL", "").strip()
    key = os.environ.get("NEXUS_LLM_API_KEY", "").strip()
    if base and key:
        return base, key
    cfg_path = Path(__file__).resolve().parents[2] / "llm_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return (base or str(cfg.get("base_url", ""))).strip(), \
               (key or str(cfg.get("api_key", ""))).strip()
    except (OSError, ValueError):
        return base, key


LLM_BASE_URL, LLM_API_KEY = _load_llm_config()

MODEL_PLANNER = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
MODEL_VISION = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
MODEL_CHAT = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

REQUEST_TIMEOUT_S = 45.0


class LLMUnavailable(Exception):
    pass


class LLMClient:
    """Minimal OpenAI-compatible chat client (stdlib only)."""

    def __init__(self, base_url: str = LLM_BASE_URL,
                 api_key: str = LLM_API_KEY) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0

    def chat(self, model: str, messages: List[Dict[str, Any]],
             max_tokens: int = 1200,
             temperature: float = 0.2) -> str:
        body = json.dumps({"model": model, "messages": messages,
                           "max_tokens": max_tokens,
                           "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(
            self._base + "/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json"})
        with self._lock:
            self.calls += 1
        try:
            with urllib.request.urlopen(req,
                                        timeout=REQUEST_TIMEOUT_S) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.failures += 1
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

    def chat_vision(self, model: str, prompt: str, image_jpeg: bytes,
                    max_tokens: int = 700) -> str:
        b64 = base64.b64encode(image_jpeg).decode("ascii")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]
        return self.chat(model, messages, max_tokens=max_tokens)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of an LLM response (handles fences)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    brace = text.find("{")
    if brace >= 0:
        candidates.append(text[brace:text.rfind("}") + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


PLANNER_SYSTEM = """You are the recommendation model inside Nexus City OS, \
a municipal traffic-management decision-support platform. You propose signal \
timing mitigations for live incidents. You are NOT trusted: an independent \
MUTCD constraint verifier will reject any unsafe output, and a human \
operator must approve every plan. Propose conservative, standard \
traffic-engineering responses.

Respond ONLY with a JSON object:
{
  "operations": [{"intersection_id": "<id from candidates>",
                  "delta_seconds": <float 1..25>}],
  "rationale": "<2-4 sentence operator-facing justification naming real \
streets and the queue-drain logic>",
  "model_certainty": <float 0..100, your own confidence in this mitigation>
}
Rules: use ONLY intersection IDs from the candidate list; delta_seconds is \
the green extension for the through phase; at most 3 operations; if the \
situation looks ambiguous, say so in the rationale and lower certainty."""

VISION_SYSTEM = """You are the visual verification model inside Nexus City \
OS, a municipal traffic operations platform. You triage live traffic-camera \
frames for human operators. Be factual; never invent details not visible.

Respond ONLY with a JSON object:
{
  "assessment": "<2-3 sentences: what is visible — traffic state, lanes \
blocked, vehicles stopped, pedestrians, weather/visibility>",
  "congestion_visible": "none" | "light" | "moderate" | "heavy",
  "incident_visible": true | false,
  "visibility": "good" | "fair" | "poor",
  "confidence_pct": <float 0..100>
}"""

CHAT_SYSTEM = """You are the operator copilot inside Nexus City OS \
(Seattle traffic operations). Answer ONLY from the provided live city \
context; if the context doesn't contain the answer, say so. Be concise \
(max 4 sentences). Never propose executing changes — operators act through \
the approval workflow."""