"""确定性 JSON 报告与终端摘要。"""

import json
from collections import Counter
from pathlib import Path


CHECK_STATUSES = ("passed", "failed", "unsupported", "not_run")


def build_report(results: list[dict], errors: list[dict], adapter: str = "demo") -> dict:
    counts = Counter(c["status"] for result in results for c in result["checks"])
    has_error = bool(errors) or any(result["errors"] for result in results)
    has_failure = bool(counts["failed"] or counts["unsupported"])
    status = "error" if has_error else "failed" if has_failure else "passed"
    return {
        "report_schema_version": 1, "adapter": adapter, "status": status,
        "summary": {"scenarios": len(results), **{s: counts[s] for s in CHECK_STATUSES}},
        "scenarios": results, "errors": errors,
    }


def report_exit_code(report: dict) -> int:
    return {"passed": 0, "failed": 1, "error": 2}[report["status"]]


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def print_summary(report: dict) -> None:
    for result in report["scenarios"]:
        counts = Counter(c["status"] for c in result["checks"])
        print(f"[{result['status']}] {result['id']}：通过 {counts['passed']} / 失败 {counts['failed']} / "
              f"不支持 {counts['unsupported']} / 未运行 {counts['not_run']}")
        for step in result["events"]:
            decision = step.get("decision")
            if decision is not None and not decision["should_reply"]:
                print(f"  t={step['event']['at_ms']}ms 事件 {decision['event_id']}：{decision['reason']}")
        for check in result["checks"]:
            if check["status"] != "passed":
                print(f"  {check['check_id']} [{check['status']}] {check['message']}")
        for error in result["errors"]:
            print(f"  错误 {error.get('path', '')}：{error['message']}")
    for error in report["errors"]:
        print(f"错误 {error.get('source', '')} {error.get('path', '')}：{error['message']}")
    summary = report["summary"]
    print(f"总体 {report['status']}：{summary['scenarios']} 个场景，通过 {summary['passed']} / "
          f"失败 {summary['failed']} / 不支持 {summary['unsupported']} / 未运行 {summary['not_run']}")
