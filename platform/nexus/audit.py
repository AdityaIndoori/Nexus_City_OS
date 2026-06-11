"""
Nexus City OS — Tamper-evident audit trail (PRD §11.3).

Append-only, hash-chained log. Each entry embeds the SHA-256 of the previous
entry, so any retroactive modification breaks the chain and is detectable via
``verify_chain()``. No API exists to delete or modify entries — not even for
Admins.

Entry content per PRD §11.3: timestamp, actor identity, AI model version,
action type, target entities, before-state, after-state, data sources
consulted, approval chain, and outcome.

Durability: when constructed with a ``Store``, every entry is written
through to disk and the chain is reloaded (and re-verified) on restart —
a crash can never erase audit history.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .models import MODEL_VERSION, now_ts

if TYPE_CHECKING:   # avoid import cycle at runtime
    from .store import Store

GENESIS_HASH = "0" * 64


class AuditTrail:
    """Append-only, hash-chained audit log (optionally store-backed)."""

    def __init__(self, store: Optional["Store"] = None) -> None:
        self._lock = threading.RLock()
        self._entries: List[Dict[str, Any]] = []
        self._store = store
        if store is not None:
            self._entries = store.load_audit()

    def record(self,
               actor: str,
               action: str,
               targets: Optional[List[str]] = None,
               before_state: Optional[Dict[str, Any]] = None,
               after_state: Optional[Dict[str, Any]] = None,
               data_sources: Optional[List[Dict[str, Any]]] = None,
               approval_chain: Optional[List[str]] = None,
               outcome: str = "ok",
               detail: str = "") -> Dict[str, Any]:
        """Append an entry. Returns the stored entry (with its hash)."""
        with self._lock:
            prev_hash = (self._entries[-1]["entry_hash"]
                         if self._entries else GENESIS_HASH)
            body = {
                "seq": len(self._entries),
                "timestamp": now_ts(),
                "actor": actor,
                "model_version": MODEL_VERSION,
                "action": action,
                "targets": targets or [],
                "before_state": before_state or {},
                "after_state": after_state or {},
                "data_sources": data_sources or [],
                "approval_chain": approval_chain or [],
                "outcome": outcome,
                "detail": detail,
                "prev_hash": prev_hash,
            }
            entry_hash = hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str)
                .encode("utf-8")).hexdigest()
            entry = dict(body)
            entry["entry_hash"] = entry_hash
            self._entries.append(entry)
            if self._store is not None:
                self._store.append_audit(entry)
            return entry

    def entries(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries[-limit:])

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def verify_chain(self) -> bool:
        """Recompute every hash; True iff the chain is intact."""
        with self._lock:
            prev = GENESIS_HASH
            for entry in self._entries:
                body = {k: v for k, v in entry.items() if k != "entry_hash"}
                if body.get("prev_hash") != prev:
                    return False
                recomputed = hashlib.sha256(
                    json.dumps(body, sort_keys=True, default=str)
                    .encode("utf-8")).hexdigest()
                if recomputed != entry["entry_hash"]:
                    return False
                prev = entry["entry_hash"]
            return True

    def export_jsonl(self) -> str:
        """Machine-readable export for legal discovery (PRD §11.3)."""
        with self._lock:
            return "\n".join(json.dumps(e, default=str) for e in self._entries)