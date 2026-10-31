"""可选真实源码接入测试；默认不要求安装 QQAgent 依赖。"""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from chatreg.adapters.qqagent import QQAgentAdapter
from chatreg.models import Check
from chatreg.runner import run_scenario
from chatreg.validation import load_scenario


@unittest.skipUnless(os.environ.get("CHATREG_QQAGENT_ROOT"), "未指定可选 QQAgent 源码路径")
class QQAgentIntegrationTests(unittest.TestCase):
    def run_scene(self, scenario, adapter=None):
        adapter = adapter or QQAgentAdapter(Path(os.environ["CHATREG_QQAGENT_ROOT"]))

        def blocked(*args, **kwargs):
            raise AssertionError("业务运行不得联网")

        with asyncio.Runner() as runner:
            runner.get_loop()
            with patch.object(socket.socket, "connect", blocked), patch.object(socket.socket, "connect_ex", blocked), \
                 patch.object(socket, "create_connection", blocked), patch.object(socket, "getaddrinfo", blocked):
                return runner.run(run_scenario(scenario, lambda: adapter))

    def scene(self):
        return load_scenario(ROOT / "integration_examples/qqagent/qqagent_cooldown.json")

    def test_real_scenarios_are_offline_and_deterministic(self):
        passed = 0
        for file in sorted((ROOT / "integration_examples/qqagent").glob("*.json")):
            scenario = load_scenario(file)
            first, second = self.run_scene(scenario), self.run_scene(scenario)
            self.assertEqual(first["status"], "passed", first["errors"])
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
            passed += sum(c["status"] == "passed" for c in first["checks"])
        self.assertEqual(passed, 52)

    def test_original_three_checks_unchanged_and_pass(self):
        import hashlib
        record = ROOT / "artifacts/phase2/handoff_sources.json"
        handoff = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
        # 本地验收文件存在时验证旧场景未被改预期；公共 CI 不需要私人产物。
        passed = 0
        for name in ("qqagent_budget", "qqagent_context_isolation", "qqagent_cooldown"):
            file = ROOT / "integration_examples/qqagent" / (name + ".json")
            if handoff:
                old = next(item for item in handoff["files"] if item["path"].replace("\\", "/") == file.relative_to(ROOT).as_posix())
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), old["sha256"])
            result = self.run_scene(load_scenario(file))
            self.assertEqual(result["status"], "passed")
            passed += len(result["checks"])
        self.assertEqual(passed, 22)

    def test_reordering_does_not_pollute_scenes(self):
        from chatreg.baseline import normalized_result
        from chatreg.reporting import build_report
        files = sorted((ROOT / "integration_examples/qqagent").glob("*.json"))
        a = build_report([self.run_scene(load_scenario(p)) for p in files], [], "qqagent-offline")
        b = build_report([self.run_scene(load_scenario(p)) for p in reversed(files)], [], "qqagent-offline")
        self.assertEqual(normalized_result(a), normalized_result(b))

    def test_real_fault_triggered_and_restored_in_same_process(self):
        from chatreg.baseline import normalized_result
        from chatreg.reporting import build_report
        scenario = load_scenario(ROOT / "integration_examples/qqagent/qqagent_boundary_exact.json")
        good = self.run_scene(scenario)
        adapter = QQAgentAdapter(Path(os.environ["CHATREG_QQAGENT_ROOT"]), test_fault=True)
        faulty = self.run_scene(scenario, adapter)
        self.assertFalse(faulty["errors"])
        self.assertEqual({c["check_id"] for c in faulty["checks"] if c["status"] == "failed"}, {"probe_sent", "probe_reason"})
        self.assertTrue(any(x["injected"] for x in adapter.fault_observations))
        self.assertNotIn("check", adapter.app.rate_limiter.__dict__)
        self.assertNotIn("conversation", adapter.app.context.__dict__)
        restored = self.run_scene(scenario)
        self.assertEqual(normalized_result(build_report([good], [])), normalized_result(build_report([restored], [])))

    def test_personality_clock_uses_virtual_seconds(self):
        adapter = QQAgentAdapter(Path(os.environ["CHATREG_QQAGENT_ROOT"]))
        result = self.run_scene(self.scene(), adapter)
        self.assertEqual(result["status"], "passed")
        if adapter.app.generator.personality is not None:
            self.assertEqual(adapter.app.generator.personality.clock(), 3.0)

    def test_no_fabricated_selected_context(self):
        scenario = self.scene()
        check = Check("selection_unavailable", "first", "decision_field_equals", event_id="first",
                      field="selected_context_message_id", expected=None)
        result = self.run_scene(replace(scenario, checks=(check,)))
        self.assertFalse(result["errors"])
        self.assertEqual(result["checks"][0]["status"], "unsupported")

    def test_wrong_expectation_really_fails(self):
        scenario = self.scene()
        check = Check("wrong_count", "first", "output_count", group_id="g1", event_id="first", expected=0)
        result = self.run_scene(replace(scenario, checks=(check,)))
        self.assertEqual(result["checks"][0]["status"], "failed")
        self.assertEqual(result["checks"][0]["actual"], 1)

    def test_exception_restores_time_and_closes_application(self):
        class Broken(QQAgentAdapter):
            async def start(self, config, clock, sink):
                await super().start(config, clock, sink)
                self.original_time = self.application_module.time

                async def fail(*args, **kwargs):
                    raise RuntimeError("测试消息入口异常")
                self.app.handle = fail

        adapter = Broken(Path(os.environ["CHATREG_QQAGENT_ROOT"]))
        result = self.run_scene(self.scene(), adapter)
        self.assertEqual(result["status"], "error")
        self.assertTrue(adapter.app._closing)
        self.assertIs(adapter.application_module.time, adapter.original_time)
        self.assertTrue(all(c["status"] == "not_run" for c in result["checks"]))
