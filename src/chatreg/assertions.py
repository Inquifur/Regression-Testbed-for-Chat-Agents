"""固定类型的结构化检查，不解释执行字符串。"""

from typing import Any

from .models import Check, CheckResult, Decision, Output


def evaluate_check(
    check: Check, capabilities: frozenset[str], decisions: dict[str, Decision],
    outputs: tuple[Output, ...], snapshot: dict[str, Any],
) -> CheckResult:
    expected = check.expected
    actual: object = None
    missing: str | None = None
    location: str
    if check.type == "output_count":
        location = f"outputs[group_id={check.group_id}, event_id={check.event_id or '*'}].count"
        if "outputs" not in capabilities:
            missing = "outputs"
        else:
            actual = sum(
                o.group_id == check.group_id and (check.event_id is None or o.event_id == check.event_id)
                for o in outputs
            )
    elif check.type == "decision_field_equals":
        location = f"decisions[{check.event_id}].{check.field}"
        if "decisions" not in capabilities or check.event_id not in decisions:
            missing = "decisions"
        elif check.field == "selected_context_message_id" and "selected_context_message_id" not in capabilities:
            missing = "selected_context_message_id"
        else:
            actual = getattr(decisions[check.event_id], check.field)
    elif check.type.startswith(("model_", "conversation_")):
        model = check.type.startswith("model_")
        capability = "model_conversation" if model else "conversation_hints"
        key = "model_inputs" if model else "conversation_hints"
        location = f"{key}[{check.event_id}]"
        observation = snapshot.get(key, {}).get(check.event_id)
        if "_context_" in check.type:
            expected = {"message_id": check.message_id, "contains": check.type.endswith("_contains")}
        if capability not in capabilities or observation is None:
            missing = capability
        else:
            actual = observation.get(check.field if check.type.endswith("_field_equals") else "related_message_ids")
            if actual is None:
                missing = capability
    else:
        location = f"groups[{check.group_id}].context_message_ids"
        expected = {"message_id": check.message_id, "contains": check.type == "context_contains"}
        groups = snapshot.get("groups")
        if "context_ids" not in capabilities or groups is None:
            missing = "context_ids"
        elif check.group_id not in groups:
            actual = []  # 声明能力但尚未创建的群，按空上下文处理。
        else:
            actual = groups[check.group_id].get("context_message_ids")
            if actual is None:
                missing = "context_ids"
    if missing:
        return CheckResult(check.id, "unsupported", check.after_event, check.type, expected, None,
                           f"事件 {check.after_event}，字段 {location}：未声明或未提供能力 {missing}")
    if check.type in {"context_contains", "context_excludes"} or "_context_" in check.type:
        passed = (check.message_id in actual) == check.type.endswith("_contains")
    else:
        passed = type(actual) is type(expected) and actual == expected
    return CheckResult(
        check.id, "passed" if passed else "failed", check.after_event, check.type,
        expected, actual,
        f"事件 {check.after_event}，字段 {location}：" + ("符合预期" if passed else f"预期 {expected!r}，实际 {actual!r}"),
    )
