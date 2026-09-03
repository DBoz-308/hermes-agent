# Hermes AutoDev plugin

This bundled plugin makes a Hermes agent a first-class AutoDev participant without reimplementing the AutoDev protocol inside Hermes.

## Configuration

The plugin is available when these are configured:

```bash
export AUTODEV_CONTROL_REPOSITORY=/srv/my-project-autodev-control
export AUTODEV_PARTICIPANT_ID=hermes.worker-1

# optional
export AUTODEV_CHATGPT_PARTICIPANT=chatgpt.overseer
export AUTODEV_CONTROL_REMOTE=origin
export AUTODEV_CONTROL_REF=refs/heads/autodev/control
export AUTODEV_EXECUTABLE=autodev
```

The repository must be a local Git checkout with credentials capable of reading/writing the configured control ref. The `autodev` executable must provide the remote mailbox commands introduced by AutoDev v0.3.

## Tools

- `autodev_identity` — inspect logical identity/config.
- `autodev_inbox` — read pending instructions/replies.
- `autodev_send` — publish status/result/question/etc.
- `autodev_ack` — mark inbox messages seen/deferred/consumed/rejected.
- `autodev_ask_chatgpt` — atomically publish a `help_request` and ChatGPT wake intent.

## Behavioral rule

`autodev_ask_chatgpt` is for real escalation, not polling. After sending a durable help request, continue independent work where possible. If the answer is required before progress can continue, report/mark the work blocked and allow the external AutoDev bridge to wake/resume the appropriate participant.

The plugin does not schedule ChatGPT itself and does not know about Work internals. It writes protocol intent; AutoDev transport adapters decide how to satisfy it.
