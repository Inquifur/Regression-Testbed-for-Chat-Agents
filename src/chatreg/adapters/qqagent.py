"""调用本地 QQAgent 应用层；固定模型响应、内存发送，完全独立于生产启动。"""

import sys
import json
import random
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ..models import Decision


class FixedClient:
    """测试专用 LLM 替身，不创建 HTTP 客户端，不保存 Prompt。"""

    def __init__(self, adapter):
        self.calls = 0
        self.adapter = adapter

    async def chat(self, messages, **kwargs):
        self.calls += 1
        # 解码真实 ResponseGenerator 最终提供给模型的结构化会话提示。
        # 不读输入事件 reply_to 来“推断”选择，不保存 Prompt/私人文本。
        raw = messages[-1]["content"].rsplit("\nconversation：", 1)
        if len(raw) != 2:
            raise ValueError("模型输入缺少结构化 conversation，接入契约可能已变化")
        hint = json.loads(raw[1])
        ids = hint["related_message_ids"]
        if type(ids) is not list or any(type(mid) is not int for mid in ids):
            raise ValueError("模型输入 related_message_ids 类型非法")
        self.adapter.model_inputs[self.adapter.current_event_id] = {
            "related_message_ids": [self.adapter.reverse_ids[mid] for mid in ids],
            "evidence": hint["evidence"],
        }
        return "离线接入测试回复"


class QQAgentAdapter:
    # 应用没有返回“单一选中消息 ID”，不伪造该能力。
    capabilities = frozenset({"outputs", "decisions", "context_ids", "model_conversation", "conversation_hints"})
    contract_version = "qqagent-offline/2"

    def __init__(self, bot_root: Path, *, test_fault: bool = False):
        self.root = Path(bot_root).resolve()
        self.app = None
        self.groups = {}
        self.users = {}
        self.message_ids = {}
        self.reverse_ids = {}
        self.messages = {}
        self.model_inputs = {}
        self.conversation_hints = {}
        self.test_fault = test_fault
        self.fault_observations = []
        self.patches = ExitStack()

    async def start(self, config, clock, sink):
        if not (self.root / "qq_agent" / "application.py").is_file():
            raise ValueError("--bot-root 必须指向包含 qq_agent/application.py 的源码目录")
        existing = sys.modules.get("qq_agent")
        if existing and Path(existing.__file__).resolve().parent.parent != self.root:
            raise ValueError("进程已加载另一份 qq_agent，请在独立进程中运行")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        # 不导入 main 或 plugins.group_receiver；二者会创建生产客户端/服务。
        import qq_agent.application as application
        from qq_agent.context import ContextBuffer, ConversationConfig
        from qq_agent.rate_limit import RateLimitConfig, RateLimiter
        from qq_agent.response import ResponseGenerator
        from qq_agent.persona import load_persona
        from qq_agent.speak_policy import SpeakPolicy
        from qq_agent.personality import PersonalityController

        if config.cooldown_ms < 1000 or config.cooldown_ms % 1000:
            raise ValueError("QQAgent 冷却配置仅支持正整数秒")
        if config.window_ms != 60000:
            raise ValueError("QQAgent 生成预算窗口固定为 60000 毫秒")
        if config.context_ttl_ms % 1000:
            raise ValueError("QQAgent 会话线索时效仅支持整数秒")
        self.clock, self.sink, self.config = clock, sink, config
        self.bot_id = 900000
        self.client = FixedClient(self)
        self.application_module = application
        seconds = lambda: clock.now_ms / 1000
        self.time_proxy = SimpleNamespace(time=lambda: 1700000000 + seconds(), monotonic=seconds)
        limits = RateLimitConfig(reply_cooldown_seconds=config.cooldown_ms // 1000,
                                 reply_max_per_minute=config.max_messages_per_window,
                                 interjection_cooldown_seconds=max(15, config.cooldown_ms // 1000))
        persona = load_persona(self.root / "persona.yaml")
        personality = PersonalityController(persona.personality, clock=seconds) if persona.personality else None
        # 使用真实生成器的文字路径和真实策略的无模型路径，模型端固定。
        self.app = application.ChatApplication(
            ContextBuffer(conversation_config=ConversationConfig(timeout_seconds=config.context_ttl_ms // 1000)),
            ResponseGenerator(self.client, persona, personality=personality), bot_name=persona.name, bot_aliases=persona.aliases,
            rate_limiter=RateLimiter(limits, clock=seconds),
            policy=SpeakPolicy(name=persona.name, aliases=persona.aliases, limits=limits, clock=seconds,
                               contextual_enabled=False, rng=random.Random(0)),
            clock=seconds,
        )
        self.effective_config = {
            "rate_limits": asdict(self.app.rate_limiter.config),
            "conversation": asdict(self.app.context.conversation_config),
            "context_capacity": self.app.context.capacity,
            "social": asdict(self.app.social.config),
            "participation": asdict(self.app.policy.participation),
            "personality": asdict(persona.personality) if persona.personality else None,
            "policy_model_enabled": False, "contextual_enabled": False,
            "memory_enabled": False, "external_tools_enabled": False,
            "model_stub": "FixedClient/v2", "sender_stub": "MemoryACK/v1", "random_seed": 0,
        }
        original_conversation = self.app.context.conversation

        def observe_conversation(*args, **kwargs):
            hint = original_conversation(*args, **kwargs)
            self.conversation_hints[self.current_event_id] = {
                "evidence": hint.evidence,
                "related_message_ids": [self.reverse_ids[mid] for mid in hint.related_message_ids],
            }
            return hint

        # 只旁路记录真实返回值，原对象继续交给应用/策略，不重算引用或时效。
        self.patches.enter_context(patch.object(self.app.context, "conversation", observe_conversation))
        if self.test_fault:
            from ..faults import install_qqagent_fault
            install_qqagent_fault(self.app.rate_limiter, self.patches, self.fault_observations)

    async def on_tick(self):
        for group in self.groups.values():
            self.app.rate_limiter.snapshot(group)
        # ContextBuffer 是条数缓存，不把 demo 的 TTL 删除规则移植到真实 Bot。

    async def handle_message(self, event):
        from qq_agent.models import ChatMessage
        from llm.trace import EvaluationTrace

        group = self.groups.setdefault(event.group_id, len(self.groups) + 1)
        user = self.users.setdefault(event.user_id, len(self.users) + 1000)
        mid = len(self.message_ids) + 1
        self.message_ids[event.id] = mid
        self.reverse_ids[mid] = event.id
        target = self.messages.get(event.reply_to)
        message = ChatMessage(group, user, "合成测试用户", mid, event.text,
                              1700000000 + self.clock.now_ms // 1000,
                              self.config.bot_id in event.mentions, False,
                              mentioned_user_ids=(self.bot_id,) if self.config.bot_id in event.mentions else (),
                              reply_to_message_id=target.message_id if target else None,
                              reply_to_user_id=target.user_id if target else None)
        self.messages[event.id] = message
        self.current_event_id = event.id
        trace = EvaluationTrace(group, mid, mid, "message", "chatreg-offline")

        async def send(text, *, trace=None):
            if type(text) is not str:
                raise TypeError("离线文字接入不支持图片/表情包发送计划")
            output = self.sink.emit(event.group_id, text)
            receipt_id = 1000000 + len(self.sink.outputs)
            self.reverse_ids[receipt_id] = output.id
            return receipt_id  # 合成确认，绝不调用 OneBot。

        # 仅替换 application 模块的时间引用；退出该消息步骤立即恢复。
        with patch.object(self.application_module, "time", self.time_proxy):
            await self.app.handle(message, self.bot_id, send, trace=trace)
        if trace.outcome not in {"sent", "blocked", "ignored"}:
            raise RuntimeError(f"QQAgent 未正常完成：{trace.outcome}/{trace.reason}")
        return Decision(event.id, event.group_id, trace.outcome == "sent", trace.reason)

    async def snapshot(self):
        result = {"profile": "real_application_fixed_llm_no_network", "model_stub_calls": self.client.calls,
                "model_inputs": self.model_inputs,
                "conversation_hints": self.conversation_hints,
                "groups": {name: {
                    "context_message_ids": [self.reverse_ids[m.message_id] for m in self.app.context.snapshot(group)],
                    "rate_limit": asdict(self.app.rate_limiter.snapshot(group)),
                } for name, group in self.groups.items()}}
        if self.test_fault:
            result["fault_injection"] = {
                "location": "qq_agent.rate_limit.RateLimiter.check (场景实例)",
                "description": "测试专用：在原 check 放行的恰好冷却边界额外返回拒绝，相当于 < 改为 <=",
                "calls": len(self.fault_observations), "observations": list(self.fault_observations)}
        return result

    async def close(self):
        try:
            if self.app is not None:
                await self.app.close_images()
        finally:
            self.patches.close()
