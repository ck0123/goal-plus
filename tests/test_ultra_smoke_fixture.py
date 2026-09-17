from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest


FIXTURE = Path(__file__).parent / "fixtures" / "ultra_smoke_codex"


class UltraSmokeFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(
            (FIXTURE / "evidence.json").read_text(encoding="utf-8")
        )

    def test_fixture_source_retains_verified_parser_behavior(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "ultra_smoke_duration",
            FIXTURE / "duration.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.parse_duration("1h 30m2s5ms"), 5_402_005)
        self.assertEqual(module.parse_duration("500ms 500MS1s"), 2_000)
        for value in ("", "0s", "1.5s", "1 s", "1\u017f"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.parse_duration(value)

    def test_smoke_evidence_proves_real_worker_and_terminal_goal(self) -> None:
        execution = self.evidence["execution"]
        goal = self.evidence["goal"]

        self.assertEqual(execution["host"], "codex")
        self.assertEqual(execution["reasoning_effort"], "max")
        self.assertEqual(execution["spawn_agent_count"], 1)
        self.assertEqual(execution["subagent_stop_count"], 1)
        self.assertEqual(
            execution["worker_handles"],
            ["/root/duration_edge_review"],
        )
        self.assertRegex(execution["transcript_sha256"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertEqual(goal["status"], "complete")
        self.assertEqual(goal["phase"], "final_audit")
        self.assertIsNone(goal["next_action"])
        self.assertEqual(goal["search_tasks_total"], 0)
        self.assertTrue(all(item["status"] == "accepted" for item in goal["work_items"]))

    def test_result_review_and_pytest_evidence_precede_completion(self) -> None:
        events = self.evidence["event_sequence"]

        def index(work_item_id: str | None, event: str) -> int:
            return next(
                position
                for position, item in enumerate(events)
                if item["work_item_id"] == work_item_id and item["event"] == event
            )

        edge_accepted = index("edge_case_review", "accepted")
        parser_accepted = index("parser_implementation", "accepted")
        integration_dispatched = index("integration_verification", "dispatch")
        integration_accepted = index("integration_verification", "accepted")
        completed = index(None, "status:complete")

        self.assertLess(index("edge_case_review", "dispatch"), edge_accepted)
        self.assertLess(index("parser_implementation", "dispatch"), parser_accepted)
        self.assertLess(integration_dispatched, integration_accepted)
        self.assertLess(integration_accepted, completed)
        self.assertEqual(events[integration_accepted]["pytest_passed"], 31)

        verification = self.evidence["verification"]
        self.assertEqual(verification["baseline"]["failed"], 6)
        self.assertEqual(verification["subagent_focused"]["passed"], 24)
        self.assertEqual(verification["pre_completion"]["passed"], 31)
        self.assertLess(
            verification["pre_completion"]["recorded_at"],
            verification["completed_at"],
        )

    def test_committed_fixture_excludes_raw_runtime_and_transcript(self) -> None:
        provenance = self.evidence["provenance"]
        self.assertFalse(provenance["raw_runtime_state_committed"])
        self.assertFalse(provenance["raw_transcript_committed"])
        self.assertFalse((FIXTURE / ".gp").exists())
        self.assertEqual(list(FIXTURE.glob("*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
