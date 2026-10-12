from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_COOKIE_BYTES = 2 * 1024 * 1024
REQUIRED_COOKIES = ("JWT", "SSUID")
VIDEO_VAULT_MAGIC = b"VIDUVLT\x01"


def _read_json(filename: str, blob: bytes) -> Any:
    if len(blob) > MAX_COOKIE_BYTES:
        raise ValueError("Cookie 文件过大")
    try:
        return json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Cookie 文件无法解析") from exc


def extract_cookie_account(filename: str, blob: bytes, domain_marker: str = "") -> tuple[str, dict[str, str]]:
    if blob.startswith(VIDEO_VAULT_MAGIC):
        raise ValueError("该文件是 custom-video 账号包，请通过 custom-video 账号包导入")
    value = _read_json(filename, blob)
    if isinstance(value, dict):
        if isinstance(value.get("cookies"), list):
            value = value["cookies"]
        elif isinstance(value.get("cookie_dict"), dict):
            value = value["cookie_dict"]
        else:
            cookie_dict = {str(key): str(item) for key, item in value.items() if item not in (None, "")}
            value = cookie_dict

    cookie_dict: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if domain_marker and domain_marker not in str(item.get("domain") or ""):
                continue
            cookie_dict[str(item["name"])] = str(item.get("value") or "")
    elif isinstance(value, dict):
        cookie_dict = {str(key): str(item) for key, item in value.items()}
    else:
        raise ValueError("Cookie 文件必须是数组或对象")

    missing = [key for key in REQUIRED_COOKIES if not cookie_dict.get(key)]
    if missing:
        raise ValueError(f"缺少登录 Cookie：{'、'.join(missing)}")
    name = Path(filename).stem[:100] or "Cookie 账号"
    return name, cookie_dict


def _video_vault_key_candidates():
    configured = os.getenv("ZAOWU_PROVIDER_VIDEO_ACCOUNT_KEY", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        Path(r"E:\work\plugin-sources\custom-video") / "account_vault.key",
        Path(r"D:\ZZDH\_internal\plugins\video_plugins\custom-video") / "account_vault.key",
    ])
    return [path for path in candidates if path.is_file()]


def _decrypt_video_vault(blob: bytes, key_path: Path) -> list[dict[str, Any]]:
    key = key_path.read_bytes()
    if len(key) != 32 or len(blob) < len(VIDEO_VAULT_MAGIC) + 12 + 16:
        raise ValueError("custom-video 账号包密钥或格式无效")
    nonce_start = len(VIDEO_VAULT_MAGIC)
    try:
        value = AESGCM(key).decrypt(
            blob[nonce_start:nonce_start + 12],
            blob[nonce_start + 12:],
            VIDEO_VAULT_MAGIC,
        )
        payload = json.loads(value)
    except (InvalidTag, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("custom-video 账号包密钥不匹配或文件损坏") from exc
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(accounts, list) or not all(isinstance(item, dict) for item in accounts):
        raise ValueError("custom-video 账号包内容格式无效")
    return accounts


def extract_cookie_accounts(filename: str, blob: bytes, domain_marker: str = "") -> list[tuple[str, dict[str, str]]]:
    if not blob.startswith(VIDEO_VAULT_MAGIC):
        return [extract_cookie_account(filename, blob, domain_marker)]
    keys = _video_vault_key_candidates()
    if not keys:
        raise ValueError("未找到 custom-video account_vault.key")
    records = _decrypt_video_vault(blob, keys[0])
    result = []
    for index, record in enumerate(records, 1):
        cookies = record.get("full_cookies")
        name = str(record.get("name") or record.get("email") or f"{Path(filename).stem}-{index}")[:100]
        if not isinstance(cookies, list):
            raise ValueError(f"账号 {name} 缺少 full_cookies")
        cookie_dict = {
            str(item.get("name")): str(item.get("value") or "")
            for item in cookies
            if isinstance(item, dict) and item.get("name")
            and (not domain_marker or domain_marker in str(item.get("domain") or ""))
        }
        missing = [key for key in REQUIRED_COOKIES if not cookie_dict.get(key)]
        if missing:
            raise ValueError(f"账号 {name} 缺少登录 Cookie：{'、'.join(missing)}")
        result.append((name, cookie_dict))
    return result


def encode_cookie_record(cookie_dict: dict[str, str]) -> str:
    return json.dumps({"cookies": cookie_dict}, ensure_ascii=False, sort_keys=True)


def decode_cookie_record(secret: str) -> dict[str, str]:
    try:
        record = json.loads(secret)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("已保存的 Cookie 账号格式无效") from None
    cookies = record.get("cookies") if isinstance(record, dict) else None
    if not isinstance(cookies, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in cookies.items()
    ):
        raise ValueError("已保存的 Cookie 账号格式无效")
    if any(not cookies.get(key, "").strip() for key in REQUIRED_COOKIES):
        raise ValueError("已保存的账号缺少登录 Cookie")
    return cookies
