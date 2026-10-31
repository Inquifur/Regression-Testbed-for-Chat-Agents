"""按文件顺序运行场景，每个场景独享状态和适配器。"""

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any

from .adapters.base import AdapterContractError, BotAdapter, OutputSink
from .adapters.demo import DemoBotAdapter
from .assertions import evaluate_check
from .clock import VirtualClock
from .models import Decision, MessageEvent, Scenario


def _stable_snapshot(raw: object, capabilities: frozenset[str]) -> dict:
    if type(raw) is not dict:
        raise AdapterContractError("snapshot：必须返回 JSON 对象")

    def inspect(value: object, path: str) -> None:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise AdapterContractError(f"{path}：JSON 对象的键必须是字符串")
                inspect(item, f"{path}.{key}")
        elif type(value) is list:
            for i, item in enumerate(value):
                inspect(item, f"{path}[{i}]")
        elif value is not None and type(value) not in {str, int, bool, float}:
            raise AdapterContractError(f"{path}：不是 JSON 数据")

    inspect(raw, "snapshot")
    try:
        snapshot = json.loads(json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise AdapterContractError(f"snapshot：无法序列化为 JSON：{exc}") from exc
    if "context_ids" in capabilities and "groups" in snapshot:
        groups = snapshot["groups"]
        if type(groups) is not dict:
            raise AdapterContractError("snapshot.groups：必须是对象")
        for group_id, group in groups.items():
            if type(group) is not dict:
                raise AdapterContractError(f"snapshot.groups.{group_id}：必须是对象")
            if "context_message_ids" in group:
                ids = group["context_message_ids"]
                if type(ids) is not list or any(type(x) is not str or not x for x in ids):
                    raise AdapterContractError(f"snapshot.groups.{group_id}.context_message_ids：必须是消息 ID 数组")
                if len(ids) != len(set(ids)):
                    raise AdapterContractError(f"snapshot.groups.{group_id}.context_message_ids：消息 ID 不得重复")
    for capability, key in (("model_conversation", "model_inputs"), ("conversation_hints", "conversation_hints")):
        if capability not in capabilities or key not in snapshot:
            continue
        if type(snapshot[key]) is not dict:
            raise AdapterContractError(f"snapshot.{key}：必须是对象")
        for event_id, observation in snapshot[key].items():
            if type(observation) is not dict:
                raise AdapterContractError(f"{key}.{event_id}：必须是对象")
            if "evidence" in observation and type(observation["evidence"]) is not str:
                raise AdapterContractError(f"{key}.{event_id}.evidence：必须为字符串")
            if "related_message_ids" in observation and (type(observation["related_message_ids"]) is not list or
                    any(type(mid) is not str for mid in observation["related_message_ids"])):
                raise AdapterContractError(f"{key}.{event_id}.related_message_ids：必须为字符串数组")
    return snapshot


def _validate_decision(decision: object, event: MessageEvent) -> None:
    # None 表示未提供该观察值；依赖它的检查将标记 unsupported。
    if decision is None:
        return
    if not isinstance(decision, Decision):
        raise AdapterContractError("decision：必须是 Decision 或 None")
    if decision.event_id != event.id or decision.group_id != event.group_id:
        raise AdapterContractError("decision.event_id/group_id：必须匹配当前消息")
    if type(decision.should_reply) is not bool:
        raise AdapterContractError("decision.should_reply：必须是布尔值")
    if type(decision.reason) is not str or not decision.reason.strip():
        raise AdapterContractError("decision.reason：必须是非空稳定原因字符串")
    if decision.selected_context_message_id is not None and (
        type(decision.selected_context_message_id) is not str or not decision.selected_context_message_id
    ):
        raise AdapterContractError("decision.selected_context_message_id：必须是消息 ID 或 null")


def empty_result(scenario: Scenario, source: str = "") -> dict[str, Any]:
    return {
        "id": scenario.id, "source": source, "description": scenario.description,
        "config": asdict(scenario.bot_config), "events": [], "decisions": [], "outputs": [],
        "checks": [], "errors": [], "status": "passed",
        "contract_version": "chatreg/1",
    }


def append_not_run(result: dict, scenario: Scenario) -> None:
    done = {check["check_id"] for check in result["checks"]}
    for check in scenario.checks:
        if check.id not in done:
            expected = check.expected
            if check.type.startswith(("context_", "model_context_", "conversation_context_")):
                expected = {"message_id": check.message_id, "contains": check.type.endswith("_contains")}
            result["checks"].append({
                "check_id": check.id, "status": "not_run", "after_event": check.after_event,
                "type": check.type, "expected": expected, "actual": None,
                "message": "由于 errors 中记录的错误，此检查未执行",
            })


async def run_scenario(
    scenario: Scenario, factory: Callable[[], BotAdapter] = DemoBotAdapter, source: str = "",
) -> dict[str, Any]:
    result = empty_result(scenario, source)
    clock = VirtualClock()
    sink = OutputSink(clock)
    decisions: dict[str, Decision] = {}
    adapter: BotAdapter | None = None
    stage, event_id = "factory", None
    try:
        adapter = factory()
        stage = "capabilities"
        raw_caps = adapter.capabilities
        if not isinstance(raw_caps, (set, frozenset)) or any(type(c) is not str for c in raw_caps):
            raise AdapterContractError("capabilities：必须是字符串集合")
        capabilities = frozenset(raw_caps)
        result["capabilities"] = sorted(capabilities)
        result["contract_version"] = getattr(adapter, "contract_version", "chatreg/1")
        stage = "start"
        await adapter.start(scenario.bot_config, clock, sink)
        if hasattr(adapter, "effective_config"):
            result["effective_config"] = _stable_snapshot(adapter.effective_config, frozenset())
        checks_by_event: dict[str, list] = {}
        for check in scenario.checks:
            checks_by_event.setdefault(check.after_event, []).append(check)
        for event in scenario.events:
            event_id = event.id
            trace = {"event": asdict(event), "decision": None, "outputs": [], "snapshot": None}
            result["events"].append(trace)
            start_output = len(sink.outputs)
            try:
                stage = "advance_to"
                clock.advance_to(event.at_ms)
                stage = "on_tick"
                await adapter.on_tick()
                if isinstance(event, MessageEvent):
                    stage = "handle_message"
                    sink._begin(event)
                    try:
                        decision = await adapter.handle_message(event)
                    finally:
                        sink._end()
                    stage = "decision"
                    _validate_decision(decision, event)
                    if decision is not None:
                        decisions[event.id] = decision
                        trace["decision"] = asdict(decision)
                        result["decisions"].append(asdict(decision))
                stage = "snapshot"
                snapshot = _stable_snapshot(await adapter.snapshot(), capabilities)
                trace["snapshot"] = snapshot
                stage = "checks"
                for check in checks_by_event.get(event.id, []):
                    result["checks"].append(asdict(evaluate_check(check, capabilities, decisions, sink.outputs, snapshot)))
            finally:
                trace["outputs"] = [asdict(o) for o in sink.outputs[start_output:]]
    except Exception as exc:
        result["errors"].append({
            "kind": "adapter_error", "stage": stage, "event_id": event_id,
            "path": f"events[{event_id}].{stage}" if event_id is not None else stage,
            "message": f"{type(exc).__name__}: {exc}",
        })
    finally:
        sink._end()
        if adapter is not None:
            try:
                await adapter.close()
            except Exception as exc:
                result["errors"].append({"kind": "adapter_error", "stage": "close", "event_id": event_id,
                                         "path": "close", "message": f"{type(exc).__name__}: {exc}"})
    result["outputs"] = [asdict(o) for o in sink.outputs]
    append_not_run(result, scenario)
    if result["errors"]:
        result["status"] = "error"
    elif any(c["status"] in {"failed", "unsupported"} for c in result["checks"]):
        result["status"] = "failed"
    return result


async def run_scenarios(
    scenarios: Sequence[Scenario], factory: Callable[[], BotAdapter] = DemoBotAdapter,
) -> list[dict[str, Any]]:
    results = []
    for scenario in scenarios:
        results.append(await run_scenario(scenario, factory))
    return results
