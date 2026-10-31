"""规则明确的演示夹具，不连接 QQ 或模型服务。"""

from dataclasses import dataclass, field

from .base import CAPABILITIES, OutputSink
from ..clock import VirtualClock
from ..models import BotConfig, Decision, MessageEvent


@dataclass
class _Group:
    context: list[MessageEvent] = field(default_factory=list)
    sent_times: list[int] = field(default_factory=list)
    last_sent_ms: int | None = None


class DemoBotAdapter:
    capabilities = CAPABILITIES

    def __init__(self) -> None:
        self._groups: dict[str, _Group] = {}

    async def start(self, config: BotConfig, clock: VirtualClock, sink: OutputSink) -> None:
        self._config, self._clock, self._sink = config, clock, sink
        self._groups = {}

    async def on_tick(self) -> None:
        now, config = self._clock.now_ms, self._config
        for group in self._groups.values():
            group.context = [m for m in group.context if now - m.at_ms < config.context_ttl_ms]
            group.sent_times = [t for t in group.sent_times if now - t < config.window_ms]

    def _in_cooldown(self, now_ms: int, last_sent_ms: int, cooldown_ms: int) -> bool:
        return now_ms - last_sent_ms < cooldown_ms

    async def handle_message(self, event: MessageEvent) -> Decision:
        config, now = self._config, self._clock.now_ms
        group = self._groups.setdefault(event.group_id, _Group())
        selected = None
        addressed = config.bot_id in event.mentions
        if addressed:
            ids = {m.id for m in group.context}
            if event.reply_to in ids:
                selected = event.reply_to
            elif group.context:
                selected = group.context[-1].id
        group.context.append(event)
        reason = "not_addressed"
        if addressed:
            if group.last_sent_ms is not None and self._in_cooldown(now, group.last_sent_ms, config.cooldown_ms):
                reason = "cooldown"
            elif len(group.sent_times) >= config.max_messages_per_window:
                reason = "rate_limit"
            else:
                self._sink.emit(event.group_id, "测试回复")
                group.last_sent_ms = now
                group.sent_times.append(now)
                reason = "replied"
        return Decision(event.id, event.group_id, reason == "replied", reason, selected)

    async def snapshot(self) -> dict:
        return {"groups": {
            group_id: {
                "context_message_ids": [m.id for m in group.context],
                "last_sent_ms": group.last_sent_ms,
                "sent_at_ms": list(group.sent_times),
            }
            for group_id, group in sorted(self._groups.items())
        }}

    async def close(self) -> None:
        self._groups.clear()
