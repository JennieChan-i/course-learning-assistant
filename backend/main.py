import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from .evaluation_review import EvaluationReview, ReviewInput
from . import __version__
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import Headers, MutableHeaders

from .agents import Agents, demo_answers
from .config import ROOT, settings
from .knowledge import Knowledge
from .llm import new_run
from .schemas import ChatInput, PracticeInput, Preference, Submission
from .store import Store


def public_practice(p):
    result = dict(p)
    if not p["result"]:
        result["questions"] = [
            {k: v for k, v in q.items() if k not in ("reference_answer", "rubric")} for q in p["questions"]
        ]
    if p.get("demo"):
        result["demo_answers"] = demo_answers()
    return result


async def cancellable(request, work):
    task = asyncio.create_task(work)
    try:
        while not task.done():
            if await request.is_disconnected():
                raise asyncio.CancelledError()
            await asyncio.wait({task}, timeout=0.25)
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class LocalRequestGuard:
    """Pure ASGI: preserve disconnect signals while enforcing the local API boundary."""

    def __init__(self, app, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        reason = None
        if scope["method"] in ("POST", "PUT", "DELETE") and scope["path"].startswith("/api/"):
            if headers.get("X-Learning-Client") != "workbench":
                reason = "缺少本地工作台请求标识。"
            origin = headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{self.port}",
                f"http://localhost:{self.port}",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ):
                reason = "不允许的请求来源。"

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["Cache-Control"] = "no-store"
            await send(message)

        if reason:
            return await JSONResponse({"detail": reason}, status_code=403)(scope, receive, guarded_send)
        await self.app(scope, receive, guarded_send)


def create_app(config=settings, knowledge=None, store=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.knowledge.close()
        await app.state.agents.llm.close()

    app = FastAPI(title="课程智能学习助手", version=__version__, lifespan=lifespan)
    if config.app_mode not in ("demo", "live") or config.dataset not in ("sample", "private"):
        raise ValueError("APP_MODE 必须为 demo/live，DATASET 必须为 sample/private。")
    if config.app_mode == "demo" and config.dataset != "sample":
        raise ValueError("演示回放仅使用 sample 自编数据，请设置 DATASET=sample。")
    app.state.knowledge = knowledge or Knowledge(config)
    app.state.store = store or Store(config.runtime / "learning.sqlite3")
    app.state.agents = Agents(config, app.state.knowledge, app.state.store)
    app.state.lock = asyncio.Lock()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Learning-Client"],
    )

    app.add_middleware(LocalRequestGuard, port=config.app_port)

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def ensure_course(course_id):
        if course_id and course_id not in {c["id"] for c in app.state.knowledge.corpus.courses()}:
            raise ValueError("所选课程不存在。")

    def ensure_idle():
        if app.state.lock.locked():
            raise HTTPException(409, "已有任务正在执行，请等待完成或取消后重试。")

    @app.get("/api/status")
    def status():
        kb = app.state.knowledge
        return {
            "name": "课程智能学习助手",
            "mode": config.app_mode,
            "dataset": config.dataset,
            "llm_ready": config.llm_ready,
            "llm_model": config.llm_model or None,
            "coach_reasoning_effort": config.llm_coach_reasoning_effort or config.llm_reasoning_effort,
            "assessor_reasoning_effort": config.llm_reasoning_effort,
            "search_ready": bool(config.tavily_api_key) or config.tavily_allow_keyless,
            "search_access_mode": "key"
            if config.tavily_api_key
            else "keyless"
            if config.tavily_allow_keyless
            else "disabled",
            "search_used": app.state.agents.web.store.search_usage(),
            "search_limit": config.search_monthly_limit,
            "device": kb.models.device,
            "index_ready": (kb.index_path / "manifest.json").exists(),
            "dataset_version": kb.corpus.version,
            "parent_count": len(kb.corpus.rows),
            "embedding_model": config.embedding_model,
            "rerank_model": config.rerank_model,
            "busy": app.state.lock.locked(),
        }

    @app.get("/api/courses")
    def courses():
        return app.state.knowledge.corpus.courses()

    @app.get("/api/sources/{source_id}")
    def source(source_id: str):
        return app.state.knowledge.corpus.source(source_id)

    @app.get("/api/sessions")
    def sessions():
        return app.state.store.sessions()

    @app.get("/api/sessions/{session_id}")
    def messages(session_id: str):
        return app.state.store.messages(session_id)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str):
        ensure_idle()
        app.state.store.delete_session(session_id)
        return {"deleted": True}

    @app.post("/api/chat")
    async def chat(body: ChatInput, request: Request):
        ensure_idle()
        ensure_course(body.course_id)
        if config.app_mode == "live" and not config.llm_ready:
            raise ValueError("请先在 .env 配置 LLM 接口，再重新启动。")
        session_id = app.state.store.session(body.session_id, body.course_id, body.message)
        body.session_id = session_id
        run = new_run(config, "chat")
        run["dataset_version"] = app.state.knowledge.corpus.version

        async def stream():
            async with app.state.lock:
                started = time.perf_counter()
                full, sources = "", []

                def encode(data):
                    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

                yield encode({"event": "start", "session_id": session_id, "run_id": run["id"]})
                app.state.store.add_message(session_id, "user", body.message)
                try:
                    async for item in app.state.agents.chat(body, run):
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if item["event"] == "delta":
                            full += item["text"]
                        if item["event"] == "sources":
                            sources = item["sources"]
                        yield encode(item)
                    run["status"] = "completed"
                    app.state.store.add_message(
                        session_id,
                        "assistant",
                        full,
                        {"sources": sources, "run_id": run["id"], "demo": config.app_mode == "demo"},
                    )
                    yield encode({"event": "done", "run_id": run["id"]})
                except asyncio.CancelledError:
                    run["status"] = "cancelled"
                    raise
                except Exception as exc:
                    run["status"] = "failed"
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "模型服务调用失败，请检查配置、连接或服务额度。"
                    )
                    yield encode({"event": "error", "text": message})
                finally:
                    run["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                    app.state.store.trace(run)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/practices")
    def practices():
        return [public_practice(p) for p in app.state.store.practices()]

    @app.get("/api/practices/{practice_id}")
    def practice(practice_id: str):
        return public_practice(app.state.store.practice(practice_id))

    @app.post("/api/practices")
    async def new_practice(body: PracticeInput, request: Request):
        ensure_idle()
        ensure_course(body.course_id)
        async with app.state.lock:
            run = new_run(config, "practice")
            run["dataset_version"] = app.state.knowledge.corpus.version
            start = time.perf_counter()
            try:
                result = await cancellable(request, app.state.agents.create_practice(body, run))
                run["status"] = "completed"
                return public_practice(result)
            except asyncio.CancelledError:
                run["status"] = "cancelled"
                raise
            except Exception as exc:
                run["status"] = "failed"
                if isinstance(exc, ValueError):
                    raise
                raise HTTPException(502, "生成练习失败，请检查模型服务后重试。") from None
            finally:
                run["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
                app.state.store.trace(run)

    @app.post("/api/practices/{practice_id}/submit")
    async def submit(practice_id: str, body: Submission, request: Request):
        ensure_idle()
        async with app.state.lock:
            p = app.state.store.practice(practice_id)
            if p["result"]:
                return public_practice(p)
            run = new_run(config, "assessment")
            run["dataset_version"] = app.state.knowledge.corpus.version
            start = time.perf_counter()
            try:
                result = await cancellable(request, app.state.agents.grade(p, body.answers, run))
                app.state.store.finish_practice(practice_id, result)
                run["status"] = "completed"
                return public_practice(app.state.store.practice(practice_id))
            except asyncio.CancelledError:
                run["status"] = "cancelled"
                raise
            except Exception as exc:
                run["status"] = "failed"
                if isinstance(exc, ValueError):
                    raise
                raise HTTPException(502, "评分未完成，本次没有写入成绩，请检查模型服务后重试。") from None
            finally:
                run["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
                app.state.store.trace(run)

    @app.get("/api/reviews")
    def reviews():
        return app.state.store.reviews()

    @app.get("/api/preferences")
    def preferences():
        return app.state.store.preferences()

    @app.put("/api/preferences")
    def set_preference(body: Preference):
        ensure_idle()
        app.state.store.preference(body.key, body.value)
        return app.state.store.preferences()

    @app.delete("/api/preferences/{key}")
    def remove_preference(key: str):
        ensure_idle()
        app.state.store.delete_preference(key)
        return {"deleted": True}

    @app.delete("/api/learning-records")
    def clear_learning():
        ensure_idle()
        app.state.store.clear_learning()
        return {"deleted": True}

    @app.get("/api/traces")
    def traces():
        return app.state.store.traces()

    @app.get("/evaluation", include_in_schema=False)
    def evaluation_page():
        return FileResponse(ROOT / "frontend/public/evaluation.html")

    @app.get("/api/evaluation/sets")
    def evaluation_sets():
        return EvaluationReview().sets()

    @app.get("/api/evaluation/runs")
    def evaluation_runs():
        runs = {}
        for folder in ("live-sample", "live-private"):
            directory = ROOT / "runtime" / folder / "reports"
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                if not path.name.startswith(("grading-", "answers-")):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                if not data.get("fingerprint"):
                    continue
                runs[(folder, data["fingerprint"])] = {
                    "id": data["fingerprint"],
                    "dataset": folder,
                    "status": data.get("status"),
                    "model": data.get("model"),
                    "cases": len(data.get("records", [])),
                    "batches": len(data.get("batches", [])),
                    "modes": data.get("modes", {}),
                    "updated_at": path.stat().st_mtime,
                }
        return sorted(runs.values(), key=lambda r: r["updated_at"], reverse=True)[:8]

    @app.get("/api/evaluation/{name}/cases")
    def evaluation_cases(name: str):
        return EvaluationReview().rows(name)

    @app.get("/api/evaluation/{name}/cases/{case_id}")
    def evaluation_case(name: str, case_id: str):
        return EvaluationReview().case(name, case_id)

    @app.put("/api/evaluation/{name}/cases/{case_id}")
    def save_evaluation_case(name: str, case_id: str, body: ReviewInput):
        return EvaluationReview().save(name, case_id, body)

    @app.get("/api/evaluation/{name}/sources/{source_id}")
    def evaluation_source(name: str, source_id: str):
        return EvaluationReview().source(name, source_id)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        dist = ROOT / "frontend" / "dist"
        target = (dist / path).resolve()
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在。")
        if target.is_relative_to(dist.resolve()) and target.is_file():
            return FileResponse(target)
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "后端已启动。请先构建前端，或启动 Vite 开发服务器。"})

    return app


app = create_app()
