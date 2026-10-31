import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from chatreg import cli
from chatreg.adapters.demo import DemoBotAdapter
from chatreg.runner import run_scenarios
from chatreg.validation import load_scenario


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def invoke(*args):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return subprocess.run([sys.executable, "-m", "chatreg", *map(str, args)],
                          cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True,
                          timeout=15, check=False)


def write_scene(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_all_examples_pass_via_real_cli_and_report_is_parseable(tmp_path):
    report_path = tmp_path / "子目录" / "report.json"
    process = invoke("run", "examples", "--adapter", "demo", "--report", report_path)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["report_schema_version"] == 1
    assert report["status"] == "passed"
    assert len(report["scenarios"]) >= 10
    assert report["summary"]["failed"] == report["summary"]["unsupported"] == report["summary"]["not_run"] == 0
    for scenario in report["scenarios"]:
        assert not scenario["errors"]
        assert all(check["status"] == "passed" for check in scenario["checks"])
        assert {"config", "events", "decisions", "outputs", "checks", "errors"} <= scenario.keys()


def test_consecutive_cli_runs_produce_byte_identical_reports(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert invoke("run", "examples", "--report", first).returncode == 0
    assert invoke("run", "examples", "--report", second).returncode == 0
    assert first.read_bytes() == second.read_bytes()


def test_recursive_discovery_sorts_relative_paths(tmp_path, scenario_data):
    inputs = tmp_path / "inputs"
    for relative, id in [("z.json", "id_first_lexically"), ("a/inner.json", "id_last_lexically"), ("b.json", "middle")]:
        scenario_data["id"] = id
        write_scene(inputs / relative, scenario_data)
    report_path = tmp_path / "report.json"
    process = invoke("run", inputs, "--report", report_path)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [s["source"] for s in report["scenarios"]] == ["a/inner.json", "b.json", "z.json"]


def test_deliberate_failure_returns_one_and_identifies_event_and_values(tmp_path):
    report_path = tmp_path / "failure.json"
    process = invoke("run", FIXTURES / "deliberate_failure.json", "--report", report_path)
    assert process.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scenario = report["scenarios"][0]
    assert scenario["id"] == "deliberate_failure"
    assert not scenario["errors"]
    assert [c["status"] for c in scenario["checks"]] == ["failed", "passed"]
    failed = scenario["checks"][0]
    assert (failed["check_id"], failed["after_event"], failed["expected"], failed["actual"]) == ("wrong_count", "m1", 0, 1)
    assert "wrong_count" in process.stdout and "m1" in process.stdout


def test_invalid_input_returns_two_and_still_writes_structured_report(tmp_path):
    report_path = tmp_path / "invalid.json"
    process = invoke("run", FIXTURES / "invalid_bool_time.json", "--report", report_path)
    assert process.returncode == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert report["errors"][0]["path"] == "events[0].at_ms"
    assert "invalid_bool_time.json" in report["errors"][0]["source"]
    assert report["scenarios"] == []


@pytest.mark.parametrize("command", ["validate", "run"])
def test_empty_directory_cannot_pass(tmp_path, command):
    process = invoke(command, tmp_path)
    assert process.returncode == 2


def test_missing_input_path_returns_two_with_report(tmp_path):
    report_path = tmp_path / "report.json"
    process = invoke("run", tmp_path / "does_not_exist", "--report", report_path)
    assert process.returncode == 2
    assert json.loads(report_path.read_text(encoding="utf-8"))["errors"]


def test_duplicate_scenario_ids_are_input_error(tmp_path, scenario_data):
    write_scene(tmp_path / "a.json", scenario_data)
    write_scene(tmp_path / "b.json", scenario_data)
    report_path = tmp_path / "report.json"
    process = invoke("run", tmp_path, "--report", report_path)
    assert process.returncode == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert any(e["path"] == "id" for e in report["errors"])
    assert report["scenarios"][0]["checks"][0]["status"] == "not_run"
    assert report["scenarios"][0]["errors"]


def test_error_priority_over_behavior_failure(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "failure.json").write_bytes((FIXTURES / "deliberate_failure.json").read_bytes())
    (inputs / "invalid.json").write_bytes((FIXTURES / "invalid_bool_time.json").read_bytes())
    report_path = tmp_path / "report.json"
    assert invoke("run", inputs, "--report", report_path).returncode == 2


def test_validate_never_creates_adapter(monkeypatch, tmp_path, scenario_data):
    def prohibited_factory():
        raise AssertionError("validate 不得创建适配器")

    monkeypatch.setitem(cli.ADAPTERS, "demo", prohibited_factory)
    scene_path = tmp_path / "scene.json"
    write_scene(scene_path, scenario_data)
    assert cli.main(["validate", str(scene_path)]) == 0


def test_adapter_name_is_registered_not_imported(tmp_path):
    report_path = tmp_path / "report.json"
    process = invoke("run", "examples", "--adapter", "os:system", "--report", report_path)
    assert process.returncode == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert any(e["path"] == "--adapter" for e in report["errors"])


def test_report_write_failure_is_explicit_error(tmp_path):
    parent_file = tmp_path / "a_file"
    parent_file.write_text("cannot create a directory here", encoding="utf-8")
    process = invoke("run", "examples/mention_reply.json", "--report", parent_file / "report.json")
    assert process.returncode == 2
    assert "报告保存失败" in process.stderr
    assert "报告已保存" not in process.stdout


def test_runtime_adapter_error_returns_two_and_later_scenario_runs(tmp_path, scenario_data, monkeypatch):
    class Broken(DemoBotAdapter):
        async def handle_message(self, event):
            raise RuntimeError("测试适配器故障")

    inputs = tmp_path / "inputs"
    write_scene(inputs / "a.json", scenario_data)
    scenario_data["id"] = "still_runs"
    write_scene(inputs / "b.json", scenario_data)
    instances = iter([Broken(), DemoBotAdapter()])
    monkeypatch.setitem(cli.ADAPTERS, "demo", lambda: next(instances))
    report_path = tmp_path / "report.json"
    assert cli.main(["run", str(inputs), "--report", str(report_path)]) == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [s["status"] for s in report["scenarios"]] == ["error", "passed"]
    assert report["scenarios"][0]["checks"][0]["status"] == "not_run"
    assert report["scenarios"][0]["errors"]


def test_normal_examples_run_with_socket_connections_and_dns_forbidden(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("演示运行不应连接外部网络")

    scenes = [load_scenario(path) for path in sorted((ROOT / "examples").glob("*.json"))]
    # Windows asyncio 创建自身唤醒管道时会用 loopback socketpair。
    # 先初始化事件循环，再禁止所有建连；被测业务与适配器全程处于禁连环境。
    with asyncio.Runner() as runner:
        runner.get_loop()
        monkeypatch.setattr(socket.socket, "connect", forbidden)
        monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        monkeypatch.setattr(socket, "getaddrinfo", forbidden)
        results = runner.run(run_scenarios(scenes))
    assert len(results) >= 10
    assert all(result["status"] == "passed" for result in results)


def test_excessive_json_nesting_is_input_error_with_report(tmp_path):
    source = tmp_path / "too_deep.json"
    source.write_text("[" * 10000 + "0" + "]" * 10000, encoding="utf-8")
    report_path = tmp_path / "report.json"
    process = invoke("run", source, "--report", report_path)
    assert process.returncode == 2, process.stdout + process.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "error"
    assert any("嵌套过深" in error["message"] for error in report["errors"])


def test_installed_console_entrypoint_runs():
    command = Path(sys.executable).with_name("chatreg.exe" if os.name == "nt" else "chatreg")
    if not command.is_file():
        pytest.skip("源码直接运行时未安装入口；安装验收中另行验证")
    process = subprocess.run([str(command), "validate", "examples"], cwd=ROOT,
                             text=True, encoding="utf-8", errors="replace", capture_output=True,
                             timeout=15, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
