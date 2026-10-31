# chatreg：聊天 Agent 离线回归测试台

第二阶段已增加显式基线包、真实实现故障注入与恢复验证、QQAgent 规则场景扩充。运行与证据说明见 [第二阶段文档](docs/phase2.md)，真实测试边界见 [接入契约 v2](docs/qqagent-test-contract.md)。默认 demo 不依赖私人 Bot。

已新增本地 QQAgent 的独立离线接入脚本，运行方法、真实覆盖范围与限制见 [本地接入说明](docs/qqagent-local.md)。默认 CLI 和 demo 示例保持独立。

`chatreg` 把群聊场景写成 JSON，用虚拟时间运行 Bot 适配器，并在指定事件后检查结构化行为。它适合检查“该回复时是否回复”“冷却边界是否正确”“上下文有没有串群”等可复现规则，帮助修改策略时发现回归。

本仓库是第一阶段实现。运行时只依赖 Python 标准库；无需 QQbot 源码、账号、API key、GPU 或网络服务。`demo` 是规则固定的测试夹具，示例通过只说明演示适配器符合这些规则，**不代表任何已有 QQbot 已通过测试**。

## 安装和运行

需要 Python **3.11 或更新版本**。先进入本项目目录；以下命令中的路径可以换成自己的路径。

Windows PowerShell：

```powershell
Set-Location 'D:\QQAgent 回归测试台\chatreg'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m chatreg validate examples
.\.venv\Scripts\python.exe -m chatreg run examples --adapter demo --report artifacts/report.json
.\.venv\Scripts\python.exe -m pytest
```

Linux：

```text
cd /path/to/chatreg
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m chatreg validate examples
.venv/bin/python -m chatreg run examples --adapter demo --report artifacts/report.json
.venv/bin/python -m pytest
```

安装测试依赖时需要软件包源；安装完成后，上述场景运行不调用外部服务。只使用工具时，可以把安装命令改为 `python -m pip install -e .`，无需安装 pytest。请使用对应虚拟环境中的 Python，避免误用系统的旧版本。

若当前目录已经有配置好的 `.venv`，Windows 下直接运行 `.\.venv\Scripts\python.exe -m chatreg run examples --adapter demo --report artifacts/report.json` 即可，无需重新创建环境。

激活虚拟环境后，也可以使用安装后的命令：

```text
chatreg validate examples
chatreg run examples/cooldown_boundary.json --adapter demo
chatreg run examples --adapter demo --report artifacts/report.json
```

`validate` 只检查文件和引用，不创建 Bot，也不执行事件。输入可以是单个 `.json` 文件或目录；目录递归发现 JSON，并按相对路径排序。空目录、跨文件重复场景 ID 都属于输入错误。`--adapter` 首版只注册 `demo`，不会把用户输入当作 Python 模块路径执行。

## 一个完整场景

保存下面内容为 `cooldown_boundary.json`，运行 `python -m chatreg run cooldown_boundary.json --adapter demo`：

```json
{
  "schema_version": 1,
  "id": "cooldown_boundary",
  "description": "冷却期内拦截，达到边界后恢复",
  "bot_config": {
    "bot_id": "bot",
    "cooldown_ms": 10000,
    "window_ms": 60000,
    "max_messages_per_window": 10,
    "context_ttl_ms": 180000
  },
  "events": [
    {"id": "m1", "type": "message", "at_ms": 0, "group_id": "g1", "user_id": "u1", "text": "你好", "mentions": ["bot"]},
    {"id": "m2", "type": "message", "at_ms": 9999, "group_id": "g1", "user_id": "u1", "text": "还在吗", "mentions": ["bot"]},
    {"id": "m3", "type": "message", "at_ms": 10000, "group_id": "g1", "user_id": "u1", "text": "现在呢", "mentions": ["bot"]}
  ],
  "checks": [
    {"id": "first_sent", "after_event": "m1", "type": "output_count", "event_id": "m1", "group_id": "g1", "expected": 1},
    {"id": "blocked", "after_event": "m2", "type": "decision_field_equals", "event_id": "m2", "field": "reason", "expected": "cooldown"},
    {"id": "boundary_sent", "after_event": "m3", "type": "output_count", "event_id": "m3", "group_id": "g1", "expected": 1}
  ]
}
```

`m1` 在 0 毫秒发送，`m2` 距上次输出 9999 毫秒，仍处于冷却期；`m3` 恰好达到 10000 毫秒，允许发送。三条输入都会进入群上下文。窗口上限设为 10，避免窗口规则遮蔽这次冷却测试。工具直接推进整数毫秒，不会真的等待 10 秒。

`after_event` 指定检查点。`output_count` 的 `event_id` 是可选筛选条件；省略时统计该检查点之前指定群的所有输出。消息事件的 `id` 同时就是消息 ID。`mentions` 可省略，默认空数组；`reply_to` 必须引用文件中更早出现的同群消息。相同时间戳严格按文件顺序执行，时间倒退直接拒绝。

所有字段严格校验，未知字段会报出路径，例如 `events[2].reply_to`。时间与计数必须是相应范围的整数，JSON 的 `true`、浮点数、负数不能代替毫秒值。`cooldown_ms` 允许 0，其余三个数值配置必须为正整数。JSON 语法错误会显示文件、行和列。

## 检查类型

| type | 参数 | 检查内容 |
| --- | --- | --- |
| `output_count` | `group_id`、可选 `event_id`、非负整数 `expected` | 到检查点为止符合筛选条件的 Bot 输出数量 |
| `decision_field_equals` | `event_id`、`field`、`expected` | 指定消息的结构化决策字段 |
| `context_contains` | `group_id`、`message_id` | 当前快照上下文包含此消息 |
| `context_excludes` | `group_id`、`message_id` | 当前快照上下文不包含此消息 |

决策字段白名单为 `should_reply`（布尔值）、`reason`（字符串）与 `selected_context_message_id`（消息 ID 或 `null`）。检查只能引用检查点时已经执行的消息；`output_count` 的消息必须与所指定的群一致。上下文检查允许引用其他群消息，以发现串群错误。声明 `context_ids` 能力时，尚未创建的群视为空上下文。

检查结果状态是 `passed`、`failed`、`unsupported`、`not_run`。缺少可观察能力不能算通过；适配器声明能力却返回不合法的数据属于执行错误。普通检查失败后继续检查，适配器异常则停止当前场景，将剩余检查标为 `not_run`，清理后继续后续场景。

## 演示适配器规则

每个群独立维护未过期用户输入、上次成功输出时间和窗口内成功输出时间：

1. 只有 `mentions` 包含 `bot_id` 才尝试回复；普通消息只存入上下文，原因是 `not_addressed`。
2. 对 @ 消息，优先选仍在上下文中的 `reply_to`；否则选本条输入之前最近的有效同群消息，没有则为 `null`。然后把当前输入存入上下文，拦截的输入也保留。
3. 先检查冷却：`now_ms - last_sent_ms < cooldown_ms` 时原因是 `cooldown`，等于边界放行。
4. 再检查窗口 `(now_ms - window_ms, now_ms]`：已有 N 条成功输出时拦截下一条，原因是 `rate_limit`。左边界的旧输出不占额度。
5. 允许回复时，经内存 sink 收集固定文本 `测试回复` 后更新发送记录，原因是 `replied`。输入和被拦截回复不消耗额度。
6. `now_ms - message_at_ms >= context_ttl_ms` 时删除上下文。`advance` 仅推进时间并清理过期状态，不主动发消息。

`should_reply` 表示通过规则后本次获准回复。上下文选择只是 ID 路由示例，不包含语义理解、多人话题识别或真正的聊天智能。

## 示例覆盖

`examples/` 有 12 个独立正常场景：@ 回复、普通消息、冷却拦截与边界、窗口上限与左边界、群上下文隔离、群冷却与配额隔离、显式引用优先、TTL 边界、过期引用回退、被拦截输入仍入上下文。窗口测试禁用冷却，冷却测试提高配额，避免规则互相遮蔽。

故意失败的测试数据位于 `tests/fixtures/`，不会混入默认示例运行。测试还覆盖无效输入、未知字段、引用约束、快照独立、生命周期清理、确定性报告、CLI 退出码及禁止 socket 建连时的离线运行。

## 终端、报告和退出码

运行终端会逐场景显示通过、失败、不支持、未运行的数量，以及总体状态。具体失败会给出场景、事件、检查/字段、预期与实际值，便于回到 JSON 定位。

单独运行上面的冷却边界场景，终端输出如下：

```text
[passed] cooldown_boundary：通过 3 / 失败 0 / 不支持 0 / 未运行 0
总体 passed：1 个场景，通过 3 / 失败 0 / 不支持 0 / 未运行 0
```

批量运行正常示例时，总体应为 12 个场景、48 个通过检查。

JSON 报告为 UTF-8，`--report` 指定的父目录会自动创建；写入失败会明确报错。顶层包含：

| 字段 | 内容 |
| --- | --- |
| `report_schema_version` | 报告格式版本，当前为 1 |
| `adapter` | 使用的显式适配器名称 |
| `status` | 总体运行状态：`passed`、`failed` 或 `error` |
| `summary` | 场景数和四种检查状态的计数 |
| `scenarios` | 按输入相对路径稳定排序的场景结果 |
| `errors` | 输入或报告等全局结构化错误 |

场景结果包含 `id`、`source`、`description`、`config`、`capabilities`、`events` 轨迹、`decisions`、`outputs`、`checks`、`errors` 与 `status`。`capabilities` 为排序后的能力名称列表；每项事件轨迹包含 `event`、`decision`、`outputs`、`snapshot`，事件自身保留虚拟时间。输出包含稳定 ID、当前事件 ID、群 ID、虚拟时间和文本。每项检查都有 `check_id`、`status`、`after_event`、`type`、`expected`、`actual`、`message`。错误与普通行为失败分开记录。无效输入也尽可能写出请求的报告，含错误文件/字段路径。

报告不写墙钟时间、随机 UUID 等不稳定内容；相同输入、适配器与执行方式连续运行可产生相同 JSON 内容。历史快照不会随 Bot 后续状态变化而改写。

| 退出码 | 含义 |
| --- | --- |
| `0` | 所有检查通过，无不支持检查和执行错误 |
| `1` | 有行为检查失败，或存在 `unsupported` |
| `2` | 输入无效、适配器执行异常、报告写入失败等错误 |

多个类别同时出现时，`2` 优先。`not_run` 会附带造成它的执行错误，不会静默当作通过。CI 可以直接使用这些退出码判断结果。

## 结构与后续接入

核心实现位于 `src/chatreg/`：`validation.py` 校验输入，`clock.py` 提供虚拟时间，`adapters/` 隔离 Bot 规则，`runner.py` 管理执行与清理，`assertions.py` 执行白名单检查，`reporting.py` 与 `cli.py` 输出报告和命令行结果。

详见 [架构说明](docs/architecture.md) 和 [真实 Bot 接入说明](docs/integration.md)。第一阶段不含 Web 前端、数据库、真实 QQ 连接、真实模型调用、模型裁判、自动生成场景或趋势平台，也不能评价聊天自然度。真实 Bot 接入与策略版本对比留待后续阶段。

GitHub Actions 配置在 `.github/workflows/ci.yml`，在 Windows/Linux 的 Python 3.11 环境执行测试、校验示例、运行示例并上传报告。这里只提供 workflow 文件，不创建远程仓库或推送代码。
