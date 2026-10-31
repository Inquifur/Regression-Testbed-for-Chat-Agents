import copy
import json

import pytest

from chatreg.clock import VirtualClock
from chatreg.validation import ValidationError, load_scenario, validate_scenario


@pytest.mark.parametrize("value", [True, False, -1, 0.5, "1", None])
def test_clock_rejects_invalid_business_time(value):
    clock = VirtualClock()
    with pytest.raises((ValueError, TypeError)):
        clock.advance_to(value)
    assert clock.now_ms == 0


def test_clock_allows_equal_time_but_never_backwards():
    clock = VirtualClock()
    clock.advance_to(12)
    clock.advance_to(12)
    with pytest.raises(ValueError):
        clock.advance_to(11)
    assert clock.now_ms == 12


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None])
@pytest.mark.parametrize("field", ["cooldown_ms", "window_ms", "context_ttl_ms", "max_messages_per_window"])
def test_config_rejects_wrong_types_and_negative_numbers(scenario_data, field, value):
    scenario_data["bot_config"][field] = value
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert f"bot_config.{field}" in caught.value.path


@pytest.mark.parametrize("field", ["window_ms", "context_ttl_ms", "max_messages_per_window"])
def test_positive_config_values_cannot_be_zero(scenario_data, field):
    scenario_data["bot_config"][field] = 0
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


def test_cooldown_zero_and_omitted_mentions_are_valid(scenario_data):
    scenario_data["bot_config"]["cooldown_ms"] = 0
    del scenario_data["events"][0]["mentions"]
    scene = validate_scenario(scenario_data)
    assert scene.events[0].mentions == ()


@pytest.mark.parametrize("where,path", [
    ("root", "typo"), ("config", "bot_config.typo"),
    ("event", "events[0].typo"), ("check", "checks[0].typo"),
])
def test_unknown_fields_have_exact_path(scenario_data, where, path):
    target = {"root": scenario_data, "config": scenario_data["bot_config"],
              "event": scenario_data["events"][0], "check": scenario_data["checks"][0]}[where]
    target["typo"] = "should not disappear"
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == path


@pytest.mark.parametrize("value", [True, -1, 0.5, "0"])
def test_event_time_rejects_boolean_and_noninteger(scenario_data, value):
    scenario_data["events"][0]["at_ms"] = value
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == "events[0].at_ms"


@pytest.mark.parametrize("kind", ["event", "check"])
def test_duplicate_ids_are_invalid(scenario_data, kind):
    key = "events" if kind == "event" else "checks"
    scenario_data[key].append(copy.deepcopy(scenario_data[key][0]))
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == f"{key}[1].id"


def test_time_order_is_rejected_instead_of_sorted(scenario_data):
    scenario_data["events"][0]["at_ms"] = 10
    scenario_data["events"].append({"id": "a1", "type": "advance", "at_ms": 9})
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == "events[1].at_ms"


@pytest.mark.parametrize("target", ["missing", "m2", "a1", "other_group"])
def test_reply_reference_must_be_prior_message_in_same_group(scenario_data, target):
    prior = {"id": "other_group", "type": "message", "at_ms": 0,
             "group_id": "g2", "user_id": "u2", "text": "别的群"}
    scenario_data["events"] = [prior, {"id": "a1", "type": "advance", "at_ms": 0},
                               scenario_data["events"][0],
                               {"id": "m2", "type": "message", "at_ms": 0,
                                "group_id": "g1", "user_id": "u2", "text": "未来"}]
    scenario_data["events"][2]["reply_to"] = target
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == "events[2].reply_to"


def test_reply_to_self_is_future_reference(scenario_data):
    scenario_data["events"][0]["reply_to"] = "m1"
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


def test_same_timestamp_prior_reference_is_valid(scenario_data):
    second = copy.deepcopy(scenario_data["events"][0])
    second.update(id="m2", reply_to="m1")
    scenario_data["events"].append(second)
    assert validate_scenario(scenario_data).events[1].reply_to == "m1"


@pytest.mark.parametrize("change,path", [
    ({"event_id": "missing"}, "checks[0].event_id"),
    ({"group_id": "g2"}, "checks[0].group_id"),
    ({"after_event": "missing"}, "checks[0].after_event"),
    ({"expected": True}, "checks[0].expected"),
    ({"type": "python_expression"}, "checks[0].type"),
])
def test_bad_check_parameters_are_rejected(scenario_data, change, path):
    scenario_data["checks"][0].update(change)
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == path


@pytest.mark.parametrize("check_type", ["output_count", "decision_field_equals", "context_contains", "context_excludes"])
def test_check_cannot_observe_a_future_message(scenario_data, check_type):
    scenario_data["events"].insert(0, {"id": "before", "type": "advance", "at_ms": 0})
    check = {"id": "future", "type": check_type, "after_event": "before"}
    if check_type == "decision_field_equals":
        check.update(event_id="m1", field="reason", expected="replied")
    elif check_type == "output_count":
        check.update(event_id="m1", group_id="g1", expected=0)
    else:
        check.update(message_id="m1", group_id="g1")
    scenario_data["checks"] = [check]
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


@pytest.mark.parametrize("field,expected", [
    ("should_reply", "true"), ("reason", False),
    ("selected_context_message_id", 7), ("__class__", "anything"),
])
def test_decision_fields_use_whitelist_and_typed_expected(scenario_data, field, expected):
    scenario_data["checks"] = [{"id": "d", "type": "decision_field_equals",
                                 "after_event": "m1", "event_id": "m1", "field": field,
                                 "expected": expected}]
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


@pytest.mark.parametrize("field", ["events", "checks"])
def test_empty_events_or_checks_are_invalid(scenario_data, field):
    scenario_data[field] = []
    with pytest.raises(ValidationError):
        validate_scenario(scenario_data)


def test_missing_required_field_is_reported(scenario_data):
    del scenario_data["events"][0]["text"]
    with pytest.raises(ValidationError) as caught:
        validate_scenario(scenario_data)
    assert caught.value.path == "events[0].text"


def test_json_error_includes_filename_line_and_column(tmp_path):
    path = tmp_path / "损坏.json"
    path.write_text('{\n  "id": }', encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_scenario(path)
    explanation = str(caught.value)
    assert path.name in explanation
    assert "2" in explanation
    assert any(word in explanation for word in ("列", "column", "col"))


def test_utf8_bom_file_can_be_loaded(tmp_path, scenario_data):
    path = tmp_path / "场景.json"
    path.write_text(json.dumps(scenario_data, ensure_ascii=False), encoding="utf-8-sig")
    assert load_scenario(path).id == scenario_data["id"]
