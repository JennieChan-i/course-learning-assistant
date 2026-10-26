"""Protocol fixtures exercise the workflow; their reviewer labels are not human gold."""

import asyncio
import json
from copy import deepcopy

import pytest

from backend.agents import Agents
from backend.badcase_replay import evaluate_export, replay, validate_snapshot
from backend.config import ROOT
from backend.knowledge import Knowledge
from backend.llm import event, new_run
from backend.observability import capture_call, capture_memory, capture_sources, start_snapshot
from backend.quality import CaseUpdate, FeedbackInput, Quality, ReplayInput, ReplayReview
from backend.store import Store


def scene(store, config, *, memory=None, session=None, status="completed", event_name=None):
    run = new_run(config, "chat")
    request = {"message": "RAG是什么？", "web_enabled": True}
    if session:
        request["session_id"] = session
    start_snapshot(run, config, request, {"session_id": session})
    run["dataset_version"] = "fixture-v1"
    run["status"] = status
    capture_memory(run, **(memory or {}))
    capture_sources(run, [Knowledge(config).corpus.source("S01")])
    run["snapshot"]["output"] = {"text": "协议测试输出[S01]"}
    if event_name:
        event(run, event_name)
    q = Quality(store, config)
    q.complete_run(run)
    return q, run["id"]


def report(q, run_id, comment="测试反馈"):
    return q.feedback(FeedbackInput(run_id=run_id, reason="accuracy", comment=comment))["case_id"]


def update(q, case_id, status, **kwargs):
    case = q.case(case_id)
    fields = {k: case[k] for k in ("severity", "category", "expected_behavior", "notes", "fix_reference")}
    fields.update(category="generation", expected_behavior="说明检索依据并引用S01。")
    fields.update(kwargs)
    return q.update(
        case_id,
        CaseUpdate(status=status, reviewer="automated protocol fixture", human_confirmed=True, **fields),
    )


def chat_run(client):
    response = client.post("/api/chat", json={"message": "RAG是什么？"})
    events = [json.loads(x[6:]) for x in response.text.split("\n\n") if x.startswith("data: ")]
    return events[0]


def test_api_feedback_survives_restart_and_duplicate_is_idempotent(client, store, config):
    first = chat_run(client)
    body = {"run_id": first["run_id"], "reason": "accuracy", "comment": "需要说明限制"}
    a = client.post("/api/feedback", json=body).json()
    b = client.post("/api/feedback", json=body).json()
    assert b["duplicate"] and a["case_id"] == b["case_id"]
    q = Quality(Store(store.path), config)
    assert q.detail(a["case_id"])["occurrences"] == 1
    messages = client.get(f"/api/sessions/{first['session_id']}").json()
    assert messages[-1]["metadata"]["run_id"] == first["run_id"]
    assert "snapshot" not in client.get("/api/traces").json()[0]
    assert client.post("/api/feedback", json={**body, "run_id": "unknown"}).status_code == 400
    assert client.post(f"/api/badcases/{a['case_id']}/export").status_code == 400
    assert client.delete(f"/api/badcases/{a['case_id']}").status_code == 200
    assert q.list() == []


@pytest.mark.parametrize(
    "event_name,reason",
    [("citation_rejected", "citation"), ("schema_rejected", "format"), (None, "system_error")],
)
def test_automatic_signals_are_only_pending(store, config, event_name, reason):
    q, _ = scene(store, config, status="failed", event_name=event_name)
    case = q.list()[0]
    assert case["reason"] == reason and case["status"] == "pending"
    assert q.detail(case["id"])["feedback"][0]["origin"] == "automatic"


def test_same_scene_merges_different_reports_but_not_cancelled_runs(store, config):
    q, run_id = scene(store, config)
    case_id = report(q, run_id)
    assert report(q, run_id, "另一个说明") == case_id
    assert q.case(case_id)["occurrences"] == 2
    scene(store, config, status="cancelled")
    assert len(q.list()) == 1


def test_snapshot_scrubs_credentials_and_reasoning_preserves_rubric(store, config):
    config.llm_api_key = "fixture-secret-llm"
    config.tavily_api_key = "fixture-secret-search"
    q, run_id = scene(store, config)
    run = store.trace_run(run_id)
    capture_call(
        run,
        [{"role": "user", "content": config.llm_api_key}],
        "coach",
        {"reasoning_content": "private reasoning", "correctness": 2, "reasoning": 1},
    )
    run["snapshot"]["memory"] = {"api_key": config.tavily_api_key, "text": config.tavily_api_key}
    q.complete_run(run)
    saved = store.trace_run(run_id)
    encoded = json.dumps(saved)
    assert config.llm_api_key not in encoded and config.tavily_api_key not in encoded
    assert "private reasoning" not in encoded
    assert saved["snapshot"]["llm_calls"][0]["output"]["reasoning"] == 1
    assert "models.lock.json" in saved["snapshot"]["build"]["files"]


def test_close_requires_replay_matching_current_fix_and_actual_manual_review(store, config):
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    with pytest.raises(ValueError, match="人工"):
        q.update(cid, CaseUpdate(status="confirmed", reviewer="fixture"))
    with pytest.raises(ValueError, match="顺序"):
        update(q, cid, "closed")
    update(q, cid, "confirmed")
    update(q, cid, "in_progress")
    update(q, cid, "verification", fix_reference="fixture-fix-1")
    with pytest.raises(ValueError, match="关闭前"):
        update(q, cid, "closed")
    entry, fresh = q.begin_replay(cid, ReplayInput(request_id="fixture-request-1"))
    assert fresh
    same, fresh = q.begin_replay(cid, ReplayInput(request_id="fixture-request-1"))
    assert not fresh and same["id"] == entry["id"]
    q.finish_replay(
        cid,
        entry["id"],
        {
            "status": "completed",
            "checks": {"execution_succeeded": True, "production_learning_unchanged": True},
            "mode_label": "demo",
        },
    )
    with pytest.raises(ValueError, match="关闭前"):
        update(q, cid, "closed")
    review = ReplayReview(
        verdict="pass",
        notes="Protocol fixture, not human model-quality evidence",
        reviewer="automated fixture",
        human_confirmed=True,
    )
    q.review_replay(cid, entry["id"], review)
    with pytest.raises(ValueError, match="关闭前"):
        update(q, cid, "closed", fix_reference="fixture-fix-2")
    assert update(q, cid, "closed")["status"] == "closed"
    report(q, run_id, "再次出现")
    assert q.case(cid)["status"] == "in_progress"


def test_failed_checks_and_demo_cannot_close_live_case(store, config):
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    update(q, cid, "confirmed")
    update(q, cid, "in_progress")
    update(q, cid, "verification", fix_reference="fixture-1")
    entry, _ = q.begin_replay(cid, ReplayInput(request_id="fixture-failed-1"))
    q.finish_replay(cid, entry["id"], {"status": "completed", "checks": {"execution_succeeded": False}})
    with pytest.raises(ValueError, match="未通过"):
        q.review_replay(
            cid,
            entry["id"],
            ReplayReview(verdict="pass", notes="fixture", reviewer="fixture", human_confirmed=True),
        )
    q.finish_replay(
        cid,
        entry["id"],
        {"status": "completed", "mode_label": "demo", "checks": {"execution_succeeded": True}},
    )
    q.review_replay(
        cid,
        entry["id"],
        ReplayReview(verdict="pass", notes="fixture", reviewer="fixture", human_confirmed=True),
    )
    with store.connect() as c:
        case = q.case(cid, c)
        case["snapshot"]["mode"] = "live"
        q.write(c, case)
    with pytest.raises(ValueError, match="真实模式"):
        update(q, cid, "closed")


def test_retrieval_case_requires_current_knowledge_to_close(store, config):
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    update(q, cid, "confirmed", category="retrieval")
    update(q, cid, "in_progress", category="retrieval")
    update(q, cid, "verification", category="retrieval", fix_reference="fixture-retrieval-fix")
    entry, _ = q.begin_replay(cid, ReplayInput(request_id="retrieval-fixture-1"))
    q.finish_replay(
        cid,
        entry["id"],
        {
            "status": "completed",
            "strategy": "frozen_evidence",
            "checks": {"execution_succeeded": True},
            "mode_label": "demo",
        },
    )
    q.review_replay(
        cid,
        entry["id"],
        ReplayReview(verdict="pass", notes="fixture", reviewer="automated fixture", human_confirmed=True),
    )
    with pytest.raises(ValueError, match="当前知识库"):
        update(q, cid, "closed", category="retrieval")


def test_failed_generation_has_reportable_run_id_without_grade_write(client, store, monkeypatch):
    async def broken(*args):
        raise ValueError("fixture provider failure")

    monkeypatch.setattr(client.app.state.agents, "create_practice", broken)
    response = client.post("/api/practices", json={})
    assert response.status_code == 400
    run_id = response.json()["run_id"]
    assert store.trace_run(run_id)["status"] == "failed"
    assert store.practices() == [] and store.reviews() == []
    assert client.post("/api/feedback", json={"run_id": run_id, "reason": "system_error"}).status_code == 200


@pytest.mark.parametrize("operation", ["delete", "change"])
def test_preference_removal_redacts_all_replay_paths_and_duplicate_comments(store, config, operation):
    store.preference("goal", "旧的个人目标")
    q, run_id = scene(store, config, memory={"preferences": {"goal": "旧的个人目标"}})
    cid = report(q, run_id, "旧目标反馈1")
    report(q, run_id, "旧目标反馈2")
    update(q, cid, "confirmed")
    if operation == "delete":
        store.delete_preference("goal")
    else:
        store.preference("goal", "新的目标")
    detail = q.detail(cid)
    assert detail["context_redacted"] and detail["status"] == "archived"
    assert "旧" not in json.dumps(detail, ensure_ascii=False)
    assert store.trace_run(run_id)["snapshot"]["redacted"]
    with pytest.raises(ValueError):
        q.export(cid)
    with pytest.raises(ValueError):
        q.begin_replay(cid, ReplayInput(request_id="fixture-redacted-1"))


def test_delete_session_and_clear_learning_remove_associated_cases(store, config):
    sid = store.session(None, None, "测试")
    q, run_id = scene(store, config, session=sid)
    report(q, run_id)
    store.delete_session(sid)
    assert q.list() == []
    q, run_id = scene(store, config)
    report(q, run_id)
    store.clear_learning()
    assert q.list() == [] and store.traces() == []


def test_unsubmitted_practice_does_not_leak_answers_via_quality(client):
    p = client.post("/api/practices", json={}).json()
    run_id = client.get("/api/traces").json()[0]["id"]
    cid = client.post("/api/feedback", json={"run_id": run_id, "reason": "other"}).json()["case_id"]
    detail = client.get(f"/api/badcases/{cid}").json()
    assert detail["answer_hidden"] and detail["original_output"] is None
    assert "reference_answer" not in json.dumps(detail)
    assert client.post(f"/api/badcases/{cid}/export").status_code == 400
    assert (
        client.post(f"/api/badcases/{cid}/replays", json={"request_id": "fixture-hidden-1"}).status_code
        == 400
    )
    client.post(f"/api/practices/{p['id']}/submit", json={"answers": p["demo_answers"]})
    assert "reference_answer" in json.dumps(client.get(f"/api/badcases/{cid}").json())


def test_startup_recovery_does_not_retry_or_lose_failed_attempts(store, config):
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    update(q, cid, "confirmed")
    q.begin_replay(cid, ReplayInput(request_id="fixture-interrupted-1"))
    q.recover_interrupted()
    entry = q.case(cid)["replays"][0]
    assert entry["status"] == "interrupted"
    assert q.begin_replay(cid, ReplayInput(request_id="fixture-interrupted-1"))[1] is False


@pytest.mark.parametrize("kind", ["chat", "practice", "assessment"])
@pytest.mark.parametrize("strategy", ["frozen_evidence", "current_knowledge"])
async def test_replay_isolated_for_all_tasks(client, store, config, kind, strategy):
    if kind == "chat":
        run_id = chat_run(client)["run_id"]
    else:
        p = client.post("/api/practices", json={}).json()
        if kind == "assessment":
            client.post(f"/api/practices/{p['id']}/submit", json={"answers": p["demo_answers"]})
        run_id = next(t["id"] for t in store.traces() if t["kind"] == kind)
    snapshot = store.trace_run(run_id)["snapshot"]
    before = store.learning_digest()
    result = await replay(snapshot, config, store, strategy, Knowledge(config))
    assert result["status"] == "completed", result
    assert all(result["checks"].values())
    assert before == store.learning_digest()
    assert result["human_semantic_review"].startswith("pending")
    assert not list((store.path.parent / "badcase-replays").glob("isolated-*"))


async def test_replay_never_enables_web_and_uses_historical_memory(store, config, monkeypatch):
    q, run_id = scene(
        store,
        config,
        memory={
            "preferences": {"goal": "历史目标"},
            "recent_dialogue": [{"role": "user", "content": "历史提问"}],
        },
    )

    async def check(agent, request, run):
        assert request.web_enabled is False
        assert agent.settings.tavily_api_key == "" and not agent.settings.tavily_allow_keyless
        assert agent.store.preferences()[0]["value"] == "历史目标"
        assert agent.store.messages(request.session_id)[0]["content"] == "历史提问"
        yield {"event": "delta", "text": "fixture"}

    monkeypatch.setattr(Agents, "chat", check)
    result = await replay(q.case(report(q, run_id))["snapshot"], config, store, "frozen_evidence")
    assert result["status"] == "completed"


async def test_current_assessment_refreshes_old_evidence_and_fails_if_source_removed(
    client, store, config, monkeypatch
):
    p = client.post("/api/practices", json={}).json()
    client.post(f"/api/practices/{p['id']}/submit", json={"answers": p["demo_answers"]})
    run_id = next(t["id"] for t in store.traces() if t["kind"] == "assessment")
    snapshot = store.trace_run(run_id)["snapshot"]
    original = deepcopy(snapshot)
    current = Knowledge(config)
    current.corpus.parents["S01"]["chunk_text"] = "当前已更新的课程证据"

    async def check(agent, practice, answers, run):
        assert practice["sources"][0]["chunk_text"] == "当前已更新的课程证据"
        return {"fixture": True}

    monkeypatch.setattr(Agents, "grade", check)
    result = await replay(snapshot, config, store, "current_knowledge", current)
    assert result["status"] == "completed"
    assert result["evidence"]["S01"]["chunk_text"] == "当前已更新的课程证据"
    assert snapshot == original
    del current.corpus.parents["S01"]
    failed = await replay(snapshot, config, store, "current_knowledge", current)
    assert failed["status"] == "failed" and "人工审核题目" in failed["error"]


async def test_replay_failure_and_cancellation_cleanup_temp_database(store, config, monkeypatch):
    q, run_id = scene(store, config)
    snapshot = store.trace_run(run_id)["snapshot"]

    async def broken(agent, request, run):
        raise ValueError("fixture timeout")
        yield

    monkeypatch.setattr(Agents, "chat", broken)
    result = await replay(snapshot, config, store, "frozen_evidence")
    assert result["status"] == "failed" and not result["checks"]["execution_succeeded"]

    async def cancelled(agent, request, run):
        raise asyncio.CancelledError()
        yield

    monkeypatch.setattr(Agents, "chat", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await replay(snapshot, config, store, "frozen_evidence")
    assert not list((store.path.parent / "badcase-replays").glob("isolated-*"))


def test_export_is_regression_only_and_locked_gold_never_modified(store, config):
    lock = ROOT / "evals" / "test-lock.json"
    before = lock.read_bytes()
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    with pytest.raises(ValueError):
        q.export(cid)
    update(q, cid, "confirmed")
    data = json.loads(q.export(cid)["content"])
    assert data["split"] == "regression"
    assert "not an automatic correctness label" in data["annotation"]
    assert lock.read_bytes() == before


async def test_batch_validates_before_any_token_spend(store, config, tmp_path, monkeypatch):
    q, run_id = scene(store, config)
    cid = report(q, run_id)
    update(q, cid, "confirmed")
    row = json.loads(q.export(cid)["content"])
    config.llm_api_key, config.llm_model, config.llm_base_url = (
        "fixture",
        "fixture",
        "https://invalid.example",
    )
    path = tmp_path / "regression.jsonl"
    path.write_text(json.dumps(row) + "\n[]\n", encoding="utf-8")

    async def forbidden(*args, **kwargs):
        pytest.fail("Validation must happen before replay")

    monkeypatch.setattr("backend.badcase_replay.replay", forbidden)
    with pytest.raises(ValueError, match="回归样例"):
        await evaluate_export(config, path, 2)
    broken = deepcopy(row["snapshot"])
    broken["source_pool"] = {"S01": {"id": "fake"}}
    with pytest.raises(ValueError, match="证据"):
        validate_snapshot(broken)


def test_replay_api_duplicate_request_does_not_run_twice(client, store, config, monkeypatch):
    first = chat_run(client)
    q = client.app.state.quality
    cid = report(q, first["run_id"])
    update(q, cid, "confirmed")
    calls = []

    async def fixture(*args):
        calls.append(True)
        return {"status": "completed", "checks": {"execution_succeeded": True}}

    monkeypatch.setattr("backend.main.replay", fixture)
    a = client.post(f"/api/badcases/{cid}/replays", json={"request_id": "fixture-api-123"})
    b = client.post(f"/api/badcases/{cid}/replays", json={"request_id": "fixture-api-123"})
    assert a.status_code == b.status_code == 200
    assert a.json()["id"] == b.json()["id"] and len(calls) == 1
