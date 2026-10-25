"""Create a one-file Dify CSV, excluding auxiliary questions from evidence."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def export_csv(source: Path, destination: Path, manifest: Path | None = None) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or (manifest and manifest.resolve() in (source, destination)):
        raise ValueError("Source, destination and manifest must be different files")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        required = {"chunk_id", "chunk_text", "doc_id", "doc_name", "subtopic"}
        if not required <= set(columns):
            raise ValueError("Missing required course columns")
        rows = list(reader)
    if not rows or any(not row["chunk_id"] or not row["chunk_text"].strip() for row in rows):
        raise ValueError("Empty course data or missing source ID/body")
    if len({row["chunk_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate source IDs")
    contents = [f"来源编号：{r['chunk_id']}\n课程编号：{r['doc_id']}\n课程：{r['doc_name']}\n"
                f"主题：{r['subtopic']}\n正文：\n{r['chunk_text']}" for r in rows]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["content"])
        writer.writeheader()
        writer.writerows({"content": content} for content in contents)
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        exported = list(csv.DictReader(stream))
    if [r["content"] for r in exported] != contents:
        raise ValueError("CSV round-trip failed")
    if hashlib.sha256(source.read_bytes()).hexdigest() != before:
        raise ValueError("Source changed while exporting")
    report = {"rows": len(rows), "source_sha256": before,
              "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "output_columns": ["content"], "source_body_preserved": True,
              "auxiliary_questions_exported": False,
              "min_record_chars": min(map(len, contents)), "max_record_chars": max(map(len, contents)),
              "bytes": destination.stat().st_size, "cloud_import_verified": False}
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_csv(args.source, args.destination, args.manifest), ensure_ascii=False))
