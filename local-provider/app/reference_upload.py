import base64
import binascii
import re
from pathlib import Path

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def image_content_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("图片内容不是支持的 PNG、JPEG 或 WebP 格式")


def image_file_type(path: Path) -> tuple[str, str]:
    with path.open("rb") as source:
        media_type = image_content_type(source.read(12))
    return media_type, IMAGE_TYPES[media_type]


def save_inline_references(root: Path, task_id: str, references: list[str]) -> list[str]:
    decoded = []
    for value in references:
        if not value.startswith("data:"):
            if not Path(value).is_file():
                raise ValueError("参考图需要上传图片内容或使用已存在的本机文件")
            decoded.append((value, None, None))
            continue
        match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)", value)
        if not match or len(match[2]) > (MAX_IMAGE_BYTES * 4 // 3 + 1024):
            raise ValueError("参考图格式无效或超过 20 MB")
        try:
            content = base64.b64decode(match[2], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("参考图 Base64 编码无效") from None
        try:
            valid = image_content_type(content) == match[1]
        except ValueError:
            valid = False
        if not valid or len(content) > MAX_IMAGE_BYTES:
            raise ValueError("参考图内容与图片格式不匹配或超过 20 MB")
        decoded.append((None, content, IMAGE_TYPES[match[1]]))
    result = []
    for index, (existing, content, extension) in enumerate(decoded):
        if existing:
            result.append(existing)
        else:
            target = root / "references" / task_id / f"{index}{extension}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            result.append(str(target))
    return result
