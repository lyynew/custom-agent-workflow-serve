"""`PesticideRecommendation`：把「作物 + 生长期 + 症状」翻成可检索的农药关键词。

提示词要求先调这个工具拿到用药方向、再去联网核实登记信息。项目里没有农药登记
数据库，硬造一个只能编数据，所以这里的定位是**关键词生成器**而不是开药方：

    作物/生长期/症状 ──调一次模型──> 病害方向 + 药剂类别 + 有效成分 + 检索式
                                        │
                                        └─ Agent 拿这组词去 web_search 核实

单独一个工具、单独一个模型配置（`MODEL_CONFIG_PESTICIDE_RECOMMENDATION`）的理由：
这一步要稳定的输出格式，且换便宜的小模型、调低温度不该影响主对话。

安全边界写在提示词里，也写在这里：不给商品名、不给用量、不给混配方案、不给安全
间隔期——这些必须由当地登记与标签确认，工具只负责"查什么"。
"""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.config import get_settings
from app.services.models import ModelConfigError, model_resolver

logger = logging.getLogger(__name__)

TOOL_NAME = "PesticideRecommendation"
#: `MODEL_CONFIG_PESTICIDE_RECOMMENDATION` 可单独指定这一步用哪个模型。
CONFIG_KEY = "pesticide_recommendation"

MAX_FIELD_CHARS = 200
MAX_OUTPUT_CHARS = 1200

_INSTRUCTIONS = """你是农药登记与植保用药检索助手。用户会给出作物、生长期（可能缺失）和症状。
你的任务不是开药方，而是把症状翻译成便于检索的农药关键词。

只输出下面 4 行，不要标题、不要解释、不要 markdown 代码块：
可能病害：<最可能的病害或虫害名称，多个用顿号分隔>
药剂类别：<作用机理类别，例如 三唑类杀菌剂、甲氧基丙烯酸酯类>
有效成分：<我国已登记的农药通用名，多个用顿号分隔>
检索关键词：<2-3 条可直接用于搜索的短句，用「；」分隔>

硬约束：
- 只写农药通用名，不写商品名、不写生产企业。
- 不给用量、稀释倍数、施药次数、混配方案和安全间隔期，这些必须由当地登记信息和标签确认。
- 症状不足以判断时，"可能病害"写"信息不足"，其余行仍要给出候选关键词。
- 不使用"特效""根治"等承诺疗效的表述。"""


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())[:MAX_FIELD_CHARS]


def _text_of(message: BaseMessage) -> str:
    content: Any = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


async def _resolve_model() -> BaseChatModel:
    """按配置取模型；测试里可替换掉，不必连数据库。"""
    return await model_resolver.resolve(get_settings().model_config_for(CONFIG_KEY))


def _user_message(crop: str, stage: str, symptoms: str) -> str:
    lines = [f"作物：{crop}"]
    if stage:
        lines.append(f"生长期：{stage}")
    lines.append(f"症状：{symptoms}")
    return "\n".join(lines)


def _fallback(crop: str, symptoms: str, reason: str) -> str:
    """模型不可用时也要给出能直接用的检索词，别让整轮对话卡死。"""
    return (
        f"农药关键词推荐失败（{reason}）。先用下面这组通用检索词继续核实：\n"
        f"检索关键词：{crop} {symptoms} 是什么病；{crop} 登记药剂 防治；"
        f"{crop} 病虫害 防控 通知 当地农业农村局\n"
        f"具体药剂、用量与安全间隔期须按当地登记与标签确认。"
    )


@tool(TOOL_NAME)
async def recommend_pesticides(
    crop: str,
    symptoms: str,
    growth_stage: str | None = None,
) -> str:
    """按作物、生长期和症状推荐农药检索关键词：可能的病害、药剂类别、已登记有效成分。

    只产出检索用的关键词，不提供用量、混配方案和安全间隔期；拿到的关键词应继续用
    web_search 核实登记状态、适用范围与当地防治通知。
    """
    crop_text = _clean(crop)
    symptom_text = _clean(symptoms)
    stage_text = _clean(growth_stage)

    if not crop_text or not symptom_text:
        return "推荐失败：作物和症状都要填，例如「小麦」「穗部发白、有粉红色霉层」。"

    try:
        model = await _resolve_model()
        reply = await model.ainvoke(
            [
                SystemMessage(content=_INSTRUCTIONS),
                HumanMessage(content=_user_message(crop_text, stage_text, symptom_text)),
            ]
        )
    except ModelConfigError as exc:
        return _fallback(crop_text, symptom_text, f"没有可用的模型配置：{exc}")
    except Exception as exc:  # noqa: BLE001 - 工具失败要降级，不能打断整轮
        logger.warning("PesticideRecommendation 调用模型失败: %s", exc)
        return _fallback(crop_text, symptom_text, type(exc).__name__)

    keywords = _text_of(reply).strip()
    if not keywords:
        return _fallback(crop_text, symptom_text, "模型没有返回内容")

    stage_label = stage_text or "生长期未注明"
    return (
        f"【{crop_text} / {stage_label} / {symptom_text}】\n"
        f"{keywords[:MAX_OUTPUT_CHARS]}\n\n"
        "以上关键词仅用于检索：具体药剂、用量、混配与安全间隔期须按当地登记和标签确认。"
    )
