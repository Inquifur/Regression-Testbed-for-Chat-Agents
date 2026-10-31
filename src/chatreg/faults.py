"""测试专用缺陷，绝不修改磁盘上的 Bot 源文件。"""
from .adapters.demo import DemoBotAdapter


class InclusiveCooldownDemo(DemoBotAdapter):
    def __init__(self):
        super().__init__()
        self.evidence = []

    def _in_cooldown(self, now_ms, last_sent_ms, cooldown_ms):
        self.evidence.append({"now_ms": now_ms, "last_sent_ms": last_sent_ms,
                              "cooldown_ms": cooldown_ms, "boundary": now_ms - last_sent_ms == cooldown_ms})
        return now_ms - last_sent_ms <= cooldown_ms

    async def snapshot(self):
        return {**await super().snapshot(), "fault_injection": {
            "location": "chatreg.adapters.demo.DemoBotAdapter._in_cooldown",
            "description": "测试专用：< 改为 <=，多拦截恰好一个边界点",
            "calls": len(self.evidence), "observations": list(self.evidence)}}


def install_qqagent_fault(limiter, patches, evidence):
    """仅替换场景内真实 RateLimiter 实例正在被应用调用的 check 方法。"""
    from unittest.mock import patch
    from qq_agent.rate_limit import LimitResult

    original = limiter.check

    def faulty_check(group_id, *, direct, contextual=False, trace=None, check_phase="precheck"):
        result = original(group_id, direct=direct, contextual=contextual, trace=trace, check_phase=check_phase)
        state = limiter._group(group_id)
        now = limiter._clock()
        lasts = [t for t in (state.last_attempt, state.last_sent) if t is not None]
        boundary = any(now - last == limiter.config.reply_cooldown_seconds for last in lasts)
        injected = result.allowed and boundary
        evidence.append({"now_ms": round(now * 1000), "last_times_ms": [round(t * 1000) for t in lasts],
                         "cooldown_ms": limiter.config.reply_cooldown_seconds * 1000,
                         "group_id": group_id, "phase": check_phase, "boundary": boundary, "injected": injected})
        if injected:
            if trace is not None:
                trace.checks["reply_cooldown"] = "blocked"
            return LimitResult(False, "reply_cooldown")
        return result

    patches.enter_context(patch.object(limiter, "check", faulty_check))
