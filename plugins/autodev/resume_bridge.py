from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ResumeBridgeError(RuntimeError):
    """Raised when the Hermes AutoDev resume bridge cannot safely operate."""


@dataclass(frozen=True)
class ResumeBridgeConfig:
    control_repository: Path
    participant_id: str
    hermes_session: str
    autodev_executable: str = "autodev"
    hermes_executable: str = "hermes"
    remote: str = "origin"
    control_ref: str = "refs/heads/autodev/control"
    poll_seconds: float = 30.0
    min_retry_seconds: float = 60.0
    max_retry_seconds: float = 900.0
    hermes_timeout_seconds: float = 3600.0

    @classmethod
    def from_env(cls) -> "ResumeBridgeConfig":
        repository = os.environ.get("AUTODEV_CONTROL_REPOSITORY", "").strip()
        participant = os.environ.get("AUTODEV_PARTICIPANT_ID", "").strip()
        session = os.environ.get("AUTODEV_HERMES_SESSION", "").strip()
        if not repository:
            raise ResumeBridgeError("AUTODEV_CONTROL_REPOSITORY is required")
        if not participant:
            raise ResumeBridgeError("AUTODEV_PARTICIPANT_ID is required")
        if not session:
            raise ResumeBridgeError("AUTODEV_HERMES_SESSION is required")
        return cls(
            control_repository=Path(repository).expanduser(),
            participant_id=participant,
            hermes_session=session,
            autodev_executable=os.environ.get(
                "AUTODEV_EXECUTABLE", "autodev"
            ).strip()
            or "autodev",
            hermes_executable=os.environ.get(
                "AUTODEV_HERMES_EXECUTABLE", "hermes"
            ).strip()
            or "hermes",
            remote=os.environ.get("AUTODEV_CONTROL_REMOTE", "origin").strip()
            or "origin",
            control_ref=os.environ.get(
                "AUTODEV_CONTROL_REF", "refs/heads/autodev/control"
            ).strip()
            or "refs/heads/autodev/control",
            poll_seconds=float(
                os.environ.get("AUTODEV_RESUME_POLL_SECONDS", "30")
            ),
            min_retry_seconds=float(
                os.environ.get("AUTODEV_RESUME_MIN_RETRY_SECONDS", "60")
            ),
            max_retry_seconds=float(
                os.environ.get("AUTODEV_RESUME_MAX_RETRY_SECONDS", "900")
            ),
            hermes_timeout_seconds=float(
                os.environ.get("AUTODEV_HERMES_TIMEOUT_SECONDS", "3600")
            ),
        )


@dataclass
class ResumeBridgeState:
    last_ref_sha: str | None = None
    last_attempt_ids: tuple[str, ...] = field(default_factory=tuple)
    failures: int = 0
    retry_not_before: float = 0.0


class ResumeBridge:
    """Cheap Git-ref watcher that resumes Hermes only for actionable mailbox work."""

    def __init__(
        self,
        config: ResumeBridgeConfig,
        *,
        run_command: Callable[
            ..., subprocess.CompletedProcess[str]
        ] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.run_command = run_command
        self.monotonic = monotonic
        self.sleep = sleep
        self.state = ResumeBridgeState()
        self._validate_config()

    def _validate_config(self) -> None:
        repo = self.config.control_repository
        if not repo.is_dir() or not (repo / ".git").exists():
            raise ResumeBridgeError(
                f"control repository is not a Git checkout: {repo}"
            )
        for executable in (
            self.config.autodev_executable,
            self.config.hermes_executable,
        ):
            if os.path.sep in executable or (
                os.path.altsep and os.path.altsep in executable
            ):
                path = Path(executable).expanduser()
                if not path.is_file() or not os.access(path, os.X_OK):
                    raise ResumeBridgeError(
                        f"executable is not runnable: {executable}"
                    )
            elif shutil.which(executable) is None:
                raise ResumeBridgeError(
                    f"executable not found: {executable}"
                )
        if self.config.poll_seconds <= 0:
            raise ResumeBridgeError("poll_seconds must be > 0")
        if (
            self.config.min_retry_seconds <= 0
            or self.config.max_retry_seconds
            < self.config.min_retry_seconds
        ):
            raise ResumeBridgeError(
                "invalid retry interval configuration"
            )

    def _run(
        self,
        argv: list[str],
        *,
        timeout: float = 60.0,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_command(
            argv,
            cwd=self.config.control_repository,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )

    def control_ref_sha(self) -> str:
        result = self._run(
            [
                "git",
                "ls-remote",
                self.config.remote,
                self.config.control_ref,
            ],
            timeout=30.0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ResumeBridgeError(
                f"git ls-remote failed: {detail or result.returncode}"
            )
        lines = result.stdout.strip().splitlines()
        if not lines:
            raise ResumeBridgeError(
                f"control ref not found: {self.config.control_ref}"
            )
        return lines[0].split()[0]

    def pending_messages(self) -> list[dict[str, Any]]:
        result = self._run(
            [
                self.config.autodev_executable,
                "remote-inbox",
                "--repository",
                str(self.config.control_repository),
                "--remote",
                self.config.remote,
                "--ref",
                self.config.control_ref,
                "--participant",
                self.config.participant_id,
            ],
            timeout=60.0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ResumeBridgeError(
                f"autodev remote-inbox failed: "
                f"{detail or result.returncode}"
            )
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise ResumeBridgeError(
                f"autodev remote-inbox returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise ResumeBridgeError(
                "autodev remote-inbox did not return a message list"
            )
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def actionable_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        always = {
            "instruction",
            "question",
            "help_request",
            "review_request",
            "cancel",
            "result",
        }
        actionable = []
        for message in messages:
            kind = str(message.get("kind") or "")
            priority = str(message.get("priority") or "normal")
            wake_policy = str(
                message.get("wake_policy") or "if-needed"
            )
            if (
                kind in always
                or priority in {"high", "urgent"}
                or wake_policy == "immediate"
            ):
                actionable.append(message)
        actionable.sort(
            key=lambda m: (
                m.get("created_at") or "",
                m.get("message_id") or "",
            )
        )
        return actionable

    def _resume_prompt(self) -> str:
        return (
            "AutoDev detected durable pending coordination messages for "
            f"your participant {self.config.participant_id}. "
            "Use autodev_inbox now. Handle the relevant pending messages, "
            "acknowledge each message you actually consume with "
            "autodev_ack, and send status/results/replies through AutoDev "
            "as appropriate. Continue the existing task if the new message "
            "unblocks it. Do not ask the user to copy coordination messages."
        )

    def resume_hermes(self) -> subprocess.CompletedProcess[str]:
        fd, path = tempfile.mkstemp(
            prefix="hermes-autodev-resume-",
            suffix=".txt",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self._resume_prompt())
            return self._run(
                [
                    self.config.hermes_executable,
                    "--cli",
                    "chat",
                    "--resume",
                    self.config.hermes_session,
                    "--query-file",
                    path,
                    "--oneshot",
                    "--quiet",
                ],
                timeout=self.config.hermes_timeout_seconds,
            )
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _retry_delay(self) -> float:
        exponent = max(0, self.state.failures - 1)
        return min(
            self.config.max_retry_seconds,
            self.config.min_retry_seconds * (2**exponent),
        )

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        now = self.monotonic()
        ref_sha = self.control_ref_sha()
        retry_waiting = (
            bool(self.state.last_attempt_ids)
            and now < self.state.retry_not_before
        )
        if not force and retry_waiting:
            return {
                "action": "backoff",
                "ref_sha": ref_sha,
                "message_ids": list(self.state.last_attempt_ids),
                "retry_not_before": self.state.retry_not_before,
                "retry_seconds": max(
                    0.0,
                    self.state.retry_not_before - now,
                ),
            }
        retry_due = (
            bool(self.state.last_attempt_ids)
            and now >= self.state.retry_not_before
        )
        if (
            not force
            and ref_sha == self.state.last_ref_sha
            and not retry_due
        ):
            return {
                "action": "no_change",
                "ref_sha": ref_sha,
            }

        messages = self.pending_messages()
        actionable = self.actionable_messages(messages)
        self.state.last_ref_sha = ref_sha
        if not actionable:
            self.state.last_attempt_ids = ()
            self.state.failures = 0
            self.state.retry_not_before = 0.0
            return {
                "action": "idle",
                "ref_sha": ref_sha,
                "pending_count": len(messages),
            }

        ids = tuple(
            sorted(
                str(message.get("message_id") or "")
                for message in actionable
                if message.get("message_id")
            )
        )
        if (
            ids == self.state.last_attempt_ids
            and now < self.state.retry_not_before
        ):
            return {
                "action": "backoff",
                "ref_sha": ref_sha,
                "message_ids": list(ids),
                "retry_not_before": self.state.retry_not_before,
                "retry_seconds": max(
                    0.0,
                    self.state.retry_not_before - now,
                ),
            }

        result = self.resume_hermes()
        if result.returncode != 0:
            self.state.last_attempt_ids = ids
            self.state.failures += 1
            self.state.retry_not_before = now + self._retry_delay()
            return {
                "action": "resume_failed",
                "ref_sha": ref_sha,
                "message_ids": list(ids),
                "returncode": result.returncode,
                "retry_not_before": self.state.retry_not_before,
                "retry_seconds": self._retry_delay(),
                "error": (result.stderr or result.stdout)
                .strip()[-2000:],
            }

        remaining = self.actionable_messages(
            self.pending_messages()
        )
        if remaining:
            remaining_ids = tuple(
                sorted(
                    str(message.get("message_id") or "")
                    for message in remaining
                    if message.get("message_id")
                )
            )
            self.state.last_attempt_ids = remaining_ids
            self.state.failures += 1
            self.state.retry_not_before = (
                now + self._retry_delay()
            )
            return {
                "action": "resumed_pending",
                "ref_sha": ref_sha,
                "message_ids": list(remaining_ids),
                "retry_not_before": self.state.retry_not_before,
                "retry_seconds": self._retry_delay(),
            }

        self.state.last_attempt_ids = ()
        self.state.failures = 0
        self.state.retry_not_before = 0.0
        return {
            "action": "resumed",
            "ref_sha": ref_sha,
            "message_ids": list(ids),
        }

    def run_forever(self) -> None:
        force = True
        while True:
            try:
                event = self.tick(force=force)
                print(
                    json.dumps(event, sort_keys=True),
                    flush=True,
                )
                force = False
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "action": "bridge_error",
                            "error": (
                                f"{type(exc).__name__}: {exc}"
                            ),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            self.sleep(self.config.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes-autodev-resume"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    bridge = ResumeBridge(ResumeBridgeConfig.from_env())
    if args.once:
        print(
            json.dumps(
                bridge.tick(force=True),
                sort_keys=True,
            )
        )
        return 0
    bridge.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
