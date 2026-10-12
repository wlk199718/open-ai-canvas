import base64
import hashlib
import json
import os
import platform
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from .security import PRODUCT_ID, decrypt_secret, encrypt_secret


LICENSE_SERVER_URL = os.getenv(
    "ZAOWU_PROVIDER_LICENSE_SERVER_URL",
    "https://license.jiaenxinxi.dpdns.org",
).rstrip("/")
LICENSE_PUBLIC_KEY_PEM = os.getenv(
    "ZAOWU_PROVIDER_LICENSE_PUBLIC_KEY_PEM",
    """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqQCJrggTXHB+hUfWQZX1
29Ldr9paz3N3lrwuHeLQ1ItDyXy++/IOxBgAtb9+UUL9aAVxj3v5LtipVlPE9BoO
jahO4NvWF6IGMTL7C20wkD2WgwyCxzUCs49caOGY4/P/w8AVcAuLoTcpbSYKki1J
mEhL+/kSFpmtcHzINWol2AEQecihrCCe08T0bjJUGxKt6y1HxgPe34sIEmbCvK0u
uSxsP25bpecZkJ71hHpFFABLOOj2DGI2yJshDYZCGeLdPf9VAupiauyAQwukTyWG
86iMlQ/DDxLWKkTPWKeL02i4qSVGBa/s6og6xY6bvIi93bxZMWVQit8C7LTHBTj5
uwIDAQAB
-----END PUBLIC KEY-----""",
)
HEARTBEAT_TIMEOUT = 10
GRACE_PERIOD_SECONDS = 15 * 60
MAX_RETRY_DELAY = 120
CLIENT_VERSION = "2026.10.11.1"


class LicenseError(RuntimeError):
    pass


class LicenseConnectionError(LicenseError):
    pass


def _machine_guid():
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value or "").strip()
    except OSError:
        return ""


def device_id():
    raw = _machine_guid() or "|".join(
        (str(uuid.getnode()), platform.node(), platform.machine())
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def device_info():
    return f"{platform.node()} / {platform.system()} {platform.release()} / {platform.machine()}"


def canonical(payload):
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def verify_response(data):
    if not isinstance(data, dict):
        raise LicenseError("授权服务器返回了无效数据")
    payload = dict(data)
    signature = str(payload.pop("signature", "") or "")
    if not signature:
        raise LicenseError("授权响应缺少签名")
    try:
        key = serialization.load_pem_public_key(LICENSE_PUBLIC_KEY_PEM.encode("ascii"))
        if not isinstance(key, RSAPublicKey):
            raise ValueError("RSA key required")
        key.verify(
            base64.b64decode(signature, validate=True),
            canonical(payload),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise LicenseError("授权响应签名无效") from exc
    return payload


class LicenseManager:
    def __init__(self, store):
        self.store = store
        self.device_id = device_id()
        self.lock = threading.RLock()
        self._io_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self._key = ""
        self._last_success = None
        self._closed = False
        self._generation = 0
        self._state = {
            "ok": False, "heartbeat_interval": 60, "expires_at": None,
            "last_success_at": None, "consecutive_failures": 0,
            "in_grace_period": False, "error": "尚未验证卡密",
        }
        self.session = requests.Session()
        self.session.trust_env = True
        self.direct_session = requests.Session()
        self.direct_session.trust_env = False

    def close(self):
        with self.lock:
            self._closed = True
            self.stop.set()
            self._state["ok"] = False
        with self._io_lock:
            self.session.close()
            self.direct_session.close()

    def state(self):
        with self.lock:
            return dict(self._state)

    def _save(self, **values):
        with self.lock:
            self._state.update(values)
            return dict(self._state)

    def _request(self, endpoint, payload, timeout):
        parsed = urlparse(LICENSE_SERVER_URL)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise LicenseError("授权服务必须配置无凭据的 HTTPS 地址")
        url = LICENSE_SERVER_URL + endpoint
        last = None
        for session, route in ((self.session, "system"), (self.direct_session, "direct")):
            response = None
            try:
                response = session.post(
                    url,
                    json=payload,
                    timeout=(min(5.0, float(timeout)), float(timeout)),
                    verify=True,
                    allow_redirects=False,
                )
                if response.status_code == 429:
                    raise LicenseConnectionError("授权请求过于频繁，请稍后重试")
                if 300 <= response.status_code < 400:
                    raise LicenseError("授权服务器返回了不受信任的重定向")
                if 400 <= response.status_code < 500:
                    raise LicenseError(f"授权请求被拒绝 (HTTP {response.status_code})")
                if response.status_code >= 500:
                    last = LicenseConnectionError(f"授权服务器暂时不可用 (HTTP {response.status_code})")
                    continue
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    raise LicenseError("授权服务器响应格式无效") from exc
                return verify_response(data)
            except LicenseError:
                raise
            except requests.RequestException as exc:
                last = LicenseConnectionError(f"无法连接授权服务器（{route}，{type(exc).__name__}）")
            finally:
                if response is not None:
                    response.close()
        raise last or LicenseConnectionError("无法连接授权服务器")

    def _verify(self, key):
        payload = self._request(
            "/api/verify",
            {
                "license_key": key,
                "device_id": self.device_id,
                "device_info": device_info(),
                "project": PRODUCT_ID,
            },
            15,
        )
        self._validate(payload, require_project=True)
        return payload

    def _expiry(self, value):
        if value is None:
            return None
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if result.tzinfo is None:
                raise ValueError("timezone required")
            return result
        except (TypeError, ValueError) as exc:
            raise LicenseError("授权到期时间格式无效") from exc

    def _validate(self, payload, require_project=False):
        if payload.get("ok") is not True:
            return
        if payload.get("project", None if require_project else PRODUCT_ID) != PRODUCT_ID:
            raise LicenseError("卡密项目标识不匹配")
        if payload.get("device_id", self.device_id) != self.device_id:
            raise LicenseError("卡密设备标识不匹配")
        expiry = self._expiry(payload.get("expires_at"))
        if expiry is not None and expiry <= datetime.now(timezone.utc):
            raise LicenseError("卡密已到期")
        try:
            int(payload.get("heartbeat_interval") or 60)
        except (TypeError, ValueError, OverflowError) as exc:
            raise LicenseError("授权心跳间隔格式无效") from exc

    def _record_success(self, payload):
        self._last_success = time.monotonic()
        return self._save(
            ok=True,
            heartbeat_interval=max(15, min(120, int(payload.get("heartbeat_interval") or 60))),
            expires_at=payload.get("expires_at", self.state().get("expires_at")),
            checked_at=time.time(),
            last_success_at=time.time(),
            consecutive_failures=0,
            in_grace_period=False,
            error="",
        )

    def _invalidate(self, message):
        self._last_success = None
        self.stop.set()
        self._save(ok=False, in_grace_period=False, error=message)

    def activate(self, key, *, persist=True, expected_generation=None):
        key = str(key or "").strip()
        if not key:
            raise LicenseError("请输入卡密")
        with self._io_lock:
            with self.lock:
                if self._closed:
                    raise LicenseError("授权服务正在关闭")
                if expected_generation is not None and expected_generation != self._generation:
                    raise LicenseError("已有新的授权操作，停止恢复旧卡密")
            try:
                payload = self._verify(key)
            except LicenseError as exc:
                with self.lock:
                    if self._key == key and not isinstance(exc, LicenseConnectionError):
                        self._invalidate(str(exc))
                raise
            if payload.get("ok") is not True:
                reason = str(payload.get("error") or "卡密验证失败").replace(key, "[REDACTED]")[:280]
                with self.lock:
                    if self._key == key:
                        self._invalidate(reason)
                raise LicenseError(reason)
            if persist:
                try:
                    encoded = base64.b64encode(encrypt_secret(key)).decode("ascii")
                    self.store.set_setting("license_credential", encoded)
                except Exception as exc:
                    raise LicenseError("无法安全保存卡密，请检查本机用户的数据目录权限") from exc
            with self.lock:
                if self._closed:
                    raise LicenseError("授权服务正在关闭")
                self._key = key
                self._generation += 1
                state = self._record_success(payload)
                self._start_heartbeat(key, state["heartbeat_interval"])
                return state

    def active(self):
        with self.lock:
            state = self.state()
            if self._closed or not self._key or not state.get("ok") or self._last_success is None:
                return False
            try:
                expiry = self._expiry(state.get("expires_at"))
            except LicenseError:
                return False
            age = time.monotonic() - self._last_success
            return (
                0 <= age < GRACE_PERIOD_SECONDS
                and (expiry is None or expiry > datetime.now(timezone.utc))
            )

    def ensure_started(self, on_ready=None):
        credential = self.store.setting("license_credential", "")
        if not credential:
            return
        with self.lock:
            generation = self._generation

        def restore():
            try:
                key = decrypt_secret(base64.b64decode(credential, validate=True))
                self.activate(key, persist=False, expected_generation=generation)
                if on_ready:
                    on_ready()
            except Exception:
                with self.lock:
                    if not self._key:
                        self._save(ok=False, error="已保存卡密未能恢复，请重新输入并在线验证")

        threading.Thread(target=restore, name="license-restore", daemon=True).start()

    def _heartbeat_once(self, key):
        payload = self._request(
            "/api/heartbeat",
            {"license_key": key, "device_id": self.device_id},
            HEARTBEAT_TIMEOUT,
        )
        if payload.get("ok") is not True and payload.get("error") == "设备未注册，请重新启动程序":
            return self._verify(key)
        self._validate(payload)
        return payload

    def _heartbeat_step(self, key, stop):
        with self._io_lock:
            with self.lock:
                if self._closed or stop.is_set() or self._key != key:
                    return False
            try:
                payload = self._heartbeat_once(key)
                with self.lock:
                    if stop.is_set() or self._key != key:
                        return False
                    if payload.get("ok") is not True:
                        reason = str(payload.get("error") or "卡密心跳被拒绝").replace(key, "[REDACTED]")[:280]
                        self._invalidate(reason)
                        return False
                    self._record_success(payload)
            except LicenseConnectionError as exc:
                with self.lock:
                    if stop.is_set() or self._key != key:
                        return False
                    failures = int(self.state().get("consecutive_failures") or 0) + 1
                    in_grace = self.active()
                    self._save(
                        ok=in_grace, in_grace_period=in_grace,
                        consecutive_failures=failures, error=str(exc),
                    )
            except LicenseError as exc:
                with self.lock:
                    if not stop.is_set() and self._key == key:
                        self._invalidate(str(exc))
                return False
            return True

    def _start_heartbeat(self, key, interval):
        self.stop.set()
        stop = threading.Event()
        self.stop = stop

        def worker():
            delay = interval
            while not stop.wait(delay):
                if not self._heartbeat_step(key, stop):
                    return
                snapshot = self.state()
                failures = int(snapshot.get("consecutive_failures") or 0)
                delay = min(MAX_RETRY_DELAY, max(
                    int(snapshot.get("heartbeat_interval") or interval),
                    2 ** min(failures, 7),
                ))

        self.thread = threading.Thread(
            target=worker,
            name=f"{PRODUCT_ID}-license-heartbeat",
            daemon=True,
        )
        self.thread.start()
