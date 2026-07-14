"""
NTCIP 1202 read-only bridge (ADR-006).

Covers:
  * BER encoder vs a HAND-VERIFIED SNMPv2c GetRequest byte fixture,
    plus a decode round-trip through the response parser.
  * Mocked socket (canned GetResponse) → correct observed-vs-expected
    verdict via verify_observed.
  * Unreachable/timeout socket degrades gracefully (None, no raise).
  * Oversized / malformed datagrams (bad BER length, deep nesting)
    → graceful None, no raise, no infinite loop.
  * Wrong request-id or wrong response source address rejected.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus.adapters import default_timing_plan
from nexus.ntcip import (
    GET_RESPONSE_TAG,
    MAX_DATAGRAM_BYTES,
    _encode_integer,
    _encode_message,
    build_get_request,
    observe_timing,
    parse_get_response,
    phase_timing_oids,
    snmp_get,
    verify_observed,
)

YELLOW_OID_P1 = "1.3.6.1.4.1.1206.4.2.1.1.2.1.8.1"   # phaseYellowChange.1

# HAND-VERIFIED fixture: SNMPv2c GetRequest, community "public",
# request-id 0x1234, one varbind (phaseYellowChange.1, NULL). Derivation:
#   OID body: 1.3→0x2B; 6,1,4,1→06 01 04 01; 1206=9*128+54→89 36;
#             4,2,1,1,2,1,8,1→04 02 01 01 02 01 08 01  (15 bytes → 06 0F ...)
#   varbind  = 30 13 [OID(17) + NULL(05 00)]           (19 bytes content)
#   vb-list  = 30 15 [varbind(21)]
#   PDU body = req-id(02 02 12 34) + err-status(02 01 00)
#              + err-index(02 01 00) + vb-list(23)     (33 = 0x21 bytes)
#   PDU      = A0 21 [...]
#   message  = 30 2E [version(02 01 01) + community(04 06 "public")
#              + PDU(35)]                              (46 = 0x2E bytes)
EXPECTED_GET_REQUEST = bytes.fromhex(
    "302e"
    "020101"
    "04067075626c6963"
    "a021"
    "02021234"
    "020100"
    "020100"
    "3015"
    "3013"
    "060f2b0601040189360402010102010801"
    "0500")


def _canned_response(request_id: int, values):
    return _encode_message("public", GET_RESPONSE_TAG, request_id,
                           [(oid, _encode_integer(v)) for oid, v in values])


class BerEncodingTest(unittest.TestCase):
    def test_get_request_matches_hand_verified_fixture(self):
        got = build_get_request("public", 0x1234, [YELLOW_OID_P1])
        self.assertEqual(got, EXPECTED_GET_REQUEST)

    def test_response_decode_round_trip(self):
        data = _canned_response(0x77, [(YELLOW_OID_P1, 40)])
        out = parse_get_response(data, 0x77)
        self.assertEqual(out, {YELLOW_OID_P1: 40})


class ParserHardeningTest(unittest.TestCase):
    def test_wrong_request_id_rejected(self):
        data = _canned_response(0x77, [(YELLOW_OID_P1, 40)])
        self.assertIsNone(parse_get_response(data, 0x78))

    def test_oversized_datagram_rejected(self):
        self.assertIsNone(parse_get_response(
            b"\x30" * (MAX_DATAGRAM_BYTES + 1), 1))

    def test_ber_length_beyond_datagram_rejected(self):
        # SEQUENCE claiming 200 content bytes in a 3-byte datagram.
        self.assertIsNone(parse_get_response(b"\x30\x81\xc8", 1))

    def test_truncated_and_garbage_datagrams_return_none(self):
        for blob in (b"", b"\x30", b"\x02\x01\x01", os.urandom(64),
                     b"\x30\x84\xff\xff\xff\xff" + b"\x00" * 10):
            self.assertIsNone(parse_get_response(blob, 1))

    def test_deep_nesting_hits_depth_cap_without_recursion_error(self):
        # Varbind value = 20 nested SEQUENCEs (> 16-level cap).
        nested = b"\x05\x00"
        for _ in range(20):
            nested = bytes([0x30, len(nested)]) + nested
        data = _encode_message("public", GET_RESPONSE_TAG, 5,
                               [(YELLOW_OID_P1, nested)])
        self.assertIsNone(parse_get_response(data, 5))


class SnmpGetTest(unittest.TestCase):
    def test_mocked_socket_yields_decoded_values(self):
        data = _canned_response(7, [(YELLOW_OID_P1, 40)])
        with mock.patch("nexus.ntcip._new_request_id", return_value=7), \
                mock.patch("nexus.ntcip._send_udp",
                           return_value=(data, ("10.0.0.5", 161))):
            out = snmp_get("10.0.0.5", 161, "public", [YELLOW_OID_P1])
        self.assertEqual(out, {YELLOW_OID_P1: 40})

    def test_unreachable_socket_returns_none_without_raise(self):
        with mock.patch("nexus.ntcip._send_udp", return_value=None):
            self.assertIsNone(
                snmp_get("10.0.0.5", 161, "public", [YELLOW_OID_P1]))

    def test_wrong_source_address_rejected(self):
        data = _canned_response(7, [(YELLOW_OID_P1, 40)])
        with mock.patch("nexus.ntcip._new_request_id", return_value=7), \
                mock.patch("nexus.ntcip._send_udp",
                           return_value=(data, ("10.9.9.9", 161))):
            self.assertIsNone(
                snmp_get("10.0.0.5", 161, "public", [YELLOW_OID_P1]))


class VerifyObservedTest(unittest.TestCase):
    def test_matching_controller_state_verifies(self):
        plan = default_timing_plan("INT-0001")   # 35.0g / 4.0y / 2.0rc ×2
        oid_map = phase_timing_oids(plan)
        values = []
        for oid, (field_key, divisor) in oid_map.items():
            secs = {"green": 35.0, "yellow": 4.0,
                    "red": 2.0}[field_key.split("_")[2]]
            values.append((oid, int(round(secs * divisor))))
        data = _canned_response(7, values)
        with mock.patch("nexus.ntcip._new_request_id", return_value=7), \
                mock.patch("nexus.ntcip._send_udp",
                           return_value=(data, ("10.0.0.5", 161))):
            observed = observe_timing("10.0.0.5", "public", plan)
        report = verify_observed(plan, observed)
        self.assertTrue(report.verified)
        self.assertEqual(len(report.matches), 6)
        self.assertEqual(report.mismatches, [])
        self.assertEqual(report.missing_fields, [])

    def test_mismatched_yellow_flagged(self):
        plan = default_timing_plan("INT-0001")
        observed = {"phase_1_green_seconds": 35.0,
                    "phase_1_yellow_seconds": 3.0,     # expected 4.0
                    "phase_1_red_clearance_seconds": 2.0,
                    "phase_2_green_seconds": 35.0,
                    "phase_2_yellow_seconds": 4.0,
                    "phase_2_red_clearance_seconds": 2.0}
        report = verify_observed(plan, observed)
        self.assertFalse(report.verified)
        self.assertEqual([m.field_key for m in report.mismatches],
                         ["phase_1_yellow_seconds"])

    def test_unreachable_controller_yields_unverified_report_no_raise(self):
        plan = default_timing_plan("INT-0001")
        with mock.patch("nexus.ntcip._send_udp", return_value=None):
            observed = observe_timing("10.0.0.5", "public", plan)
        self.assertIsNone(observed)
        report = verify_observed(plan, observed)
        self.assertFalse(report.verified)
        self.assertEqual(len(report.missing_fields), 6)

    def test_report_never_contains_community_string(self):
        plan = default_timing_plan("INT-0001")
        report = verify_observed(plan, {"phase_1_green_seconds": 35.0})
        self.assertNotIn("public", repr(report))


if __name__ == "__main__":
    unittest.main()
