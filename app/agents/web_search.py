"""Tavily 联网搜索：直接用 LangChain 官方 `langchain-tavily` 集成。

HTTP 请求、参数校验、空结果提示、异常兜底全部由 `TavilySearch` 负责，这里只保留
项目自己决定的三件事：

1. 工具名固定为 `web_search`——提示词、检索预算中间件、测试都按这个名字定位工具；
2. 描述换成中文，告诉模型哪些问题值得联网、查询该怎么组织；
3. 密钥按项目约定从 `env:TAVILY_API_KEY` 解析。缺密钥时返回 `None`，让 Agent 干脆
   不挂载这个工具，而不是启动即崩、或让模型假装查过。
"""

import logging
from typing import Literal

from langchain_core.tools import BaseTool
from langchain_tavily import TavilySearch

from app.auth.errors import SecretResolutionError
from app.auth.secrets import resolve_secret

logger = logging.getLogger(__name__)

SEARCH_TOOL_NAME = "web_search"
TAVILY_SECRET_REF = "env:TAVILY_API_KEY"
MAX_RESULTS = 5
SEARCH_DEPTH: Literal["basic"] = "basic"

SEARCH_TOOL_DESCRIPTION = (
    "联网检索农业相关信息。适合查询政策补贴、天气预警、病虫害情报、农药登记等"
    "会随时间变化的内容。查询写具体：作物 + 地区 + 时间 + 具体问题，"
    "例如「河南 2026 年小麦赤霉病 预警」。"
)


def build_web_search_tool() -> BaseTool | None:
    """按需构建 Tavily 搜索工具；没有密钥时返回 None（Agent 退化为离线回答）。"""
    try:
        api_key = resolve_secret(TAVILY_SECRET_REF)
    except SecretResolutionError:
        logger.warning("%s 未配置，智能体将以离线模式运行（不挂载联网搜索）", TAVILY_SECRET_REF)
        return None

    return TavilySearch(
        name=SEARCH_TOOL_NAME,
        description=SEARCH_TOOL_DESCRIPTION,
        tavily_api_key=api_key,
        max_results=MAX_RESULTS,
        search_depth=SEARCH_DEPTH,
        include_answer=False,
    )
