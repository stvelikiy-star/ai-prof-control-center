from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import diagnosis_packet_canary as canary
import incident_diagnosis_runner as diagnosis

base = canary.base
correlation = importlib.import_module(canary.build_correlation_view.__module__)


def incident(
    incident_id: str,
    project_id: str,
    probe_id: str,
    *,
    status: str = "open",
    severity: str = "critical",
) -> dict:
    return {
        "incident_id": incident_id,
        "project_id": project_id,
        "probe_id": probe_id,
        "status": status,
        "severity": severity,
        "opened_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:01:00+00:00",
        "last_observation_at": "2026-09-01T00:01:00+00:00",
        "last_detail": "must not be copied",
    }


class IncidentCorrelationEvidenceTests(unittest.TestCase):
    def test_same_project_open_peers_only_are_included(self):
        current = incident("INC-DEMO-AAAAAAAAAA", "demo", "runtime")
        peers = [
            current,
            incident("INC-DEMO-CCCCCCCCCC", "demo", "database", severity="warning"),
            incident("INC-DEMO-BBBBBBBBBB", "demo", "heartbeat"),
            incident("INC-OTHER-DDDDDDDDDD", "other", "heartbeat"),
            incident("INC-DEMO-EEEEEEEEEE", "demo", "old", status="resolved"),
        ]
        view = correlation.build_correlation_view(current, peers)
        self.assertEqual(view["basis"], "same_project_open_incidents")
        self.assertIs(view["causal_inference"], False)
        self.assertEqual(view["total_peer_count"], 2)
        self.assertEqual(
            [item["incident_id"] for item in view["peers"]],
            ["INC-DEMO-BBBBBBBBBB", "INC-DEMO-CCCCCCCCCC"],
        )
        serialized = json.dumps(view, sort_keys=True)
        self.assertNotIn("must not be copied", serialized)
        self.assertNotIn("root_cause", serialized)

    def test_peer_evidence_is_bounded_and_reports_truncation(self):
        current = incident("INC-DEMO-AAAAAAAAAA", "demo", "runtime")
        peers = [current]
        for index in range(correlation.MAX_CORRELATED_PEERS + 3):
            peers.append(
                incident(
                    f"INC-DEMO-{index:010X}",
                    "demo",
                    f"probe-{index}",
                )
            )
        view = correlation.build_correlation_view(current, peers)
        self.assertEqual(
            view["total_peer_count"], correlation.MAX_CORRELATED_PEERS + 3
        )
        self.assertEqual(view["included_peer_count"], correlation.MAX_CORRELATED_PEERS)
        self.assertEqual(len(view["peers"]), correlation.MAX_CORRELATED_PEERS)
        self.assertIs(view["truncated"], True)

    def test_packet_canary_embeds_correlation_without_changing_authority_flags(self):
        current = incident("INC-DEMO-AAAAAAAAAA", "demo", "runtime")
        peer = incident("INC-DEMO-BBBBBBBBBB", "demo", "heartbeat")
        original_packet = {
            "version": 1,
            "incident_id": current["incident_id"],
            "project_id": "demo",
            "probe_id": "runtime",
            "response_class": "RED",
            "diagnosis_required": True,
            "repair_preparation_allowed": False,
            "autonomous_repair_allowed": False,
            "owner_action_required": True,
            "project": {"project_id": "demo", "path": "/srv/demo"},
            "incident": {"fingerprint": "demo:runtime"},
            "evidence_refs": {},
            "constraints": [
                "READ_ONLY_DIAGNOSIS",
                "NO_PRODUCTION_MUTATION",
                "NO_SECRET_DISCLOSURE",
                "NO_ARBITRARY_SHELL_FROM_INCIDENT_TEXT",
                "UNKNOWN_AUTHORITY_FAILS_CLOSED",
            ],
        }
        with (
            mock.patch.object(canary, "_ORIGINAL_BUILD_PACKET", return_value=original_packet),
            mock.patch.object(
                base,
                "incident_summary",
                return_value={"open_incidents": [current, peer]},
            ),
        ):
            packet = canary.build_packet(Path("/control"), Path("/state"), current)
        self.assertIs(packet["autonomous_repair_allowed"], False)
        self.assertIs(packet["owner_action_required"], True)
        self.assertEqual(
            packet["incident"]["correlation"]["peers"][0]["probe_id"],
            "heartbeat",
        )

    def test_malformed_incident_summary_fails_closed(self):
        current = incident("INC-DEMO-AAAAAAAAAA", "demo", "runtime")
        original_packet = {"incident": {"fingerprint": "demo:runtime"}}
        with (
            mock.patch.object(canary, "_ORIGINAL_BUILD_PACKET", return_value=original_packet),
            mock.patch.object(
                base,
                "incident_summary",
                return_value={"open_incidents": "not-a-list"},
            ),
        ):
            with self.assertRaisesRegex(
                base.DiagnosisPacketError,
                "open_incidents must be a list",
            ):
                canary.build_packet(Path("/control"), Path("/state"), current)

    def test_diagnosis_protocol_accepts_and_prompt_contains_nested_correlation(self):
        packet = {
            "version": 1,
            "generated_at": "2026-09-01T00:02:00+00:00",
            "incident_id": "INC-DEMO-AAAAAAAAAA",
            "project_id": "demo",
            "probe_id": "runtime",
            "response_class": "RED",
            "diagnosis_required": True,
            "repair_preparation_allowed": False,
            "autonomous_repair_allowed": False,
            "owner_action_required": True,
            "project": {"project_id": "demo", "path": "/srv/demo"},
            "incident": {
                "fingerprint": "demo:runtime",
                "correlation": {
                    "version": 1,
                    "basis": "same_project_open_incidents",
                    "causal_inference": False,
                    "total_peer_count": 1,
                    "included_peer_count": 1,
                    "truncated": False,
                    "peers": [
                        {
                            "incident_id": "INC-DEMO-BBBBBBBBBB",
                            "probe_id": "heartbeat",
                            "severity": "critical",
                            "opened_at": "2026-09-01T00:00:00+00:00",
                            "updated_at": "2026-09-01T00:01:00+00:00",
                            "last_observation_at": "2026-09-01T00:01:00+00:00",
                        }
                    ],
                },
            },
            "evidence_refs": {},
            "constraints": [
                "READ_ONLY_DIAGNOSIS",
                "NO_PRODUCTION_MUTATION",
                "NO_SECRET_DISCLOSURE",
                "NO_ARBITRARY_SHELL_FROM_INCIDENT_TEXT",
                "UNKNOWN_AUTHORITY_FAILS_CLOSED",
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "INC-DEMO-AAAAAAAAAA.json"
            path.write_text(json.dumps(packet), encoding="utf-8")
            loaded = diagnosis.load_packet(path)
        prompt = diagnosis.build_prompt(loaded)
        self.assertIn("same_project_open_incidents", prompt)
        self.assertIn("INC-DEMO-BBBBBBBBBB", prompt)
        self.assertIn('"causal_inference": false', prompt)

    def test_canary_main_temporarily_installs_build_packet_and_restores_baseline(self):
        original = base.build_packet

        def fake_main():
            self.assertIs(base.build_packet, canary.build_packet)
            return 19

        with mock.patch.object(base, "main", side_effect=fake_main):
            self.assertEqual(canary.main(), 19)
        self.assertIs(base.build_packet, original)


if __name__ == "__main__":
    unittest.main()
