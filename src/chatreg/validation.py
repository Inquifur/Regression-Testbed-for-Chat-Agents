"""严格校验 JSON 场景结构与跨字段引用，不执行任何 Bot 代码。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AdvanceEvent, BotConfig, Check, Event, MessageEvent, Scenario


class ValidationError(ValueError):
    """带字段位置的输入错误，供终端和机器报告共同使用。"""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def _path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _object(value: object, path: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(path or "$", "必须是 JSON 对象")
    allowed = required | (optional or set())
    for key in value:
        if not isinstance(key, str):
            raise ValidationError(path or "$", "对象字段名必须是字符串")
        if key not in allowed:
            raise ValidationError(_path(path, key), "未知字段")
    for key in sorted(required):
        if key not in value:
            raise ValidationError(_path(path, key), "缺少必填字段")
    return value


def _string(value: object, path: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValidationError(path, "必须是字符串")
    if nonempty and not value.strip():
        raise ValidationError(path, "必须是非空字符串")
    return value


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        requirement = "正整数" if minimum == 1 else "非负整数"
        raise ValidationError(path, f"必须是{requirement}，不能是布尔值或浮点数")
    return value


def _list(value: object, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(path, "必须是 JSON 数组")
    if nonempty and not value:
        raise ValidationError(path, "至少需要一项")
    return value


def _config(value: object) -> BotConfig:
    fields = {"bot_id", "cooldown_ms", "window_ms", "max_messages_per_window", "context_ttl_ms"}
    config = _object(value, "bot_config", fields)
    return BotConfig(
        bot_id=_string(config["bot_id"], "bot_config.bot_id"),
        cooldown_ms=_integer(config["cooldown_ms"], "bot_config.cooldown_ms"),
        window_ms=_integer(config["window_ms"], "bot_config.window_ms", minimum=1),
        max_messages_per_window=_integer(
            config["max_messages_per_window"], "bot_config.max_messages_per_window", minimum=1
        ),
        context_ttl_ms=_integer(config["context_ttl_ms"], "bot_config.context_ttl_ms", minimum=1),
    )


def _events(value: object) -> tuple[Event, ...]:
    events: list[Event] = []
    by_id: dict[str, Event] = {}
    previous_time = 0
    for index, raw in enumerate(_list(value, "events", nonempty=True)):
        location = f"events[{index}]"
        # 先读取 discriminator，随后按对应类型拒绝额外字段。
        if not isinstance(raw, dict):
            raise ValidationError(location, "必须是 JSON 对象")
        if "type" not in raw:
            raise ValidationError(f"{location}.type", "缺少必填字段")
        kind = _string(raw["type"], f"{location}.type")
        if kind not in {"message", "advance"}:
            raise ValidationError(f"{location}.type", f"未知事件类型 {kind!r}")
        required = {"id", "type", "at_ms"}
        optional: set[str] = set()
        if kind == "message":
            required |= {"group_id", "user_id", "text"}
            optional = {"mentions", "reply_to"}
        event_data = _object(raw, location, required, optional)
        event_id = _string(event_data["id"], f"{location}.id")
        if event_id in by_id:
            raise ValidationError(f"{location}.id", f"事件 ID 重复：{event_id!r}")
        at_ms = _integer(event_data["at_ms"], f"{location}.at_ms")
        if at_ms < previous_time:
            raise ValidationError(f"{location}.at_ms", "事件时间必须非递减；不会自动排序输入")
        if kind == "advance":
            event: Event = AdvanceEvent(event_id, "advance", at_ms)
        else:
            group_id = _string(event_data["group_id"], f"{location}.group_id")
            mentions = tuple(
                _string(mention, f"{location}.mentions[{mention_index}]")
                for mention_index, mention in enumerate(
                    _list(event_data.get("mentions", []), f"{location}.mentions")
                )
            )
            reply_to = event_data.get("reply_to")
            if reply_to is not None:
                reply_to = _string(reply_to, f"{location}.reply_to")
                target = by_id.get(reply_to)
                if not isinstance(target, MessageEvent):
                    raise ValidationError(
                        f"{location}.reply_to", "必须引用本场景中已经出现的 message，不能悬空、引用未来或 advance"
                    )
                if target.group_id != group_id:
                    raise ValidationError(f"{location}.reply_to", "不能引用其他群的消息")
            event = MessageEvent(
                id=event_id,
                type="message",
                at_ms=at_ms,
                group_id=group_id,
                user_id=_string(event_data["user_id"], f"{location}.user_id"),
                text=_string(event_data["text"], f"{location}.text", nonempty=False),
                mentions=mentions,
                reply_to=reply_to,
            )
        events.append(event)
        by_id[event_id] = event
        previous_time = at_ms
    return tuple(events)


def _checks(value: object, events: tuple[Event, ...]) -> tuple[Check, ...]:
    positions = {event.id: index for index, event in enumerate(events)}
    by_id = {event.id: event for event in events}
    check_ids: set[str] = set()
    checks: list[Check] = []

    def executed_message(raw_id: object, path: str, checkpoint: str) -> MessageEvent:
        message_id = _string(raw_id, path)
        event = by_id.get(message_id)
        if not isinstance(event, MessageEvent):
            raise ValidationError(path, "必须引用本场景中的 message 事件")
        if positions[message_id] > positions[checkpoint]:
            raise ValidationError(path, f"消息 {message_id!r} 在检查点 {checkpoint!r} 尚未执行")
        return event

    for index, raw in enumerate(_list(value, "checks", nonempty=True)):
        location = f"checks[{index}]"
        if not isinstance(raw, dict):
            raise ValidationError(location, "必须是 JSON 对象")
        if "type" not in raw:
            raise ValidationError(f"{location}.type", "缺少必填字段")
        kind = _string(raw["type"], f"{location}.type")
        required = {"id", "after_event", "type"}
        optional: set[str] = set()
        if kind == "output_count":
            required |= {"group_id", "expected"}
            optional = {"event_id"}
        elif kind == "decision_field_equals":
            required |= {"event_id", "field", "expected"}
        elif kind in {"model_field_equals", "conversation_field_equals"}:
            required |= {"event_id", "field", "expected"}
        elif kind in {"model_context_contains", "model_context_excludes", "conversation_context_contains", "conversation_context_excludes"}:
            required |= {"event_id", "message_id"}
        elif kind in {"context_contains", "context_excludes"}:
            required |= {"group_id", "message_id"}
        else:
            raise ValidationError(f"{location}.type", f"未知检查类型 {kind!r}")
        check_data = _object(raw, location, required, optional)
        check_id = _string(check_data["id"], f"{location}.id")
        if check_id in check_ids:
            raise ValidationError(f"{location}.id", f"检查 ID 重复：{check_id!r}")
        checkpoint = _string(check_data["after_event"], f"{location}.after_event")
        if checkpoint not in by_id:
            raise ValidationError(f"{location}.after_event", f"不存在事件 {checkpoint!r}")
        params: dict[str, Any] = {}
        if kind == "output_count":
            params["group_id"] = _string(check_data["group_id"], f"{location}.group_id")
            params["expected"] = _integer(check_data["expected"], f"{location}.expected")
            if "event_id" in check_data:
                event = executed_message(check_data["event_id"], f"{location}.event_id", checkpoint)
                if event.group_id != params["group_id"]:
                    raise ValidationError(f"{location}.group_id", "必须与 event_id 引用消息的群一致")
                params["event_id"] = event.id
        elif kind in {"decision_field_equals", "model_field_equals", "conversation_field_equals"}:
            event = executed_message(check_data["event_id"], f"{location}.event_id", checkpoint)
            params["event_id"] = event.id
            field = _string(check_data["field"], f"{location}.field")
            if kind in {"model_field_equals", "conversation_field_equals"} and field != "evidence":
                raise ValidationError(f"{location}.field", "模型会话字段只允许 evidence")
            if kind == "decision_field_equals" and field not in {"should_reply", "reason", "selected_context_message_id"}:
                raise ValidationError(f"{location}.field", "只允许 should_reply、reason、selected_context_message_id")
            expected = check_data["expected"]
            if field == "should_reply":
                if type(expected) is not bool:
                    raise ValidationError(f"{location}.expected", "should_reply 的期望必须是布尔值")
            elif field in {"reason", "evidence"}:
                _string(expected, f"{location}.expected", nonempty=False)
            elif expected is not None:
                _string(expected, f"{location}.expected")
            params["field"] = field
            params["expected"] = expected
        elif kind in {"model_context_contains", "model_context_excludes", "conversation_context_contains", "conversation_context_excludes"}:
            params["event_id"] = executed_message(check_data["event_id"], f"{location}.event_id", checkpoint).id
            params["message_id"] = executed_message(check_data["message_id"], f"{location}.message_id", checkpoint).id
        else:
            params["group_id"] = _string(check_data["group_id"], f"{location}.group_id")
            event = executed_message(check_data["message_id"], f"{location}.message_id", checkpoint)
            # 此处故意不限制同群：允许检查跨群消息是否泄漏。
            params["message_id"] = event.id
        checks.append(Check(id=check_id, after_event=checkpoint, type=kind, **params))
        check_ids.add(check_id)
    return tuple(checks)


def validate_scenario(data: object) -> Scenario:
    root = _object(data, "", {"schema_version", "id", "bot_config", "events", "checks"}, {"description"})
    version = _integer(root["schema_version"], "schema_version", minimum=1)
    if version != 1:
        raise ValidationError("schema_version", "只支持版本 1")
    scenario_id = _string(root["id"], "id")
    description = _string(root.get("description", ""), "description", nonempty=False)
    config = _config(root["bot_config"])
    events = _events(root["events"])
    checks = _checks(root["checks"], events)
    return Scenario(scenario_id, description, config, events, checks)


def load_scenario(path: Path | str) -> Scenario:
    source = Path(path)
    try:
        # utf-8-sig 同时接受普通 UTF-8 和带 BOM 的 Windows 文件。
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(str(source), f"无法以 UTF-8 读取文件：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            str(source), f"JSON 格式错误，第 {exc.lineno} 行、第 {exc.colno} 列：{exc.msg}"
        ) from exc
    except RecursionError as exc:
        raise ValidationError(str(source), "JSON 嵌套过深，无法安全解析") from exc
    except ValueError as exc:
        raise ValidationError(str(source), f"JSON 数据无法解析：{exc}") from exc
    return validate_scenario(data)
