"""原版→故障→恢复，内层真实进程退出码 0/1/0，外层验收成功返回 0。"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from chatreg.adapters.demo import DemoBotAdapter
from chatreg.baseline import canonical_bytes, digest, normalized_result
from chatreg.faults import InclusiveCooldownDemo
from chatreg.offline import run_offline
from chatreg.reporting import print_summary, report_exit_code, write_report
from chatreg.suite import execute_suite


def verify_reports(reports, codes, hashes_unchanged):
    original, faulty, restored = reports
    failures = [{"scenario_id": s["id"], "check_id": c["check_id"], "event_id": c["after_event"],
                 "expected": c["expected"], "actual": c["actual"]}
                for s in faulty["scenarios"] for c in s["checks"] if c["status"] == "failed"]
    adapter = faulty["adapter"]
    target = "qqagent_boundary_exact" if adapter == "qqagent-offline" else "demo_boundary_exact"
    expected = {(target, "probe_sent"), (target, "probe_reason")}
    observed = {(f["scenario_id"], f["check_id"]) for f in failures}
    injections = [step["snapshot"]["fault_injection"] for scene in faulty["scenarios"]
                  for step in scene["events"] if step.get("snapshot") and "fault_injection" in step["snapshot"]]
    triggered = any(o.get("injected", o.get("boundary", False)) for item in injections for o in item["observations"])
    no_errors = not any(r["errors"] or any(s["errors"] for s in r["scenarios"]) for r in reports)
    no_skips = not any(r["summary"][k] for r in reports for k in ("unsupported", "not_run"))
    original_passed = original["status"] == restored["status"] == "passed" and original["summary"]["passed"] > 0
    recovered = normalized_result(original) == normalized_result(restored)
    success = (codes == [0, 1, 0] and original_passed and faulty["status"] == "failed" and
               expected <= observed and triggered and no_errors and no_skips and recovered and hashes_unchanged)
    return {"verification_passed": success, "inner_exit_codes": codes,
            "expected_failure_checks": [{"scenario_id": s, "check_id": c} for s, c in sorted(expected)],
            "actual_failed_checks": failures, "injection_triggered": triggered,
            "injection_evidence": injections, "normalized_original_equals_restored": recovered,
            "correct_scenarios_and_expectations_unchanged": hashes_unchanged,
            "no_execution_errors": no_errors, "no_unsupported_or_not_run": no_skips}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", choices=["demo", "qqagent"], default="demo")
    parser.add_argument("--bot-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--worker", choices=["original", "fault"], help=argparse.SUPPRESS)
    parser.add_argument("--scenarios", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        # 仅 Demo 使用本 worker；QQAgent 使用同一正式离线入口。
        report = run_offline(execute_suite(args.scenarios, InclusiveCooldownDemo if args.worker == "fault"
                                          else DemoBotAdapter, "demo"))
        print_summary(report)
        write_report(report, args.report)
        return report_exit_code(report)
    if not args.output_dir or (args.adapter == "qqagent" and not args.bot_root):
        parser.error("必须提供 --output-dir；qqagent 还需 --bot-root")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        scene_dir = args.output_dir / "scenarios"
        scene_dir.mkdir()
        if args.adapter == "demo":
            sources = sorted((ROOT / "tests/fixtures/fault_suites/demo").glob("*.json"))
        else:
            sources = sorted((ROOT / "integration_examples/qqagent").glob("qqagent_boundary_*.json"))
        if len(sources) != 3:
            raise ValueError("需要三个独立的 T-1/T/T+1 正确场景")
        hashes = {p.name: digest(p.read_bytes()) for p in sources}
        for source in sources:
            shutil.copyfile(source, scene_dir / source.name)
        codes, reports = [], []
        for mode in ("original", "fault", "restored"):
            report_path = args.output_dir / (mode + ".json")
            if args.adapter == "qqagent":
                cmd = [sys.executable, "-X", "utf8", str(ROOT / "tools/run_qqagent.py"), "--bot-root", str(args.bot_root)]
                if mode == "fault":
                    cmd += ["--test-fault", "cooldown-inclusive"]
            else:
                cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--worker",
                       "fault" if mode == "fault" else "original"]
            process = subprocess.run(cmd + ["--scenarios", str(scene_dir.resolve()), "--report", str(report_path.resolve())],
                                     capture_output=True, encoding="utf-8", timeout=60, check=False)
            (args.output_dir / (mode + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
            codes.append(process.returncode)
            reports.append(json.loads(report_path.read_text(encoding="utf-8")))
        unchanged = all(digest(p.read_bytes()) == hashes[p.name] and
                        digest((scene_dir / p.name).read_bytes()) == hashes[p.name] for p in sources)
        summary = verify_reports(reports, codes, unchanged)
        summary["scenario_sha256"] = hashes
        (args.output_dir / "summary.json").write_bytes(canonical_bytes(summary))
        rows = ["# 冷却边界缺陷检测验证", "", "| 阶段 | 内层退出码 | 业务状态 |", "| --- | --- | --- |"]
        rows += [f"| {mode} | {code} | {r['status']} |" for mode, code, r in zip(("原版", "故障", "恢复"), codes, reports)]
        rows += ["", f"外层验收：{summary['verification_passed']}。原版与恢复归一化结果相等：{summary['normalized_original_equals_restored']}。",
                 "", "目标失败：" + str(summary["expected_failure_checks"]), "实际失败：" + str(summary["actual_failed_checks"]),
                 "", "注入的调用次数、边界参数及原版/故障/恢复完整轨迹见 JSON。故障退出 2 不算检出成功。"]
        (args.output_dir / "summary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"{args.adapter} 故障验证：{summary['verification_passed']}；内层退出码 {codes}；证据 {args.output_dir}")
        return 0 if summary["verification_passed"] else 1
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"故障验证执行错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
