# 本地 QQAgent 的首次离线接入

第二阶段更新：当前默认套件为 **11 个场景、52 项检查**，可选集成测试为8项；历史三场景22项仍完整保留。显式基线、故障注入、时间补齐及会话/模型输入观测见 [第二阶段说明](phase2.md) 和 [接入契约 v2](qqagent-test-contract.md)。下文首次接入的22项和4项计数描述的是历史范围，不能代替本轮结果。

接入对象：`D:\QQAgent` 工作区当前代码。只在本测试台新增文件，不修改 Bot 源码、依赖、`.env` 或生产数据。Bot 仓库原有未提交修改全部保留。

## 直接运行

使用 Bot 已安装好依赖的 `.venv-m1`，脚本自动加入测试台源码路径，无需额外安装依赖：

```powershell
Set-Location 'D:\QQAgent 回归测试台\chatreg'
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 tools/run_qqagent.py --bot-root 'D:\QQAgent'
```

报告默认写入 `artifacts/qqagent_report.json`，可用 `--report` 改位置。`--scenarios` 可指定另一个场景目录或 JSON 文件。退出码沿用 0/1/2。该独立脚本不是原 CLI 的动态插件加载功能，`python -m chatreg` 仍只注册 demo。

## 测试实际经过哪些代码

`ChatApplication.handle` → `ContextBuffer` / `SocialStateManager` → `RateLimiter` → `SpeakPolicy` → `ResponseGenerator` → 内存发送确认 → 真实发送计数与上下文更新。

使用真实人格文件组装生成输入，但不保存或输出内部 Prompt。`FixedClient` 返回固定文字“离线接入测试回复”；策略不设置模型客户端，因此只有真实代码能直接判定的 @ / 直接点名路径参与回应，普通消息不会经模型取得发言资格。`Decision.reason` 保留真实 `EvaluationTrace.reason`，例如 `reply_cooldown`、`reply_rate_limit`、`onebot_confirmed`；`should_reply` 表示应用层实际完成合成发送确认。它不等同于单独的策略批准字段。

时间从虚拟毫秒转换为 Bot 的单调秒时钟。消息时间戳使用固定合成纪元；应用模块记录 Bot 消息时的 `time.time()` 在单步内替换，步骤结束（含异常）立即恢复，不修改全局 time 模块或源码。这个局部替换只适用于独立、串行的离线进程，不应嵌入正在运行的生产 Bot。

场景字符串 ID 稳定映射为合成整数账号/消息 ID；发送回执是合成 ACK，并非 QQ 平台确认。上下文快照来自真实缓存，包含用户事件 ID 与合成输出 ID，不提供或伪造单一 `selected_context_message_id`。依赖该能力的检查会 unsupported。

## 配置与边界

- 不读取 `.env` 或运行 main / NoneBot 插件，不启动真实 QQ、HTTP 模型客户端、调度器、图片、搜索、Pixiv、生产记忆或数据库。
- 运行期间封锁 socket 建连与 DNS。Windows asyncio 初始化所需的内部 socketpair 在封锁前建立。
- 本次显式测试配置为冷却 3 秒、每群 6 次生成/60 秒，其他硬限制沿用真实 `RateLimitConfig` 默认值；不是对当前 `.env` 生产配置的验收。
- `cooldown_ms` 要求正整数秒；`window_ms` 必须 60000；`max_messages_per_window` 映射**生成尝试预算**，不是 demo 的成功输出额度。生成失败也可能消耗预算。
- `context_ttl_ms` 映射 `ConversationConfig.timeout_seconds`（1–600 整数秒）。真实 `ContextBuffer` 按容量保留，不在 TTL 边界删除消息。因此不能直接把 demo 的 TTL 示例当成真实 Bot 期望。
- advance 仅触发可观察的限流过期清理，不主动发言。

三个场景共 22 项检查：普通消息留存、@ 回应、2999/3000ms 冷却边界、跨群独立、6 次额度后第 7 次拦截、59999/60000ms 窗口边界、上下文隔离和真实缓存保留语义。

## 接入回归测试

```powershell
Set-Location 'D:\QQAgent 回归测试台\chatreg'
$env:CHATREG_QQAGENT_ROOT = 'D:\QQAgent'
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 -m unittest discover -s tests -p test_qqagent_integration.py -v
Remove-Item Env:\CHATREG_QQAGENT_ROOT
```

四项测试验证场景通过与重复轨迹一致、缺失能力 unsupported、故意错误期望确实失败、异常后的时间恢复与应用清理。未设置变量时，普通 pytest 会跳过这四项可选接入测试，不要求默认开发环境安装 QQAgent 依赖。

这次结果是**真实代码的离线应用层规则测试**，不涵盖 OneBot 接收规范化、群白名单、自身过滤、真实发送、真实模型质量、主动参与、图片、搜索和记忆，也不评价自然群聊效果。后续可逐项增加场景和可控替身，不应把这次通过解释成完整 QQ 群验收。
