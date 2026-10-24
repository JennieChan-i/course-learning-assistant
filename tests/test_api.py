import json


def test_demo_learning_loop_and_reference_privacy(client):
    assert client.get("/api/status").json()["mode"] == "demo"
    assert len(client.get("/api/courses").json()) == 4
    response = client.post("/api/chat", json={"message": "RAG 是什么？"})
    events = [json.loads(x[6:]) for x in response.text.split("\n\n") if x.startswith("data: ")]
    assert events[-1]["event"] == "done"
    assert "固定的演示回放" in "".join(e.get("text", "") for e in events)
    source_id = next(e for e in events if e["event"] == "sources")["sources"][0]["id"]
    assert client.get(f"/api/sources/{source_id}").json()["type"] == "course"
    practice = client.post("/api/practices", json={"course_id": "01"}).json()
    assert len(practice["questions"]) == 3
    assert "reference_answer" not in practice["questions"][0]
    assert "rubric" not in practice["questions"][0]
    result = client.post(
        f"/api/practices/{practice['id']}/submit", json={"answers": practice["demo_answers"]}
    ).json()
    assert result["result"]["demo"] is True
    assert "reference_answer" in result["questions"][0]
    assert len(client.get("/api/reviews").json()) == 2
    repeated = client.post(
        f"/api/practices/{practice['id']}/submit", json={"answers": practice["demo_answers"]}
    ).json()
    assert repeated["result"] == result["result"]
    assert len(client.get("/api/reviews").json()) == 2


def test_delete_preference_and_learning_records(client):
    client.put("/api/preferences", json={"key": "goal", "value": "转岗产品经理"})
    assert client.get("/api/preferences").json()[0]["value"] == "转岗产品经理"
    client.delete("/api/preferences/goal")
    assert client.get("/api/preferences").json() == []
    p = client.post("/api/practices", json={}).json()
    client.post(f"/api/practices/{p['id']}/submit", json={"answers": p["demo_answers"]})
    client.delete("/api/learning-records")
    assert client.get("/api/practices").json() == []
    assert client.get("/api/reviews").json() == []
    assert client.get("/api/traces").json() == []


def test_request_boundaries(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "hello", "course_id": "not-a-course"}).status_code == 400
    assert (
        client.post(
            "/api/chat", json={"message": "hello"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/chat", json={"message": "hello"}, headers={"X-Learning-Client": ""}).status_code
        == 403
    )
    assert client.get("/api/not-a-route").status_code == 404
    assert client.get("/api/sources/nonexistent").status_code == 400


def test_partial_submission_never_changes_memory(client):
    p = client.post("/api/practices", json={}).json()
    response = client.post(f"/api/practices/{p['id']}/submit", json={"answers": {"q1": "部分答案"}})
    assert response.status_code == 400
    assert client.get("/api/reviews").json() == []
    assert client.get(f"/api/practices/{p['id']}").json()["result"] is None


def test_session_course_scope_cannot_silently_change(client):
    r = client.post("/api/chat", json={"message": "测试", "course_id": "01"})
    first = json.loads(r.text.split("\n\n")[0][6:])
    assert (
        client.post(
            "/api/chat", json={"message": "继续", "session_id": first["session_id"], "course_id": "02"}
        ).status_code
        == 400
    )
