import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from .config import ROOT, Settings
from .knowledge import Corpus, Knowledge


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(path))


def download(config, include_base=False):
    from huggingface_hub import HfApi, snapshot_download

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    names = [config.embedding_model, config.rerank_model]
    if include_base:
        names.append("BAAI/bge-base-zh-v1.5")
    for name in dict.fromkeys(names):
        path = ROOT / "models" / name.replace("/", "--")
        lock_path = ROOT / "models.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
        info = HfApi().model_info(name, revision=lock.get(name))
        filenames = [s.rfilename for s in info.siblings]
        weights = (
            ["*.safetensors"]
            if any(n.endswith(".safetensors") and "/" not in n for n in filenames)
            else ["pytorch_model.bin"]
        )
        snapshot_download(
            name,
            revision=info.sha,
            local_dir=path,
            max_workers=2,
            allow_patterns=["*.json", "*.txt", *weights, "*.model", "README.md", "LICENSE"],
            ignore_patterns=["onnx/*", "openvino/*", "1_Pooling/*"],
        )
        if not list(path.glob("*.safetensors")) and not (path / "pytorch_model.bin").exists():
            raise ValueError(f"模型权重缺失：{name}")
        write_report(path / "download-manifest.json", {"model": name, "revision": info.sha})


def diagnostic(config):
    result = {"llm": "not_run: no API call in offline diagnostics", "cuda": "not_run"}
    try:
        import torch

        result.update(torch_version=torch.__version__, cuda_available=torch.cuda.is_available())
        if torch.cuda.is_available():
            x = torch.randn(64, 64, device="cuda")
            result["cuda"] = {
                "device": torch.cuda.get_device_name(),
                "matmul_finite": bool(torch.isfinite(x @ x).all().item()),
            }
        kb = Knowledge(config)
        vectors = kb.models.embed(["课程检索需要相关证据。"])
        scores = kb.models.rerank(
            "重排的作用是什么？", ["重排在召回之后对候选证据重新排序。", "今天中午吃什么？"]
        )
        result.update(embedding_dimension=len(vectors[0]), rerank_scores=scores, device=kb.models.device)
        if torch.cuda.is_available():
            result["peak_vram_mb"] = round(torch.cuda.max_memory_allocated() / 1024**2, 1)
        kb.close()
    except Exception as e:
        result["error"] = str(e)
    return result


async def api_probe(config):
    from .llm import LLM, new_run

    llm = LLM(config)
    run = new_run(config, "compatibility")
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM configuration missing"}
    try:
        tool = {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Return a word",
                "parameters": {
                    "type": "object",
                    "properties": {"word": {"type": "string"}},
                    "required": ["word"],
                },
            },
        }
        m = await llm.complete(
            [{"role": "user", "content": "请调用 echo，word 设置为 test。"}],
            run,
            "probe",
            [tool],
            {"type": "function", "function": {"name": "echo"}},
        )
        native = bool(m.tool_calls and m.tool_calls[0].function.name == "echo")
        streamed = ""
        async for part in llm.stream([{"role": "user", "content": "请只回复：连接成功"}], run):
            streamed += part
        return {
            "status": "passed" if native and streamed else "failed",
            "native_tool_call": native,
            "stream_content": bool(streamed),
            "usage": run["usage"],
            "model": config.llm_model,
        }
    except Exception:
        return {
            "status": "failed",
            "reason": "API compatibility request failed; check provider configuration",
        }
    finally:
        await llm.close()


def main():
    parser = argparse.ArgumentParser(description="课程助手本地工具")
    parser.add_argument(
        "command",
        choices=[
            "download",
            "audit",
            "index",
            "doctor",
            "probe",
            "eval",
            "grade-eval",
            "answer-eval",
            "serve",
        ],
    )
    parser.add_argument("--dataset", choices=["sample", "private"], default=None)
    parser.add_argument("--embedding", default=None)
    parser.add_argument("--include-base", action="store_true")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--questions", type=Path)
    args = parser.parse_args()
    config = Settings()
    if args.dataset:
        config.dataset = args.dataset
    if args.embedding:
        config.embedding_model = args.embedding
    if args.command != "serve":
        config.app_mode = "live"
    if args.command == "download":
        download(config, args.include_base)
    elif args.command == "audit":
        write_report(config.runtime / "quality-report.json", Corpus(config.course_path).audit())
    elif args.command == "index":
        start = time.perf_counter()
        kb = Knowledge(config)
        report = kb.build()
        report["elapsed_seconds"] = round(time.perf_counter() - start, 2)
        write_report(config.runtime / "index-build.json", report)
        kb.close()
    elif args.command == "doctor":
        write_report(config.runtime / "diagnostics.json", diagnostic(config))
    elif args.command == "probe":
        write_report(config.runtime / "api-compatibility.json", asyncio.run(api_probe(config)))
    elif args.command == "eval":
        from .evaluation import retrieval_eval

        question_path = args.questions or (
            ROOT / "evals" / "retrieval.jsonl"
            if config.dataset == "sample"
            else config.runtime / "evals" / "retrieval.jsonl"
        )
        write_report(
            config.runtime
            / "reports"
            / f"retrieval-{args.split}-{config.embedding_model.split('/')[-1]}.json",
            retrieval_eval(config, question_path, args.split),
        )
    elif args.command == "grade-eval":
        from .evaluation import grading_eval

        write_report(config.runtime / "reports" / "grading-ablation.json", asyncio.run(grading_eval(config)))
    elif args.command == "answer-eval":
        from .evaluation import answer_eval

        question_path = args.questions or (
            ROOT / "evals/retrieval.jsonl"
            if config.dataset == "sample"
            else config.runtime / "evals/retrieval.jsonl"
        )
        write_report(
            config.runtime / "reports" / f"answer-{args.split}.json",
            asyncio.run(answer_eval(config, question_path, args.split)),
        )
    else:
        import uvicorn

        uvicorn.run("backend.main:app", host="127.0.0.1", port=config.app_port, workers=1)


if __name__ == "__main__":
    main()
