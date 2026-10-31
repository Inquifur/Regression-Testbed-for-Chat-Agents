"""显式创建不可覆盖的通过基线；元数据与确定性业务结果分离。"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


class BaselineError(ValueError):
    pass


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def normalized_result(report):
    """只排除输入文件的定位路径与执行排列；保留所有业务轨迹和失败信息。"""
    result = json.loads(canonical_bytes(report))
    result["scenarios"].sort(key=lambda scene: scene["id"])
    for scene in result["scenarios"]:
        scene.pop("source", None)
    return result


def audit_markdown(report):
    def cell(value):
        return json.dumps(value, ensure_ascii=False).replace("|", "\\|").replace("\n", " ")
    lines = ["# 检查核对摘要", "", "上下文检查按成员关系比较，expected 对象不要求与 actual 列表相等。", "",
             "| 场景 | 检查 | 类型 | expected | actual | 状态 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for scene in report["scenarios"]:
        for check in scene["checks"]:
            lines.append("| " + " | ".join(cell(x) for x in (scene["id"], check["check_id"], check["type"],
                           check["expected"], check["actual"], check["status"])) + " |")
    return "\n".join(lines) + "\n"


def validate_passing_report(report):
    try:
        if report["report_schema_version"] != 1 or type(report["report_schema_version"]) is not int:
            raise BaselineError("不支持的报告版本")
        if report["status"] != "passed" or report["errors"] != [] or not report["adapter"]:
            raise BaselineError("只有无错误的 passed 报告可创建基线")
        scenes = report["scenarios"]
        if type(scenes) is not list or not scenes:
            raise BaselineError("空套件不可创建基线")
        seen = set()
        count = 0
        for scene in scenes:
            if scene["id"] in seen or not scene["id"]:
                raise BaselineError("场景 ID 为空或重复")
            seen.add(scene["id"])
            if scene["status"] != "passed" or scene["errors"] != [] or not scene["checks"] or not scene["events"]:
                raise BaselineError("场景必须有事件和检查，且全部通过，无执行错误")
            if not isinstance(scene["config"], dict):
                raise BaselineError("场景缺少配置")
            if set(scene["config"]) != BOT_FIELDS or set(scene.get("effective_config", {})) - EFFECTIVE_FIELDS:
                raise BaselineError("报告配置含不在允许列表的字段，拒绝归档")
            from .validation import _config
            _config(scene["config"])
            checkpoints = {step["event"]["id"] for step in scene["events"]}
            check_ids = set()
            for check in scene["checks"]:
                if check["status"] != "passed" or check["check_id"] in check_ids or not check["check_id"]:
                    raise BaselineError("检查不全通过或 ID 非法")
                check_ids.add(check["check_id"])
                if check["after_event"] not in checkpoints:
                    raise BaselineError("检查点不存在")
                expected, actual = check["expected"], check["actual"]
                if check["type"] in {"context_contains", "context_excludes", "model_context_contains", "model_context_excludes", "conversation_context_contains", "conversation_context_excludes"}:
                    valid = (type(actual) is list and type(expected["contains"]) is bool and
                             expected["contains"] == check["type"].endswith("_contains") and
                             (expected["message_id"] in actual) == expected["contains"])
                elif check["type"] in {"output_count", "decision_field_equals", "model_field_equals", "conversation_field_equals"}:
                    valid = type(expected) is type(actual) and expected == actual
                    if check["type"] == "output_count":
                        valid = valid and type(expected) is int and expected >= 0
                else:
                    raise BaselineError("未知检查类型不能创建基线")
                if not valid:
                    raise BaselineError("检查状态与 expected/actual 不一致")
                count += 1
        desired = dict(scenarios=len(scenes), passed=count, failed=0, unsupported=0, not_run=0)
        if report["summary"] != desired or any(type(v) is not int for v in report["summary"].values()):
            raise BaselineError("报告 summary 与实际检查不符")
        canonical_bytes(report)
    except (KeyError, TypeError, IndexError) as exc:
        raise BaselineError(f"报告缺少有效结构：{exc}") from exc


def git_identity(root):
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10,
                              encoding="utf-8", errors="replace", check=False)
    try:
        sha, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
        if sha.returncode or status.returncode:
            return {"commit_sha": "unknown", "dirty": "unknown"}
        return {"commit_sha": sha.stdout.strip(), "dirty": bool(status.stdout)}
    except (OSError, subprocess.TimeoutExpired):
        return {"commit_sha": "unknown", "dirty": "unknown"}


def loaded_sources(root, prefixes):
    """仅查看 sys.modules 中明确命名空间的已加载 .py；不遍历磁盘。"""
    root = Path(root).resolve()
    paths = set()
    for name, module in list(sys.modules.items()):
        if not any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            continue
        file = getattr(module, "__file__", None)
        if file:
            path = Path(file).resolve()
            if path.suffix == ".py" and path.is_relative_to(root):
                relative = path.relative_to(root)
                if not any(part.startswith(".venv") for part in relative.parts):
                    paths.add(path)
    main = Path(getattr(sys.modules.get("__main__"), "__file__", "__missing__")).resolve()
    if main.suffix == ".py" and main.is_relative_to(root / "tools"):
        paths.add(main)
    files = [{"path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes())} for path in sorted(paths)]
    return {"scope": "本进程已加载的指定命名空间 .py 和当前 tools 入口；非完整仓库备份，排除依赖/数据/密钥",
            "module_prefixes": list(prefixes), "files": files, "digest": digest(canonical_bytes(files))}


EFFECTIVE_FIELDS = {"rate_limits", "conversation", "context_capacity", "social", "participation", "personality",
                    "policy_model_enabled", "contextual_enabled", "memory_enabled", "external_tools_enabled",
                    "model_stub", "sender_stub", "random_seed"}
BOT_FIELDS = {"bot_id", "cooldown_ms", "window_ms", "max_messages_per_window", "context_ttl_ms"}


def provenance(report, scenario_files, tool_root, bot_root=None):
    configs = []
    for scene in report["scenarios"]:
        configs.append({"scenario_id": scene["id"],
                        "scenario_config": {k: v for k, v in scene["config"].items() if k in BOT_FIELDS},
                        "effective": {k: v for k, v in scene.get("effective_config", {}).items() if k in EFFECTIVE_FIELDS}})
    meta = {
        "adapter_id": report["adapter"], "contract_versions": sorted({s.get("contract_version", "chatreg/1") for s in report["scenarios"]}),
        "python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
        "tool": {"git": git_identity(tool_root), "python_sources": loaded_sources(tool_root, ("chatreg",))},
        "bot": {"git": {"commit_sha": "unknown", "dirty": "unknown"}, "applicable": False},
        "scenarios": scenario_files, "effective_configs": configs, "config_sha256": digest(canonical_bytes(configs)),
        "model_stub": "FixedClient/v2" if bot_root else "DemoBot/no-model",
        "sender_stub": "MemoryACK/v1" if bot_root else "OutputSink/v1", "random_seed": 0 if bot_root else None,
        "reproducibility_limits": ["摘要提供来源可追溯性，不是完整环境归档或运行时内存证明；不包含第三方依赖源码。"],
    }
    if bot_root:
        persona = Path(bot_root) / "persona.yaml"
        meta["bot"] = {"applicable": True, "git": git_identity(bot_root),
                       "python_sources": loaded_sources(bot_root, ("qq_agent", "llm")),
                       "persona_content_sha256": digest(persona.read_bytes())}
        meta["reproducibility_limits"].append("私人 Persona 只记录内容摘要，不导出姓名/身份/风格正文；跨机器需自行提供同一文件。未读取 .env；显式测试配置不是生产配置。")
    return meta


def create_baseline(report, destination, metadata):
    validate_passing_report(report)
    destination = Path(destination)
    # 独占创建：不存在覆盖/接受新预期的路径；manifest 最后写入为完成标志。
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise BaselineError(f"基线目标已存在，不允许覆盖：{destination}") from exc
    try:
        report_data = canonical_bytes(report)
        audit = audit_markdown(report).encode("utf-8")
        manifest = {"baseline_format_version": 1, "complete": True, "provenance": metadata,
                    "report_sha256": digest(report_data), "audit_sha256": digest(audit),
                    "normalized_business_sha256": digest(canonical_bytes(normalized_result(report))),
                    "comparison_policy": "仅业务 report 参与比较；manifest 的 Git/Python/来源配置摘要属于元数据，不混入轨迹。"}
        (destination / "report.json").write_bytes(report_data)
        (destination / "checks.md").write_bytes(audit)
        (destination / "manifest.json").write_bytes(canonical_bytes(manifest))
    except BaseException:
        # 只清理本调用刚刚独占创建的目录，绝不触及预先存在的基线。
        shutil.rmtree(destination)
        raise
