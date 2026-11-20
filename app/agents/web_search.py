"""Tavily-backed web search exposed to agents as a tool.

The tool returns readable text (title / url / snippet) rather than raw JSON: the
model consumes it directly, and an unparseable blob wastes context on braces.
Failures are returned as text too, so a search outage degrades the answer instead
of aborting the run.
"""

import logging
from typing import Any

from langchain_core.tools import tool

from app.auth.errors import SecretResolutionError
from app.auth.secrets import resolve_secret

logger = logging.getLogger(__name__)

TAVILY_SECRET_REF = "env:TAVILY_API_KEY"
MAX_RESULTS = 5
CONTENT_CHARS = 320
SEARCH_TIMEOUT_SECONDS = 20

_client: Any | None = None


def _get_client() -> Any:
    """Lazy singleton: importing the module must not require a key."""
    global _client
    if _client is None:
        from tavily import TavilyClient

        _client = TavilyClient(api_key=resolve_secret(TAVILY_SECRET_REF))
    return _client


def reset_client() -> None:
    """Drop the cached client (tests and key rotation)."""
    global _client
    _client = None


@tool
def web_search(query: str) -> str:
    """联网搜索农业相关信息。适合查询政策补贴、天气预警、病虫害情报、农药登记等时效性内容。

    查询请写具体：作物 + 地区 + 时间 + 具体问题，例如"河南 2026 年小麦赤霉病 预警"。
    """
    text = query.strip()
    if not text:
        return "搜索失败：查询内容为空。"

    try:
        result = _get_client().search(
            query=text,
            search_depth="basic",
            topic="general",
            max_results=MAX_RESULTS,
            include_answer=False,
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except SecretResolutionError:
        logger.warning("Web search requested without TAVILY_API_KEY")
        return "搜索失败：未配置联网搜索密钥，请基于已有知识回答并说明未能联网核实。"
    except Exception as exc:  # noqa: BLE001 - any provider error must not kill the run
        logger.warning("Web search failed: %s", exc)
        return f"搜索失败（{type(exc).__name__}）。请基于已有知识回答并说明未能联网核实。"

    items = result.get("results") or []
    if not items:
        return "未检索到相关结果。"

    lines = [f"“{text}”检索到 {len(items)} 条结果："]
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = " ".join(str(item.get("content") or "").split())[:CONTENT_CHARS]
        published = str(item.get("published_date") or "").strip()
        header = f"{index}. {title}"
        if published:
            header += f"（{published}）"
        lines.append(f"{header}\n   {url}\n   {content}")

    return "\n".join(lines)
