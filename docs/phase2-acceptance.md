# 第二阶段本轮验收

日期：2026-10-31（Asia/Shanghai）。本地环境：Windows、Python 3.12.14。所有统计来自本轮运行，不沿用历史22项记录冒充新结果。

## 实际结果

| 验收项 | 实际结果与证据 |
| --- | --- |
| 通用工具与新增功能 | `pytest -q --basetemp artifacts/phase2/pytest-final`：145 passed、8 skipped；skipped 是未在通用环境提供私人 Bot 的可选集成测试 |
| 私人集成测试 | 使用 Bot 的 `.venv-m1` 单独运行 unittest：8项通过，日志 `artifacts/phase2/qqagent-integration-tests.log` |
| 原有 demo | 12场景48项通过，`artifacts/phase2/demo_report_final.json` |
| 原有真实 Bot 三场景 | 本轮22项通过，旧三个 JSON 字节摘要与交接记录一致；早期留存基线 `artifacts/phase2/baseline-original-three` |
| 扩充真实规则 | 新增8个独立场景，合计11场景52项通过，无 failed/unsupported/not_run/errors；`artifacts/phase2/qqagent_report_final.json` |
| 重复与反序 | final/repeat JSON 逐字节一致；reverse 按场景ID归一化后完整业务结果一致，未删除失败轨迹 |
| Demo 实现故障 | `artifacts/phase2/demo-fault-final/summary.json`：内层0/1/0，外层0，注入已触发、目标检查失败、恢复后业务结果相等 |
| QQAgent 实现故障 | `artifacts/phase2/qqagent-fault-final/summary.json`：内层0/1/0，外层0；并有同进程补丁恢复测试 |
| 基线可靠性 | invalid/empty/zero-check/failed/unsupported/not_run/伪 passed/错误 summary/未知配置/重复目标均拒绝；模拟 manifest 写入失败后新目标目录被清理；有真 CLI 基线创建/重复拒绝验证 |
| 离线约束 | 完整私有运行禁建连/DNS；业务吞掉被阻断网络异常的回归用例仍导致 error，不能伪装成通过 |
| 来源与完整性 | final基线 report/核对表/归一化摘要与当前内容一致；所有采样 Python 源文件逐一复核SHA-256。结果见 `artifacts/phase2/acceptance.json` |
| 外部仓库保护 | Bot Git status 与本轮开始一致；实际加载的26个 Bot Python 文件摘要一致；历史报告/核对表/反向测试报告与三个旧场景的摘要未变 |

## 被检出的缺陷

刻意将**真实 Bot 场景实例的通用冷却边界**改成“等于冷却时间仍拦截”，而不是修改正确 expected。实际通过 `RateLimiter.check` 注入，证据为 now=3000ms、last_attempt/last_sent=0、cooldown=3000ms、boundary=true、injected=true。工作中的 Bot 源文件没有修改。

| 故障场景/检查 | 正确 expected | 故障 actual | 结果 |
| --- | --- | --- | --- |
| qqagent_boundary_exact / probe_sent | 1 | 0 | failed |
| qqagent_boundary_exact / probe_reason | onebot_confirmed | reply_cooldown | failed |

故障版共7项通过、2项失败，没有执行异常、unsupported 或 not_run。T−1仍拦截、T+1仍放行，没有额外连锁失败。恢复版9项通过，归一化结果与原版相同。这个结果说明能检测**该刻意注入的实现缺陷**，不表示发现了原始 Bot 存在同样缺陷，也不承诺可检测所有缺陷。

原来的“把 boundary_sent.expected 改为0”夹具仍保留，仅作为断言机制反向测试，与本轮实现故障注入分开。

## 本轮基线包

- `artifacts/phase2/demo-baseline-final/`：公共 demo 的 report.json / checks.md / manifest.json。
- `artifacts/phase2/qqagent-baseline-final/`：真实 Bot 扩充套件的同名三文件。
- 私有基线采样16个测试台/入口 Python 文件、26个 Bot Python 文件；Git记录 Bot HEAD `0c77e446c3e6d279e9f84d2d98d86d874b89e122`、dirty=true。测试台无 Git 信息，明确记 unknown。
- `artifacts/phase2/handoff_sources.json` 是开始时的历史文件摘要，不重建缺失历史，也不改写历史报告。`baseline-original-three` 记录了扩充前那次运行时的代码；后续新增观测使其源码摘要与最终版本不同，这是正常的版本差异，不把它称为最终源码基线。
- 私人 Persona 仅记录内容SHA-256和允许的数值行为配置，未导出完整人格。第三方依赖未归档，所以这些是可追溯基线，不是可独立恢复整个私人环境的备份。

## 未验证项

没有执行真实QQ发送、真实模型、语义质量评判、主动接话/调度、并发竞态、图片、搜索或生产Memory。引用和话题TTL测试观察真实会话线索；它们关闭主动接话，不冒充模型已生成或采纳引用的证据。独立的模型会话提示隔离检查确实捕获了模型替身收到的结构化ID。

公共 Windows/Linux Python 3.11 GitHub Actions 已配置，未创建远程仓库、未推送，也未声称远程CI已运行。已检查 Python3.11语法兼容，但本地实际执行环境是Windows/Python3.12，不等同于实测Linux/3.11。

复现命令、基线规则与两层退出码见 [第二阶段说明](phase2.md)，函数/源码证据和所有场景覆盖范围见 [测试契约](qqagent-test-contract.md)。
