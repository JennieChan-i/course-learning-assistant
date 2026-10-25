"""Normalize actual MCP text/JSON outputs; empty results bypass the LLM."""
import json


def main(text: str, payload: list) -> dict:
    queue, results, urls, error = [payload, text], [], set(), False
    steps = 0
    while queue and steps < 250:
        item = queue.pop(0)
        steps += 1
        if isinstance(item, str):
            value = item.strip()
            if value.startswith(("{", "[")) and len(value) < 150000:
                try:
                    queue.append(json.loads(value))
                except ValueError:
                    pass
        elif isinstance(item, list):
            queue.extend(item[:30])
        elif isinstance(item, dict):
            if item.get("isError") or item.get("error"):
                error = True
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")) and url not in urls:
                urls.add(url)
                results.append({"title": str(item.get("title", ""))[:200], "url": url,
                                "content": str(item.get("content") or item.get("snippet") or "")[:1400]})
            queue.extend(item.values())
    results = results[:5]
    return {"evidence": json.dumps(results, ensure_ascii=False),
            "has_results": "yes" if results else "no",
            "status": "ok" if results else ("tool_error" if error else "empty"),
            "notice": "本轮没有获得可用的外部证据，未完成联网核查。可以继续问课程；不能据此推断官方没有相关功能或文档。"}
