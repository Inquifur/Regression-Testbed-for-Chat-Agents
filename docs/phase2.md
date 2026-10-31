# 第二阶段：基线、故障检测与真实规则场景

第二阶段在原测试台上扩展，不修改 `D:\QQAgent` 的源码、配置或生产数据。测试契约及真实/替身边界见 [qqagent-test-contract.md](qqagent-test-contract.md)。

## 本地命令

下面从项目目录执行，路径可替换。基线和故障验证目录必须使用**尚不存在的新目录**；再次运行请改目录名，不删除旧基线，不自动接受新结果。

```powershell
Set-Location 'D:\QQAgent 回归测试台\chatreg'

# 通用工具：完全不需要私人 Bot
.\.venv\Scripts\python.exe -m chatreg run examples --report artifacts/demo-new.json --baseline-dir artifacts/demo-baseline-new
.\.venv\Scripts\python.exe tools/verify_faults.py --adapter demo --output-dir artifacts/demo-fault-new

# 真实 Bot：使用其现有依赖，不额外安装或改动 Bot 环境
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 tools/run_qqagent.py --bot-root 'D:\QQAgent' --report artifacts/qqagent-new.json --baseline-dir artifacts/qqagent-baseline-new
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 tools/verify_faults.py --adapter qqagent --bot-root 'D:\QQAgent' --output-dir artifacts/qqagent-fault-new

# 反序业务比较；工具不自动判定其相等，集成测试会按场景 ID 比较
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 tools/run_qqagent.py --bot-root 'D:\QQAgent' --reverse --report artifacts/qqagent-reversed-new.json

# 公共测试与可选真实接入测试
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
$env:CHATREG_QQAGENT_ROOT = 'D:\QQAgent'
& 'D:\QQAgent\.venv-m1\Scripts\python.exe' -X utf8 -m unittest discover -s tests -p test_qqagent_integration.py -v
Remove-Item Env:\CHATREG_QQAGENT_ROOT
```

Windows 默认临时 pytest 目录若有旧权限问题，可加 `--basetemp artifacts/pytest-new` 指向项目内的新目录。本轮本地测试使用了该方式。无需修改系统目录权限。

私有脚本默认仅显示业务摘要、关键虚拟时间和拒绝 reason；`--verbose` 可打开诊断日志。真实规则测试固定模型文本，不连接 QQ、LLM 或外部业务服务。`run_offline` 记录被阻断的 socket/DNS 尝试，即使业务代码吞掉该异常也将报告设为 error；不将“尝试失败但最终通过”视为离线成功。

## 基线包

显式 `--baseline-dir` 创建三个文件：

| 文件 | 内容 |
| --- | --- |
| report.json | 完整报告、配置、契约版本、事件轨迹、决策、输出与检查 |
| checks.md | 逐场景/检查 ID、类型、expected、actual、状态 |
| manifest.json | 完成标记、格式版本、适配器/契约、Git SHA/dirty、加载源码摘要及范围、场景摘要、有效配置及摘要、Python/替身/种子、报告/核对表校验和、归一化结果摘要 |

只有有场景、有事件、有检查、全部 passed、无错误和 unsupported/not_run，且 summary 与实际检查相符的报告可成为通过基线。状态与 expected/actual 不符也拒绝。配置不在 allowlist 时拒绝。目标目录通过独占 mkdir 保护，不覆盖已有目录（即使目录为空）。报告、核对表写好后最后写 manifest；失败只清理本调用新建的目录，不保留看似完成的半成品。

普通 run 不创建基线、不改 expected。基线保存失败返回 2；行为失败/unsupported 返回 1；一般输入/适配器/写入错误返回 2。请求基线但结果不合格时，基线操作失败也返回 2，原运行报告仍保留供检查。

取样范围不是整个仓库备份。没有 Git 的测试台明确 unknown；私人 Bot 的 dirty 状态另外辅以实际加载 .py 内容摘要。Persona 仅哈希，不导出内容，第三方依赖也未归档；这些都是明确的可复现性限制。manifest 不混入业务结果比较。

## 故障验证的两层退出码

`verify_faults.py` 复制正确的 T−1/T/T+1 场景，并核验复制前后内容哈希；然后启动三个真实子进程，保存 original.json、fault.json、restored.json 和各自日志，最后产出 summary.json/summary.md。外层为演示与真实 Bot 共用。

- 内层期望退出码：原版 0，故障版 1，恢复版 0。
- 外层退出码：所有条件满足返回 0；未触发、未检出、恢复不一致等验证失败返回 1；执行/输入问题返回 2。
- 判断不仅看通过数：检查指定失败 ID、实际失败明细、边界调用参数、注入触发、errors、unsupported/not_run、场景哈希与恢复结果。
- 实现注入仅用于测试。QQAgent 的注入位置是真实限流器实例 `check`；Demo 的注入位置是真实 `_in_cooldown`。未修改磁盘 Bot 文件、未改正确 expected、未通过伪造 actual 制造失败。

旧的 `tests/fixtures/qqagent_wrong_boundary.json` 仍保留，它只验证错误预期会被断言抓住，不能算实现缺陷注入。第二阶段的实现故障流程是额外验证。

## 公共 CI 与已知范围

沿用 Windows/Linux Python 3.11 workflow，运行通用 pytest、demo 校验/运行/基线、demo 实现故障注入及恢复，上传公共 demo 报告。没有私人 Bot 时 8 项可选集成测试明确 skipped，CI 也明确打印真实集成未运行；不上传私人源码、Persona 或 QQAgent 报告。

本轮本地验收见 [phase2-acceptance.md](phase2-acceptance.md)。本地 Windows/Python 3.12 实测不等于 GitHub Windows/Linux 3.11 工作流已经执行。没有真实 QQ 投递、真实 LLM 调用、自然语言质量、并发竞态、主动回访、图片/搜索/Memory 集成或性能结论。
