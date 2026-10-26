import csv
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

import jieba
from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from .config import ROOT, Settings

jieba.setLogLevel(logging.ERROR)
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
FIELDS = (
    "chunk_id",
    "chunk_text",
    "doc_id",
    "doc_name",
    "topic",
    "subtopic",
    "content_type",
    "suitable_questions",
    "chunk_order",
)


def words(text):
    return [s.lower() for s in jieba.lcut(text) if re.search(r"[\w\u4e00-\u9fff]", s)]


class Corpus:
    def __init__(self, path: Path):
        self.path = path
        self.version = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        with path.open(encoding="utf-8-sig", newline="") as f:
            self.rows = list(csv.DictReader(f))
        self.parents = {}
        self.quality = []
        for r in self.rows:
            if any(not r.get(k, "").strip() for k in FIELDS):
                raise ValueError(f"缺少必要字段：{r.get('chunk_id', 'unknown')}")
            if r["chunk_id"] in self.parents:
                raise ValueError(f"重复片段 ID：{r['chunk_id']}")
            self.parents[r["chunk_id"]] = r
            flags = []
            text = r["chunk_text"]
            if len(text) < 120 and re.search("签到|下课|休息|声音.*听|打开麦克风", text):
                flags.append("transactional_excluded")
            if re.search("我们就开始|今天上课|听得到|休息一下", text):
                flags.append("classroom_chatter_review")
            if re.search(r"就是说|那个|然后呢|这个这个", text):
                flags.append("transcription_review")
            if re.search(r"百分之百|100%|一定不会|永远不会|最先进|最新", text):
                flags.append("factual_or_temporal_review")
            if flags:
                self.quality.append({"chunk_id": r["chunk_id"], "flags": flags})
        self.excluded = {q["chunk_id"] for q in self.quality if "transactional_excluded" in q["flags"]}

    def courses(self):
        result = {}
        for r in self.rows:
            result.setdefault(r["doc_id"], {"id": r["doc_id"], "name": r["doc_name"], "count": 0})[
                "count"
            ] += 1
        return list(result.values())

    def source(self, source_id):
        if source_id not in self.parents:
            raise ValueError("来源片段不存在。")
        return {
            **self.parents[source_id],
            "id": source_id,
            "type": "course",
            "version": self.version,
            "quality_flags": next((q["flags"] for q in self.quality if q["chunk_id"] == source_id), []),
        }

    def audit(self):
        return {
            "dataset_version": self.version,
            "parent_count": len(self.rows),
            "courses": self.courses(),
            "excluded_count": len(self.excluded),
            "quality_flags": self.quality,
            "note": "规则筛查不是知识事实核验；疑似问题需要人工复核。",
        }


class LocalModels:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.loaded = False
        self.device = "not_loaded"

    def model_path(self, name):
        return ROOT / "models" / name.replace("/", "--")

    def revision(self, name):
        path = self.model_path(name) / "download-manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))["revision"] if path.exists() else "unverified"

    def load(self):
        with self.lock:
            if self.loaded:
                return
            import torch
            from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

            requested = self.settings.device
            self.device = (
                ("cuda" if torch.cuda.is_available() else "cpu") if requested == "auto" else requested
            )
            if self.device == "cuda" and not torch.cuda.is_available():
                self.device = "cpu"
            ep, rp = (
                self.model_path(self.settings.embedding_model),
                self.model_path(self.settings.rerank_model),
            )
            if not (ep / "config.json").exists() or not (rp / "config.json").exists():
                raise ValueError("本地模型尚未下载。请运行 scripts/download-models.ps1。")
            self.et = AutoTokenizer.from_pretrained(ep, local_files_only=True)
            self.rt = AutoTokenizer.from_pretrained(rp, local_files_only=True)
            dtype = torch.float16 if self.device == "cuda" else torch.float32
            self.em = AutoModel.from_pretrained(ep, local_files_only=True, dtype=dtype).to(self.device).eval()
            self.rm = (
                AutoModelForSequenceClassification.from_pretrained(rp, local_files_only=True, dtype=dtype)
                .to(self.device)
                .eval()
            )
            self.loaded = True

    @staticmethod
    def shorten(tokenizer, text, limit):
        offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, verbose=False)[
            "offset_mapping"
        ]
        return text[: offsets[limit - 1][1]] if len(offsets) > limit else text

    def embed(self, texts, query=False):
        import torch

        self.load()
        prepared = [QUERY_PREFIX + self.shorten(self.et, t, 128) if query else t for t in texts]
        output = []
        with self.lock, torch.inference_mode():
            for start in range(0, len(prepared), self.settings.local_inference_batch_size):
                batch = prepared[start : start + self.settings.local_inference_batch_size]
                if any(len(self.et(t)["input_ids"]) > 512 for t in batch):
                    raise ValueError("Embedding 输入超长，需重新切分。")
                inputs = self.et(batch, padding=True, truncation=False, return_tensors="pt").to(self.device)
                value = self.em(**inputs).last_hidden_state[:, 0]
                value = torch.nn.functional.normalize(value.float(), p=2, dim=1)
                output.extend(value.cpu().numpy().tolist())
        return output

    def rerank(self, query, texts):
        import torch

        self.load()
        query = self.shorten(self.rt, query, 128)
        pairs = [[query, t] for t in texts]
        result = []
        with self.lock, torch.inference_mode():
            for start in range(0, len(pairs), self.settings.local_inference_batch_size):
                batch = pairs[start : start + self.settings.local_inference_batch_size]
                if any(len(self.rt(q, t)["input_ids"]) > 512 for q, t in batch):
                    raise ValueError("Rerank 输入超长，需重新切分。")
                inputs = self.rt(batch, padding=True, truncation=False, return_tensors="pt").to(self.device)
                result.extend(self.rm(**inputs).logits.view(-1).float().cpu().tolist())
        return result

    def split(self, parent):
        self.load()
        text = parent["chunk_text"]
        start = 0
        parts = []
        while start < len(text):
            tail = text[start:]
            offsets = [
                t(tail, add_special_tokens=False, return_offsets_mapping=True, verbose=False)[
                    "offset_mapping"
                ]
                for t in (self.et, self.rt)
            ]
            length = min(o[319][1] if len(o) > 320 else len(tail) for o in offsets)
            end = start + length
            if end < len(text):
                boundaries = list(re.finditer(r"[。！？；\n]", text[start:end]))
                if boundaries and boundaries[-1].end() >= length // 2:
                    end = start + boundaries[-1].end()
            body = text[start:end]
            prefix = self.shorten(self.et, f"{parent['topic']} {parent['subtopic']}", 48)
            parts.append(
                {
                    "id": f"{parent['chunk_id']}:{len(parts)}",
                    "parent_id": parent["chunk_id"],
                    "doc_id": parent["doc_id"],
                    "text": body,
                    "embedding_text": prefix + "\n" + body,
                    "lexical_text": body + " " + parent["suitable_questions"],
                    "start": start,
                    "end": end,
                }
            )
            if end == len(text):
                break
            token_offsets = self.et(body, add_special_tokens=False, return_offsets_mapping=True)[
                "offset_mapping"
            ]
            overlap_start = token_offsets[-48][0] if len(token_offsets) > 48 else len(body)
            start = max(start + 1, start + overlap_start)
        return parts


class Knowledge:
    def __init__(self, settings: Settings, index_override=None):
        self.settings = settings
        self._index_override = index_override
        self.corpus = Corpus(settings.course_path)
        self.models = LocalModels(settings)
        self.client = None
        self.children = []
        self.bm25 = None
        self.lock = threading.RLock()
        if settings.app_mode == "demo":
            self.children = [
                {
                    "id": r["chunk_id"],
                    "parent_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "text": r["chunk_text"],
                    "lexical_text": r["chunk_text"] + r["suitable_questions"],
                }
                for r in self.corpus.rows
                if r["chunk_id"] not in self.corpus.excluded
            ]
            self._lexical()

    @property
    def index_path(self):
        if self._index_override is not None:
            return self._index_override
        tag = self.settings.embedding_model.replace("/", "--")
        return self.settings.runtime / "indexes" / f"{self.corpus.version}-{tag}"

    def _lexical(self):
        self.by_id = {c["id"]: c for c in self.children}
        self.bm25 = BM25Okapi([words(c["lexical_text"]) for c in self.children])

    def build(self):
        self.models.load()
        path = self.index_path
        path.mkdir(parents=True, exist_ok=True)
        manifest_path = path / "manifest.json"
        # A manifest is only written after successful upsert. Failed builds are not served.
        if manifest_path.exists():
            manifest_path.unlink()
        children = [
            c
            for r in self.corpus.rows
            if r["chunk_id"] not in self.corpus.excluded
            for c in self.models.split(r)
        ]
        if not children:
            raise ValueError("没有可索引的课程片段。")
        vectors = self.models.embed([c["embedding_text"] for c in children])
        client = QdrantClient(path=str(path / "vectors"))
        try:
            if client.collection_exists("course"):
                client.delete_collection("course")
            client.create_collection(
                "course",
                vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
            )
            client.upsert(
                "course",
                points=[
                    models.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, c["id"])),
                        vector=v,
                        payload={"child_id": c["id"], "doc_id": c["doc_id"]},
                    )
                    for c, v in zip(children, vectors, strict=True)
                ],
            )
        finally:
            client.close()
        (path / "children.json").write_text(json.dumps(children, ensure_ascii=False), encoding="utf-8")
        manifest = {
            "dataset_version": self.corpus.version,
            "embedding_model": self.settings.embedding_model,
            "rerank_model": self.settings.rerank_model,
            "split_version": 1,
            "embedding_revision": self.models.revision(self.settings.embedding_model),
            "rerank_revision": self.models.revision(self.settings.rerank_model),
            "parents": len(self.corpus.rows),
            "children": len(children),
            "dimension": len(vectors[0]),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (path / "quality-report.json").write_text(
            json.dumps(self.corpus.audit(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def open(self):
        with self.lock:
            if self.client is not None or self.settings.app_mode == "demo":
                return
            path = self.index_path
            if not (path / "manifest.json").exists():
                raise ValueError("知识库尚未构建，请先运行 scripts/build-index.ps1。")
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            if manifest["rerank_model"] != self.settings.rerank_model or manifest["split_version"] != 1:
                raise ValueError("模型或切分版本变化，请重新构建索引。")
            if manifest.get("embedding_revision") != self.models.revision(
                self.settings.embedding_model
            ) or manifest.get("rerank_revision") != self.models.revision(self.settings.rerank_model):
                raise ValueError("模型权重版本变化或旧索引缺少版本，请重新构建索引。")
            self.children = json.loads((path / "children.json").read_text(encoding="utf-8"))
            self._lexical()
            self.client = QdrantClient(path=str(path / "vectors"))

    def search(self, query, course_id=None, limit=6, mode="rerank"):
        started = time.perf_counter()
        with self.lock:
            self.open()
            scores = self.bm25.get_scores(words(query))
            eligible = [i for i, c in enumerate(self.children) if not course_id or c["doc_id"] == course_id]
            lexical = [
                self.children[i]["id"]
                for i in sorted(eligible, key=lambda i: scores[i], reverse=True)[:20]
                if scores[i] > 0
            ]
            ranked = lexical
            dense, fused_order = [], []
            if self.settings.app_mode != "demo" and mode != "bm25":
                vector = self.models.embed([query], query=True)[0]
                filt = (
                    models.Filter(
                        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=course_id))]
                    )
                    if course_id
                    else None
                )
                hits = self.client.query_points("course", query=vector, query_filter=filt, limit=20).points
                dense = [p.payload["child_id"] for p in hits]
                if mode == "dense":
                    ranked = dense
                else:
                    fused = {}
                    for ids in (lexical, dense):
                        for rank, key in enumerate(ids):
                            fused[key] = fused.get(key, 0) + 1 / (60 + rank + 1)
                    ranked = sorted(fused, key=fused.get, reverse=True)[:20]
                    fused_order = list(ranked)
                    if mode == "rerank" and ranked:
                        rerank = self.models.rerank(query, [self.by_id[k]["text"] for k in ranked])
                        ranked = [
                            ranked[i]
                            for i in sorted(range(len(ranked)), key=lambda i: rerank[i], reverse=True)
                        ]
            sources = []
            seen = set()
            for child_id in ranked:
                child = self.by_id[child_id]
                if child["parent_id"] in seen:
                    continue
                seen.add(child["parent_id"])
                sources.append({**self.corpus.source(child["parent_id"]), "matched_text": child["text"]})
                if len(sources) >= limit:
                    break
            return {
                "sources": sources,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "mode": "demo_lexical" if self.settings.app_mode == "demo" else mode,
                "dataset_version": self.corpus.version,
                "diagnostics": {
                    "query": query,
                    "course_id": course_id,
                    "lexical": lexical,
                    "dense": dense,
                    "fused": fused_order,
                    "reranked": list(ranked),
                    "candidates": [
                        {
                            "child_id": k,
                            "parent_id": self.by_id[k]["parent_id"],
                            "text": self.by_id[k]["text"],
                        }
                        for k in dict.fromkeys(lexical + dense)
                    ],
                },
            }

    def close(self):
        if self.client:
            self.client.close()
