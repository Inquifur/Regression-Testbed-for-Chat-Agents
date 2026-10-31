"""命令行入口：只允许显式注册的离线适配器。"""

import argparse
import asyncio
import sys
from pathlib import Path

from .adapters.demo import DemoBotAdapter
from .reporting import build_report, print_summary, report_exit_code, write_report
from .runner import append_not_run, empty_result, run_scenario
from .validation import ValidationError, load_scenario


ADAPTERS = {"demo": DemoBotAdapter}


def discover_files(path: Path) -> list[tuple[str, Path]]:
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise ValueError("场景文件必须使用 .json 扩展名")
        return [(path.name, path)]
    if not path.is_dir():
        raise ValueError("输入路径不存在或不是文件/目录")
    files = [(p.relative_to(path).as_posix(), p) for p in path.rglob("*")
             if p.is_file() and p.suffix.lower() == ".json"]
    if not files:
        raise ValueError("目录中没有 JSON 场景，不能零测试通过")
    return sorted(files, key=lambda item: item[0])


def _parse_inputs(path: Path) -> tuple[list[tuple[str, object]], list[dict]]:
    loaded, errors = [], []
    try:
        files = discover_files(path)
    except (OSError, ValueError) as exc:
        return [], [{"kind": "input_error", "source": str(path), "path": "$", "message": str(exc)}]
    ids: dict[str, str] = {}
    for source, file in files:
        try:
            scenario = load_scenario(file)
            if scenario.id in ids:
                errors.append({"kind": "input_error", "source": source, "path": "id",
                               "message": f"场景 ID {scenario.id!r} 重复；首次出现在 {ids[scenario.id]}"})
            else:
                ids[scenario.id] = source
                loaded.append((source, scenario))
        except ValidationError as exc:
            errors.append({"kind": "input_error", "source": source, "path": exc.path, "message": exc.message})
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append({"kind": "input_error", "source": source, "path": "$", "message": str(exc)})
    return loaded, errors


async def _execute(loaded: list, adapter: str) -> list[dict]:
    results = []
    for source, scenario in loaded:
        results.append(await run_scenario(scenario, ADAPTERS[adapter], source))
    return results


def main(argv: list[str] | None = None) -> int:
    # Windows 重定向默认可能使用本地代码页；CLI 输出统一 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="chatreg", description="离线聊天 Agent 回归测试（虚拟时间、零网络运行）")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="只校验 JSON 场景，不创建适配器")
    validate.add_argument("path", type=Path, help="场景文件或目录")
    run = subparsers.add_parser("run", help="运行场景并生成摘要与可选 JSON 报告")
    run.add_argument("path", type=Path, help="场景文件或目录")
    run.add_argument("--adapter", default="demo", help="已注册适配器：demo（默认）")
    run.add_argument("--report", type=Path, help="JSON 报告保存路径")
    run.add_argument("--baseline-dir", type=Path, help="显式创建通过基线，目标目录必须不存在")
    args = parser.parse_args(argv)
    loaded, errors = _parse_inputs(args.path)
    records = None
    if args.command == "run" and args.baseline_dir and not errors:
        from .suite import scenario_records
        records = scenario_records(args.path)
    if args.command == "validate":
        for error in errors:
            print(f"错误 {error['source']} {error['path']}：{error['message']}", file=sys.stderr)
        if errors:
            print(f"校验失败：{len(errors)} 个输入错误", file=sys.stderr)
            return 2
        for source, scenario in loaded:
            print(f"[valid] {source}：{scenario.id}")
        print(f"校验通过：{len(loaded)} 个场景（未执行 Bot）")
        return 0
    if args.adapter not in ADAPTERS:
        errors.append({"kind": "input_error", "source": "", "path": "--adapter",
                       "message": f"未知适配器 {args.adapter!r}；可用：demo"})
    if errors:
        # 先完成全量校验；任何输入错误都会阻止本批执行。
        results = []
        for source, scenario in loaded:
            result = empty_result(scenario, source)
            result["errors"].append({"kind": "input_error", "path": "$",
                                     "message": "本批输入校验失败，详见报告顶层 errors"})
            append_not_run(result, scenario)
            result["status"] = "error"
            results.append(result)
    else:
        results = asyncio.run(_execute(loaded, args.adapter))
    report = build_report(results, errors, args.adapter)
    print_summary(report)
    if args.report is not None:
        try:
            write_report(report, args.report)
        except (OSError, ValueError, TypeError) as exc:
            print(f"报告保存失败 {args.report}：{exc}", file=sys.stderr)
            return 2
        print(f"报告已保存：{args.report}")
    if args.baseline_dir is not None:
        from .baseline import create_baseline, provenance
        from .suite import scenario_records
        try:
            if records is not None and records != scenario_records(args.path):
                raise ValueError("执行期间场景文件已变化，不创建基线")
            create_baseline(report, args.baseline_dir,
                            provenance(report, records or [], Path(__file__).resolve().parents[2]))
        except (OSError, ValueError) as exc:
            print(f"基线创建失败：{exc}", file=sys.stderr)
            return 2
        print(f"通过基线已创建：{args.baseline_dir}")
    return report_exit_code(report)
