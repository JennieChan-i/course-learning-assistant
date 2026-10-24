from copy import deepcopy

import pytest

from backend.agents import demo_grades, demo_questions
from backend.store import Store


def test_search_limit_counts_failures_and_survives_restart(store):
    assert store.reserve_search(2) == 1
    assert Store(store.path).reserve_search(2) == 2
    with pytest.raises(ValueError, match="额度"):
        store.reserve_search(2)
    assert store.search_usage() == 2


def test_memory_is_transactional_and_idempotent(store):
    p = store.add_practice({"questions": demo_questions(), "course_id": "01"})
    result = {"grades": demo_grades(), "answers": {"q1": "ignored raw answer"}}
    store.finish_practice(p["id"], result)
    first = deepcopy(store.reviews())
    store.finish_practice(p["id"], result)
    assert store.reviews() == first
    assert Store(store.path).reviews() == first


def test_review_uses_worst_answer_for_same_topic(store):
    questions = demo_questions()
    for q in questions:
        q["topic"] = "同一知识点"
    p = store.add_practice({"questions": questions, "course_id": "01"})
    store.finish_practice(p["id"], {"grades": demo_grades()})
    assert len(store.reviews()) == 1
    assert store.reviews()[0]["stage"] == 0
    assert "价格" in store.reviews()[0]["misconception"]


def test_no_mastery_record_for_reading_or_preferences(store):
    store.preference("goal", "学习 RAG")
    sid = store.session(None, None, "RAG")
    store.add_message(sid, "user", "我懂了")
    assert store.reviews() == []


def test_removed_preference_cannot_reenter_through_old_dialogue(store):
    store.preference("goal", "某个旧目标")
    sid = store.session(None, None, "测试")
    store.add_message(sid, "assistant", "我会根据某个旧目标来解释。")
    assert len(store.context_messages(sid)) == 1
    store.delete_preference("goal")
    assert store.context_messages(sid) == []
    assert len(store.messages(sid)) == 1
    store.add_message(sid, "user", "重新开始")
    assert len(store.context_messages(sid)) == 1
