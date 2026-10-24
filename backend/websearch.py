import asyncio
import json
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class WebSearch:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    async def search(self, query):
        if not self.settings.tavily_api_key:
            return {"sources": [], "warning": "未配置免费搜索密钥，本次未联网核查。"}
        self.store.reserve_search(self.settings.search_monthly_limit)
        try:
            async with asyncio.timeout(15):
                async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"}, timeout=12
                ) as http:
                    async with streamable_http_client("https://mcp.tavily.com/mcp/", http_client=http) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            advertised = await session.list_tools()
                            names = {t.name for t in advertised.tools}
                            name = next((n for n in ("tavily_search", "tavily-search") if n in names), None)
                            if not name:
                                raise ValueError("搜索 MCP 未提供允许的基础搜索工具。")
                            result = await session.call_tool(
                                name,
                                {
                                    "query": query,
                                    "search_depth": "basic",
                                    "max_results": 5,
                                    "include_images": False,
                                    "include_raw_content": False,
                                    "include_answer": False,
                                    "auto_parameters": False,
                                },
                            )
                            if result.isError:
                                raise ValueError("搜索服务返回错误。")
                            text = "\n".join(c.text for c in result.content if c.type == "text")
                            payload = json.loads(text)
            sources = []
            for item in payload.get("results", [])[:5]:
                url = item.get("url", "")
                if urlparse(url).scheme not in ("https", "http"):
                    continue
                sources.append(
                    {
                        "id": f"W{len(sources) + 1}",
                        "type": "web",
                        "doc_name": item.get("title", url),
                        "url": url,
                        "chunk_text": item.get("content", "")[:1800],
                        "subtopic": "外部搜索资料",
                    }
                )
            return {"sources": sources, "warning": "" if sources else "未获得可用的外部资料。"}
        except Exception:
            # Never expose request headers, credentials, or a provider's raw error text.
            return {"sources": [], "warning": "搜索服务暂不可用或额度不足，本次未完成联网核查。"}
