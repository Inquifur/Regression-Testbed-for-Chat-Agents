"""使用 Bot 自己的环境运行私有源码；不导入生产启动入口。"""
import argparse
from functools import partial
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chatreg.adapters.qqagent import QQAgentAdapter
from chatreg.baseline import create_baseline, provenance
from chatreg.offline import run_offline
from chatreg.reporting import build_report, print_summary, report_exit_code, write_report
from chatreg.suite import execute_suite, scenario_records


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="QQAgent 应用层离线接入，固定模型、内存确认、禁止网络")
    parser.add_argument("--bot-root", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "integration_examples" / "qqagent")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts" / "qqagent_report.json")
    parser.add_argument("--baseline-dir", type=Path, help="显式创建通过基线，目录必须不存在")
    parser.add_argument("--test-fault", choices=["cooldown-inclusive"], help="仅测试专用：临时破坏实际冷却边界")
    parser.add_argument("--reverse", action="store_true", help="反序运行场景，检查隔离")
    parser.add_argument("--verbose", action="store_true", help="显示完整诊断日志，默认仅警告与摘要")
    args = parser.parse_args()
    try:
        # 仅调整本独立工具进程的日志输出，生产 Bot 不受影响。
        from nonebot.log import logger
        logger.remove()
        logger.add(sys.stderr, level="INFO" if args.verbose else "WARNING")
        records = scenario_records(args.scenarios)
        report = run_offline(execute_suite(
            args.scenarios, partial(QQAgentAdapter, args.bot_root, test_fault=bool(args.test_fault)),
            "qqagent-offline", reverse=args.reverse))
        if records != scenario_records(args.scenarios):
            raise ValueError("执行期间场景文件已变化")
    except Exception as exc:
        report = build_report([], [{"kind": "integration_error", "path": str(args.scenarios),
                                    "message": f"{type(exc).__name__}: {exc}"}], "qqagent-offline")
    print_summary(report)
    try:
        write_report(report, args.report)
        print(f"报告已保存：{args.report}")
        if args.baseline_dir is not None:
            if args.test_fault:
                raise ValueError("故障注入运行不能创建通过基线")
            create_baseline(report, args.baseline_dir, provenance(report, records, ROOT, args.bot_root))
            print(f"通过基线已创建：{args.baseline_dir}")
    except (OSError, ValueError) as exc:
        print(f"报告/基线保存失败：{exc}", file=sys.stderr)
        return 2
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
