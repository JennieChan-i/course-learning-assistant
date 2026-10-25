"""Paste into a Dify Code node; input body:string, output context_json:string."""

import json


def main(body: str) -> dict:
    payload = json.loads(body)
    if payload.get("ok") is not True or not isinstance(payload.get("context"), dict):
        raise ValueError("受控上下文读取失败。")
    return {"context_json": json.dumps(payload["context"], ensure_ascii=False)}
