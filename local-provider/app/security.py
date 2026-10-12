import hashlib
import os
import re

PRODUCT_ID = "zaowu"

def digest(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encrypt_secret(value: str) -> bytes:
    if os.name != "nt":
        raise RuntimeError("账号加密仅支持 Windows")
    import win32crypt
    return win32crypt.CryptProtectData(value.encode(), PRODUCT_ID, None, None, None, 0)


def decrypt_secret(data: bytes) -> str:
    import win32crypt
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1].decode()


def safe_error(error):
    text = str(error)
    text = re.sub(r"(?i)(bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?:https?|socks5h?)://[^\s\"']+", "[URL]", text)
    return text[:280]
