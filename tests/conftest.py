"""小型 JSON 测试数据；测试运行器始终使用公开场景校验入口。"""

import pytest


@pytest.fixture
def scenario_data():
    return {
        "schema_version": 1,
        "id": "test_scene",
        "bot_config": {
            "bot_id": "bot",
            "cooldown_ms": 10,
            "window_ms": 100,
            "max_messages_per_window": 10,
            "context_ttl_ms": 1000,
        },
        "events": [
            {"id": "m1", "type": "message", "at_ms": 0,
             "group_id": "g1", "user_id": "u1", "text": "你好", "mentions": ["bot"]},
        ],
        "checks": [
            {"id": "sent", "after_event": "m1", "type": "output_count",
             "group_id": "g1", "event_id": "m1", "expected": 1},
        ],
    }
