# QQAgent 离线测试契约 v2

契约标识 `qqagent-offline/2`。依据本轮实际读取的 Bot 工作区源码，不依据第一阶段示例推测规则。源码内容摘要、Git SHA 与 dirty 状态以相应基线的 manifest 为准，本文行号只帮助定位，不替代源码摘要。

## 真实逻辑与替身边界

| 环节 | 实际函数/模块 | 真实逻辑或替身 | 可验证行为 |
| --- | --- | --- | --- |
| 消息转换与入口 | `QQAgentAdapter.handle_message` → `qq_agent.application.ChatApplication.handle`（application.py:162） | 转换为合成 `ChatMessage`，调用真实应用 | 文件步骤顺序、同步入缓存；不包含 `GroupReceiver`/OneBot 规范化、白名单、自身过滤和去重 |
| 发言与限流 | `ChatApplication._respond`（281）、`SpeakPolicy.decide`（speak_policy.py:112）、`RateLimiter.check/reserve/record_sent`（rate_limit.py:142/189/203） | 真实逻辑；明确 `contextual_enabled=False`、策略无模型客户端 | @ 直接回复、真实 reason、冷却/预算/群隔离；普通输入关闭模型参与。不说明生产 Bot 普通消息一定不回复 |
| 缓存与线索 | `ContextBuffer.append/snapshot/conversation`（context.py:76/89/108）、`SocialStateManager` | 真实缓存、会话关联、临时社交状态 | 缓存与会话线索分别观察；话题索引是每次重建的临时推断，不是完整缓存，也非语义智能评测 |
| 回复生成 | `ResponseGenerator.generate`（response.py:187）→ `FixedClient.chat` | 真实 Prompt 组装/文字后处理；模型响应固定 | 捕获实际交给模型替身的结构化 conversation，不记录完整 Prompt，不评价答案质量 |
| 发送与确认 | 应用调用注入的 `send` → `OutputSink.emit` → 合成整数 ACK | 内存替身；确认后的 Bot 状态更新是真实代码 | 固定文字输出、确认后计数/入缓存；日志 confirmed/onebot_confirmed 是当前测试环境的合成确认，不是 QQ 投递成功 |

没有导入 `main`/`plugins.group_receiver`，没有加载 `.env`，没有启动数据库、生产 Memory、Vision、搜索、天气、Pixiv 或调度器。人格文件仅在本地读取，完整内容、账号和 Prompt 不写入测试报告。未修改 Bot 工作区文件。

## 时间与状态

- `VirtualClock` 从 0 非负整数毫秒起步，经 `/1000` 提供同一个单调秒时钟给 `ChatApplication`、`RateLimiter`、`SpeakPolicy`、`SocialStateManager`、`PersonalityController`（含 MoodUpdater）。发现第一阶段生成器默认构建人格控制器时未显式注入时钟，第二阶段已补齐。关闭的外部工具/调度分支不属于本次验证。
- 输入时间戳是固定合成纪元 `1700000000 + now_ms // 1000`；Bot 发送入缓存使用的 `application.time.time()`（application.py:494）在单条消息的上下文管理器内使用同一纪元，finally 恢复。只替换该模块的引用，不替换全局 `time`。日志诊断耗时和 trace `_started` 仍可使用墙钟，但不写入业务报告，不参与业务判断或性能结论。
- 冷却源码判断为任一 last_attempt / last_sent 非 None 且 `now-last < cooldown`。0 是真实发送时间，不是“从未发送”哨兵；T−1/T/T+1 分别使用三个全新场景验证。当前测试冷却是 3 秒，2999/3000/3001ms 可精确区别。
- 生成预算窗口是 `(now−60s, now]`，计数是**生成尝试**，失败不会退还；直接 @ 仍受通用冷却与生成预算约束，但绕过主动发言专属限制。不会强加 demo 的成功输出窗口语义。
- 会话时效使用整数秒事件时间，`timestamp <= cutoff` 的旧消息不再形成活跃话题。179/180s 使用独立场景验证；不声称测试了子秒级话题时效。原始容量 30 的 ContextBuffer 仍可能包含已失效的旧消息。
- 每场景 factory 创建全新适配器、时钟、限流器、社交状态和人格状态。`close` 在 finally 清理；会话观测包装、故障包装在 ExitStack 恢复；单步时间补丁即使处理异常也恢复。已做同进程恢复与场景反序验证。
- 字符串 ID 稳定映射到每场景合成整数，输出回执另有确定范围，不使用真实账号。映射只是定位，不提供发言权或复算选中对象。

## 观察值及新增白名单检查

保留 `Decision.reason = EvaluationTrace.reason`，不把 `reply_cooldown`、`reply_rate_limit`、`disabled`、`onebot_confirmed` 折叠。`should_reply` 表示应用完成了本次合成发送；不是孤立的 SpeakPolicy.should_speak。`selected_context_message_id` 仍未声明，相关检查会 unsupported。

| 状态 | 来源 | 能力 | 检查类型 |
| --- | --- | --- | --- |
| `groups[g].context_message_ids` | 真实 ContextBuffer 快照，含输入 ID 和输出 ID | `context_ids` | 原 context_contains/excludes |
| `conversation_hints[event]` | 在真实 `context.conversation` 返回时旁路记录，同一返回对象继续交给 Bot | `conversation_hints` | conversation_context_contains/excludes、conversation_field_equals |
| `model_inputs[event]` | 真实 ResponseGenerator 追加到模型输入末尾的 `conversation：` JSON，FixedClient 在实际调用边界解码 | `model_conversation` | model_context_contains/excludes、model_field_equals |

两组新增检查都要求 `event_id` 已执行；包含检查还要求 `message_id` 是已执行的消息。field_equals 仅允许 `field="evidence"` 且 expected 为字符串。不支持表达式或任意属性。缺少能力、未调用模型或缺少观察值时 unsupported；声明能力但提供非法类型时是适配器错误。历史观察值由运行器深拷贝。

模型输入观测只保存解码后的 `related_message_ids` 和 `evidence`，不是搜索聊天文本子串。它说明模型实际收到的会话提示，不证明模型采用了提示，也不证明所有 Prompt 内容均已做隔离检查。引用场景在关闭主动接话时不会调用模型，所以明确检查真实 ConversationHint，不能冒充模型输入或输出质量结果。原 ContextBuffer 隔离、会话线索隔离、模型会话提示隔离是三个不同层次。

## 场景依据与覆盖

所有场景使用明确关闭主动接话的测试配置。旧三文件与 expected 保持字节不变。冷却 3 秒、窗口 60 秒、会话 180 秒；默认旧预算 6，独立冷却/引用/TTL/顺序场景预算 100，限流群隔离场景预算 1。其余有效配置写入报告 allowlist 和 manifest 摘要，而非用 `.env` 文件名推断。

| 场景 ID | 依据/观察对象 | 边界与限制 |
| --- | --- | --- |
| qqagent_budget（保留） | RateLimiter.check/_group，输出和原 reason | 第6/7次、59999/60000ms；没有真实失败生成 |
| qqagent_context_isolation（保留） | ContextBuffer.snapshot | 同用户不同群的原始缓存；advance 不主动发言 |
| qqagent_cooldown（保留） | 通用冷却及缓存 | 0/2999/3000ms；拦截输入保留；不是三独立边界场景的替代 |
| qqagent_boundary_minus/exact/plus（新增3） | RateLimiter.check，真实输出及拒绝 reason | 独立 T−1/T/T+1，首条 0ms；提高预算避免遮蔽 |
| qqagent_quote_priority（新增） | ContextBuffer.conversation 显式引用分支 | 先A再B后引用A，实际 related IDs 包含A排除B，evidence=explicit_quote；不调用模型 |
| qqagent_topic_ttl_before/exact（新增2） | ContextBuffer.conversation 的 cutoff/explicit_quote_expired | 179/180秒；话题成员失效但缓存仍保留A；无语义判分 |
| qqagent_quota_group_isolation（新增） | 每群 RateLimiter 与模型实际结构化会话提示 | A耗尽1次预算后B同用户可回复，B提示不含A |
| qqagent_same_timestamp（新增） | 文件顺序、同步缓存、真实模型会话提示 | z_first 先于 a_second；先前普通消息在提示中；冷却拦截输入仍在缓存 |

此外，集成 unittest 做同场景连续运行、整套反序按 ID 比较、未声明能力、错误期望、同进程故障恢复及异常清理。11 个场景共 52 个业务检查；测试数量以当次日志为准。

## 实现缺陷注入契约

`chatreg.faults.install_qqagent_fault` 在实际 `RateLimiter` 场景实例上包装 `check`。先调用真实 check；若本应允许且 elapsed 恰好等于配置冷却，再错误返回 `LimitResult(False, "reply_cooldown")`，等价于把该边界的 `<` 写为 `<=`。应用层真实消费这个错误结果，因此实际发送减少，断言自然失败。没有改导出的 actual/expected/reason 或强制 AssertionError。

证据记录调用次数、now/last 时间、cooldown、check phase、boundary/injected 标记。故障验证保持正确 JSON 字节不变。目标失败是 `qqagent_boundary_exact/probe_sent` 和 `probe_reason`；本轮这两项实际失败，未发生额外连锁失败。故障内部进程退出 1；导入/执行错误退出 2 不算检出。外层只有原版通过、注入已触发、目标失败、无 unsupported/not_run/错误、恢复通过且归一化结果一致时返回 0。

## 可追溯性与复现限制

基线的 manifest 记录 Git HEAD 和 dirty；无 Git 时明确 unknown。由于 Bot 有未提交修改，另保存本进程实际加载的 `qq_agent`/`llm`/`chatreg` Python 模块与当前 tools 入口的相对路径和内容 SHA-256。只按 sys.modules 中的明确范围读取 .py，不扫描 D 盘、.venv、媒体、模型、密钥或聊天数据。文件摘要不是完整内存快照；执行期间不应另行修改源码，也未归档第三方依赖。

有效配置为 allowlist：场景的五个 bot_config 字段，以及实际限流、会话、容量、Social/Participation/Personality 数值配置、功能开关、替身标识和种子0。不导出私人 Persona 正文，只提供其内容 SHA-256；因此换机器仍需自行提供相同人格文件，不能声称仅靠基线即可完全还原私人环境。

结果 JSON 不含机器路径、墙钟或诊断耗时。manifest 独立记录版本/配置/Python 元数据；归一化业务比较只按场景 ID 排序并移除相对 source 定位字段，保留完整事件、输出、失败和检查。没有为了相等删除失败轨迹。
