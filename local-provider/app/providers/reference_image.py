import hashlib
import io
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageOps


def prepare_reference(path: Path) -> tuple[bytes, int, int]:
    content = path.read_bytes()
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError("参考图为空或超过 20 MB")
    with Image.open(io.BytesIO(content)) as original:
        original.seek(0)
        image = ImageOps.exif_transpose(original)
        image.load()
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    width, height = image.size
    if width * height > 3840 * 2160:
        scale = (3840 * 2160 / (width * height)) ** 0.5
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    while True:
        for quality in (86, 80, 74, 68, 60, 52):
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=quality, method=6)
            payload = output.getvalue()
            if len(payload) <= 4 * 1024 * 1024:
                return payload, image.width, image.height
        if image.size == (1, 1):
            raise ValueError("参考图无法压缩到上传限制")
        scale = min(0.9, max(0.5, (4 * 1024 * 1024 / len(payload)) ** 0.5 * 0.95))
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)


def upload_reference(client, path: Path) -> dict:
    payload, width, height = prepare_reference(path)
    upload = client.request(
        "POST", "/tools/v1/files/uploads",
        json={"metadata": {"image-height": str(height), "image-width": str(width)}, "scene": "vidu"},
        timeout=client.upload_timeout,
    )
    upload_id, put_url = upload.get("id"), upload.get("put_url")
    if not upload_id or not isinstance(put_url, str) or not put_url.startswith("https://"):
        raise ValueError("参考图上传初始化缺少 ID 或 HTTPS 地址")
    # Signed media requests must not inherit account Cookie or JSON headers.
    with requests.Session() as media:
        media.trust_env = False
        media.proxies.update(client.session.proxies)
        response = media.put(
            put_url, data=payload,
            headers={"Content-Type": "image/webp", "x-amz-meta-image-height": str(height), "x-amz-meta-image-width": str(width)},
            timeout=client.phase_timeout(client.upload_timeout),
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("参考图上传地址重定向被拒绝")
        etag = (response.headers.get("ETag") or hashlib.md5(payload).hexdigest()).strip('"')
    client.request(
        "PUT", f"/tools/v1/files/uploads/{quote(str(upload_id), safe='')}/finish",
        json={"etag": etag, "id": str(upload_id)}, timeout=client.upload_timeout,
    )
    return {"uri": f"ssupload:?id={upload_id}", "width": width, "height": height}
