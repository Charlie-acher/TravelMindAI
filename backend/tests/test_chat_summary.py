"""历史摘要测试：验证连续批次、覆盖版本和静默模型调用。"""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.schemas.requirement.conversation import HistorySummary
from app.services.chat.context import latest_summary, summary_batch
from app.services.chat.events import event_sink
from app.services.chat.summary import summarize_history
from tests.test_chat_context import _turn


def test_summary_batch_is_contiguous_and_stops_before_recent_window() -> None:
    turns = [_turn(index, f"问题{index}", "答" * 8) for index in range(1, 7)]
    recent = turns[-2:]
    batch = summary_batch(
        turns, HistorySummary(text="旧摘要", covered_revision=2), recent, max_chars=30,
    )
    assert [turn.revision for turn in batch] == [3, 4]


def test_oversized_first_pending_turn_does_not_advance_or_skip() -> None:
    turns = [_turn(1, "很长" * 20, "答复" * 20), _turn(2, "短", "短")]
    assert summary_batch(turns, None, [], max_chars=10) == []


def test_summary_uses_program_revision_accepts_empty_and_disables_stream_events() -> None:
    model = Mock()
    model.generate_json.return_value = json.dumps({"text": ""})
    events: list[tuple[str, dict[str, object]]] = []
    token = event_sink.set(lambda event, data: events.append((event, data)))
    try:
        summary = summarize_history(
            HistorySummary(text="旧摘要", covered_revision=2),
            [_turn(3, "我不去虎丘", "收到")], model,
        )
    finally:
        event_sink.reset(token)
    assert summary == HistorySummary(text="", covered_revision=3)
    assert events == []
    sent = model.generate_json.call_args.args[0]
    assert any("[第3轮 用户原话]" in message["content"] for message in sent)
    assert any("[第3轮 助手最终回答]" in message["content"] for message in sent)


def test_latest_summary_keeps_saved_empty_summary() -> None:
    empty = HistorySummary(text="", covered_revision=4)
    turns = [_turn(4, "总结", "好", history_summary=empty), _turn(5, "继续", "好")]
    assert latest_summary(turns) == empty


def test_summary_rejects_oversized_model_output_instead_of_truncating() -> None:
    model = Mock()
    model.generate_json.return_value = json.dumps({"text": "长" * 2001})
    with pytest.raises(ValidationError):
        summarize_history(None, [_turn(1, "问题", "回答")], model)


def test_summary_rejects_revision_gap_without_calling_model() -> None:
    model = Mock()
    with pytest.raises(ValueError, match="连续"):
        summarize_history(
            HistorySummary(text="旧摘要", covered_revision=1),
            [_turn(3, "跳过第二轮", "错误")], model,
        )
    model.generate_json.assert_not_called()


def test_summary_rejects_oversized_batch_instead_of_marking_it_covered() -> None:
    model = Mock()
    with pytest.raises(ValueError, match="12000"):
        summarize_history(None, [_turn(1, "问题", "回答" * 6001)], model)
    model.generate_json.assert_not_called()
