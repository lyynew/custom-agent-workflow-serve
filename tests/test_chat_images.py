"""图片输入：url / base64 归一、content blocks、落库文本与参数校验。"""

import base64
import json

import pytest

from app.services.multimodal import (
    IMAGE_ONLY_PLACEHOLDER,
    build_runtime_content,
    normalise_image,
    stored_user_text,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 32).decode()


def _user(app_id: str = "agriculture_app", user_id: str = "10001"):
    from aegra_api.models.auth import User

    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"],
        allowed_workflows=["*"],
    )


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name: str | None = None
    data: list[str] = []

    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "":
            if name is not None:
                events.append((name, json.loads("\n".join(data) or "{}")))
            name, data = None, []

    return events


def test_text_only_stays_a_plain_string():
    """老路径不能被图片支持影响：没有图片时 content 仍是 str。"""
    assert build_runtime_content("你好", []) == "你好"


def test_text_and_image_become_blocks():
    blocks = build_runtime_content("看图", ["https://example.com/a.jpg"])

    assert blocks == [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}},
    ]


def test_image_only_has_no_empty_text_block():
    blocks = build_runtime_content("   ", ["https://example.com/a.jpg"])

    assert blocks == [{"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}]


def test_base64_is_normalised_to_a_data_url():
    data_url = normalise_image(url=None, base64_data=PNG_B64, mime_type=None)

    assert data_url == f"data:image/png;base64,{PNG_B64}"


def test_base64_dat_url_prefix_is_stripped_and_rebuilt():
    data_url = normalise_image(url=None, base64_data=f"data:image/png;base64,{PNG_B64}", mime_type="image/png")

    assert data_url == f"data:image/png;base64,{PNG_B64}"


def test_base64_accepts_line_wrapped_input():
    wrapped = "\n".join(PNG_B64[i : i + 20] for i in range(0, len(PNG_B64), 20))

    assert normalise_image(url=None, base64_data=wrapped, mime_type=None) == f"data:image/png;base64,{PNG_B64}"


def test_stored_text_keeps_a_count_and_never_the_bytes():
    assert stored_user_text("这叶子怎么了", 2) == "这叶子怎么了\n\n（附图 2 张）"
    assert stored_user_text("", 1) == f"{IMAGE_ONLY_PLACEHOLDER}\n\n（附图 1 张）"
    assert stored_user_text("纯文字", 0) == "纯文字"


async def test_url_image_reaches_the_runtime_as_blocks(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.assistant_text = "看起来像赤霉病"

    response = await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat",
        json={"message": "这叶子怎么了", "images": [{"url": "https://example.com/leaf.jpg"}]},
    )

    assert response.status_code == 200
    content = fake_runtime.wait_calls[0]["input"]["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "这叶子怎么了"},
        {"type": "image_url", "image_url": {"url": "https://example.com/leaf.jpg"}},
    ]


async def test_base64_image_reaches_the_runtime_as_a_data_url(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat",
        json={"message": "看看", "images": [{"base64": PNG_B64}]},
    )

    assert response.status_code == 200
    content = fake_runtime.wait_calls[0]["input"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "看看"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_mixed_sources_keep_their_order(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())

    await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat",
        json={
            "message": "两张图",
            "images": [{"url": "https://example.com/1.jpg"}, {"base64": JPEG_B64}],
        },
    )

    content = fake_runtime.wait_calls[0]["input"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == "https://example.com/1.jpg"
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_image_only_message_is_allowed_and_persisted_with_a_marker(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat",
        json={"images": [{"url": "https://example.com/leaf.jpg"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_message"]["content"] == "（图片）\n\n（附图 1 张）"

    conversation = await asgi_client.get(f"/api/v1/conversations/{body['conversation_id']}")
    assert conversation.json()["title"] == "（图片）"

    # 图片字节从不落库：正文里只有计数。
    assert "base64" not in body["user_message"]["content"]


async def test_stream_endpoint_also_carries_images(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat/stream",
        json={"message": "看图", "images": [{"url": "https://example.com/leaf.jpg"}]},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [name for name, _ in events][0] == "meta"
    assert [name for name, _ in events][-1] == "done"
    content = fake_runtime.stream_calls[0]["input"]["messages"][0]["content"]
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://example.com/leaf.jpg"}}


@pytest.mark.parametrize(
    "images",
    [
        [{"url": "https://example.com/a.jpg", "base64": PNG_B64}],
        [{}],
        [{"url": "file:///tmp/a.jpg"}],
        [{"url": "ftp://example.com/a.jpg"}],
        [{"base64": PNG_B64, "mime_type": "image/jpeg"}],
        [{"base64": base64.b64encode(b"<svg/>").decode()}],
        [{"base64": "not-base64!!!"}],
        [{"url": "https://example.com/a.jpg"}] * 5,
    ],
)
async def test_invalid_images_are_rejected_before_the_run(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, images
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/agents/bingfeng_agent/chat",
        json={"message": "看图", "images": images},
    )

    assert response.status_code == 422
    assert fake_runtime.wait_calls == []


@pytest.mark.parametrize("payload", [{}, {"message": "   "}, {"images": []}])
async def test_empty_text_and_images_is_rejected(seeded_apps, authenticated_as, asgi_client, payload):
    authenticated_as(_user())

    response = await asgi_client.post("/api/v1/agents/bingfeng_agent/chat", json=payload)

    assert response.status_code == 422
