import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.autodev import resume_bridge as rb


class Result:
    def __init__(self, code=0, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "control"
        (self.repo / ".git").mkdir(parents=True)
        self.clock = FakeClock()
        self.config = rb.ResumeBridgeConfig(
            control_repository=self.repo,
            participant_id="hermes.worker-1",
            hermes_session="session-abc",
            autodev_executable="/bin/true",
            hermes_executable="/bin/true",
            poll_seconds=5,
            min_retry_seconds=10,
            max_retry_seconds=40,
            hermes_timeout_seconds=60,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def message(
        message_id,
        kind="instruction",
        *,
        priority="normal",
        wake_policy="if-needed",
        body="secret body",
    ):
        return {
            "message_id": message_id,
            "kind": kind,
            "priority": priority,
            "wake_policy": wake_policy,
            "body": body,
        }

    def make_bridge(
        self,
        *,
        refs,
        inboxes,
        hermes_results=None,
        prompt_capture=None,
    ):
        refs = list(refs)
        inboxes = list(inboxes)
        hermes_results = list(
            hermes_results or [Result(0, "ok")]
        )
        calls = []

        def run(argv, **kwargs):
            calls.append(list(argv))
            if argv[0] == "git":
                sha = refs.pop(0) if refs else "sha-stable"
                return Result(
                    0,
                    f"{sha}\trefs/heads/autodev/control\n",
                )
            if (
                argv[0] == "/bin/true"
                and len(argv) > 1
                and argv[1] == "remote-inbox"
            ):
                payload = inboxes.pop(0) if inboxes else []
                return Result(0, json.dumps(payload))
            if argv[0] == "/bin/true" and "--resume" in argv:
                if prompt_capture is not None:
                    query_index = (
                        argv.index("--query-file") + 1
                    )
                    prompt_capture.append(
                        Path(argv[query_index]).read_text(
                            encoding="utf-8"
                        )
                    )
                return (
                    hermes_results.pop(0)
                    if hermes_results
                    else Result(0, "ok")
                )
            raise AssertionError(
                f"unexpected argv: {argv}"
            )

        bridge = rb.ResumeBridge(
            self.config,
            run_command=run,
            monotonic=self.clock.monotonic,
            sleep=lambda _: None,
        )
        return bridge, calls

    def test_routine_status_does_not_resume_hermes(self):
        bridge, calls = self.make_bridge(
            refs=["sha1"],
            inboxes=[
                [
                    self.message("hb", "heartbeat"),
                    self.message("st", "status"),
                ]
            ],
        )
        result = bridge.tick(force=True)
        self.assertEqual(result["action"], "idle")
        self.assertFalse(
            any("--resume" in call for call in calls)
        )

    def test_actionable_instruction_resumes_without_body_leak(self):
        prompts = []
        secret = "do not expose this exact mailbox body"
        bridge, calls = self.make_bridge(
            refs=["sha1"],
            inboxes=[
                [
                    self.message(
                        "m1",
                        "instruction",
                        body=secret,
                    )
                ],
                [],
            ],
            prompt_capture=prompts,
        )
        result = bridge.tick(force=True)
        self.assertEqual(result["action"], "resumed")
        hermes_calls = [
            call for call in calls if "--resume" in call
        ]
        self.assertEqual(len(hermes_calls), 1)
        argv = hermes_calls[0]
        self.assertIn("--query-file", argv)
        self.assertNotIn(secret, " ".join(argv))
        self.assertNotIn(secret, prompts[0])
        self.assertIn("autodev_inbox", prompts[0])

    def test_same_ref_without_retry_is_no_change(self):
        bridge, calls = self.make_bridge(
            refs=["sha1", "sha1"],
            inboxes=[[]],
        )
        self.assertEqual(
            bridge.tick(force=True)["action"],
            "idle",
        )
        self.assertEqual(
            bridge.tick()["action"],
            "no_change",
        )
        inbox_calls = [
            call
            for call in calls
            if "remote-inbox" in call
        ]
        self.assertEqual(len(inbox_calls), 1)

    def test_failed_resume_backs_off(self):
        bridge, calls = self.make_bridge(
            refs=["sha1", "sha1"],
            inboxes=[[self.message("m1")]],
            hermes_results=[
                Result(1, "", "provider failed")
            ],
        )
        first = bridge.tick(force=True)
        self.assertEqual(
            first["action"],
            "resume_failed",
        )
        self.assertEqual(first["retry_seconds"], 10)
        second = bridge.tick()
        self.assertEqual(second["action"], "backoff")
        hermes_calls = [
            call for call in calls if "--resume" in call
        ]
        self.assertEqual(len(hermes_calls), 1)

    def test_pending_after_resume_retries_then_consumes(self):
        bridge, calls = self.make_bridge(
            refs=["sha1", "sha1", "sha1", "sha1"],
            inboxes=[
                [self.message("m1")],
                [self.message("m1")],
                [self.message("m1")],
                [],
            ],
            hermes_results=[
                Result(0, "ok"),
                Result(0, "ok"),
            ],
        )
        first = bridge.tick(force=True)
        self.assertEqual(
            first["action"],
            "resumed_pending",
        )
        self.assertEqual(first["retry_seconds"], 10)
        self.assertEqual(
            bridge.tick()["action"],
            "backoff",
        )
        self.clock.advance(10)
        self.assertEqual(
            bridge.tick()["action"],
            "resumed",
        )
        hermes_calls = [
            call for call in calls if "--resume" in call
        ]
        self.assertEqual(len(hermes_calls), 2)

    def test_high_priority_status_is_actionable(self):
        bridge, _ = self.make_bridge(
            refs=["sha1"],
            inboxes=[
                [
                    self.message(
                        "st",
                        "status",
                        priority="high",
                    )
                ],
                [],
            ],
        )
        self.assertEqual(
            bridge.tick(force=True)["action"],
            "resumed",
        )

    def test_immediate_status_is_actionable(self):
        bridge, _ = self.make_bridge(
            refs=["sha1"],
            inboxes=[
                [
                    self.message(
                        "st",
                        "status",
                        wake_policy="immediate",
                    )
                ],
                [],
            ],
        )
        self.assertEqual(
            bridge.tick(force=True)["action"],
            "resumed",
        )


class ConfigTests(unittest.TestCase):
    def test_from_env_requires_core_identity(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(rb.ResumeBridgeError):
                rb.ResumeBridgeConfig.from_env()


if __name__ == "__main__":
    unittest.main()
