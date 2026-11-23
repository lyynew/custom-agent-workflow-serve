"""多模态输入：图片校验，以及运行时 content blocks 的拼装。

只做「把外部图片转成模型能吃的形状」这一件事：

- `url`：只接受 http/https，由模型服务自己去取图；
- `base64`：解码一次、按魔数（magic bytes）核对真实格式、限制大小，再重新编码成
  `data:image/...;base64,...`。OpenAI 兼容接口的 `image_url.url` 既吃公网地址也吃
  data URL，所以两种来源最终都归一成一串 URL。

运行时（Aegra → LangGraph）收到的是 OpenAI 风格的 content blocks，与
`HumanMessage(content=[...])` 完全一致，业务层不需要再做转换。

图片本身**不入库**：base64 动辄几 MB，塞进 PG 会让消息表和备份一起失控。落库只留
文字加一行「（附图 N 张）」，真正的图片留存需要对象存储（S3/MinIO），见文档。
"""

import base64
import binascii
import re
from collections.abc import Sequence
from typing import Any

MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MAX_URL_CHARS = 2048

#: 只留文字时的占位，避免整条消息落库为空。
IMAGE_ONLY_PLACEHOLDER = "（图片）"

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.*)$",
    re.DOTALL | re.IGNORECASE,
)
#: 常见误写：`image/jpg` 不是合法 MIME，但调用方经常这么传。
_MIME_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png"}


class ImageInputError(ValueError):
    """图片参数不合法；schema 层会把它变成 422。"""


def _sniff_mime(raw: bytes) -> str | None:
    """按文件头判断真实格式，不信任调用方声明的 mime。"""
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _normalise_mime(value: str | None) -> str | None:
    if not value:
        return None
    mime = value.strip().lower().split(";")[0]
    return _MIME_ALIASES.get(mime, mime)


def normalise_image(*, url: str | None, base64_data: str | None, mime_type: str | None) -> str:
    """把一次图片入参归一成 `image_url` 能直接用的字符串。

    Raises:
        ImageInputError: 二选一没满足、格式不支持、体积超限、base64 损坏。
    """
    url_text = (url or "").strip()
    data_text = (base64_data or "").strip()

    if bool(url_text) == bool(data_text):
        raise ImageInputError("图片必须且只能提供 url 或 base64 之一")

    if url_text:
        if len(url_text) > MAX_URL_CHARS:
            raise ImageInputError(f"图片 url 过长（上限 {MAX_URL_CHARS} 字符）")
        if not url_text.lower().startswith(("http://", "https://")):
            raise ImageInputError("图片 url 必须以 http:// 或 https:// 开头")
        return url_text

    declared = _normalise_mime(mime_type)
    payload = data_text
    header = _DATA_URL_RE.match(data_text)
    if header:
        header_mime = _normalise_mime(header.group("mime"))
        if declared and header_mime and declared != header_mime:
            raise ImageInputError("mime_type 与 base64 前缀中的类型不一致")
        declared = declared or header_mime
        payload = header.group("data")

    payload = "".join(payload.split())
    if not payload:
        raise ImageInputError("图片 base64 内容为空")
    # 先按长度粗筛，避免为了报错去解码一个几十 MB 的字符串。
    if len(payload) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        raise ImageInputError(f"图片过大（上限 {MAX_IMAGE_BYTES // 1024 // 1024} MB）")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageInputError("图片 base64 不是合法的编码内容") from exc

    if not raw:
        raise ImageInputError("图片 base64 内容为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageInputError(f"图片过大（上限 {MAX_IMAGE_BYTES // 1024 // 1024} MB）")

    actual = _sniff_mime(raw)
    if actual is None:
        raise ImageInputError("无法识别的图片格式，只支持 jpeg/png/webp/gif")
    if actual not in ALLOWED_IMAGE_MIME_TYPES:
        raise ImageInputError("只支持 jpeg/png/webp/gif 格式的图片")
    if declared and declared != actual:
        raise ImageInputError(f"mime_type 声明为 {declared}，实际格式为 {actual}")

    return f"data:{actual};base64,{base64.b64encode(raw).decode()}"


def build_runtime_content(text: str, image_data_urls: Sequence[str]) -> str | list[dict[str, Any]]:
    """没有图片时保持纯字符串（老路径完全不变）；有图片时拼 content blocks。"""
    if not image_data_urls:
        return text

    blocks: list[dict[str, Any]] = []
    if text.strip():
        blocks.append({"type": "text", "text": text})
    blocks.extend({"type": "image_url", "image_url": {"url": url}} for url in image_data_urls)
    return blocks


def stored_user_text(text: str, image_count: int) -> str:
    """落库用的文本：图片不进库，只留一行计数，前端据此展示附件条。"""
    base = text.strip() or IMAGE_ONLY_PLACEHOLDER
    if not image_count:
        return base
    return f"{base}\n\n（附图 {image_count} 张）"


def title_text(text: str, image_count: int) -> str:
    return text.strip() or (IMAGE_ONLY_PLACEHOLDER if image_count else "")
