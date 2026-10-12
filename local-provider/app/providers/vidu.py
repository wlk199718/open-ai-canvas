from __future__ import annotations

import uuid
import re
import time

import requests

BASE = "https://service.vidu.com"
ORIGIN = "https://www.vidu.com"


class SubmissionRejected(requests.HTTPError):
    """The upstream explicitly refused the request before creating a task."""


def task_identifier(value):
    if not isinstance(value, dict):
        return ""
    for key in ("id", "task_id", "taskId"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool) and str(candidate).strip():
            return str(candidate).strip()
    for key in ("task", "data", "result", "payload"):
        found = task_identifier(value.get(key))
        if found:
            return found
    return ""


def response_error_code(value):
    if not isinstance(value, dict):
        return ""
    for key in ("code", "reason", "err_code", "error_code", "error"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            text = str(candidate)
            if text.lower() not in {"0", "200", "ok", "success", ""} and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", text):
                return text
    for key in ("error", "data"):
        found = response_error_code(value.get(key))
        if found:
            return found
    return ""


class ViduClient:
    def __init__(self, cookies: dict[str, str], timeout: int = 60, referer: str = "character2video"):
        if not isinstance(cookies, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            or any(char in key + value for char in "\r\n")
            for key, value in cookies.items()
        ):
            raise ValueError("登录 Cookie 格式无效")
        if any(not cookies.get(key, "").strip() for key in ("JWT", "SSUID")):
            raise ValueError("缺少登录 Cookie：JWT 或 SSUID")
        self.cookies = cookies
        self.timeout = timeout
        self.browser_assist_enabled = False
        self.browser_window_count = 3
        self.deadline = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "zh",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Origin": ORIGIN,
            "Referer": f"{ORIGIN}/zh/create/{referer}",
            "Sec-CH-UA": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
            "Cookie": "; ".join(f"{key}={value}" for key, value in cookies.items()),
            "X-Platform": "web",
            "X-App-Version": "-",
        })
        waf_token = next(
            (cookies[key].strip() for key in ("aws-waf-token", "aws_waf_token", "awsWafToken")
             if cookies.get(key, "").strip()),
            "",
        )
        if waf_token:
            self.session.headers["X-Aws-Waf-Token"] = waf_token

    def close(self):
        self.session.close()

    def phase_timeout(self, requested):
        if self.deadline is None:
            return requested
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("任务总超时已到，未重新提交")
        return max(0.1, min(requested, remaining))

    def request(self, method: str, path: str, **kwargs):
        headers = {"X-Request-Id": str(uuid.uuid4()), **kwargs.pop("headers", {})}
        timeout = self.phase_timeout(kwargs.pop("timeout", self.timeout))
        if self.browser_assist_enabled and path.startswith("/vidu/v1/tasks"):
            from ..video_browser import browser_pool
            response = browser_pool.request(
                self.cookies, self.session.proxies.get("https", ""),
                self.browser_window_count, method, BASE + path,
                {**self.session.headers, **headers}, kwargs.get("json"), timeout,
            )
        else:
            response = self.session.request(
                method, BASE + path, headers=headers, timeout=timeout, verify=False, **kwargs
            )
        status = response.status_code
        waf_action = response.headers.get("x-amzn-waf-action", "").lower()
        if waf_action in {"captcha", "challenge"}:
            raise requests.HTTPError(
                f"上游要求浏览器验证（HTTP {status}），未确认登录失效",
                response=response,
            )
        if status >= 400:
            try:
                code = response_error_code(response.json())
            except ValueError:
                code = ""
            if any(secret and secret in code for secret in self.cookies.values()):
                code = "[REDACTED]"
            message = (
                "上游拒绝访问（HTTP 403），未确认登录失效"
                if status == 403 else f"上游请求失败（HTTP {status}）"
            )
            if code:
                message += f"，原因：{code}"
            if 400 <= status < 500 and status != 408:
                raise SubmissionRejected(message, response=response)
            raise requests.HTTPError(message, response=response)
        create = method.upper() == "POST" and path in {"/vidu/v1/tasks", "/vidu/v1/tasks/tool"}
        header_id = ""
        if create:
            for key in ("X-Task-Id", "Task-Id", "Location", "Content-Location"):
                candidate = response.headers.get(key, "").strip()
                if key in {"Location", "Content-Location"}:
                    match = re.search(r"/tasks/([A-Za-z0-9_-]+)(?:[/?#]|$)", candidate)
                    candidate = match[1] if match else ""
                if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
                    header_id = candidate
                    break
        if not response.content:
            if header_id:
                return {"id": header_id}
            if status != 204:
                raise ValueError(f"上游返回空响应（HTTP {status}），请检查网络或浏览器验证")
            return {}
        try:
            value = response.json()
        except ValueError:
            if header_id:
                return {"id": header_id}
            raise ValueError(f"上游返回非 JSON 响应（HTTP {status}），请检查网络或浏览器验证") from None
        if create:
            identifier = task_identifier(value) or header_id
            if identifier:
                return {"id": identifier}
            code = response_error_code(value)
            if any(secret and secret in code for secret in self.cookies.values()):
                code = "[REDACTED]"
            if code:
                raise SubmissionRejected(f"上游明确拒绝提交（HTTP {status}），原因：{code}", response=response)
        return value.get("data", value) if isinstance(value, dict) else value

    def credits(self):
        value = self.request("GET", "/credit/v1/credits/me")
        if not isinstance(value, dict):
            raise ValueError("账号积分响应无效")
        return value

    def trials(self):
        value = self.request("GET", "/credit/v1/trials/me/all")
        if not isinstance(value, dict) or not isinstance(value.get("trials"), list):
            raise ValueError("账号试用额度响应无效")
        return value
