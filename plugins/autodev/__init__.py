"""AutoDev coordination plugin for Hermes.

Exposes native Hermes tools that talk to the AutoDev CLI rather than
reimplementing the AutoDev Git/mailbox protocol inside Hermes.
"""

from __future__ import annotations

from plugins.autodev.tools import (
    AUTODEV_ACK_SCHEMA,
    AUTODEV_HELP_SCHEMA,
    AUTODEV_IDENTITY_SCHEMA,
    AUTODEV_INBOX_SCHEMA,
    AUTODEV_SEND_SCHEMA,
    _check_autodev_available,
    _handle_autodev_ack,
    _handle_autodev_help,
    _handle_autodev_identity,
    _handle_autodev_inbox,
    _handle_autodev_send,
)

_TOOLS = (
    ("autodev_identity", AUTODEV_IDENTITY_SCHEMA, _handle_autodev_identity, "🔗"),
    ("autodev_inbox", AUTODEV_INBOX_SCHEMA, _handle_autodev_inbox, "📥"),
    ("autodev_send", AUTODEV_SEND_SCHEMA, _handle_autodev_send, "📤"),
    ("autodev_ack", AUTODEV_ACK_SCHEMA, _handle_autodev_ack, "✅"),
    ("autodev_ask_chatgpt", AUTODEV_HELP_SCHEMA, _handle_autodev_help, "🆘"),
)


def register(ctx) -> None:
    """Register AutoDev tools when bundled plugins are loaded."""

    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="autodev",
            schema=schema,
            handler=handler,
            check_fn=_check_autodev_available,
            emoji=emoji,
        )
