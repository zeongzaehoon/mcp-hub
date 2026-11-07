"""Outbound notification port for monitors. No channel is registered yet (monitors are read through MCP);
Slack / mail are added by implementing `Notifier` and calling `register()` at startup — monitor and
scheduler code do not change. Notifiers receive the same redacted event that MCP returns and must never
put data of one company into another company's channel (the event carries company_id for routing)."""

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    async def send(self, event: dict[str, Any], target: dict[str, Any]) -> None: ...


_REGISTRY: dict[str, Notifier] = {}


def register(n: Notifier) -> None:
    _REGISTRY[n.name] = n


def available() -> set[str]:
    return set(_REGISTRY)


async def dispatch(event: dict[str, Any], channels: list[dict[str, Any]]) -> list[str]:
    """Best effort: a failing channel is logged and never fails the monitor run. Returns delivered channel names."""
    sent = []
    for ch in channels:
        n = _REGISTRY.get(ch.get("type", ""))
        if n is None:
            continue
        try:
            await n.send(event, ch)
            sent.append(n.name)
        except Exception:  # noqa: BLE001
            log.exception("notifier %s failed for monitor %s", n.name, event.get("monitor_id"))
    return sent
