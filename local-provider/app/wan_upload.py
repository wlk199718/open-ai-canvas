import base64
import hashlib
import hmac
import io
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, ImageOps

from .reference_upload import MAX_IMAGE_BYTES, image_content_type
from .wan import public_https


def image_payload(path, compress):
    content = path.read_bytes()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Wan 参考图超过 20 MB")
    media_type = image_content_type(content)
    if not compress:
        return content, media_type
    with Image.open(io.BytesIO(content)) as original:
        if original.width * original.height > 40_000_000:
            raise ValueError("参考图像素数量过大")
        image = ImageOps.exif_transpose(original)
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        transparent = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
        output = io.BytesIO()
        image.convert("RGBA" if transparent else "RGB").save(output, format="PNG" if transparent else "JPEG", quality=85, optimize=True)
        data = output.getvalue()
        if len(data) < len(content) or max(original.size) > 2048:
            return data, "image/png" if transparent else "image/jpeg"
        return content, media_type


def upload_reference(client, path):
    content, media_type = image_payload(path, client.compress_images)
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]
    presign = client.request("POST", "/api/uploads/presign", timeout=client.upload_timeout, json={
        "filename": path.stem + extension, "content_type": media_type,
        "size_bytes": len(content), "purpose": "generic", "upload_mode": "multipart",
    })
    bucket = str(presign.get("bucket") or "")
    object_key = str(presign.get("object_key") or presign.get("storage_key") or "").lstrip("/")
    access_key = str(presign.get("access_key_id") or "")
    secret = str(presign.get("secret_access_key") or "")
    token = str(presign.get("session_token") or "")
    if not all((bucket, object_key, access_key, secret, token)):
        raise ValueError("Wan 上传预签名缺少临时凭据")
    endpoint = urlsplit(presign.get("endpoint") or "https://oss-accelerate.aliyuncs.com")
    upload_url = presign.get("upload_url") or f"{endpoint.scheme}://{bucket}.{endpoint.netloc}/{object_key}"
    public_https(upload_url)

    def signed_request(method, body=b"", query=(), content_type=media_type):
        parsed = urlsplit(upload_url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True) + list(query)
        resource = f"/{bucket}/{object_key}"
        signed = sorted((key, value) for key, value in pairs if key in {"uploads", "uploadId", "partNumber"})
        if signed:
            resource += "?" + "&".join(f"{key}={value}" if value else key for key, value in signed)
        date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
        headers = {"Content-Type": content_type, "x-oss-date": date, "x-oss-security-token": token}
        canonical = "".join(f"{key}:{value}\n" for key, value in sorted(headers.items()) if key.startswith("x-oss-"))
        value = f"{method}\n\n{content_type}\n{date}\n{canonical}{resource}"
        signature = base64.b64encode(hmac.new(secret.encode(), value.encode(), hashlib.sha1).digest()).decode()
        headers["Authorization"] = f"OSS {access_key}:{signature}"
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ""))
        # The media session has no account Authorization/Cookie headers.
        response = client.media_session.request(method, url, data=body, headers=headers,
                                                timeout=client.phase_timeout(client.upload_timeout), allow_redirects=False)
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("Wan 对象存储重定向被拒绝")
        return response

    part_size = 5 * 1024 * 1024
    if len(content) <= part_size:
        signed_request("PUT", content)
    else:
        response = signed_request("POST", query=[("uploads", "")])
        try:
            xml = ET.fromstring(response.content)
            upload_id = next(node.text for node in xml.iter() if node.tag.rsplit("}", 1)[-1] == "UploadId")
            if not upload_id:
                raise ValueError()
        except (ET.ParseError, StopIteration, ValueError):
            raise ValueError("Wan 分片上传初始化失败") from None
        try:
            parts = ET.Element("CompleteMultipartUpload")
            for index, offset in enumerate(range(0, len(content), part_size), start=1):
                response = signed_request("PUT", content[offset:offset + part_size], [("partNumber", str(index)), ("uploadId", upload_id)])
                etag = response.headers.get("ETag")
                if not etag:
                    raise ValueError("Wan 分片上传未返回 ETag")
                part = ET.SubElement(parts, "Part")
                ET.SubElement(part, "PartNumber").text = str(index)
                ET.SubElement(part, "ETag").text = etag
            signed_request("POST", ET.tostring(parts), [("uploadId", upload_id)], "application/xml")
        except Exception:
            try:
                signed_request("DELETE", query=[("uploadId", upload_id)])
            except Exception:
                pass
            raise
    result = presign.get("file_url") or presign.get("url") or f"https://cdn.vivareel.ai/{object_key}"
    return public_https(result)
