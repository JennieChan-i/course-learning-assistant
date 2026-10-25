"""K02: turn real retrieval results into bounded, numbered evidence."""
import json
import re


def main(results: list) -> dict:
    evidence, seen = [], set()
    for item in results or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        match = re.search(r"来源编号[：:]\s*([A-Za-z0-9_.:-]{1,80})", content)
        source_id = match.group(1) if match else "K" + str(len(evidence) + 1)
        if source_id in seen:
            continue
        seen.add(source_id)
        metadata = item.get("metadata") or {}
        evidence.append({"source_id": source_id,
                         "title": str(item.get("title") or metadata.get("document_name") or source_id),
                         "text": content[:2400], "truncated": len(content) > 2400})
        if len(evidence) == 6:
            break
    return {"evidence": json.dumps(evidence, ensure_ascii=False),
            "catalog": json.dumps([{"source_id": x["source_id"], "title": x["title"]} for x in evidence], ensure_ascii=False),
            "source_ids": json.dumps([x["source_id"] for x in evidence]),
            "has_evidence": "yes" if evidence else "no"}
