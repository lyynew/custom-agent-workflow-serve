"""兵峰耘农：用 LangChain `create_agent` 组装的农业助手。

模型 + 工具 + 系统提示词交给 `create_agent`，plan/tool 循环、工具调用解析、消息
历史全由框架生成，本文件不再手写 StateGraph 节点和边。

两条项目自己的约束，都挂到框架自带的扩展点上，而不是自己写循环：

* **每轮检索上限**：`ToolCallLimitMiddleware(tool_name="web_search", run_limit=N,
  exit_behavior="continue")`。每次 run 只放行 N 次真实检索，超出的调用会被换成一条
  "已达上限"的 ToolMessage 回给模型，模型据此收口作答；模型一次并行发多个搜索时，
  按数量裁掉超出的部分。只靠提示词写"最多搜 3 次"不可靠，模型会超。
* **当天日期**：用 `@dynamic_prompt` 在每次调模型时重新注入。图在服务启动时构建，
  若把日期写进 `system_prompt`，服务跑过零点就会给模型过期日期。

提示词单独放在 `app/agents/prompts/bingfeng_yunnong.md`，方便产品/农技人员直接改。
"""

from datetime import UTC, datetime
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRequest,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.agents.tools import TOOL_CALLING_PROVIDERS
from app.agents.web_search import SEARCH_TOOL_NAME, build_web_search_tool
from app.config import get_settings
from app.services.models import model_resolver

AGENT_ID = "bingfeng_agent"

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "bingfeng_yunnong.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip()


def _today() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d")


@dynamic_prompt
def _prompt_with_today(request: ModelRequest) -> str:
    """在系统提示词后追加当天日期，避免模型猜年份、搜过期信息。"""
    return f"{SYSTEM_PROMPT}\n\n今天是 {_today()}。"


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool] | None,
    max_searches: int,
) -> CompiledStateGraph:
    """把模型、工具、中间件拼成一个可运行的 Agent 图。

    单独抽出来是为了让测试可以用假模型验证「检索上限」，不必连真实大模型。
    """
    middleware = [_prompt_with_today]
    if tools and max_searches > 0:
        middleware.append(
            ToolCallLimitMiddleware(
                tool_name=SEARCH_TOOL_NAME,
                run_limit=max_searches,
                exit_behavior="continue",
            )
        )

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        name=AGENT_ID,
    )


async def build():
    """Aegra 接受零参工厂，启动时调用一次；模型配置从 `biz.model_configs` 读取。"""
    settings = get_settings()
    resolved = await model_resolver.resolve_full(settings.model_config_for(AGENT_ID))

    tools: list[BaseTool] | None = None
    if resolved.provider in TOOL_CALLING_PROVIDERS and settings.agent_max_web_searches > 0:
        search_tool = build_web_search_tool()
        if search_tool is not None:
            tools = [search_tool]

    return build_agent(model=resolved.model, tools=tools, max_searches=settings.agent_max_web_searches)


graph = build
