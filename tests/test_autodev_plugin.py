import json
from unittest.mock import patch

from plugins import autodev as plugin
from plugins.autodev import tools as autodev_tools


class _Context:
    def __init__(self):
        self.calls = []

    def register_tool(self, **kwargs):
        self.calls.append(kwargs)


def _configured_env(tmp_path):
    repo = tmp_path / "control"
    (repo / ".git").mkdir(parents=True)
    return {
        "AUTODEV_CONTROL_REPOSITORY": str(repo),
        "AUTODEV_PARTICIPANT_ID": "hermes.worker-1",
        "AUTODEV_CHATGPT_PARTICIPANT": "chatgpt.overseer",
        "AUTODEV_CONTROL_REMOTE": "origin",
        "AUTODEV_CONTROL_REF": "refs/heads/autodev/control",
        "AUTODEV_EXECUTABLE": "autodev",
    }


def test_plugin_registers_five_autodev_tools():
    ctx = _Context()
    plugin.register(ctx)
    assert [call["name"] for call in ctx.calls] == [
        "autodev_identity",
        "autodev_inbox",
        "autodev_send",
        "autodev_ack",
        "autodev_ask_chatgpt",
    ]
    assert {call["toolset"] for call in ctx.calls} == {"autodev"}


def test_availability_requires_control_checkout_and_executable(tmp_path):
    env = _configured_env(tmp_path)
    with patch.dict("os.environ", env, clear=False):
        with patch("plugins.autodev.tools.shutil.which", return_value="/usr/bin/autodev"):
            assert autodev_tools._check_autodev_available() is True

    env["AUTODEV_CONTROL_REPOSITORY"] = str(tmp_path / "missing")
    with patch.dict("os.environ", env, clear=False):
        with patch("plugins.autodev.tools.shutil.which", return_value="/usr/bin/autodev"):
            assert autodev_tools._check_autodev_available() is False


def test_help_tool_uses_durable_help_and_wake_ids(tmp_path):
    env = _configured_env(tmp_path)
    with patch.dict("os.environ", env, clear=False):
        with patch(
            "plugins.autodev.tools._run_autodev",
            return_value={"message": {"message_id": "help-1"}, "wake": {"wake_id": "wake-1"}},
        ) as run:
            result = json.loads(
                autodev_tools._handle_autodev_help(
                    {
                        "body": "Need a decision.",
                        "urgency": "high",
                        "message_id": "help-1",
                        "wake_id": "wake-1",
                    }
                )
            )

    assert result["queued"] is True
    assert result["message_id"] == "help-1"
    assert result["wake_id"] == "wake-1"
    argv = run.call_args.args
    assert argv[0] == "remote-help"
    assert "--from-participant" in argv
    assert "hermes.worker-1" in argv
    assert "--chatgpt-participant" in argv
    assert "chatgpt.overseer" in argv


def test_inbox_reports_pending_messages(tmp_path):
    env = _configured_env(tmp_path)
    with patch.dict("os.environ", env, clear=False):
        with patch(
            "plugins.autodev.tools._run_autodev",
            return_value=[
                {"message_id": "m1", "kind": "instruction"},
                {"message_id": "m2", "kind": "result"},
            ],
        ):
            result = json.loads(autodev_tools._handle_autodev_inbox({}))

    assert result["participant_id"] == "hermes.worker-1"
    assert result["pending_count"] == 2


def test_send_rejects_unknown_message_kind(tmp_path):
    env = _configured_env(tmp_path)
    with patch.dict("os.environ", env, clear=False):
        result = json.loads(
            autodev_tools._handle_autodev_send(
                {
                    "to_participant": "chatgpt.overseer",
                    "kind": "invented",
                    "body": "x",
                }
            )
        )
    assert result["error"] is True


def test_ack_calls_remote_ack_with_explicit_id(tmp_path):
    env = _configured_env(tmp_path)
    with patch.dict("os.environ", env, clear=False):
        with patch(
            "plugins.autodev.tools._run_autodev",
            return_value={"ack_id": "ack-1", "disposition": "consumed"},
        ) as run:
            result = json.loads(
                autodev_tools._handle_autodev_ack(
                    {
                        "message_id": "m1",
                        "disposition": "consumed",
                        "ack_id": "ack-1",
                    }
                )
            )

    assert result["ack_id"] == "ack-1"
    argv = run.call_args.args
    assert argv[0] == "remote-ack"
    assert "ack-1" in argv
