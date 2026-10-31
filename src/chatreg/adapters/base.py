"""适配器协议与只能在当前消息步骤内写入的输出收集器。"""

from typing import Protocol

from ..clock import VirtualClock
from ..models import BotConfig, Decision, MessageEvent, Output


CAPABILITIES = frozenset({"decisions", "outputs", "context_ids", "selected_context_message_id"})


class AdapterContractError(ValueError):
    """适配器返回的数据不符合契约。"""


class OutputSink:
    """仅收集内存输出；事件 ID、时间及输出 ID 由运行器决定。"""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock
        self._event: MessageEvent | None = None
        self._outputs: list[Output] = []

    @property
    def outputs(self) -> tuple[Output, ...]:
        return tuple(self._outputs)

    def _begin(self, event: MessageEvent) -> None:
        self._event = event

    def _end(self) -> None:
        self._event = None

    def emit(self, group_id: str, text: str) -> Output:
        event = self._event
        if event is None:
            raise AdapterContractError("sink：只能在 handle_message 内收集输出，tick 不得发消息")
        if type(group_id) is not str or group_id != event.group_id:
            raise AdapterContractError("sink.group_id：输出必须属于当前消息的群")
        if type(text) is not str:
            raise AdapterContractError("sink.text：必须为字符串")
        output = Output(
            id=f"output-{len(self._outputs) + 1:04d}", event_id=event.id,
            group_id=group_id, at_ms=self._clock.now_ms, text=text,
        )
        self._outputs.append(output)
        return output


class BotAdapter(Protocol):
    capabilities: frozenset[str]

    async def start(self, config: BotConfig, clock: VirtualClock, sink: OutputSink) -> None: ...
    async def on_tick(self) -> None: ...
    async def handle_message(self, event: MessageEvent) -> Decision | None: ...
    async def snapshot(self) -> dict: ...
    async def close(self) -> None: ...
