"""Hermes tools for the AutoDev ChatGPT↔agent coordination protocol."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

_ALLOWED_MESSAGE_KINDS = {
    "status",
    "result",
    "question",
    "help_request",
    "review_request",
    "instruction",
    "cancel",
    "heartbeat",
}
_ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
_ALLOWED_WAKE_POLICIES = {"never", "if-needed", "immediate", "fallback"}
_ALLOWED_ACKS = {"seen", "consumed", "rejected", "deferred"}
_ALLOWED_URGENCY = {"low", "normal", "high", "urgent"}


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _configuration() -> dict[str, str]:
    return {
        "executable": _env("AUTODEV_EXECUTABLE", "autodev"),
        "repository": _env("AUTODEV_CONTROL_REPOSITORY"),
        "remote": _env("AUTODEV_CONTROL_REMOTE", "origin"),
        "ref": _env("AUTODEV_CONTROL_REF", "refs/heads/autodev/control"),
        "participant_id": _env("AUTODEV_PARTICIPANT_ID"),
        "chatgpt_participant": _env(
            "AUTODEV_CHATGPT_PARTICIPANT",
            "chatgpt.overseer",
        ),
    }


def _resolved_executable(config: dict[str, str]) -> str | None:
    executable = config["executable"]
    if os.path.sep in executable or (os.path.altsep and os.path.altsep in executable):
        path = Path(executable).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(executable)


def _check_autodev_available() -> bool:
    config = _configuration()
    if not config["repository"] or not config["participant_id"]:
        return False
    repository = Path(config["repository"]).expanduser()
    if not repository.is_dir() or not (repository / ".git").exists():
        return False
    return _resolved_executable(config) is not None


def _common_args(config: dict[str, str]) -> list[str]:
    return [
        "--repository",
        str(Path(config["repository"]).expanduser()),
        "--remote",
        config["remote"],
        "--ref",
        config["ref"],
    ]


def _run_autodev(*args: str, timeout_seconds: float = 30.0) -> Any:
    config = _configuration()
    executable = _resolved_executable(config)
    if executable is None:
        raise RuntimeError(
            "AutoDev executable not found; set AUTODEV_EXECUTABLE or install autodev"
        )
    if not config["repository"]:
        raise RuntimeError("AUTODEV_CONTROL_REPOSITORY is not configured")
    if not config["participant_id"]:
        raise RuntimeError("AUTODEV_PARTICIPANT_ID is not configured")

    result = subprocess.run(
        [executable, *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"AutoDev command failed ({result.returncode}): {detail or 'no output'}"
        )
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"raw_output": output}


def _handle_autodev_identity(args: dict, **kw) -> str:
    del args, kw
    config = _configuration()
    return tool_result(
        {
            "available": _check_autodev_available(),
            "participant_id": config["participant_id"] or None,
            "chatgpt_participant": config["chatgpt_participant"] or None,
            "repository": config["repository"] or None,
            "remote": config["remote"],
            "ref": config["ref"],
        }
    )


def _handle_autodev_inbox(args: dict, **kw) -> str:
    del args, kw
    config = _configuration()
    try:
        payload = _run_autodev(
            "remote-inbox",
            *_common_args(config),
            "--participant",
            config["participant_id"],
        )
        messages = payload if isinstance(payload, list) else []
        return tool_result(
            {
                "participant_id": config["participant_id"],
                "pending_count": len(messages),
                "messages": messages,
            }
        )
    except Exception as exc:
        return tool_error(f"AutoDev inbox failed: {type(exc).__name__}: {exc}")


def _handle_autodev_send(args: dict, **kw) -> str:
    del kw
    config = _configuration()
    target = str(args.get("to_participant") or "").strip()
    kind = str(args.get("kind") or "").strip()
    body = str(args.get("body") or "").strip()
    priority = str(args.get("priority") or "normal").strip()
    wake_policy = str(args.get("wake_policy") or "if-needed").strip()

    if not target:
        return tool_error("to_participant is required")
    if kind not in _ALLOWED_MESSAGE_KINDS:
        return tool_error(f"Unsupported AutoDev message kind: {kind}")
    if not body:
        return tool_error("body is required")
    if priority not in _ALLOWED_PRIORITIES:
        return tool_error("priority must be low, normal, high, or urgent")
    if wake_policy not in _ALLOWED_WAKE_POLICIES:
        return tool_error("invalid wake_policy")

    message_id = str(args.get("message_id") or f"msg-{uuid.uuid4().hex}")
    try:
        payload = _run_autodev(
            "remote-send",
            *_common_args(config),
            "--from-participant",
            config["participant_id"],
            "--to-participant",
            target,
            "--kind",
            kind,
            "--body",
            body,
            "--message-id",
            message_id,
            "--priority",
            priority,
            "--wake-policy",
            wake_policy,
        )
        return tool_result(payload)
    except Exception as exc:
        return tool_error(f"AutoDev send failed: {type(exc).__name__}: {exc}")


def _handle_autodev_ack(args: dict, **kw) -> str:
    del kw
    config = _configuration()
    message_id = str(args.get("message_id") or "").strip()
    disposition = str(args.get("disposition") or "consumed").strip()
    note = str(args.get("note") or "").strip()
    if not message_id:
        return tool_error("message_id is required")
    if disposition not in _ALLOWED_ACKS:
        return tool_error("invalid acknowledgement disposition")

    ack_id = str(args.get("ack_id") or f"ack-{uuid.uuid4().hex}")
    try:
        payload = _run_autodev(
            "remote-ack",
            *_common_args(config),
            "--participant",
            config["participant_id"],
            "--message-id",
            message_id,
            "--ack-id",
            ack_id,
            "--disposition",
            disposition,
            "--note",
            note,
        )
        return tool_result(payload)
    except Exception as exc:
        return tool_error(f"AutoDev acknowledgement failed: {type(exc).__name__}: {exc}")


def _handle_autodev_help(args: dict, **kw) -> str:
    """Ask ChatGPT for help without requiring a human to copy the request."""

    del kw
    config = _configuration()
    body = str(args.get("body") or "").strip()
    urgency = str(args.get("urgency") or "high").strip()
    if not body:
        return tool_error("body is required")
    if urgency not in _ALLOWED_URGENCY:
        return tool_error("urgency must be low, normal, high, or urgent")
    if not config["chatgpt_participant"]:
        return tool_error("AUTODEV_CHATGPT_PARTICIPANT is not configured")

    # Generate stable IDs before invoking AutoDev so any adapter-level retry
    # can replay the exact same logical records.
    message_id = str(args.get("message_id") or f"help-{uuid.uuid4().hex}")
    wake_id = str(args.get("wake_id") or f"wake-{uuid.uuid4().hex}")

    command = [
        "remote-help",
        *_common_args(config),
        "--from-participant",
        config["participant_id"],
        "--chatgpt-participant",
        config["chatgpt_participant"],
        "--body",
        body,
        "--message-id",
        message_id,
        "--wake-id",
        wake_id,
        "--urgency",
        urgency,
    ]
    fallback_at = str(args.get("fallback_at") or "").strip()
    if fallback_at:
        command.extend(["--fallback-at", fallback_at])

    try:
        payload = _run_autodev(*command)
        return tool_result(
            {
                "queued": True,
                "message_id": message_id,
                "wake_id": wake_id,
                "chatgpt_participant": config["chatgpt_participant"],
                "request": payload,
                "guidance": (
                    "The request is durable. Do not repeatedly poll ChatGPT. "
                    "Continue independent work if possible; otherwise mark this work blocked "
                    "and wait for an AutoDev reply."
                ),
            }
        )
    except Exception as exc:
        return tool_error(f"AutoDev ChatGPT help request failed: {type(exc).__name__}: {exc}")


AUTODEV_IDENTITY_SCHEMA = {
    "name": "autodev_identity",
    "description": (
        "Show this Hermes agent's AutoDev participant identity and control transport. "
        "Use when coordination configuration or routing is unclear."
    ),
    "parameters": {"type": "object", "properties": {}},
}

AUTODEV_INBOX_SCHEMA = {
    "name": "autodev_inbox",
    "description": (
        "Read pending AutoDev messages addressed to this Hermes participant. "
        "Messages remain pending until consumed or rejected with autodev_ack."
    ),
    "parameters": {"type": "object", "properties": {}},
}

AUTODEV_SEND_SCHEMA = {
    "name": "autodev_send",
    "description": (
        "Send a durable AutoDev message to another participant. Use for status, results, "
        "questions, review requests, instructions, cancellation, or heartbeat messages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_participant": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": sorted(_ALLOWED_MESSAGE_KINDS),
            },
            "body": {"type": "string"},
            "priority": {
                "type": "string",
                "enum": sorted(_ALLOWED_PRIORITIES),
                "default": "normal",
            },
            "wake_policy": {
                "type": "string",
                "enum": sorted(_ALLOWED_WAKE_POLICIES),
                "default": "if-needed",
            },
            "message_id": {
                "type": "string",
                "description": "Optional stable ID for explicit idempotent replay.",
            },
        },
        "required": ["to_participant", "kind", "body"],
    },
}

AUTODEV_ACK_SCHEMA = {
    "name": "autodev_ack",
    "description": (
        "Record how this Hermes participant handled an AutoDev inbox message. "
        "seen/deferred keep it pending; consumed/rejected are terminal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string"},
            "disposition": {
                "type": "string",
                "enum": sorted(_ALLOWED_ACKS),
                "default": "consumed",
            },
            "note": {"type": "string"},
            "ack_id": {
                "type": "string",
                "description": "Optional stable ID for explicit idempotent replay.",
            },
        },
        "required": ["message_id"],
    },
}

AUTODEV_HELP_SCHEMA = {
    "name": "autodev_ask_chatgpt",
    "description": (
        "Escalate a durable question/blocker to the configured ChatGPT AutoDev participant. "
        "This atomically writes a help_request plus wake intent. Use when ChatGPT reasoning, "
        "review, connected context, or intervention is genuinely required; do not use for "
        "routine status updates or polling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": (
                    "Self-contained request including relevant evidence, what is blocked, "
                    "and the exact decision/help needed."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": sorted(_ALLOWED_URGENCY),
                "default": "high",
            },
            "fallback_at": {
                "type": "string",
                "description": "Optional timezone-aware ISO timestamp for fallback attention.",
            },
            "message_id": {
                "type": "string",
                "description": "Optional stable ID for idempotent retry.",
            },
            "wake_id": {
                "type": "string",
                "description": "Optional stable wake ID for idempotent retry.",
            },
        },
        "required": ["body"],
    },
}
