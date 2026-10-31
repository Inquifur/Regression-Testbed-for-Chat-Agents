"""场景与可观察行为的数据结构；时间统一使用整数毫秒。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BotConfig:
    bot_id: str
    cooldown_ms: int
    window_ms: int
    max_messages_per_window: int
    context_ttl_ms: int


@dataclass(frozen=True)
class MessageEvent:
    id: str
    type: Literal["message"]
    at_ms: int
    group_id: str
    user_id: str
    text: str
    mentions: tuple[str, ...] = ()
    reply_to: str | None = None


@dataclass(frozen=True)
class AdvanceEvent:
    id: str
    type: Literal["advance"]
    at_ms: int


Event = MessageEvent | AdvanceEvent


@dataclass(frozen=True)
class Check:
    id: str
    after_event: str
    type: str
    group_id: str | None = None
    event_id: str | None = None
    expected: object = None
    field: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    bot_config: BotConfig
    events: tuple[Event, ...]
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Decision:
    event_id: str
    group_id: str
    should_reply: bool
    reason: str
    selected_context_message_id: str | None = None


@dataclass(frozen=True)
class Output:
    id: str
    event_id: str
    group_id: str
    at_ms: int
    text: str


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: Literal["passed", "failed", "unsupported", "not_run"]
    after_event: str
    type: str
    expected: object
    actual: object
    message: str
