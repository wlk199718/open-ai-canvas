from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"WANVLT01"
PRIVATE_KEY_NAME = "account_vault_private.pem"
MAX_VAULT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 256 * 1024


def private_key_candidates() -> list[Path]:
    configured = os.getenv("ZAOWU_PROVIDER_ACCOUNT_PRIVATE_KEY", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        [
            Path(r"D:\ZZDH\_internal\plugins\video_plugins\custom-video-wan") / PRIVATE_KEY_NAME,
            Path(__file__).resolve().parent / PRIVATE_KEY_NAME,
        ]
    )
    return [path for path in candidates if str(path)]


def resolve_private_key() -> Path:
    for candidate in private_key_candidates():
        if candidate.is_file():
            return candidate
    raise ValueError(
        "未找到 Wan 账号解密私钥，请确认 ZZDH 插件目录存在 account_vault_private.pem，"
        "或设置 ZAOWU_PROVIDER_ACCOUNT_PRIVATE_KEY"
    )


def _load_private_key(path: Path):
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError) as exc:
        raise ValueError("Wan 账号解密私钥无效") from exc


def decrypt_vault(blob: bytes, private_key_path: Path) -> dict[str, Any]:
    if len(blob) > MAX_VAULT_BYTES:
        raise ValueError("账号文件过大")
    if len(blob) < 38 or blob[:8] != MAGIC:
        raise ValueError("加密账号格式不支持")
    wrapped_length = int.from_bytes(blob[8:10], "big")
    if wrapped_length < 128 or len(blob) < 22 + wrapped_length + 16:
        raise ValueError("加密账号已损坏")
    prefix = blob[:22]
    wrapped = blob[22:22 + wrapped_length]
    ciphertext = blob[22 + wrapped_length:]
    try:
        key = _load_private_key(private_key_path).decrypt(
            wrapped,
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        value = AESGCM(key).decrypt(prefix[10:22], ciphertext, prefix)
        record = json.loads(value)
    except (InvalidTag, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("账号密钥不匹配或文件损坏") from exc
    if not isinstance(record, dict):
        raise ValueError("账号内容不是对象")
    return record


def _read_json(blob: bytes) -> dict[str, Any]:
    if len(blob) > MAX_JSON_BYTES:
        raise ValueError("账号 JSON 过大")
    try:
        value = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("账号 JSON 无法解析") from exc
    if not isinstance(value, dict):
        raise ValueError("账号 JSON 必须是对象")
    return value


def read_record(filename: str, blob: bytes, private_key_path: Path | None = None) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.casefold()
    if suffix == ".vault":
        return decrypt_vault(blob, private_key_path or resolve_private_key())
    if suffix == ".json":
        return _read_json(blob)
    raise ValueError("仅支持 .vault 或 .json 账号文件")


def _access_token(record: dict[str, Any]) -> str:
    auth = record.get("auth") if isinstance(record.get("auth"), dict) else {}
    value = (
        auth.get("access_token")
        or auth.get("accessToken")
        or auth.get("user_token")
        or auth.get("token")
        or record.get("access_token")
        or record.get("user_token")
        or ""
    )
    token = re.sub(r"^Bearer\s+", "", str(value).strip(), flags=re.IGNORECASE).strip()
    if len(token) < 10:
        raise ValueError("账号缺少可用的 auth.access_token")
    return token


def account_name(record: dict[str, Any], filename: str) -> str:
    account = record.get("account") if isinstance(record.get("account"), dict) else {}
    value = account.get("label") or account.get("email") or account.get("id")
    return str(value or Path(filename).stem or "Wan 账号").strip()[:100] or "Wan 账号"


def extract_account(filename: str, blob: bytes, private_key_path: Path | None = None) -> tuple[str, str]:
    name, token, _ = extract_account_details(filename, blob, private_key_path)
    return name, token


def extract_account_details(filename: str, blob: bytes, private_key_path: Path | None = None) -> tuple[str, str, str]:
    record = read_record(filename, blob, private_key_path)
    account = record.get("account") if isinstance(record.get("account"), dict) else {}
    email = str(account.get("email") or record.get("email") or "").strip()[:254]
    return account_name(record, filename), _access_token(record), email
