import json
import base64
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from urllib.parse import urlparse

from .licensing import LicenseError, LicenseManager
from .account_import import extract_account_details, resolve_private_key
from .providers.cookie_import import decode_cookie_record, encode_cookie_record, extract_cookie_accounts
from .providers.image import ImageClient, MODELS as IMAGE_MODELS, RATIOS as IMAGE_RATIOS, RESOLUTIONS as IMAGE_RESOLUTIONS
from .providers.video import CookieVideoClient, CLAW_MODELS, Q4_MODELS, RATIOS as VIDEO_RATIOS, RESOLUTIONS as VIDEO_RESOLUTIONS
from .providers.vidu import SubmissionRejected
from .security import PRODUCT_ID, decrypt_secret, digest, encrypt_secret, safe_error
from .store import Store, now
from .result_storage import configure_results
from .reference_upload import IMAGE_TYPES, image_file_type
from .wan import MODELS, RATIOS, WanClient, public_https
from .wan_management import EXPERIENCES, WanManagement
from .wan_browser import open_account_browser, open_cookie_browser
from .runtime_settings import (
    WanSettings, VideoSettings, ImageSettings,
    migrate_settings, public_settings, save_settings, settings_for,
)
from .proxy_settings import apply_proxy
from .video_browser import browser_pool


def data_root():
    return Path(os.getenv("ZAOWU_PROVIDER_DATA_DIR") or Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ZaowuProvider")


store = Store(data_root())
result_directory = configure_results(store)
migrate_settings(store)
app = FastAPI(title="造物本地生成服务", docs_url=None, redoc_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "参数格式或范围无效，请检查设置", "fields": [
        ".".join(str(item) for item in problem["loc"]) for problem in error.errors()
    ]})


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$",
    allow_origins=["null"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
worker_lock = threading.Lock()
claim_lock = threading.Lock()
worker_running = False
DEV_MODE = os.getenv("ZAOWU_PROVIDER_DEV_MODE") == "1" and os.getenv("ZAOWU_PROVIDER_ALLOW_DEV_MODE") == "1"
WAN_DEFAULTS = WanSettings().model_dump(exclude={"proxy_server"})
VIDEO_DEFAULTS = VideoSettings().model_dump(exclude={"proxy_server"})
IMAGE_DEFAULTS = ImageSettings().model_dump(exclude={"proxy_server"})
IMAGE_MIN_CREDITS = 50
ACCOUNT_PROVIDERS = {
    "wan-video": "wan-video",
    "image": "vidu-cookie",
    "q4-video": "vidu-cookie",
    "claw-video": "vidu-cookie",
}
claw_message_locks: dict[str, threading.Lock] = {}
claw_message_lock_guard = threading.Lock()
claw_batch_lock = threading.Lock()
ADMIN_SESSION_HOURS = max(1, min(168, int(os.getenv("ZAOWU_PROVIDER_ADMIN_SESSION_HOURS", "24"))))
license_manager = LicenseManager(store)


def license_active():
    return DEV_MODE or license_manager.active()


def require_admin(authorization: str = Header(default="")):
    if not license_active():
        raise HTTPException(403, "请先验证有效卡密")
    presented = authorization.removeprefix("Bearer ").strip()
    session = store.setting("admin_session", {})
    try:
        expires_at = datetime.fromisoformat(str(session.get("expires_at", "")).replace("Z", "+00:00"))
        valid = (
            bool(presented)
            and bool(session.get("hash"))
            and expires_at > datetime.now(timezone.utc)
            and secrets.compare_digest(digest(presented), str(session["hash"]))
        )
    except (AttributeError, TypeError, ValueError, KeyError):
        valid = False
    if not valid:
        raise HTTPException(401, "会话已失效，请重新输入卡密")


def require_client(authorization: str = Header(default="")):
    presented = authorization.removeprefix("Bearer ").strip()
    saved = store.setting("client_hash", "")
    if not presented or not saved or not secrets.compare_digest(digest(presented), saved):
        raise HTTPException(401, "连接令牌无效")
    if not license_active():
        raise HTTPException(403, "本地服务尚未激活")


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "仅允许本机来源访问")


class Activation(BaseModel):
    license: str = Field(min_length=1, max_length=8192)


class AccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    access_token: str = Field(min_length=10, max_length=8192)
    provider: str = "wan-video"
    credit_profile: str = "shared"


class TaskInput(BaseModel):
    provider: str = "wan-video"
    task_type: str = "video"
    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    duration: int = 0
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    sample_count: int = Field(default=1, ge=1, le=4)
    references: list[str] = Field(default_factory=list, max_length=15)


class ClaimInput(BaseModel):
    task_keys: list[str] = Field(default_factory=list, max_length=30)
    activity_ids: list[int] = Field(default_factory=list, max_length=20)
    redemption_code: str = Field(default="", max_length=128)
    categories: list[str] = Field(default_factory=lambda: ["daily", "weekly"], max_length=2)
    include_activities: bool = False
    experience_ids: list[str] = Field(default_factory=list, max_length=3)


class WanBatchClaimInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=200)
    categories: list[str] = Field(default_factory=lambda: ["daily", "weekly"], max_length=2)
    include_activities: bool = False
    redemption_code: str = Field(default="", max_length=128)
    experience_ids: list[str] = Field(default_factory=list, max_length=3)


class WanLoginInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=200)
    password: str = Field(min_length=1, max_length=1024)
    concurrency: int | None = Field(default=None, ge=1, le=20)


class AccountCheckInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=200)
    concurrency: int | None = Field(default=None, ge=1, le=50)


class ClawBatchInput(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=200)


class CreditProfileInput(BaseModel):
    credit_profile: str = Field(pattern="^(shared|video|image)$")


class ClawTestInput(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)


def account_public(row):
    result = {key: row[key] for key in (
        "id", "name", "provider", "credential_kind", "credit_profile",
        "enabled", "balance", "video_balance", "image_balance", "checked_at",
        "video_checked_at", "image_checked_at", "status", "auth_state",
        "auth_error", "check_error",
    )}
    for key, column in (
        ("q4_trials", "q4_trials_json"),
        ("image_trials", "image_trials_json"),
        ("claw_state", "claw_state_json"),
        ("wan_state", "wan_state_json"),
    ):
        try:
            result[key] = json.loads(row[column] or "{}")
        except (KeyError, TypeError, json.JSONDecodeError):
            result[key] = {}
    result["can_login"] = bool(row.get("wan_login_email"))
    return result


def task_public(row):
    return {key: row[key] for key in (
        "id", "account_id", "provider", "task_type", "model", "status", "progress", "upstream_id",
        "error", "created_at", "updated_at"
    )} | {"result_ready": bool(row["result_path"])}


def client_for(account, task_provider=None):
    provider = task_provider or account.get("provider", "wan-video")
    settings = settings_for(store, provider)
    secret = decrypt_secret(account["secret"])
    if provider == "wan-video":
        client = WanClient(secret, timeout=settings["request_timeout"])
        client.retry_count = settings["retry_count"]
        client.compress_images = settings["compress_images"]
    elif provider == "image":
        client = ImageClient(decode_cookie_record(secret), timeout=settings["request_timeout"])
    elif provider in {"q4-video", "claw-video", "vidu-cookie"}:
        client = CookieVideoClient(decode_cookie_record(secret), timeout=settings["request_timeout"])
        client.browser_assist_enabled = provider == "q4-video" and settings["browser_assist_enabled"]
        client.browser_window_count = settings["browser_window_count"]
    else:
        raise ValueError("未知账号渠道")
    try:
        client.upload_timeout = settings["upload_timeout"]
        client.submit_timeout = settings["submit_timeout"]
        client.download_timeout = settings["download_timeout"]
        proxy = apply_proxy(client.session, settings, account["id"])
        if provider == "wan-video" and proxy:
            client.media_session.proxies.update({"http": proxy, "https": proxy})
        return client
    except Exception:
        client.close()
        raise


def provider_models():
    return [
        {"provider": "wan-video", **model, "max_references": 9, "ratios": sorted(RATIOS)}
        for model in MODELS
    ] + [
        {"provider": "q4-video", **model, "max_references": 15, "ratios": list(VIDEO_RATIOS), "resolutions": list(VIDEO_RESOLUTIONS)}
        for model in Q4_MODELS
    ] + [
        {"provider": "claw-video", **model, "max_references": 0, "ratios": list(VIDEO_RATIOS), "resolutions": list(VIDEO_RESOLUTIONS)}
        for model in CLAW_MODELS
    ] + [
        {"provider": "image", **model, "max_references": 7, "ratios": list(IMAGE_RATIOS), "resolutions": list(IMAGE_RESOLUTIONS)}
        for model in IMAGE_MODELS
    ]


def reference_paths(references: list[str], max_count: int):
    paths = []
    for value in references[:max_count]:
        path = Path(value)
        if not path.is_file():
            raise ValueError("参考素材必须是本机已存在的文件路径")
        paths.append(path)
    return paths


def credit_profile_for(provider: str) -> str:
    return "image" if provider == "image" else "video"


def account_credit_for(account, profile=None):
    if account["provider"] == "wan-video":
        return account.get("balance")
    profile = profile or account.get("credit_profile")
    if profile == "shared":
        values = [account.get("video_balance"), account.get("image_balance")]
        return max((value for value in values if value is not None), default=None)
    return account.get("image_balance" if profile == "image" else "video_balance")


def check_account(account_id):
    account = store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if not account:
        raise HTTPException(404, "账号不存在")
    client = None
    stamp = now()
    try:
        client = client_for(
            account,
            "image" if account["provider"] == "vidu-cookie" and account["credit_profile"] == "image" else None,
        )
        warnings = []
        if account["provider"] == "wan-video":
            warm = getattr(client, "warm_growth_center", None)
            if callable(warm):
                warm()
            wallet = client.wallet()
            amount = client.wallet_amount(wallet)
            column, checked_column = "balance", "checked_at"
            extra = {}
        else:
            wallet = client.credits()
            raw_amount = wallet.get("credits")
            if raw_amount is None:
                raw_amount = wallet.get("current_credits", wallet.get("balance"))
            if raw_amount is None:
                raise ValueError("积分接口未返回余额字段")
            amount = float(raw_amount)
            profile = account["credit_profile"]
            if profile == "image":
                column, checked_column = "image_balance", "image_checked_at"
            else:
                column, checked_column = "video_balance", "video_checked_at"
            extra = {}
            if profile in {"video", "shared"}:
                try:
                    claw_state = json.loads(account.get("claw_state_json") or "{}")
                    claw_state.update(client.claw_workspace())
                    extra["claw_state_json"] = json.dumps(claw_state)
                except Exception as exc:
                    warnings.append(f"Claw 状态核查失败：{safe_error(exc)}")
            try:
                trial_data = client.trials()
                q4 = {}
                image_trials = {}
                for item in trial_data.get("trials", []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        remaining = max(0, int(item.get("remaining_times") or 0))
                    except (TypeError, ValueError):
                        continue
                    if "3.4" in (item.get("model_versions") or []):
                        for resolution in item.get("resolutions") or []:
                            if resolution in VIDEO_RESOLUTIONS:
                                q4[resolution] = remaining
                    for model in item.get("model_versions") or []:
                        for task_type in item.get("task_types") or []:
                            if task_type not in {"text2image", "reference2image"}:
                                continue
                            image_trials[f"{model}:{task_type}"] = {
                                "remaining_times": remaining,
                                "state": "available" if remaining else "exhausted",
                                "checked_at": stamp,
                            }
                if profile in {"video", "shared"}:
                    extra["q4_trials_json"] = json.dumps({
                        "remaining_by_resolution": q4, "checked_at": stamp,
                    })
                if profile in {"image", "shared"}:
                    extra["image_trials_json"] = json.dumps(image_trials)
            except Exception as exc:
                warnings.append(f"试用额度核查失败：{safe_error(exc)}")
            if profile == "shared":
                extra["image_balance"] = amount
                extra["image_checked_at"] = stamp
        updates = {
            "balance": amount,
            column: amount,
            checked_column: stamp,
            "checked_at": stamp,
            "status": "ready",
            "auth_state": "valid",
            "auth_error": "",
            "check_error": "；".join(warnings),
            **extra,
        }
        with store.transaction() as db:
            db.execute(
                f"UPDATE accounts SET {', '.join(f'{key} = ?' for key in updates)} WHERE id = ?",
                (*updates.values(), account_id),
            )
        updated = store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return account_public(updated)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        response = getattr(exc, "response", None)
        waf_action = response.headers.get("x-amzn-waf-action", "").lower() if response is not None else ""
        if status_code == 401 and waf_action not in {"captcha", "challenge"}:
            with store.transaction() as db:
                db.execute(
                    "UPDATE accounts SET status = 'expired', auth_state = 'expired', "
                    "auth_error = '登录已失效', check_error = '登录已失效', checked_at = ? WHERE id = ?",
                    (stamp, account_id),
                )
            raise HTTPException(502, "登录已失效（上游 401），请重新登录或导入账号") from None
        error = (
            "上游拒绝访问（HTTP 403），未确认登录失效，请检查网络或浏览器验证"
            if status_code == 403 else safe_error(exc)
        )
        with store.transaction() as db:
            db.execute(
                "UPDATE accounts SET status = 'check_error', auth_state = ?, auth_error = '', "
                "check_error = ?, checked_at = ? WHERE id = ?",
                ("valid" if account["auth_state"] == "valid" else "unchecked", error, stamp, account_id),
            )
        raise HTTPException(502, error) from None
    finally:
        if client is not None:
            client.close()


def fail(task_id, error, status="failed"):
    store.update_task(task_id, status=status, error=safe_error(error))


def run_task(task_id):
    task = store.task(task_id)
    if not task or task["status"] in {"completed", "failed", "submission_unknown"}:
        return
    if not license_active():
        return
    account = None
    client = None
    try:
        provider = task["provider"]
        settings = settings_for(store, provider)
        task_deadline = time.monotonic() + settings["task_timeout"]
        if not task["upstream_id"]:
            account_filter = "provider = ? AND "
            if provider not in ACCOUNT_PROVIDERS:
                fail(task_id, "未知任务渠道")
                return
            profile = credit_profile_for(provider)
            balance_column = "image_balance" if profile == "image" else "video_balance"
            account = store.one(
                f"SELECT * FROM accounts WHERE {account_filter} enabled = 1 AND status NOT IN ('expired', 'check_error') "
                "AND (credit_profile = ? OR credit_profile = 'shared') "
                f"AND (COALESCE({balance_column}, balance) IS NULL OR COALESCE({balance_column}, balance) > ?) "
                f"ORDER BY COALESCE({balance_column}, balance, 0) DESC LIMIT 1",
                (ACCOUNT_PROVIDERS[provider], profile, settings.get("min_balance", 0)),
            )
            if not account:
                fail(task_id, f"没有可用的 {provider} 账号")
                return
            client = client_for(account, provider)
            client.deadline = task_deadline
            params = json.loads(task["input_json"])
            store.update_task(task_id, account_id=account["id"], status="preparing", progress=5)
            if provider == "wan-video":
                urls = [
                    public_https(value) if str(value).startswith("https://") else client.upload_reference(value)
                    for value in params["references"]
                ]
                project_id = client.create_project()
                if store.task(task_id)["status"] == "cancel_requested":
                    store.update_task(task_id, status="cancelled")
                    return
                client.save_project(
                    project_id,
                    urls,
                    task["prompt"],
                    task["model"],
                    params["duration"],
                    params["aspect_ratio"],
                )
                store.update_task(task_id, status="submitting", progress=15, error=None)
                upstream_id = client.submit(task["prompt"], task["model"], params["duration"],
                                            params["aspect_ratio"], urls, project_id)
            elif provider == "image":
                store.update_task(task_id, status="submitting", progress=15, error=None)
                upstream_id = client.submit(task["prompt"], task["model"], params["resolution"],
                                            params["aspect_ratio"], params["sample_count"],
                                            reference_paths(params["references"], 7))
            elif provider == "q4-video":
                store.update_task(task_id, status="submitting", progress=15, error=None)
                upstream_id = client.submit_q4(task["prompt"], params["duration"], params["aspect_ratio"],
                                               params["resolution"], reference_paths(params["references"], 15))
            else:
                store.update_task(task_id, status="submitting", progress=15, error=None)
                upstream_id, _ = client.submit_claw(task["prompt"], params["resolution"])
            store.update_task(task_id, upstream_id=upstream_id, status="submitted", progress=20)
        else:
            provider = task["provider"]
            account = store.one("SELECT * FROM accounts WHERE id = ?", (task["account_id"],))
            if not account:
                fail(task_id, "原生成账号已删除，请恢复账号后重试查询")
                return
            client = client_for(account, provider)
            client.deadline = task_deadline
            upstream_id = task["upstream_id"]

        deadline = task_deadline
        if provider in {"q4-video", "claw-video"}:
            deadline = min(deadline, time.monotonic() + settings["generation_timeout"])
        client.deadline = deadline
        while True:
            current = store.task(task_id)
            if current["status"] == "cancel_requested":
                store.update_task(task_id, status="cancelled", error="本地停止查询；上游任务可能继续运行")
                return
            try:
                state = (
                    client.poll(upstream_id)
                    if provider in {"wan-video", "image"}
                    else client.poll(upstream_id, provider)
                )
            except Exception as exc:
                fail(task_id, exc, status="poll_failed")
                return
            if state["status"] in {"failed", "error", "cancelled", "canceled"}:
                fail(task_id, "上游任务失败，请到上游查看详情")
                return
            if state["status"] in {"completed", "succeeded", "success", "done"}:
                if state["url"]:
                    store.update_task(task_id, status="downloading", result_url=state["url"], progress=95)
                    break
                fail(task_id, "上游已完成但未返回结果地址", status="poll_failed")
                return
            store.update_task(task_id, status="processing", progress=max(20, min(90, int(state["progress"]))))
            if time.monotonic() >= deadline:
                fail(task_id, "查询已超时，可继续查询已提交的上游任务", status="poll_failed")
                return
            time.sleep(settings["poll_interval"])
        client.deadline = task_deadline
        download_task(task_id, client)
    except Exception as exc:
        current = store.task(task_id)
        if current and current["status"] == "submitting":
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            rejected = isinstance(exc, SubmissionRejected) or (
                status_code is not None and 400 <= status_code < 500 and status_code != 408
            )
            if rejected:
                fail(task_id, exc)
            else:
                fail(task_id, f"提交状态不确定，请到上游核查后人工处理；原因：{safe_error(exc)}", status="submission_unknown")
        elif current and current["status"] not in {"completed", "failed"}:
            fail(task_id, exc)
    finally:
        if client:
            client.close()


def download_task(task_id, client=None):
    row = store.task(task_id)
    if not row or not row["result_url"]:
        return
    account = store.one("SELECT * FROM accounts WHERE id = ?", (row["account_id"],))
    if not account:
        fail(task_id, "账号不存在", status="download_failed")
        return
    own_client = client is None
    client = client or client_for(account, row["provider"])
    try:
        extension = ".png" if row["task_type"] == "image" else ".mp4"
        result = result_directory / f"{task_id}{extension}"
        client.download(row["result_url"], result)
        if row["task_type"] == "image":
            _, extension = image_file_type(result)
            final_result = result.with_suffix(extension)
            if final_result != result:
                result.replace(final_result)
                result = final_result
        store.update_task(task_id, status="completed", result_path=str(result), progress=100, error=None)
    except Exception as exc:
        fail(task_id, exc, status="download_failed")
    finally:
        if own_client:
            client.close()


def schedule_worker():
    global worker_running
    if not worker_lock.acquire(blocking=False):
        return
    try:
        while license_active():
            row = store.one("""
                SELECT id FROM tasks WHERE status IN
                ('queued', 'submitted', 'processing', 'downloading')
                ORDER BY created_at LIMIT 1
            """)
            if not row:
                return
            task = store.task(row["id"])
            if task["status"] in {"downloading", "download_failed"} and task["result_url"]:
                download_task(row["id"])
            else:
                run_task(row["id"])
            # Interrupted tasks stay outside the queue until explicitly resumed.
    finally:
        worker_lock.release()


def wake_worker():
    threading.Thread(target=schedule_worker, name="wan-tasks", daemon=True).start()


@app.on_event("startup")
def restore_tasks():
    license_manager.ensure_started(on_ready=wake_worker)
    # submitting 表示进程仍在执行可计费 POST。只有进程重启后才进入
    # submission_unknown，避免把仍在执行中的请求提前暴露为终态失败。
    with store.transaction() as db:
        db.execute(
            "UPDATE tasks SET status = 'submission_unknown', "
            "error = '服务在提交请求期间重启，请到上游核查后人工处理', updated_at = ? "
            "WHERE status = 'submitting'",
            (now(),),
        )
    # preparing has not issued the billable POST, but project creation may
    # have completed; leave it for inspection instead of replaying writes.
    wake_worker()


@app.on_event("shutdown")
def close_licensing():
    license_manager.close()
    browser_pool.close()


@app.get("/api/health")
def health():
    licensing = license_manager.state()
    return {
        "ok": True,
        "service": PRODUCT_ID,
        "api_version": "v1",
        "activated": license_active(),
        "dev_mode": DEV_MODE,
        "active_tasks": store.one(
            "SELECT COUNT(*) AS total FROM tasks WHERE status IN "
            "('queued','preparing','submitting','submitted','processing','downloading','cancel_requested')"
        )["total"] + int(worker_running or claim_lock.locked() or claw_batch_lock.locked()),
        "storage_directory_id": digest(str(result_directory)),
        "license": {
            "project": PRODUCT_ID,
            "expires_at": licensing.get("expires_at"),
            "heartbeat_interval": licensing.get("heartbeat_interval"),
            "last_success_at": licensing.get("last_success_at"),
            "consecutive_failures": licensing.get("consecutive_failures", 0),
            "in_grace_period": licensing.get("in_grace_period", False),
            "error": licensing.get("error", ""),
        },
    }


def issue_admin_session():
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ADMIN_SESSION_HOURS)
    store.set_setting("admin_session", {"hash": digest(token), "expires_at": expires_at.isoformat()})
    return {"token": token, "session_expires_at": expires_at.isoformat()}


@app.post("/api/admin/logout", dependencies=[Depends(check_origin), Depends(require_admin)])
def logout():
    store.set_setting("admin_session", {})
    return {"ok": True}


@app.post("/api/admin/activate", dependencies=[Depends(check_origin)])
def activate(body: Activation):
    try:
        state = license_manager.activate(body.license)
    except LicenseError as exc:
        reason = str(exc).replace(body.license.strip(), "[REDACTED]") if body.license.strip() else str(exc)
        raise HTTPException(400, safe_error(reason)) from None
    session = issue_admin_session()
    wake_worker()
    return {
        "activated": True,
        "project": PRODUCT_ID,
        "expires_at": state.get("expires_at"),
        "heartbeat_interval": state.get("heartbeat_interval"),
        **session,
    }


@app.post("/api/admin/client-token", dependencies=[Depends(check_origin), Depends(require_admin)])
def rotate_token():
    token = secrets.token_urlsafe(40)
    credential = base64.b64encode(encrypt_secret(token)).decode("ascii")
    with store.transaction() as db:
        db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("client_hash", json.dumps(digest(token))), ("client_credential", json.dumps(credential))],
        )
    return {"token": token}


@app.post("/api/admin/connection", dependencies=[Depends(check_origin), Depends(require_admin)])
def client_connection():
    credential = store.setting("client_credential", "")
    if credential:
        token = decrypt_secret(base64.b64decode(credential, validate=True))
        if secrets.compare_digest(digest(token), store.setting("client_hash", "")):
            return {"token": token}
    if store.setting("client_hash", ""):
        raise HTTPException(409, "已有连接令牌，请填写原令牌或在本地服务中重新生成后连接")
    return rotate_token()


@app.get("/api/admin/overview", dependencies=[Depends(require_admin)])
def overview():
    accounts = store.rows("SELECT * FROM accounts ORDER BY name")
    tasks = store.rows("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status")
    return {
        "activated": license_active(), "dev_mode": DEV_MODE, "client_connected": bool(store.setting("client_hash")),
        "accounts": [account_public(row) for row in accounts],
        "tasks": tasks, "models": MODELS, "providers": provider_models(),
        "ratios": sorted(RATIOS),
        "wan_settings": public_settings(store, "wan"),
        "video_settings": public_settings(store, "video"),
        "image_settings": public_settings(store, "image"),
        "browser_helper": browser_pool.state(),
    }


@app.put("/api/admin/wan-settings", dependencies=[Depends(check_origin), Depends(require_admin)])
def update_wan_settings(body: WanSettings):
    return save_settings(store, "wan", body)


@app.put("/api/admin/video-settings", dependencies=[Depends(check_origin), Depends(require_admin)])
def update_video_settings(body: VideoSettings):
    return save_settings(store, "video", body)


@app.put("/api/admin/image-settings", dependencies=[Depends(check_origin), Depends(require_admin)])
def update_image_settings(body: ImageSettings):
    return save_settings(store, "image", body)


@app.get("/api/admin/runtime-settings/{kind}", dependencies=[Depends(require_admin)])
def runtime_settings(kind: Literal["wan", "video", "image"]):
    return public_settings(store, kind)


@app.post("/api/admin/video-browser/restart", dependencies=[Depends(check_origin), Depends(require_admin)])
def restart_video_browser():
    if store.one("SELECT 1 FROM tasks WHERE provider='q4-video' AND status IN ('preparing','submitting','submitted','processing','downloading')"):
        raise HTTPException(409, "Q4 任务运行中，请完成任务后再重启浏览器辅助")
    browser_pool.close()
    return {"ok": True, **browser_pool.state()}


@app.post("/api/admin/accounts", dependencies=[Depends(check_origin), Depends(require_admin)])
def add_account(body: AccountInput):
    account_id = uuid4().hex
    with store.transaction() as db:
        db.execute("INSERT INTO accounts (id, name, secret) VALUES (?, ?, ?)", (account_id, body.name, encrypt_secret(body.access_token)))
    return {"id": account_id}


@app.post("/api/admin/accounts/import", dependencies=[Depends(check_origin), Depends(require_admin)])
async def import_accounts(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(422, "请选择账号文件")
    if len(files) > 200:
        raise HTTPException(422, "单次最多导入 200 个账号文件")
    private_key = None
    if any(Path(file.filename or "").suffix.casefold() == ".vault" for file in files):
        private_key = resolve_private_key()
    existing_tokens = set()
    for row in store.rows("SELECT secret FROM accounts"):
        try:
            existing_tokens.add(decrypt_secret(row["secret"]))
        except Exception:
            continue
    imported = []
    skipped = []
    failed = []
    batch_tokens = set()
    for file in files:
        filename = Path(file.filename or "").name
        try:
            blob = await file.read()
            name, token, email = extract_account_details(filename, blob, private_key)
            if token in existing_tokens or token in batch_tokens:
                skipped.append({"file": filename, "name": name, "reason": "账号已存在"})
                continue
            account_id = uuid4().hex
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO accounts (id, name, secret, wan_login_email) VALUES (?, ?, ?, ?)",
                    (account_id, name, encrypt_secret(token), encrypt_secret(email) if email else None),
                )
            batch_tokens.add(token)
            imported.append({"id": account_id, "name": name, "file": filename})
        except Exception as exc:
            failed.append({"file": filename, "error": safe_error(exc)})
        finally:
            await file.close()
    return {
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "count": len(imported),
    }


@app.post("/api/admin/cookie-accounts/import", dependencies=[Depends(check_origin), Depends(require_admin)])
async def import_cookie_accounts(
    provider: str = Form("vidu-cookie"),
    credit_profile: str = Form("shared"),
    files: list[UploadFile] = File(...),
):
    if provider in {"q4-video", "claw-video"}:
        credit_profile = "video"
    elif provider == "image":
        credit_profile = "image"
    provider = "vidu-cookie"
    if credit_profile not in {"shared", "video", "image"}:
        raise HTTPException(422, "积分类型无效")
    if not files or len(files) > 200:
        raise HTTPException(422, "请选择 1–200 个 Cookie 账号文件")
    imported, skipped, failed = [], [], []
    existing: dict[str, dict] = {}
    for row in store.rows("SELECT * FROM accounts WHERE provider = ?", (provider,)):
        try:
            existing[decrypt_secret(row["secret"])] = row
        except Exception:
            pass
    batch = set()
    for file in files:
        filename = Path(file.filename or "").name
        try:
            accounts = extract_cookie_accounts(filename, await file.read(), "vidu.com")
            for name, cookies in accounts:
                secret = encode_cookie_record(cookies)
                if secret in existing or secret in batch:
                    reason = "账号已存在"
                    if secret in existing and existing[secret]["credit_profile"] != credit_profile:
                        reason = (
                            f"账号已存在且用途为 {existing[secret]['credit_profile']}；"
                            "如需共用，请在账号设置中显式修改用途"
                        )
                    skipped.append({"file": filename, "name": name, "reason": reason})
                    continue
                account_id = uuid4().hex
                with store.transaction() as db:
                    db.execute(
                        "INSERT INTO accounts (id, name, secret, provider, credential_kind, credit_profile) VALUES (?, ?, ?, ?, ?, ?)",
                        (account_id, name, encrypt_secret(secret), provider, "cookies", credit_profile),
                    )
                batch.add(secret)
                imported.append({"id": account_id, "name": name, "file": filename})
        except Exception as exc:
            failed.append({"file": filename, "error": safe_error(exc)})
        finally:
            await file.close()
    return {"imported": imported, "skipped": skipped, "failed": failed, "count": len(imported)}


@app.delete("/api/admin/accounts/{account_id}", dependencies=[Depends(check_origin), Depends(require_admin)])
def delete_account(account_id: str):
    lock = account_message_lock(account_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该账号正在执行消息操作")
    try:
        if store.one("SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')", (account_id,)):
            raise HTTPException(409, "该账号有未结束的任务")
        with store.transaction() as db:
            changed = db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            if not changed.rowcount:
                raise HTTPException(404, "账号不存在")
        return {"ok": True}
    finally:
        lock.release()


@app.post("/api/admin/accounts/{account_id}/toggle", dependencies=[Depends(check_origin), Depends(require_admin)])
def toggle_account(account_id: str):
    with store.transaction() as db:
        changed = db.execute("UPDATE accounts SET enabled = 1 - enabled WHERE id = ?", (account_id,))
        if not changed.rowcount:
            raise HTTPException(404, "账号不存在")
    return {"ok": True}


@app.post("/api/admin/accounts/{account_id}/balance", dependencies=[Depends(check_origin), Depends(require_admin)])
def balance(account_id: str):
    return check_account(account_id)


@app.post("/api/admin/accounts/check", dependencies=[Depends(check_origin), Depends(require_admin)])
def check_accounts(body: AccountCheckInput):
    rows = (
        store.rows("SELECT id,provider,credit_profile FROM accounts ORDER BY name")
        if not body.account_ids
        else store.rows(
            f"SELECT id,provider,credit_profile FROM accounts WHERE id IN ({','.join('?' for _ in body.account_ids)})",
            tuple(dict.fromkeys(body.account_ids)),
        )
    )
    def check_one(row):
        try:
            return {"id": row["id"], "ok": True, "account": check_account(row["id"])}
        except HTTPException as exc:
            return {"id": row["id"], "ok": False, "error": str(exc.detail)}

    configured = min((
        settings_for(store, "image" if row["provider"] != "wan-video" and row["credit_profile"] == "image" else row["provider"])["check_concurrency"]
        for row in rows
    ), default=6)
    with ThreadPoolExecutor(max_workers=min(body.concurrency or configured, len(rows) or 1)) as executor:
        results = list(executor.map(check_one, rows))
    return {
        "ok": all(item["ok"] for item in results) if results else True,
        "total": len(results),
        "checked": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


@app.post("/api/admin/accounts/{account_id}/credit-profile", dependencies=[Depends(check_origin), Depends(require_admin)])
def set_credit_profile(account_id: str, body: CreditProfileInput):
    account = store.one("SELECT id FROM accounts WHERE id = ?", (account_id,))
    if not account:
        raise HTTPException(404, "账号不存在")
    with store.transaction() as db:
        db.execute("UPDATE accounts SET credit_profile = ? WHERE id = ?", (body.credit_profile, account_id))
    return account_public(store.one("SELECT * FROM accounts WHERE id = ?", (account_id,)))


def account_message_lock(account_id):
    with claw_message_lock_guard:
        return claw_message_locks.setdefault(account_id, threading.Lock())


@app.post("/api/admin/claw/test-message", dependencies=[Depends(check_origin), Depends(require_admin)])
def claw_test_message(body: ClawTestInput):
    lock = account_message_lock(body.account_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该账号正在执行消息操作")
    try:
        return send_claw_message(body.account_id)
    finally:
        lock.release()


def send_claw_message(account_id: str):
    account = store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if not account or account["provider"] != "vidu-cookie":
        raise HTTPException(404, "Claw Cookie 账号不存在")
    if account["credit_profile"] == "image":
        raise HTTPException(422, "图片积分账号不能执行 Claw 测试消息")
    if not account["enabled"]:
        raise HTTPException(409, "账号已停用")
    if store.one("SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')", (account_id,)):
        raise HTTPException(409, "该账号有未结束的任务")
    claw_state = json.loads(account.get("claw_state_json") or "{}")
    if claw_state.get("test_message_status") == "success":
        return {"ok": True, "skipped": True, "reason": "该账号已经发送过消息"}
    if claw_state.get("test_message_status") in {"sending", "unknown"}:
        raise HTTPException(409, "此前消息提交结果待核实，请勿重复发送")

    def save_state(status, **details):
        claw_state.update({"test_message_status": status, "test_message_at": now(), **details})
        with store.transaction() as db:
            db.execute("UPDATE accounts SET claw_state_json = ? WHERE id = ?", (json.dumps(claw_state), account_id))

    save_state("checking", test_message_error="")
    client = None
    attempted = False
    try:
        checked = check_account(account_id)
        amount = checked.get("video_balance")
        claw_state.update(checked.get("claw_state") or {})
        if amount is None or float(amount) <= 180:
            raise HTTPException(422, "Claw 测试消息要求余额大于 180 积分")
        client = client_for(account, "claw-video")
        save_state("sending", credits=amount)
        attempted = True
        result = client.send_claw_test_message()
        save_state("success", **result)
        try:
            check_account(account_id)
        except HTTPException:
            pass
        return {"ok": True, **result}
    except HTTPException as exc:
        save_state("failed", test_message_error=str(exc.detail))
        raise
    except Exception as exc:
        save_state("unknown" if attempted else "failed", test_message_error=safe_error(exc))
        raise HTTPException(502, safe_error(exc)) from None
    finally:
        if client:
            client.close()


@app.post("/api/admin/claw/test-messages", dependencies=[Depends(check_origin), Depends(require_admin)])
def claw_test_messages(body: ClawBatchInput):
    if not claw_batch_lock.acquire(blocking=False):
        raise HTTPException(409, "批量发送正在执行")
    try:
        workers = settings_for(store, "video")["claw_message_workers"]
        ids = list(dict.fromkeys(body.account_ids))

        def send_one(account_id):
            try:
                return {"id": account_id, **claw_test_message(ClawTestInput(account_id=account_id))}
            except HTTPException as exc:
                return {"id": account_id, "ok": False, "error": str(exc.detail)}

        with ThreadPoolExecutor(max_workers=min(workers or len(ids), len(ids))) as executor:
            results = list(executor.map(send_one, ids))
        return {
            "succeeded": sum(item["ok"] and not item.get("skipped") for item in results),
            "skipped": sum(bool(item.get("skipped")) for item in results),
            "failed": sum(not item["ok"] for item in results), "results": results,
        }
    finally:
        claw_batch_lock.release()


@app.post("/api/admin/cookie-accounts/reset", dependencies=[Depends(check_origin), Depends(require_admin)])
def reset_cookie_accounts():
    rows = store.rows("SELECT * FROM accounts WHERE provider = 'vidu-cookie'")
    reset, skipped = [], []
    for row in rows:
        lock = account_message_lock(row["id"])
        if not lock.acquire(blocking=False):
            skipped.append(row["id"])
            continue
        try:
            if store.one("SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')", (row["id"],)):
                skipped.append(row["id"])
                continue
            with store.transaction() as db:
                db.execute("UPDATE accounts SET status = 'unchecked', auth_state = 'unchecked', auth_error = '', check_error = '', q4_trials_json = '{}', checked_at = NULL, video_checked_at = NULL, image_checked_at = NULL WHERE id = ?", (row["id"],))
            reset.append(row["id"])
        finally:
            lock.release()
    return {"count": len(reset), "skipped": skipped}


@app.post("/api/admin/image-accounts/clean-exhausted", dependencies=[Depends(check_origin), Depends(require_admin)])
def clean_image_exhausted_accounts():
    rows = store.rows(
        "SELECT * FROM accounts WHERE provider = 'vidu-cookie' "
        "AND credit_profile = 'image' "
        "AND status = 'ready' AND auth_state = 'valid' "
        "AND image_checked_at IS NOT NULL AND image_balance < ?",
        (IMAGE_MIN_CREDITS,),
    )
    deleted = []
    skipped = []
    for row in rows:
        lock = account_message_lock(row["id"])
        if not lock.acquire(blocking=False):
            skipped.append({"id": row["id"], "name": row["name"], "reason": "账号操作执行中"})
            continue
        try:
            if store.one(
                "SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')",
                (row["id"],),
            ):
                skipped.append({"id": row["id"], "name": row["name"], "reason": "有未结束任务"})
                continue
            current = store.one("SELECT * FROM accounts WHERE id = ?", (row["id"],))
            if (not current or current["credit_profile"] != "image" or current["status"] != "ready"
                    or current["auth_state"] != "valid" or current["image_checked_at"] is None
                    or current["image_balance"] is None or current["image_balance"] >= IMAGE_MIN_CREDITS):
                skipped.append({"id": row["id"], "name": row["name"], "reason": "图片积分状态已更新"})
                continue
            trials = json.loads(current.get("image_trials_json") or "{}")
            if any(isinstance(item, dict) and int(item.get("remaining_times") or 0) > 0 for item in trials.values()):
                skipped.append({"id": row["id"], "name": row["name"], "reason": "仍有图片试用次数"})
                continue
            with store.transaction() as db:
                archived = dict(current)
                for key in ("secret", "wan_login_email"):
                    if archived.get(key) is not None:
                        archived[key] = {"encrypted": base64.b64encode(archived[key]).decode("ascii")}
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                    (f"archived_account:{row['id']}", json.dumps(archived)),
                )
                db.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
            deleted.append({"id": row["id"], "name": row["name"], "reason": "image_credits_exhausted"})
        finally:
            lock.release()
    return {
        "ok": True,
        "count": len(deleted),
        "deleted": deleted,
        "skipped": skipped,
        "threshold": IMAGE_MIN_CREDITS,
    }


@app.post("/api/admin/cookie-accounts/{account_id}/browser", dependencies=[Depends(check_origin), Depends(require_admin)])
def cookie_account_browser(account_id: str, channel: str = "q4-video"):
    if channel not in {"q4-video", "claw-video"}:
        raise HTTPException(422, "浏览器渠道无效")
    account = store.one("SELECT * FROM accounts WHERE id = ? AND provider = 'vidu-cookie'", (account_id,))
    if not account:
        raise HTTPException(404, "Cookie 账号不存在")
    try:
        return open_cookie_browser(decode_cookie_record(decrypt_secret(account["secret"])), channel)
    except Exception as exc:
        raise HTTPException(502, safe_error(exc)) from None


@app.post("/api/admin/accounts/clean-invalid", dependencies=[Depends(check_origin), Depends(require_admin)])
def clean_invalid_accounts(provider: str = "", include_exhausted: bool = False):
    if provider not in {"", "wan-video", "vidu-cookie"}:
        raise HTTPException(422, "账号渠道无效")
    rows = store.rows(
        "SELECT * FROM accounts WHERE (status = 'expired' OR auth_state = 'missing'"
        + (" OR (provider = 'vidu-cookie' AND auth_state = 'valid' AND status = 'ready' AND video_checked_at IS NOT NULL AND video_balance = 0 AND credit_profile = 'video')" if include_exhausted else "")
        + ")"
        + (" AND provider = ?" if provider else ""),
        (provider,) if provider else (),
    )
    deleted = []
    skipped = []
    for row in rows:
        message_lock = account_message_lock(row["id"])
        if not message_lock.acquire(blocking=False):
            skipped.append({"id": row["id"], "name": row["name"], "reason": "消息操作执行中"})
            continue
        try:
            if store.one(
                "SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')",
                (row["id"],),
            ):
                skipped.append({"id": row["id"], "name": row["name"], "reason": "有未结束任务"})
                continue
            current = store.one("SELECT * FROM accounts WHERE id = ?", (row["id"],))
            if not current:
                continue
            invalid = current["status"] == "expired" or current["auth_state"] == "missing"
            exhausted = (include_exhausted and current["provider"] == "vidu-cookie"
                         and current["credit_profile"] == "video" and current["auth_state"] == "valid"
                         and current["status"] == "ready" and current["video_checked_at"] is not None
                         and current["video_balance"] == 0)
            if not invalid and not exhausted:
                skipped.append({"id": row["id"], "name": row["name"], "reason": "账号状态已更新"})
                continue
            with store.transaction() as db:
                archived = dict(current)
                for key in ("secret", "wan_login_email"):
                    if archived.get(key) is not None:
                        archived[key] = {"encrypted": base64.b64encode(archived[key]).decode("ascii")}
                db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)",
                           (f"archived_account:{row['id']}", json.dumps(archived)))
                db.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
            deleted.append({"id": row["id"], "name": row["name"], "reason": row["status"] or row["auth_state"]})
        finally:
            message_lock.release()
    return {"ok": True, "deleted": deleted, "skipped": skipped, "count": len(deleted)}


@app.get("/api/admin/accounts/{account_id}/rewards", dependencies=[Depends(require_admin)])
def rewards(account_id: str):
    account = store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if not account:
        raise HTTPException(404, "账号不存在")
    if account["provider"] != "wan-video":
        raise HTTPException(422, "该渠道不支持 Wan 积分任务")
    client = client_for(account)
    try:
        return WanManagement(store, account, client).snapshot()
    except Exception as exc:
        raise HTTPException(502, safe_error(exc)) from None
    finally:
        client.close()


def _claim_wan_account(account, body: ClaimInput):
    """Use the same write/reconciliation flow for single and batch actions."""
    categories = {str(value).strip().lower() for value in body.categories}
    if not categories.issubset({"daily", "weekly"}) or (
        not categories and not body.include_activities and not body.activity_ids
        and not body.redemption_code and not body.experience_ids
    ):
        raise HTTPException(422, "任务分类只能是每日或每周")
    if body.redemption_code and not body.redemption_code.strip():
        raise HTTPException(422, "兑换码不能为空")
    body.categories = sorted(categories)
    if any(value not in {item["id"] for item in EXPERIENCES} for value in body.experience_ids):
        raise HTTPException(422, "免费体验任务标识无效")
    if any(value <= 0 for value in body.activity_ids):
        raise HTTPException(422, "活动 ID 无效")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) for value in body.task_keys):
        raise HTTPException(422, "任务标识格式无效")
    if store.one("SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')",
                 (account["id"],)):
        return {"id": account["id"], "name": account["name"], "ok": True,
                "skipped": True, "results": [], "balance": account["balance"], "reason": "账号有未结束任务"}
    if not account["enabled"]:
        return {"id": account["id"], "name": account["name"], "ok": True,
                "skipped": True, "results": [], "balance": account["balance"], "reason": "账号已停用"}
    client = client_for(account)
    try:
        return WanManagement(store, account, client).perform(body)
    finally:
        client.close()


@app.post("/api/admin/accounts/{account_id}/claim", dependencies=[Depends(check_origin), Depends(require_admin)])
def claim_rewards(account_id: str, body: ClaimInput):
    if not claim_lock.acquire(blocking=False):
        raise HTTPException(409, "已有领取操作正在执行")
    try:
        account = store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not account:
            raise HTTPException(404, "账号不存在")
        if account["provider"] != "wan-video":
            raise HTTPException(422, "该渠道不支持 Wan 积分任务")
        return _claim_wan_account(account, body)
    finally:
        claim_lock.release()


@app.post("/api/admin/wan/claim", dependencies=[Depends(check_origin), Depends(require_admin)])
def claim_wan_pool(body: WanBatchClaimInput):
    if not claim_lock.acquire(blocking=False):
        raise HTTPException(409, "已有领取操作正在执行")
    try:
        categories = {str(value).strip().lower() for value in body.categories}
        if not categories.issubset({"daily", "weekly"}) or (
            not categories and not body.include_activities and not body.redemption_code and not body.experience_ids
        ):
            raise HTTPException(422, "任务分类只能是每日或每周")
        if body.redemption_code and not body.redemption_code.strip():
            raise HTTPException(422, "兑换码不能为空")
        if any(value not in {item["id"] for item in EXPERIENCES} for value in body.experience_ids):
            raise HTTPException(422, "免费体验任务标识无效")
        rows = (
            store.rows("SELECT * FROM accounts WHERE provider = 'wan-video' ORDER BY name")
            if not body.account_ids
            else store.rows(
                f"SELECT * FROM accounts WHERE provider = 'wan-video' AND id IN ({','.join('?' for _ in body.account_ids)}) ORDER BY name",
                tuple(dict.fromkeys(body.account_ids)),
            )
        )
        if not rows:
            raise HTTPException(409, "账号池中没有 Wan 账号")
        def claim_one(account):
            try:
                return _claim_wan_account(account, ClaimInput(
                    categories=sorted(categories),
                    include_activities=body.include_activities,
                    redemption_code=body.redemption_code,
                    experience_ids=body.experience_ids,
                ))
            except Exception as exc:
                return {"id": account["id"], "name": account["name"], "ok": False,
                        "results": [], "balance": None, "error": safe_error(exc)}
        with ThreadPoolExecutor(max_workers=min(settings_for(store, "wan")["claim_concurrency"], len(rows))) as executor:
            results = list(executor.map(claim_one, rows))
        claimed_count = sum(
            item.get("status") == "claimed"
            for account in results for item in account.get("results", [])
        )
        failed_count = sum(1 for account in results if not account["ok"])
        skipped = sum(1 for account in results if account.get("skipped"))
        return {
            "ok": failed_count == 0,
            "accounts": results,
            "checked": len(results) - failed_count - skipped,
            "skipped": skipped,
            "failed": failed_count,
            "claimed": claimed_count,
        }
    finally:
        claim_lock.release()


@app.post("/api/admin/wan/login", dependencies=[Depends(check_origin), Depends(require_admin)])
def login_wan_accounts(body: WanLoginInput):
    if not claim_lock.acquire(blocking=False):
        raise HTTPException(409, "账号池正在执行操作，请稍候")
    try:
        rows = store.rows("SELECT * FROM accounts WHERE provider = 'wan-video' AND status = 'expired'")
        if body.account_ids:
            rows = [row for row in rows if row["id"] in body.account_ids]

        def login_one(account):
            client = None
            try:
                if not account.get("wan_login_email"):
                    raise ValueError("账号文件未包含登录邮箱，无法执行 API 登录")
                if store.one("SELECT 1 FROM tasks WHERE account_id = ? AND status NOT IN ('completed','failed','cancelled')",
                             (account["id"],)):
                    raise ValueError("账号有未结束任务")
                email = decrypt_secret(account["wan_login_email"])
                client = client_for(account)
                token = client.login(email, body.password)
                with store.transaction() as db:
                    db.execute("UPDATE accounts SET secret = ?, status = 'unchecked', auth_state = 'valid', "
                               "auth_error = '', check_error = '' WHERE id = ?", (encrypt_secret(token), account["id"]))
                account = check_account(account["id"])
                return {"id": account["id"], "ok": True}
            except Exception as exc:
                error = safe_error(str(exc).replace(body.password, "[REDACTED]"))
                with store.transaction() as db:
                    db.execute("UPDATE accounts SET check_error = ? WHERE id = ?", (error, account["id"]))
                return {"id": account["id"], "ok": False, "error": error}
            finally:
                if client:
                    client.close()
        with ThreadPoolExecutor(max_workers=min(body.concurrency or settings_for(store, "wan")["login_concurrency"], len(rows) or 1)) as executor:
            results = list(executor.map(login_one, rows))
        return {"results": results, "succeeded": sum(item["ok"] for item in results),
                "failed": sum(not item["ok"] for item in results)}
    finally:
        claim_lock.release()


@app.post("/api/admin/accounts/{account_id}/browser", dependencies=[Depends(check_origin), Depends(require_admin)])
def wan_account_browser(account_id: str):
    account = store.one("SELECT * FROM accounts WHERE id = ? AND provider = 'wan-video'", (account_id,))
    if not account:
        raise HTTPException(404, "Wan 账号不存在")
    if account["status"] == "expired":
        raise HTTPException(409, "请先通过 API 恢复失效账号登录")
    try:
        token = decrypt_secret(account["secret"])
        email = decrypt_secret(account["wan_login_email"]) if account.get("wan_login_email") else ""
        return open_account_browser(token, email)
    except Exception as exc:
        raise HTTPException(502, safe_error(exc)) from None


@app.get("/api/v1/models", dependencies=[Depends(require_client)])
def models():
    return {"data": provider_models()}


@app.get("/api/v1/capabilities", dependencies=[Depends(require_client)])
def capabilities():
    return {
        "service": PRODUCT_ID,
        "api_version": "v1",
        "providers": [
            {
                "id": "wan-video",
                "name": "Wan 视频",
                "status": "ready",
                "models_endpoint": "/api/v1/models",
                "tasks_endpoint": "/api/v1/tasks",
                "result_delivery": "download",
                "references": {"mode": "public_https", "max": 9},
            },
            {
                "id": "q4-video", "name": "Q4 视频", "status": "ready",
                "models_endpoint": "/api/v1/models", "tasks_endpoint": "/api/v1/tasks",
                "result_delivery": "download", "references": {"mode": "inline_image_or_local_path", "max": 15},
            },
            {
                "id": "claw-video", "name": "Claw 视频", "status": "ready",
                "models_endpoint": "/api/v1/models", "tasks_endpoint": "/api/v1/tasks",
                "result_delivery": "download", "references": {"mode": "none", "max": 0},
            },
            {
                "id": "image", "name": "图片生成", "status": "ready",
                "models_endpoint": "/api/v1/models", "tasks_endpoint": "/api/v1/tasks",
                "result_delivery": "download", "references": {"mode": "inline_image_or_local_path", "max": 7},
            },
        ],
    }


@app.post("/api/v1/tasks", dependencies=[Depends(check_origin), Depends(require_client)])
def create_task(body: TaskInput, idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    selected = next(
        (item for item in provider_models() if item["provider"] == body.provider and item["id"] == body.model),
        None,
    )
    if not selected:
        raise HTTPException(422, "模型或生成渠道不受支持")
    if body.provider in {"wan-video", "q4-video", "claw-video"}:
        if not selected["duration_min"] <= body.duration <= selected["duration_max"]:
            raise HTTPException(422, "视频时长不受模型支持")
        if body.aspect_ratio not in selected["ratios"]:
            raise HTTPException(422, "视频比例不受模型支持")
    else:
        if body.aspect_ratio not in selected["ratios"] or body.resolution not in selected["resolutions"]:
            raise HTTPException(422, "图片比例或分辨率不受模型支持")
    if body.provider == "wan-video":
        try:
            references = [
                public_https(value) if value.startswith("https://") else value
                for value in body.references
            ]
        except (ValueError, OSError) as exc:
            raise HTTPException(422, safe_error(exc)) from None
        if not references:
            raise HTTPException(422, "Wan 视频至少需要一个参考素材地址")
    else:
        references = list(body.references)
    if body.provider == "claw-video" and references:
        raise HTTPException(422, "Claw 当前不接受参考素材")
    if len(references) > selected["max_references"]:
        raise HTTPException(422, "参考素材数量超过模型限制")
    if body.sample_count != 1:
        raise HTTPException(422, "当前本地生成服务每个任务只交付一个结果")
    if not store.one(
        "SELECT 1 FROM accounts WHERE enabled = 1 AND provider = ? "
        "AND (credit_profile = ? OR credit_profile = 'shared')",
        (ACCOUNT_PROVIDERS[body.provider], credit_profile_for(body.provider)),
    ):
        raise HTTPException(409, "请先添加对应渠道账号")
    if idempotency_key and not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", idempotency_key):
        raise HTTPException(422, "幂等键格式无效")
    payload = json.dumps({
        "duration": body.duration, "aspect_ratio": body.aspect_ratio,
        "resolution": body.resolution, "sample_count": body.sample_count,
        "references": references,
        "reference_fingerprints": [digest(value) for value in references],
    }, sort_keys=True)
    with store.transaction() as db:
        existing = db.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)).fetchone() if idempotency_key else None
        if existing:
            previous = json.loads(existing["input_json"])
            requested = json.loads(payload)
            if body.provider in {"image", "q4-video", "wan-video"}:
                requested["references"] = previous["references"]
            if previous != requested or existing["provider"] != body.provider or existing["model"] != body.model or existing["prompt"] != body.prompt:
                raise HTTPException(409, "幂等键已用于其他请求")
            return task_public(dict(existing))
        task_id = uuid4().hex
        if body.provider in {"image", "q4-video", "wan-video"}:
            from .reference_upload import save_inline_references
            try:
                local_indexes = [index for index, value in enumerate(references) if not (body.provider == "wan-video" and value.startswith("https://"))]
                saved = save_inline_references(store.root, task_id, [references[index] for index in local_indexes])
                for index, value in zip(local_indexes, saved):
                    references[index] = value
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            payload = json.dumps({
                "duration": body.duration, "aspect_ratio": body.aspect_ratio,
                "resolution": body.resolution, "sample_count": body.sample_count,
                "references": references,
                "reference_fingerprints": [digest(value) for value in body.references],
            }, sort_keys=True)
        stamp = now()
        db.execute(
            "INSERT INTO tasks (id, provider, task_type, model, prompt, input_json, status, created_at, updated_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (task_id, body.provider, "image" if body.provider == "image" else "video",
             body.model, body.prompt, payload, stamp, stamp, idempotency_key or None),
        )
    wake_worker()
    return task_public(store.task(task_id))


@app.get("/api/v1/tasks", dependencies=[Depends(require_client)])
def list_tasks():
    return {"tasks": [task_public(row) for row in store.rows("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100")]}


@app.get("/api/admin/tasks", dependencies=[Depends(require_admin)])
def admin_tasks():
    return list_tasks()


@app.post("/api/admin/test-task", dependencies=[Depends(check_origin), Depends(require_admin)])
def admin_test_task(body: TaskInput):
    if not license_active():
        raise HTTPException(403, "请先激活")
    return create_task(body, "")


@app.post("/api/admin/tasks/{task_id}/resume", dependencies=[Depends(check_origin), Depends(require_admin)])
def admin_resume(task_id: str):
    return resume_task(task_id)


@app.post("/api/admin/tasks/{task_id}/cancel", dependencies=[Depends(check_origin), Depends(require_admin)])
def admin_cancel(task_id: str):
    return cancel_task(task_id)


@app.get("/api/admin/tasks/{task_id}/result", dependencies=[Depends(require_admin)])
def admin_result(task_id: str):
    return task_result_response(task_id)


def task_result_response(task_id: str):
    row = store.task(task_id)
    if not row or row["status"] != "completed":
        raise HTTPException(404, "结果尚未就绪")
    target = Path(row["result_path"] or "")
    extensions = set(IMAGE_TYPES.values()) | {".jpeg"} if row["task_type"] == "image" else {".mp4"}
    if (
        target.name not in {f"{task_id}{extension}" for extension in extensions}
        or target.resolve() != result_directory.resolve() / target.name
        or not target.is_file()
    ):
        raise HTTPException(404, "结果文件不存在")
    media_type, extension = "video/mp4", ".mp4"
    if row["task_type"] == "image":
        try:
            media_type, extension = image_file_type(target)
        except (OSError, ValueError):
            raise HTTPException(404, "结果图片格式无效或文件不可读") from None
    return FileResponse(target, media_type=media_type, filename=f"{task_id}{extension}")


@app.get("/api/v1/tasks/{task_id}", dependencies=[Depends(require_client)])
def get_task(task_id: str):
    row = store.task(task_id)
    if not row:
        raise HTTPException(404, "任务不存在")
    return task_public(row)


@app.post("/api/v1/tasks/{task_id}/resume", dependencies=[Depends(check_origin), Depends(require_client)])
def resume_task(task_id: str):
    row = store.task(task_id)
    if not row or row["status"] not in {"poll_failed", "download_failed"} or (row["status"] == "poll_failed" and not row["upstream_id"]):
        raise HTTPException(409, "当前任务不可恢复")
    store.update_task(task_id, status="submitted" if row["status"] == "poll_failed" else "downloading", error=None)
    wake_worker()
    return task_public(store.task(task_id))


@app.post("/api/v1/tasks/{task_id}/cancel", dependencies=[Depends(check_origin), Depends(require_client)])
def cancel_task(task_id: str):
    row = store.task(task_id)
    if not row or row["status"] in {"completed", "failed", "cancelled", "submitting", "submission_unknown"}:
        raise HTTPException(409, "当前任务不能取消")
    if row["status"] == "queued":
        store.update_task(task_id, status="cancelled")
    else:
        store.update_task(task_id, status="cancel_requested")
    return {"status": "cancelled" if row["status"] == "queued" else "cancel_requested", "upstream_cancelled": False}


@app.get("/api/v1/tasks/{task_id}/result", dependencies=[Depends(require_client)])
def result(task_id: str):
    return task_result_response(task_id)


static = Path(__file__).resolve().parents[1] / "ui" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    target = (static / path).resolve()
    if target.is_relative_to(static.resolve()) and target.is_file():
        return FileResponse(target)
    index = static / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "请先构建管理页面：cd ui && bun install && bun run build"}
