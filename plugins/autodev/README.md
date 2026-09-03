# Hermes AutoDev plugin

This bundled plugin makes a Hermes agent a first-class AutoDev participant without reimplementing the AutoDev protocol inside Hermes.

## Configuration

The plugin is available when these are configured:

```bash
export AUTODEV_CONTROL_REPOSITORY=/srv/my-project-autodev-control
export AUTODEV_PARTICIPANT_ID=hermes.worker-1
export AUTODEV_HERMES_SESSION=<session-id-or-title>

# optional
export AUTODEV_CHATGPT_PARTICIPANT=chatgpt.overseer
export AUTODEV_CONTROL_REMOTE=origin
export AUTODEV_CONTROL_REF=refs/heads/autodev/control
export AUTODEV_EXECUTABLE=autodev
```

The repository must be a local Git checkout with credentials capable of reading/writing the configured control ref. The `autodev` executable must provide the remote mailbox commands introduced by AutoDev v0.3/v0.4.

`AUTODEV_HERMES_SESSION` identifies the existing Hermes session that should resume when a durable AutoDev reply becomes actionable. Hermes accepts a session ID or resolvable title.

## Tools

- `autodev_identity` — inspect logical identity/config.
- `autodev_inbox` — read pending instructions/replies.
- `autodev_send` — publish status/result/question/etc.
- `autodev_ack` — mark inbox messages seen/deferred/consumed/rejected.
- `autodev_ask_chatgpt` — atomically publish a `help_request` and ChatGPT wake intent.

## Behavioral rule

`autodev_ask_chatgpt` is for real escalation, not polling. After sending a durable help request, continue independent work where possible. If the answer is required before progress can continue, report/mark the work blocked and allow the external AutoDev bridge to wake/resume the appropriate participant.

The plugin does not schedule ChatGPT itself and does not know about Work internals. It writes protocol intent; AutoDev transport adapters decide how to satisfy it.


## Cheap resume bridge

The plugin includes a zero-model polling bridge that watches only the exact Git control ref:

```bash
python -m plugins.autodev.resume_bridge
```

For a one-shot diagnostic:

```bash
python -m plugins.autodev.resume_bridge --once
```

The bridge uses `git ls-remote` between changes. It only reads the full AutoDev inbox when the control ref changes or a failed resume retry becomes due.

Routine heartbeat and ordinary status messages do **not** wake Hermes. Instructions, questions, help/review requests, cancellations, results, high/urgent messages, and explicit immediate-wake messages do.

When work is actionable, the bridge resumes the configured session headlessly using Hermes' supported interface:

```text
hermes --cli chat --resume <session> --query-file <private-tempfile> --oneshot --quiet
```

The resume prompt contains no mailbox body. Hermes is instructed to use `autodev_inbox` to read authoritative messages, acknowledge consumed messages with `autodev_ack`, and continue its existing task.

If Hermes exits unsuccessfully, or returns successfully without consuming the actionable message, the bridge applies exponential retry backoff. Defaults:

```bash
AUTODEV_RESUME_POLL_SECONDS=30
AUTODEV_RESUME_MIN_RETRY_SECONDS=60
AUTODEV_RESUME_MAX_RETRY_SECONDS=900
AUTODEV_HERMES_TIMEOUT_SECONDS=3600
```

This bridge deliberately does not ask a model merely to discover whether work exists.
