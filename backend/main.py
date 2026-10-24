import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

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


def create_app(config=settings, knowledge=None, store=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.knowledge.close()
        await app.state.agents.llm.close()

    app = FastAPI(title="课程智能学习助手", version="0.1.0", lifespan=lifespan)
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

    @app.middleware("http")
    async def local_request_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
            if request.headers.get("X-Learning-Client") != "workbench":
                return JSONResponse({"detail": "缺少本地工作台请求标识。"}, status_code=403)
            origin = request.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{config.app_port}",
                f"http://localhost:{config.app_port}",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ):
                return JSONResponse({"detail": "不允许的请求来源。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

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
            "search_ready": bool(config.tavily_api_key),
            "search_used": app.state.store.search_usage(),
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
    async def new_practice(body: PracticeInput):
        ensure_idle()
        ensure_course(body.course_id)
        async with app.state.lock:
            run = new_run(config, "practice")
            run["dataset_version"] = app.state.knowledge.corpus.version
            start = time.perf_counter()
            try:
                result = await app.state.agents.create_practice(body, run)
                run["status"] = "completed"
                return public_practice(result)
            except Exception as exc:
                run["status"] = "failed"
                if isinstance(exc, ValueError):
                    raise
                raise HTTPException(502, "生成练习失败，请检查模型服务后重试。") from None
            finally:
                run["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
                app.state.store.trace(run)

    @app.post("/api/practices/{practice_id}/submit")
    async def submit(practice_id: str, body: Submission):
        ensure_idle()
        async with app.state.lock:
            p = app.state.store.practice(practice_id)
            if p["result"]:
                return public_practice(p)
            run = new_run(config, "assessment")
            run["dataset_version"] = app.state.knowledge.corpus.version
            start = time.perf_counter()
            try:
                result = await app.state.agents.grade(p, body.answers, run)
                app.state.store.finish_practice(practice_id, result)
                run["status"] = "completed"
                return public_practice(app.state.store.practice(practice_id))
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
