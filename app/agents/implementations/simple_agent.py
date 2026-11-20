"""The shortest agent: LangChain's built-in `create_agent`.

Three things go in — model, tools, system prompt — and a runnable graph comes
out. There is no hand-written state, node or edge; `create_agent` builds the
plan/tool loop for you.

Use this when "model + tools + prompt" is all you need. Use the hand-written
`assistant_agent.py` instead when you need custom routing, your own state
fields, or a provider without native tool calling.

Note the module-level `graph = build`: Aegra accepts a zero-argument factory and
calls it once at startup, which is how the model can be resolved from
`biz.model_configs` (a database read) before the graph is compiled.
"""

from typing import Any

from langchain.agents import create_agent

from app.agents.tools import TOOLS
from app.config import get_settings
from app.services.models import model_resolver

SYSTEM_PROMPT = (
    "你是企业内部助手。需要精确结果时优先调用工具，不要凭记忆做算术或猜测当前时间。"
    "确认信息不足时直接说明，不要编造。回答简洁。"
)

#: Providers whose models implement `bind_tools`. Passing tools to a model that
#: does not support them fails at build time, so the demo provider gets none.
TOOL_CALLING_PROVIDERS = frozenset({"openai"})


async def build() -> Any:
    resolved = await model_resolver.resolve_full(get_settings().model_config_for("simple_agent"))
    tools = TOOLS if resolved.provider in TOOL_CALLING_PROVIDERS else None

    return create_agent(
        model=resolved.model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        name="simple_agent",
    )


graph = build
