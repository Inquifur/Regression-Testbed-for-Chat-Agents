# 真实 Bot 接入：后续阶段准备

更新：已为 `D:\QQAgent` 提供独立的首次离线接入，见 [本地接入与实测说明](qqagent-local.md)。下文的协议代码仍是通用占位骨架；内置 CLI 的 demo 注册与该独立接入脚本分开。

本项目当前只内置 `DemoBotAdapter`。它没有连接已有 QQbot，示例通过不代表真实 Bot 通过。本阶段不修改 Bot 仓库，不连接 QQ 或模型服务。

## 需要提供的入口

接入前，应在 Bot 侧确认以下边界：

1. **消息处理入口**：能够直接传入群 ID、用户 ID、消息 ID、文本、mentions、reply_to；不要求 QQ 客户端在线。平台对象的转换放在适配器中。
2. **统一时钟注入**：冷却、限流和上下文过期都通过注入的 `clock.now_ms` 读取时间。真实 `time.time()`、sleep 和独立定时器不能决定测试业务行为。过期工作应能由 `on_tick()` 驱动。
3. **输出拦截**：原发送函数替换为内存 `sink.emit(group_id, text)`，不能真正发送 QQ 消息或访问模型服务。输出成功进入 sink 后再更新发送计数；sink 关联当前事件，适配器不能自带另一事件 ID。
4. **隔离生命周期**：每场景新建 Bot 与状态，启动时不连接网络，`close()` 释放资源。全局单例、缓存、数据库状态与后台任务需要禁用或替换为场景内实例。
5. **按需调试信息**：只有实际可以观测时才声明决策、上下文 ID 或选中消息 ID。快照必须稳定、JSON 可序列化并与可变 Bot 状态分离。

若真实消息处理依赖 LLM，应在离线接入中注入明确的固定响应或可控模拟服务。本阶段不测试模型回答质量，也不把网络调用混入确定性规则测试。

## 协议骨架（占位，尚未接入）

下面只展示需要实现的位置，**不能直接运行，也未注册到 CLI**。`NotImplementedError` 明确标识必须由 Bot 项目实现的入口，不能把这段代码当作已完成的接入。

```python
from copy import deepcopy

from chatreg.adapters.base import OutputSink
from chatreg.clock import VirtualClock
from chatreg.models import BotConfig, Decision, MessageEvent


class ExistingBotAdapter:
    # 起步只声明真实能观察的输出。若能提供决策/上下文，再逐项增加。
    capabilities = frozenset({"outputs"})

    async def start(
        self, config: BotConfig, clock: VirtualClock, sink: OutputSink
    ) -> None:
        self.clock = clock
        self.sink = sink
        self.bot = None
        # 待接入：创建全新的离线 Bot，注入 clock 和发送拦截函数。
        # 发送拦截函数调用 self.sink.emit(group_id, text)。
        raise NotImplementedError("待实现离线 Bot 创建与依赖注入")

    async def on_tick(self) -> None:
        # 待接入：让 Bot 在 self.clock.now_ms 清理过期状态，不主动发送。
        raise NotImplementedError("待实现虚拟时间驱动的状态清理")

    async def handle_message(self, event: MessageEvent) -> Decision | None:
        # 待接入：转换并依次等待处理 event，通过 sink 捕获输出。
        # 未声明 decisions 时可返回 None，相关检查将 unsupported。
        raise NotImplementedError("待实现消息入口与输出拦截")

    async def snapshot(self) -> dict:
        # 没有声明 context_ids 时，不必伪造上下文。
        # 声明后按真实状态返回：
        # {"groups": {"g1": {"context_message_ids": ["m1"]}}}
        return deepcopy({})

    async def close(self) -> None:
        # start 部分失败后也可能调用 close，清理应可安全重复。
        # 待接入：取消场景内任务并关闭 Bot 资源，禁止遗留后台任务。
        self.bot = None
```

`handle_message()` 返回决策时应使用当前 `event.id` 与 `event.group_id`，`should_reply` 为布尔值，`reason` 为稳定字符串，`selected_context_message_id` 是消息 ID 或 `None`。若只声明输出能力，可以只运行 `output_count`；其他检查应报告 `unsupported`，不能伪造 `passed`。

增加能力时，在适配器实例的 `capabilities` 中显式声明 `decisions`、`context_ids` 或 `selected_context_message_id`。不应仅为消除 `unsupported` 而提供假的状态；已声明却提供不合法数据会被视作执行错误。CLI 只使用代码中显式注册的适配器，不执行命令行给出的任意模块路径。后续增加新适配器需要修改注册表，并补充协议和生命周期测试。

## 接入后的最小验证顺序

先用一个 @ 场景确认输出能被收集且没有外部发送，再验证冷却/窗口/TTL 的精确边界、同时间戳顺序、跨群隔离、显式引用和异常清理。最后用完全相同的输入连续运行并比较 JSON 报告，确认没有墙钟时间或遗留状态造成漂移。

其他真实 Bot 接入仍需要源码或可调用消息入口、可替换时钟、可拦截输出和可重置状态。已有 QQAgent 应用层接入结果见上面的本地说明；不代表真实模型或 QQ 平台验收。策略版本比较与真实平台联调仍属于后续阶段。
