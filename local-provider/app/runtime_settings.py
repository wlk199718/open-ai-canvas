import base64
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .security import decrypt_secret, encrypt_secret


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_concurrency: int = Field(default=6, ge=1, le=50)
    request_timeout: int = Field(default=60, ge=10, le=600)
    upload_timeout: int = Field(default=600, ge=30, le=3600)
    submit_timeout: int = Field(default=120, ge=30, le=600)
    download_timeout: int = Field(default=300, ge=30, le=3600)
    poll_interval: int = Field(default=5, ge=2, le=60)
    task_timeout: int = Field(default=3600, ge=60, le=14400)
    min_balance: int = Field(default=0, ge=0, le=1000000)
    proxy_server: str | None = Field(default=None, max_length=8192)
    proxy_subscription_timeout: int = Field(default=120, ge=15, le=600)
    max_proxy_nodes: int = Field(default=6, ge=1, le=50)

    @field_validator("proxy_server")
    @classmethod
    def validate_proxy(cls, value):
        if value is None or not value.strip():
            return value
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or parsed.fragment:
            raise ValueError("代理需为 HTTP / HTTPS / SOCKS5 地址或 HTTPS 订阅链接")
        try:
            parsed.port
        except ValueError:
            raise ValueError("代理端口无效") from None
        if parsed.scheme != "https" and (parsed.query or parsed.path not in {"", "/"}):
            raise ValueError("订阅链接必须使用 HTTPS")
        return value.strip()


class WanSettings(RuntimeSettings):
    request_timeout: int = Field(default=30, ge=10, le=600)
    upload_timeout: int = Field(default=300, ge=30, le=3600)
    submit_timeout: int = Field(default=90, ge=30, le=600)
    task_timeout: int = Field(default=3600, ge=60, le=7200)
    retry_count: int = Field(default=2, ge=0, le=10)
    compress_images: bool = True
    login_concurrency: int = Field(default=3, ge=1, le=20)
    claim_concurrency: int = Field(default=6, ge=1, le=20)


class VideoSettings(RuntimeSettings):
    generation_timeout: int = Field(default=2400, ge=60, le=14400)
    claw_message_workers: int = Field(default=0, ge=0, le=500)
    browser_assist_enabled: bool = False
    browser_window_count: int = Field(default=3, ge=1, le=50)


class ImageSettings(RuntimeSettings):
    task_timeout: int = Field(default=600, ge=60, le=14400)
    poll_interval: int = Field(default=5, ge=3, le=60)


SETTING_MODELS = {"wan": WanSettings, "video": VideoSettings, "image": ImageSettings}
PROVIDER_SETTINGS = {"wan-video": "wan", "q4-video": "video", "claw-video": "video", "vidu-cookie": "video", "image": "image"}


def settings_for(store, provider, *, include_proxy=True):
    kind = PROVIDER_SETTINGS.get(provider, provider)
    model = SETTING_MODELS[kind]
    values = model().model_dump(exclude={"proxy_server"})
    values.update({key: value for key, value in store.setting(kind + "_settings", {}).items() if key in values})
    if include_proxy:
        credential = store.setting(kind + "_proxy_credential", "")
        values["proxy_server"] = decrypt_secret(base64.b64decode(credential, validate=True)) if credential else ""
    return values


def public_settings(store, kind):
    values = settings_for(store, kind, include_proxy=False)
    values["proxy_configured"] = bool(store.setting(kind + "_proxy_credential", ""))
    return values


def save_settings(store, kind, settings):
    values = settings.model_dump(exclude={"proxy_server"})
    credential = None
    if settings.proxy_server is not None:
        value = settings.proxy_server.strip()
        credential = base64.b64encode(encrypt_secret(value)).decode("ascii") if value else ""
    with store.transaction() as db:
        import json
        pairs = [(kind + "_settings", json.dumps(values))]
        if credential is not None:
            pairs.append((kind + "_proxy_credential", json.dumps(credential)))
        db.executemany("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", pairs)
    return public_settings(store, kind)


def migrate_settings(store):
    if store.setting("runtime_settings_version", 0) >= 1:
        return
    old_cookie = store.setting("cookie_settings", {})
    for kind in SETTING_MODELS:
        existing = store.setting(kind + "_settings", {})
        inherited = old_cookie if kind in {"video", "image"} else {}
        defaults = SETTING_MODELS[kind]().model_dump(exclude={"proxy_server"})
        values = {**defaults, **{key: value for key, value in inherited.items() if key in defaults}, **existing}
        store.set_setting(kind + "_settings", values)
    store.set_setting("runtime_settings_version", 1)
