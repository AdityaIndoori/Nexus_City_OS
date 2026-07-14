"""
Nexus City OS — NTCIP Read Bridge (prototype).

Prototype NTCIP 1202 READ-ONLY controller bridge for a joint city-DOT pilot
(ADR-006). Polls signal-controller phase/timing objects over SNMPv2c GET and
compares the observed values against the expected SignalTimingPlan —
"verification before actuation". Honest limits: MIB object layouts are
vendor-specific (Econolite/Siemens/McCain/Intelight differ), SNMPv1 is
deprecated in favor of v2c/v3, and polling requires whitelisted access to the
city's controller network. This module has NO write/SET capability of any
kind; `controller_bridge()` in adapters.py still returns None for mutation.

Untrusted-bytes hardening (plan D1 acceptance): BER lengths are rejected when
they exceed the remaining datagram; parse depth is capped (no unbounded
recursion); the recvfrom buffer is fixed at <= 64KB; any parse error yields
None (never raises out); controller addresses come ONLY from adapter/env
configuration (never a request body — no UDP SSRF primitive); the SNMP
request-id AND the response source address are both verified; community
strings never appear in reports or error text.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .models import SignalTimingPlan, now_ts

# -- SNMP / BER constants ----------------------------------------------------

SNMP_VERSION_2C = 1              # RFC 1901 — version field value for v2c
GET_REQUEST_TAG = 0xA0           # RFC 3416 GetRequest-PDU context tag
GET_RESPONSE_TAG = 0xA2          # RFC 3416 Response-PDU context tag
INTEGER_TAG = 0x02               # X.690 BER universal tags
OCTET_STRING_TAG = 0x04
NULL_TAG = 0x05
OID_TAG = 0x06
SEQUENCE_TAG = 0x30
CONSTRUCTED_BIT = 0x20           # X.690 §8.1.2.5 — constructed encoding flag

MAX_DATAGRAM_BYTES = 65535       # fixed recvfrom buffer cap (plan D1 hardening)
MAX_PARSE_DEPTH_LEVELS = 16      # BER nesting cap — no unbounded recursion (D1)
MAX_INTEGER_BYTES = 8            # bound INTEGER bodies (64-bit is plenty)
DEFAULT_SNMP_PORT = 161          # IANA snmp
DEFAULT_TIMEOUT_S = 2.0          # UDP round-trip budget per explicit poll
MATCH_TOLERANCE_S = 0.11         # one NTCIP tenth-of-second quantum + epsilon

# NTCIP 1202 phaseEntry columns (1.3.6.1.4.1.1206 = NEMA enterprise arc).
# NOTE: vendor MIBs vary — Econolite/Siemens/McCain/Intelight all extend or
# remap parts of this table; a real pilot substitutes the vendor's OIDs here.
NTCIP_PHASE_ENTRY_PREFIX = "1.3.6.1.4.1.1206.4.2.1.1.2.1"
PHASE_COL_MIN_GREEN = 4          # phaseMinimumGreen — whole seconds
PHASE_COL_YELLOW_CHANGE = 8      # phaseYellowChange — tenths of seconds
PHASE_COL_RED_CLEAR = 9          # phaseRedClear — tenths of seconds


# -- BER encoding (subset: what an SNMPv2c GET needs) -------------------------

def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _encode_integer(value: int) -> bytes:
    # Minimal two's-complement per X.690 §8.3.
    body = value.to_bytes(value.bit_length() // 8 + 1, "big", signed=True)
    return bytes([INTEGER_TAG]) + _encode_length(len(body)) + body


def _encode_octet_string(value: bytes) -> bytes:
    return bytes([OCTET_STRING_TAG]) + _encode_length(len(value)) + value


def _encode_null() -> bytes:
    return bytes([NULL_TAG, 0x00])


def _encode_oid(oid_str: str) -> bytes:
    arcs = [int(a) for a in oid_str.split(".")]
    if len(arcs) < 2 or any(a < 0 for a in arcs):
        raise ValueError("invalid OID")
    body = bytearray([arcs[0] * 40 + arcs[1]])  # X.690 §8.19 first-two-arcs rule
    for arc in arcs[2:]:
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.insert(0, 0x80 | (arc & 0x7F))
            arc >>= 7
        body += chunk
    return bytes([OID_TAG]) + _encode_length(len(body)) + bytes(body)


def _encode_sequence(body: bytes, tag: int = SEQUENCE_TAG) -> bytes:
    return bytes([tag]) + _encode_length(len(body)) + body


def _encode_message(community: str, pdu_tag: int, request_id: int,
                    varbinds: List[Tuple[str, bytes]]) -> bytes:
    vb_list = b"".join(
        _encode_sequence(_encode_oid(oid) + value_tlv)
        for oid, value_tlv in varbinds)
    pdu_body = (_encode_integer(request_id)
                + _encode_integer(0)          # error-status
                + _encode_integer(0)          # error-index
                + _encode_sequence(vb_list))
    return _encode_sequence(_encode_integer(SNMP_VERSION_2C)
                            + _encode_octet_string(community.encode("utf-8"))
                            + _encode_sequence(pdu_body, tag=pdu_tag))


def build_get_request(community: str, request_id: int,
                      oids: List[str]) -> bytes:
    return _encode_message(community, GET_REQUEST_TAG, request_id,
                           [(oid, _encode_null()) for oid in oids])


# -- BER decoding (hardened against untrusted datagrams) ----------------------

def _decode_tlv(data: bytes, pos: int, end: int) -> Tuple[int, int, int]:
    # Returns (tag, content_start, content_end); ValueError on malformation.
    if pos + 2 > end:
        raise ValueError("truncated TLV header")
    tag = data[pos]
    first = data[pos + 1]
    pos += 2
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        if n == 0 or n > 4 or pos + n > end:
            raise ValueError("bad long-form length")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    if pos + length > end:  # reject BER length beyond datagram (D1 hardening)
        raise ValueError("length exceeds remaining bytes")
    return tag, pos, pos + length


def _decode_int(data: bytes, start: int, end: int) -> int:
    if end <= start or end - start > MAX_INTEGER_BYTES:
        raise ValueError("bad INTEGER body")
    return int.from_bytes(data[start:end], "big", signed=True)


def _decode_oid(body: bytes) -> str:
    if not body:
        raise ValueError("empty OID")
    arcs = [body[0] // 40, body[0] % 40]
    val = 0
    continuing = False
    for b in body[1:]:
        val = (val << 7) | (b & 0x7F)
        if val > 0xFFFFFFFF:
            raise ValueError("OID sub-identifier overflow")
        continuing = bool(b & 0x80)
        if not continuing:
            arcs.append(val)
            val = 0
    if continuing:
        raise ValueError("truncated OID sub-identifier")
    return ".".join(str(a) for a in arcs)


def _decode_value(tag: int, data: bytes, start: int, end: int,
                  depth: int) -> Any:
    if depth > MAX_PARSE_DEPTH_LEVELS:
        raise ValueError("BER nesting exceeds depth cap")
    if tag == INTEGER_TAG:
        return _decode_int(data, start, end)
    if tag == OCTET_STRING_TAG:
        return bytes(data[start:end])
    if tag == NULL_TAG:
        if end != start:
            raise ValueError("non-empty NULL")
        return None
    if tag == OID_TAG:
        return _decode_oid(data[start:end])
    if tag & CONSTRUCTED_BIT:
        items: List[Any] = []
        pos = start
        while pos < end:
            t, s, e = _decode_tlv(data, pos, end)
            items.append(_decode_value(t, data, s, e, depth + 1))
            pos = e
        return items
    return None  # unknown primitive (Counter32, noSuchObject, ...) — tolerated


def parse_get_response(datagram: bytes,
                       request_id: int) -> Optional[Dict[str, Any]]:
    # Any malformation → None, never raise (untrusted UDP bytes).
    try:
        if not datagram or len(datagram) > MAX_DATAGRAM_BYTES:
            return None
        tag, s, e = _decode_tlv(datagram, 0, len(datagram))
        if tag != SEQUENCE_TAG or e != len(datagram):
            return None
        tag, vs, ve = _decode_tlv(datagram, s, e)
        if tag != INTEGER_TAG or _decode_int(datagram, vs, ve) != SNMP_VERSION_2C:
            return None
        # Community: type-checked only — the value is never surfaced anywhere.
        tag, cs, ce = _decode_tlv(datagram, ve, e)
        if tag != OCTET_STRING_TAG:
            return None
        tag, ps, pe = _decode_tlv(datagram, ce, e)
        if tag != GET_RESPONSE_TAG or pe != e:
            return None
        tag, rs, rid_end = _decode_tlv(datagram, ps, pe)
        if tag != INTEGER_TAG or _decode_int(datagram, rs, rid_end) != request_id:
            return None  # request-id mismatch — reject (D1 hardening)
        tag, es, ee = _decode_tlv(datagram, rid_end, pe)
        if tag != INTEGER_TAG or _decode_int(datagram, es, ee) != 0:
            return None  # controller reported an error-status
        tag, ixs, ixe = _decode_tlv(datagram, ee, pe)
        if tag != INTEGER_TAG:
            return None
        tag, ls, le = _decode_tlv(datagram, ixe, pe)
        if tag != SEQUENCE_TAG or le != pe:
            return None
        out: Dict[str, Any] = {}
        pos = ls
        while pos < le:
            tag, bs, be = _decode_tlv(datagram, pos, le)
            if tag != SEQUENCE_TAG:
                return None
            otag, os_, oe = _decode_tlv(datagram, bs, be)
            if otag != OID_TAG:
                return None
            vtag, vs2, ve2 = _decode_tlv(datagram, oe, be)
            if ve2 != be:
                return None
            out[_decode_oid(datagram[os_:oe])] = _decode_value(
                vtag, datagram, vs2, ve2, 0)
            pos = be
        return out
    except (ValueError, IndexError, OverflowError):
        return None


# -- SNMPv2c GET over UDP (explicit call only — no background thread) ---------

def _new_request_id() -> int:
    return int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF


def _send_udp(host: str, port: int, payload: bytes,
              timeout_s: float) -> Optional[Tuple[bytes, Tuple[str, int]]]:
    # The socket seam — tests mock this, same pattern as livedata._fetch_json.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_s)
            sock.sendto(payload, (host, port))
            data, addr = sock.recvfrom(MAX_DATAGRAM_BYTES)
            return data, addr
    except OSError:
        return None  # unreachable / timeout / DNS failure — degrade gracefully


def snmp_get(host: str, port: int, community: str, oids: List[str],
             timeout_s: float = DEFAULT_TIMEOUT_S) -> Optional[Dict[str, Any]]:
    if not oids:
        return None
    request_id = _new_request_id()
    result = _send_udp(host, port, build_get_request(
        community, request_id, oids), timeout_s)
    if result is None:
        return None
    data, addr = result
    if addr[0] != host:
        return None  # response source address must equal the target (D1)
    return parse_get_response(data, request_id)


def controller_target_from_env() -> Optional[Tuple[str, int, str]]:
    # Controller addresses come ONLY from deployment env/adapter config —
    # never from any request body (no UDP SSRF primitive; D1 hardening).
    host = os.environ.get("NEXUS_NTCIP_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.environ.get("NEXUS_NTCIP_PORT", "").strip() or
                   DEFAULT_SNMP_PORT)
    except ValueError:
        port = DEFAULT_SNMP_PORT
    community = os.environ.get("NEXUS_NTCIP_COMMUNITY", "public").strip()
    return host, port, community


# -- Observed-vs-expected verification ----------------------------------------

def phase_timing_oids(plan: SignalTimingPlan) -> Dict[str, Tuple[str, float]]:
    # OID -> (field key, divisor to seconds). Plan green is compared against
    # phaseMinimumGreen — the closest widely-available column; a vendor pilot
    # swaps in the vendor's actual split/green OIDs.
    out: Dict[str, Tuple[str, float]] = {}
    for phase in plan.phases:
        pid = phase.phase_id
        base = NTCIP_PHASE_ENTRY_PREFIX
        out[f"{base}.{PHASE_COL_MIN_GREEN}.{pid}"] = (
            f"phase_{pid}_green_seconds", 1.0)
        out[f"{base}.{PHASE_COL_YELLOW_CHANGE}.{pid}"] = (
            f"phase_{pid}_yellow_seconds", 10.0)
        out[f"{base}.{PHASE_COL_RED_CLEAR}.{pid}"] = (
            f"phase_{pid}_red_clearance_seconds", 10.0)
    return out


def observe_timing(host: str, community: str, plan: SignalTimingPlan,
                   port: int = DEFAULT_SNMP_PORT,
                   timeout_s: float = DEFAULT_TIMEOUT_S
                   ) -> Optional[Dict[str, float]]:
    oid_map = phase_timing_oids(plan)
    values = snmp_get(host, port, community, sorted(oid_map), timeout_s)
    if values is None:
        return None
    observed: Dict[str, float] = {}
    for oid, (field_key, divisor) in oid_map.items():
        raw = values.get(oid)
        if isinstance(raw, int):
            observed[field_key] = raw / divisor
    return observed


@dataclass
class ObservedField:
    field_key: str
    expected_seconds: float
    observed_seconds: float
    matches: bool


@dataclass
class ObservationReport:
    intersection_id: str
    plan_id: str
    matches: List[ObservedField] = field(default_factory=list)
    mismatches: List[ObservedField] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    verified: bool = False
    observed_at: float = 0.0


def verify_observed(expected_plan: SignalTimingPlan,
                    observed: Optional[Dict[str, float]]) -> ObservationReport:
    # Observation ONLY — this compares, it never actuates. The report carries
    # no community string and no raw datagram bytes.
    report = ObservationReport(
        intersection_id=expected_plan.intersection_id,
        plan_id=expected_plan.plan_id,
        observed_at=now_ts())
    expected: Dict[str, float] = {}
    for phase in expected_plan.phases:
        pid = phase.phase_id
        expected[f"phase_{pid}_green_seconds"] = phase.green_seconds
        expected[f"phase_{pid}_yellow_seconds"] = phase.yellow_seconds
        expected[f"phase_{pid}_red_clearance_seconds"] = (
            phase.red_clearance_seconds)
    observed = observed or {}
    for field_key in sorted(expected):
        if field_key not in observed:
            report.missing_fields.append(field_key)
            continue
        obs = ObservedField(
            field_key=field_key,
            expected_seconds=expected[field_key],
            observed_seconds=observed[field_key],
            matches=abs(expected[field_key] - observed[field_key])
            <= MATCH_TOLERANCE_S)
        (report.matches if obs.matches else report.mismatches).append(obs)
    report.verified = (bool(report.matches) and not report.mismatches
                       and not report.missing_fields)
    return report
