"""Local, bounded evidence snapshots. Credentials and reasoning content never enter storage."""

import hashlib
import json
import re
import subprocess
from functools import lru_cache

from .config import ROOT


@lru_cache(maxsize=1)
def build_info():
    files = (
        sorted((ROOT / "backend").glob("*.py"))
        + sorted((ROOT / "skills").rglob("*.md"))
        + sorted((ROOT / "skills").rglob("manifest.json"))
        + [ROOT / "models.lock.json"]
    )
    hashes = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for p in files
    }
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unavailable"
    return {
        "commit": commit,
        "source_fingerprint": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "files": hashes,
    }


def clean(value, config=None):
    blocked = {
        "api_key",
        "llm_api_key",
        "tavily_api_key",
        "authorization",
        "reasoning_content",
        "reasoning",
        "analysis",
    }
    # `reasoning` in a grade is a numeric rubric dimension, not model chain-of-thought.
    if isinstance(value, dict):
        return {
            str(k): clean(v, config)
            for k, v in value.items()
            if str(k).lower() not in blocked or (k == "reasoning" and isinstance(v, int))
        }
    if isinstance(value, (list, tuple)):
        return [clean(v, config) for v in value]
    if isinstance(value, str):
        if config:
            for secret in (config.llm_api_key, config.tavily_api_key):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return re.sub(
            r"(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})",
            "[REDACTED]",
            value,
        )
    return value


def start_snapshot(run, config, request, subject=None):
    revisions = {}
    for name in (config.embedding_model, config.rerank_model):
        manifest = ROOT / "models" / name.replace("/", "--") / "download-manifest.json"
        try:
            revisions[name] = json.loads(manifest.read_text(encoding="utf-8"))["revision"]
        except (OSError, ValueError, KeyError):
            revisions[name] = "unverified"
    run["snapshot"] = {
        "schema_version": 1,
        "kind": run["kind"],
        "mode": config.app_mode,
        "dataset": config.dataset,
        "request": clean(request, config),
        "subject": subject or {},
        "memory": {},
        "source_pool": {},
        "retrievals": [],
        "llm_calls": [],
        "build": build_info(),
        "configuration": {
            "model": config.llm_model,
            "generation_parameters": run["generation_parameters"],
            "embedding_model": config.embedding_model,
            "rerank_model": config.rerank_model,
            "local_model_revisions": revisions,
        },
    }


def capture_memory(run, **values):
    if "snapshot" in run:
        run["snapshot"]["memory"].update(values)


def capture_sources(run, sources, retrieval=None):
    if "snapshot" not in run:
        return
    snapshot = run["snapshot"]
    for source in sources:
        if len(snapshot["source_pool"]) < 80:
            snapshot["source_pool"][source["id"]] = source
    if retrieval and len(snapshot["retrievals"]) < 12:
        snapshot["retrievals"].append(retrieval)


def capture_call(run, messages, role, result=None):
    if "snapshot" not in run:
        return
    calls = run["snapshot"]["llm_calls"]
    if len(calls) < 24:
        entry = {"agent": role, "messages": clean(messages), "output": clean(result)}
        calls.append(entry)
        return entry


def finish_snapshot(run, config):
    if "snapshot" not in run:
        return clean(run, config)
    snapshot = run["snapshot"]
    snapshot["dataset_version"] = run.get("dataset_version")
    snapshot["skill_versions"] = run["skill_versions"]
    snapshot["prompt_version"] = run["prompt_version"]
    run = clean(run, config)
    run["snapshot"].pop("digest", None)
    encoded = json.dumps(run["snapshot"], ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > 1_000_000:
        # Keep user-visible evidence and context; trace expansion must not cause unbounded storage.
        run["snapshot"]["llm_calls"] = []
        run["snapshot"]["trace_truncated"] = True
    run["snapshot"]["digest"] = hashlib.sha256(
        json.dumps(run["snapshot"], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return run
