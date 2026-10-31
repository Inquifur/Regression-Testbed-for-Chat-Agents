import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from chatreg.adapters.demo import DemoBotAdapter
from chatreg.baseline import (BaselineError, canonical_bytes, create_baseline, digest, loaded_sources,
                             normalized_result, provenance, validate_passing_report)
from chatreg.offline import run_offline
from chatreg.reporting import build_report
from chatreg.runner import run_scenario
from chatreg.validation import validate_scenario, ValidationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def passing(scenario_data):
    return build_report([asyncio.run(run_scenario(validate_scenario(scenario_data)))], [])


def test_baseline_has_verifiable_report_and_sources(tmp_path, passing):
    metadata = provenance(passing, [{"relative_path": "scene.json", "sha256": "example"}], ROOT)
    passing["scenarios"][0]["config"]["api_key"] = "do-not-export"
    safe_metadata = provenance(passing, [], ROOT)
    assert "do-not-export" not in json.dumps(safe_metadata)
    passing["scenarios"][0]["config"].pop("api_key")
    target = tmp_path / "baseline"
    create_baseline(passing, target, metadata)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"]
    assert manifest["report_sha256"] == digest((target / "report.json").read_bytes())
    assert manifest["audit_sha256"] == digest((target / "checks.md").read_bytes())
    assert manifest["provenance"]["tool"]["python_sources"]["files"]
    for source in manifest["provenance"]["tool"]["python_sources"]["files"]:
        assert source["sha256"] == digest((ROOT / source["path"]).read_bytes())
        assert source["path"].startswith("src/chatreg/")
    assert "expected" in (target / "checks.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["failed", "unsupported", "not_run", "error", "empty", "zero_checks", "fake_actual", "summary", "malformed", "missing_config", "secret_config"])
def test_baseline_rejects_invalid_or_nonpassing_reports(tmp_path, passing, mutation):
    report = copy.deepcopy(passing)
    if mutation in {"failed", "unsupported", "not_run"}:
        report["scenarios"][0]["checks"][0]["status"] = mutation
    elif mutation == "error":
        report["errors"].append({"kind": "error"})
    elif mutation == "empty":
        report["scenarios"] = []
    elif mutation == "zero_checks":
        report["scenarios"][0]["checks"] = []
    elif mutation == "fake_actual":
        report["scenarios"][0]["checks"][0]["actual"] = 99
    elif mutation == "summary":
        report["summary"]["passed"] += 1
    elif mutation == "missing_config":
        report["scenarios"][0]["config"] = {}
    elif mutation == "secret_config":
        report["scenarios"][0]["config"]["api_key"] = "must-not-archive"
    else:
        report.pop("scenarios")
    with pytest.raises(BaselineError):
        create_baseline(report, tmp_path / "invalid", {})
    assert not (tmp_path / "invalid").exists()


def test_baseline_refuses_existing_target_without_overwrite(tmp_path, passing):
    create_baseline(passing, tmp_path / "existing", {})
    original = {p.name: p.read_bytes() for p in (tmp_path / "existing").iterdir()}
    with pytest.raises(BaselineError, match="已存在"):
        create_baseline(passing, tmp_path / "existing", {"different": True})
    assert original == {p.name: p.read_bytes() for p in (tmp_path / "existing").iterdir()}


def test_baseline_save_failure_cleans_only_its_new_directory(tmp_path, passing, monkeypatch):
    original = Path.write_bytes

    def write(self, data):
        if self.name == "manifest.json":
            raise OSError("模拟磁盘写入失败")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(OSError):
        create_baseline(passing, tmp_path / "failed", {})
    assert not (tmp_path / "failed").exists()


def test_loaded_sources_excludes_other_namespaces_and_dependencies(tmp_path):
    result = loaded_sources(tmp_path, ("chatreg",))
    assert result["files"] == []


def test_network_attempt_still_errors_when_business_swallows_exception(passing):
    async def swallowing():
        try:
            socket.getaddrinfo("example.invalid", 443)
        except RuntimeError:
            pass
        return passing

    report = run_offline(swallowing())
    assert report["status"] == "error"
    assert report["errors"][0]["operations"] == ["getaddrinfo"]


@pytest.mark.parametrize("kind", ["model_context_contains", "model_context_excludes", "model_field_equals",
                                  "conversation_context_contains", "conversation_context_excludes", "conversation_field_equals"])
def test_observation_checks_validate_types_and_unsupported(scenario_data, kind):
    check = {"id": "observe", "type": kind, "after_event": "m1", "event_id": "m1"}
    if kind.endswith("field_equals"):
        check.update(field="evidence", expected="direct_address")
    else:
        check["message_id"] = "m1"
    scenario_data["checks"] = [check]
    result = asyncio.run(run_scenario(validate_scenario(scenario_data)))
    assert result["checks"][0]["status"] == "unsupported"
    if kind.endswith("field_equals"):
        check["field"] = "__class__"
    else:
        check["message_id"] = "future"
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


@pytest.mark.parametrize("value", [{"evidence": True}, {"related_message_ids": [42]}, []])
def test_malformed_observations_are_adapter_error(scenario_data, value):
    class Bad(DemoBotAdapter):
        capabilities = DemoBotAdapter.capabilities | {"model_conversation"}

        async def snapshot(self):
            return {"model_inputs": {"m1": value}}

    result = asyncio.run(run_scenario(validate_scenario(scenario_data), Bad))
    assert result["errors"][0]["stage"] == "snapshot"
    assert result["checks"][0]["status"] == "not_run"


def test_public_demo_fault_verification_subprocess(tmp_path):
    target = tmp_path / "demo_fault"
    result = subprocess.run([sys.executable, "-X", "utf8", "tools/verify_faults.py", "--adapter", "demo",
                             "--output-dir", str(target)], cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert summary["inner_exit_codes"] == [0, 1, 0]
    assert summary["injection_triggered"] and summary["normalized_original_equals_restored"]
    assert summary["correct_scenarios_and_expectations_unchanged"]


def test_fault_execution_error_or_missing_trigger_cannot_count_as_detection(tmp_path):
    spec = importlib.util.spec_from_file_location("verification", ROOT / "tools/verify_faults.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path / "fault"
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/verify_faults.py"), "--output-dir", str(folder)], check=True,
                   cwd=ROOT, capture_output=True, timeout=30)
    reports = [json.loads((folder / (mode + ".json")).read_text(encoding="utf-8")) for mode in ("original", "fault", "restored")]
    assert not module.verify_reports(reports, [0, 2, 0], True)["verification_passed"]
    for scene in reports[1]["scenarios"]:
        for step in scene["events"]:
            step["snapshot"].pop("fault_injection", None)
    assert not module.verify_reports(reports, [0, 1, 0], True)["verification_passed"]


def test_explicit_cli_baseline_then_reject_same_directory(tmp_path):
    cmd = [sys.executable, "-X", "utf8", "-m", "chatreg", "run", "examples/mention_reply.json",
           "--baseline-dir", str(tmp_path / "baseline")]
    first = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=30)
    second = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=30)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 2, second.stderr
