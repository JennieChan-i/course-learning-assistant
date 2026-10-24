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

    async def search(self, query, include_domains=None):
        if not self.settings.tavily_api_key and not self.settings.tavily_allow_keyless:
            return {"sources": [], "warning": "未配置免费搜索密钥，本次未联网核查。"}
        headers = (
            {"Authorization": f"Bearer {self.settings.tavily_api_key}"}
            if self.settings.tavily_api_key
            else {"X-Tavily-Access-Mode": "keyless"}
        )
        try:
            try:
                self.store.reserve_search(self.settings.search_monthly_limit)
            except ValueError:
                return {"sources": [], "warning": "本项目本月搜索上限已用完，本次未完成联网核查。"}
            async with asyncio.timeout(15):
                async with httpx.AsyncClient(headers=headers, timeout=12) as http:
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
                            tool = next(t for t in advertised.tools if t.name == name)
                            properties = tool.inputSchema.get("properties", {})
                            if not {"query", "search_depth", "max_results"} <= set(properties):
                                raise ValueError("搜索工具不支持可控制的基础搜索参数。")
                            if include_domains and "include_domains" not in properties:
                                raise ValueError("搜索工具不支持指定站点范围。")
                            requested = {
                                "query": query,
                                "search_depth": "basic",
                                "max_results": 5,
                                "include_images": False,
                                "include_raw_content": False,
                                "include_answer": False,
                                "auto_parameters": False,
                            }
                            if include_domains:
                                requested["include_domains"] = include_domains
                            result = await session.call_tool(
                                name,
                                {k: v for k, v in requested.items() if k in properties},
                            )
                            if result.isError:
                                raise ValueError("搜索服务返回错误。")
                            text = "\n".join(c.text for c in result.content if c.type == "text")
                            payload = json.loads(text)
            if payload.get("code") or payload.get("error"):
                return {
                    "sources": [],
                    "warning": "搜索服务的免费额度已用完或请求受限；可等待恢复或配置自己的免费 API Key。本次未完成联网核查。",
                }
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
