import asyncio
import copy
import time
from dataclasses import replace

import pytest

from chatreg.adapters.demo import DemoBotAdapter
from chatreg.reporting import build_report, report_exit_code
from chatreg.runner import run_scenario, run_scenarios
from chatreg.validation import validate_scenario


def message(id, at=0, group="g1", addressed=True, reply_to=None):
    event = {"id": id, "type": "message", "at_ms": at, "group_id": group,
             "user_id": "user", "text": id, "mentions": ["bot"] if addressed else []}
    if reply_to is not None:
        event["reply_to"] = reply_to
    return event


def output_check(id, after, expected, group="g1", event=None):
    check = {"id": id, "after_event": after, "type": "output_count",
             "group_id": group, "expected": expected}
    if event is not None:
        check["event_id"] = event
    return check


def decision_check(id, event, field, expected):
    return {"id": id, "after_event": event, "type": "decision_field_equals",
            "event_id": event, "field": field, "expected": expected}


def run(data, factory=DemoBotAdapter):
    return asyncio.run(run_scenario(validate_scenario(data), factory))


def assert_passed(result):
    assert result["errors"] == []
    assert result["checks"]
    assert all(c["status"] == "passed" for c in result["checks"]), result["checks"]


def test_cooldown_boundary_and_blocked_messages_remain_context(scenario_data):
    scenario_data["events"] = [message("m1"), message("m2", 9), message("m3", 10), message("m4", 10)]
    scenario_data["checks"] = [
        output_check("total", "m4", 2),
        decision_check("inside", "m2", "reason", "cooldown"),
        decision_check("at_boundary", "m3", "reason", "replied"),
        decision_check("blocked_context", "m3", "selected_context_message_id", "m2"),
    ]
    result = run(scenario_data)
    assert_passed(result)
    assert [d["reason"] for d in result["decisions"]] == ["replied", "cooldown", "replied", "cooldown"]
    assert [o["event_id"] for o in result["outputs"]] == ["m1", "m3"]
    assert [o["at_ms"] for o in result["outputs"]] == [0, 10]
    assert all(o["text"] == "测试回复" for o in result["outputs"])


def test_window_counts_only_successful_outputs_and_drops_left_boundary(scenario_data):
    scenario_data["bot_config"].update(cooldown_ms=0, max_messages_per_window=2)
    scenario_data["events"] = [message("normal", addressed=False), message("first"), message("second"),
                               message("blocked", 1), message("still_blocked", 99), message("boundary", 100)]
    scenario_data["checks"] = [
        decision_check("normal", "normal", "reason", "not_addressed"),
        decision_check("nth_allowed", "second", "reason", "replied"),
        decision_check("n_plus_one_blocked", "blocked", "reason", "rate_limit"),
        decision_check("before_boundary", "still_blocked", "reason", "rate_limit"),
        decision_check("left_boundary", "boundary", "reason", "replied"),
        output_check("total", "boundary", 3),
    ]
    assert_passed(run(scenario_data))


def test_cooldown_is_checked_before_rate_limit(scenario_data):
    scenario_data["bot_config"]["max_messages_per_window"] = 1
    scenario_data["events"] = [message("m1"), message("m2", 1), message("m3", 10)]
    scenario_data["checks"] = [decision_check("first_gate", "m2", "reason", "cooldown"),
                               decision_check("next_gate", "m3", "reason", "rate_limit")]
    assert_passed(run(scenario_data))


def test_group_context_cooldown_and_quota_are_independent(scenario_data):
    scenario_data["bot_config"]["max_messages_per_window"] = 1
    scenario_data["events"] = [message("a1"), message("b1", group="g2"), message("a2", 10), message("b2", 10, group="g2")]
    scenario_data["checks"] = [
        output_check("a", "b2", 1), output_check("b", "b2", 1, group="g2"),
        decision_check("no_leak", "b1", "selected_context_message_id", None),
        {"id": "context_a", "after_event": "b2", "type": "context_contains", "group_id": "g1", "message_id": "a1"},
        {"id": "context_b", "after_event": "b2", "type": "context_excludes", "group_id": "g1", "message_id": "b1"},
        {"id": "not_created", "after_event": "b2", "type": "context_excludes", "group_id": "never_created", "message_id": "a1"},
    ]
    assert_passed(run(scenario_data))


def test_reply_priority_and_expired_reference_fallback(scenario_data):
    scenario_data["bot_config"].update(cooldown_ms=0, context_ttl_ms=10)
    scenario_data["events"] = [message("old", addressed=False), message("recent", 5, addressed=False),
                               message("reference", 6, reply_to="old"), message("fallback", 10, reply_to="old"),
                               {"id": "expire", "type": "advance", "at_ms": 20}]
    scenario_data["checks"] = [
        decision_check("explicit_older", "reference", "selected_context_message_id", "old"),
        decision_check("expired_fallback", "fallback", "selected_context_message_id", "reference"),
        {"id": "old_gone", "after_event": "fallback", "type": "context_excludes", "group_id": "g1", "message_id": "old"},
        {"id": "ttl_boundary", "after_event": "expire", "type": "context_excludes", "group_id": "g1", "message_id": "fallback"},
        output_check("advance_no_output", "expire", 2),
    ]
    result = run(scenario_data)
    assert_passed(result)
    assert result["events"][0]["snapshot"]["groups"]["g1"]["context_message_ids"] == ["old"]
    assert result["events"][-1]["snapshot"]["groups"]["g1"]["context_message_ids"] == []


def test_same_timestamp_uses_file_order_not_identifier_sort(scenario_data):
    scenario_data["bot_config"]["cooldown_ms"] = 0
    scenario_data["events"] = [message("z_first", addressed=False), message("a_second")]
    scenario_data["checks"] = [decision_check("ordered", "a_second", "selected_context_message_id", "z_first")]
    result = run(scenario_data)
    assert_passed(result)
    assert [t["event"]["id"] for t in result["events"]] == ["z_first", "a_second"]


def test_virtual_time_never_sleeps(scenario_data, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("业务虚拟时间不得 sleep")

    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(asyncio, "sleep", forbidden)
    scenario_data["events"].append({"id": "far_future", "type": "advance", "at_ms": 10**15})
    scenario_data["checks"] = [output_check("total", "far_future", 1)]
    assert_passed(run(scenario_data))


def test_each_scenario_uses_fresh_adapter_clock_sink_and_state(scenario_data):
    instances = []

    class RecordedDemo(DemoBotAdapter):
        async def start(self, config, clock, sink):
            instances.append((self, clock, sink))
            await super().start(config, clock, sink)

    first = validate_scenario(scenario_data)
    second_data = copy.deepcopy(scenario_data)
    second_data["id"] = "second_scene"
    results = asyncio.run(run_scenarios([first, validate_scenario(second_data)], RecordedDemo))
    assert len(instances) == 2
    for index in range(3):
        assert instances[0][index] is not instances[1][index]
    for result in results:
        assert_passed(result)
        assert result["outputs"][0]["id"] == results[0]["outputs"][0]["id"]
        assert result["outputs"][0]["at_ms"] == 0


def test_runner_deep_copies_even_reused_mutable_snapshot(scenario_data):
    class SharedSnapshotDemo(DemoBotAdapter):
        async def start(self, config, clock, sink):
            await super().start(config, clock, sink)
            self.shared = {"groups": {"g1": {"context_message_ids": []}}}

        async def handle_message(self, event):
            result = await super().handle_message(event)
            self.shared["groups"]["g1"]["context_message_ids"].append(event.id)
            return result

        async def snapshot(self):
            return self.shared

        async def close(self):
            self.shared["groups"]["g1"]["context_message_ids"].clear()
            await super().close()

    scenario_data["events"].append(message("m2", 10))
    result = run(scenario_data, SharedSnapshotDemo)
    assert_passed(result)
    assert result["events"][0]["snapshot"]["groups"]["g1"]["context_message_ids"] == ["m1"]
    assert result["events"][1]["snapshot"]["groups"]["g1"]["context_message_ids"] == ["m1", "m2"]


def test_faulty_cooldown_variant_is_detected_with_localized_failure(scenario_data):
    class FaultyCooldownDemo(DemoBotAdapter):
        """仅测试专用：刻意将冷却边界错误地写成 <=。"""
        def _in_cooldown(self, now_ms, last_sent_ms, cooldown_ms):
            return now_ms - last_sent_ms <= cooldown_ms

    scenario_data["events"].append(message("boundary", 10))
    scenario_data["checks"] = [output_check("boundary_must_send", "boundary", 1, event="boundary"),
                               decision_check("still_checked", "boundary", "reason", "cooldown")]
    result = run(scenario_data, FaultyCooldownDemo)
    assert not result["errors"]
    assert [c["status"] for c in result["checks"]] == ["failed", "passed"]
    failed = result["checks"][0]
    assert failed["check_id"] == "boundary_must_send"
    assert failed["after_event"] == "boundary"
    assert failed["expected"] == 1 and failed["actual"] == 0
    assert "boundary" in failed["message"]
    assert report_exit_code(build_report([result], [])) == 1


@pytest.mark.parametrize("capability,check", [
    ("outputs", output_check("no_outputs", "m1", 1)),
    ("decisions", decision_check("no_decisions", "m1", "reason", "replied")),
    ("selected_context_message_id", decision_check("no_selection", "m1", "selected_context_message_id", None)),
    ("context_ids", {"id": "no_context", "after_event": "m1", "type": "context_contains", "group_id": "g1", "message_id": "m1"}),
])
def test_undeclared_capability_cannot_pass_even_when_data_is_present(scenario_data, capability, check):
    class LimitedDemo(DemoBotAdapter):
        capabilities = DemoBotAdapter.capabilities - {capability}

    scenario_data["checks"] = [check]
    result = run(scenario_data, LimitedDemo)
    assert result["errors"] == []
    assert result["checks"][0]["status"] == "unsupported"
    assert report_exit_code(build_report([result], [])) == 1


def test_missing_observations_are_unsupported(scenario_data):
    class NoObservations(DemoBotAdapter):
        async def handle_message(self, event):
            await super().handle_message(event)
            return None

        async def snapshot(self):
            return {}

    scenario_data["checks"] = [decision_check("no_decision", "m1", "reason", "replied"),
                               {"id": "no_context", "after_event": "m1", "type": "context_contains", "group_id": "g1", "message_id": "m1"}]
    result = run(scenario_data, NoObservations)
    assert result["errors"] == []
    assert [c["status"] for c in result["checks"]] == ["unsupported", "unsupported"]


def test_group_without_context_observation_is_unsupported_not_empty(scenario_data):
    class MissingContextField(DemoBotAdapter):
        async def snapshot(self):
            return {"groups": {"g1": {"some_other_debug_field": []}}}

    scenario_data["checks"] = [{"id": "missing", "after_event": "m1", "type": "context_excludes",
                                "group_id": "g1", "message_id": "m1"}]
    result = run(scenario_data, MissingContextField)
    assert result["errors"] == []
    assert result["checks"][0]["status"] == "unsupported"


def test_on_tick_exception_cleans_up_and_never_handles_message(scenario_data):
    calls = []

    class TickFailure(DemoBotAdapter):
        async def on_tick(self):
            calls.append("tick")
            raise RuntimeError("测试 tick 故障")

        async def handle_message(self, event):
            calls.append("message")
            return await super().handle_message(event)

        async def close(self):
            calls.append("close")
            await super().close()

    result = run(scenario_data, TickFailure)
    assert calls == ["tick", "close"]
    assert result["errors"][0]["stage"] == "on_tick"
    assert result["checks"][0]["status"] == "not_run"


def test_factory_exception_is_structured_and_other_scenarios_continue(scenario_data):
    scene = validate_scenario(scenario_data)
    calls = []

    def sometimes_broken():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("测试 factory 故障")
        return DemoBotAdapter()

    results = asyncio.run(run_scenarios([scene, scene], sometimes_broken))
    assert results[0]["errors"][0]["stage"] == "factory"
    assert results[0]["checks"][0]["status"] == "not_run"
    assert_passed(results[1])


@pytest.mark.parametrize("change", [{"should_reply": "yes"}, {"event_id": "forged"}, {"selected_context_message_id": 1}])
def test_malformed_declared_decision_is_adapter_error(scenario_data, change):
    class MalformedDemo(DemoBotAdapter):
        async def handle_message(self, event):
            return replace(await super().handle_message(event), **change)

    result = run(scenario_data, MalformedDemo)
    assert result["errors"] and result["errors"][0]["kind"] == "adapter_error"
    assert result["errors"][0]["event_id"] == "m1"
    assert result["checks"][0]["status"] == "not_run"
    assert report_exit_code(build_report([result], [])) == 2


@pytest.mark.parametrize("snapshot", [
    {"groups": {"g1": {"context_message_ids": [42]}}},
    {"groups": []}, {"not_json": object()}, {"nan": float("nan")},
])
def test_invalid_snapshots_are_execution_errors(scenario_data, snapshot):
    class BadSnapshot(DemoBotAdapter):
        async def snapshot(self):
            return snapshot

    result = run(scenario_data, BadSnapshot)
    assert result["errors"] and result["errors"][0]["stage"] == "snapshot"
    assert result["checks"][0]["status"] == "not_run"


@pytest.mark.parametrize("phase", ["start", "handle_message", "close"])
def test_errors_clean_up_resources_and_do_not_stop_later_scenarios(scenario_data, phase):
    cleaned = []

    class BrokenDemo(DemoBotAdapter):
        async def start(self, config, clock, sink):
            await super().start(config, clock, sink)
            if phase == "start":
                raise RuntimeError("测试 start 故障")

        async def handle_message(self, event):
            if phase == "handle_message":
                raise RuntimeError("测试消息处理故障")
            return await super().handle_message(event)

        async def close(self):
            cleaned.append(True)
            await super().close()
            if phase == "close":
                raise RuntimeError("测试清理故障")

    first = validate_scenario(scenario_data)
    scenario_data["id"] = "after_error"
    adapters = iter([BrokenDemo(), DemoBotAdapter()])
    results = asyncio.run(run_scenarios([first, validate_scenario(scenario_data)], lambda: next(adapters)))
    assert cleaned == [True]
    assert results[0]["errors"][0]["stage"] == phase
    assert results[0]["status"] == "error"
    if phase != "close":
        assert all(c["status"] == "not_run" for c in results[0]["checks"])
    assert_passed(results[1])
    assert report_exit_code(build_report(results, [])) == 2


@pytest.mark.parametrize("mode", ["tick", "other_group"])
def test_sink_prohibits_tick_outputs_and_cross_group_forgery(scenario_data, mode):
    class BadEmitter(DemoBotAdapter):
        async def on_tick(self):
            await super().on_tick()
            if mode == "tick":
                self._sink.emit("g1", "越权消息")

        async def handle_message(self, event):
            self._sink.emit("other_group", "越权消息")

    result = run(scenario_data, BadEmitter)
    assert result["errors"]
    assert result["outputs"] == []
    assert result["checks"][0]["status"] == "not_run"
