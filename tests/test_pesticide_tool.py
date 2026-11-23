"""`PesticideRecommendation`：输入归一、模型调用与失败降级。"""

import pytest
from langchain_core.messages import AIMessage

from app.agents import pesticide
from app.agents.pesticide import TOOL_NAME, recommend_pesticides
from app.services.models import ModelConfigError

MODEL_REPLY = (
    "可能病害：小麦赤霉病\n"
    "药剂类别：三唑类杀菌剂、苯并咪唑类\n"
    "有效成分：戊唑醇、咪鲜胺、氰烯菌酯\n"
    "检索关键词：小麦 赤霉病 登记药剂；小麦 赤霉病 戊唑醇 安全间隔期"
)


class _FakeModel:
    def __init__(self, reply: str = MODEL_REPLY, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.seen: list[list] = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return AIMessage(content=self.reply)


def _patch_model(monkeypatch, fake: _FakeModel) -> _FakeModel:
    async def _resolve():
        return fake

    monkeypatch.setattr(pesticide, "_resolve_model", _resolve)
    return fake


def test_tool_name_matches_what_the_prompt_asks_for():
    assert recommend_pesticides.name == "PesticideRecommendation"
    assert TOOL_NAME == "PesticideRecommendation"


def test_growth_stage_is_optional():
    schema = recommend_pesticides.args_schema.model_json_schema()

    assert set(schema["required"]) == {"crop", "symptoms"}
    assert "growth_stage" in schema["properties"]


async def test_returns_the_model_keywords(monkeypatch):
    fake = _patch_model(monkeypatch, _FakeModel())

    output = await recommend_pesticides.ainvoke(
        {"crop": "小麦", "symptoms": "穗部发白、有粉红色霉层", "growth_stage": "抽穗扬花期"}
    )

    assert "小麦赤霉病" in output
    assert "戊唑醇" in output
    # 用量/安全间隔期这类结论必须留给登记信息核实，工具不能自己给。
    assert "须按当地登记和标签确认" in output

    prompt = fake.seen[0][1].content
    assert "作物：小麦" in prompt
    assert "生长期：抽穗扬花期" in prompt
    assert "症状：穗部发白、有粉红色霉层" in prompt


async def test_stage_can_be_omitted(monkeypatch):
    fake = _patch_model(monkeypatch, _FakeModel())

    output = await recommend_pesticides.ainvoke({"crop": "玉米", "symptoms": "叶片有灰白色斑点"})

    assert "生长期未注明" in output
    assert "生长期" not in fake.seen[0][1].content


async def test_inputs_are_trimmed_and_truncated(monkeypatch):
    fake = _patch_model(monkeypatch, _FakeModel())

    await recommend_pesticides.ainvoke({"crop": "  水   稻  ", "symptoms": "x" * 500})

    prompt = fake.seen[0][1].content
    assert "作物：水 稻" in prompt
    assert len(prompt) < 500


@pytest.mark.parametrize(
    "payload",
    [
        {"crop": "", "symptoms": "叶斑"},
        {"crop": "小麦", "symptoms": "   "},
    ],
)
async def test_missing_required_input_is_rejected_without_calling_the_model(monkeypatch, payload):
    fake = _patch_model(monkeypatch, _FakeModel())

    output = await recommend_pesticides.ainvoke(payload)

    assert output.startswith("推荐失败")
    assert fake.seen == []


async def test_model_config_error_degrades_to_generic_search_words(monkeypatch):
    _patch_model(monkeypatch, _FakeModel(error=ModelConfigError("demo 不存在")))

    output = await recommend_pesticides.ainvoke({"crop": "小麦", "symptoms": "叶斑"})

    assert output.startswith("农药关键词推荐失败")
    assert "小麦 叶斑 是什么病" in output


async def test_unexpected_provider_error_degrades_too(monkeypatch):
    _patch_model(monkeypatch, _FakeModel(error=RuntimeError("boom")))

    output = await recommend_pesticides.ainvoke({"crop": "小麦", "symptoms": "叶斑"})

    assert output.startswith("农药关键词推荐失败")
    assert "RuntimeError" in output


async def test_empty_model_reply_degrades(monkeypatch):
    _patch_model(monkeypatch, _FakeModel(reply="   "))

    output = await recommend_pesticides.ainvoke({"crop": "小麦", "symptoms": "叶斑"})

    assert output.startswith("农药关键词推荐失败")
