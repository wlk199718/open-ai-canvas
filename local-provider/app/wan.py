import ipaddress
import json
import math
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests


MODELS = [
    {"id": "dashscope-wan3.0-video", "name": "Wan 3.0 Video", "rate": 12, "duration_min": 2, "duration_max": 30},
    {"id": "dashscope-happyhorse-1.1-r2v", "name": "HappyHorse 1.1 R2V", "rate": 11, "duration_min": 3, "duration_max": 15},
    {"id": "minimax-H3", "name": "MiniMax H3", "rate": 8, "duration_min": 5, "duration_max": 15},
]
RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
BASE = "https://vivareel.ai"


def public_https(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("参考素材或结果必须是公开的 HTTPS 地址")
    for info in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("不允许访问本地或私网地址")
    return url


def claimed(row):
    return bool(
        row.get("claimed_at") or row.get("claimed") is True or row.get("is_claimed") is True
        or any(str(row.get(key) or "").lower() in {"claimed", "already_claimed"} for key in ("status", "reward_status", "claim_status"))
    )


def claimable(row):
    return not claimed(row) and (
        row.get("claimable") is True or row.get("is_claimable") is True
        or any(str(row.get(key) or "").lower() in {"completed", "complete", "ready_to_claim", "claimable"} for key in ("status", "reward_status"))
    )


def registered(row):
    if not isinstance(row, dict) or not row:
        return False
    if row.get("registered") is False or row.get("is_registered") is False:
        return False
    if row.get("status") in {"not_registered", "unregistered", "none"}:
        return False
    return bool(row.get("id") or row.get("registration_id") or row.get("registered_at")
                or row.get("registered") is True or row.get("is_registered") is True
                or row.get("status") in {"registered", "active", "pending", "rewarded", "completed"})


def activity_eligible(detail):
    if not isinstance(detail, dict) or detail.get("is_active") is not True:
        return False
    if detail.get("activity_type") != "registration" or detail.get("registration_enabled") is not True:
        return False
    if detail.get("payment_product_id") is not None or detail.get("ticket") or detail.get("registration_form_config"):
        return False
    reward = detail.get("reward")
    if not isinstance(reward, dict) or reward.get("reward_type") != "general_points":
        return False
    try:
        start = datetime.fromisoformat(str(detail.get("registration_start_at") or detail.get("start_time")))
        end = datetime.fromisoformat(str(detail.get("registration_end_at") or detail.get("end_time")))
        return (int(reward.get("points_amount")) > 0 and start.tzinfo is not None and end.tzinfo is not None
                and start <= datetime.now(timezone.utc) <= end)
    except (TypeError, ValueError):
        return False


class WanClient:
    def __init__(self, token, timeout=30):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ZZDH-VivaReel-Canvas-Plugin/2.0",
        })
        self.timeout = timeout
        self.submit_timeout = timeout
        self.upload_timeout = 300
        self.download_timeout = 120
        self.retry_count = 0
        self.compress_images = True
        self.deadline = None
        self.media_session = requests.Session()
        self.media_session.trust_env = False

    def close(self):
        self.session.close()
        self.media_session.close()

    def phase_timeout(self, requested):
        if self.deadline is None:
            return requested
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("任务总超时已到，未重新提交")
        return max(0.1, min(requested, remaining))

    def request(self, method, path, **kwargs):
        safe = method.upper() in {"GET", "HEAD", "OPTIONS"} or path == "/api/uploads/presign"
        attempts = 1 + self.retry_count if safe else 1
        headers = {
            "Connection": "close", "Cache-Control": "no-cache", "Pragma": "no-cache",
            "x-client-timezone": "Asia/Shanghai", "x-locale": "zh", **kwargs.pop("headers", {}),
        }
        timeout = kwargs.pop("timeout", self.timeout if method.upper() in {"GET", "HEAD", "OPTIONS"} else self.submit_timeout)
        for attempt in range(attempts):
            try:
                response = self.session.request(method, BASE + path, timeout=self.phase_timeout(timeout), headers=headers, **kwargs)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    response.close()
                    time.sleep(min(0.25 * (attempt + 1), self.phase_timeout(timeout)))
                    continue
                break
            except requests.RequestException:
                if attempt + 1 == attempts:
                    raise
                time.sleep(min(0.25 * (attempt + 1), self.phase_timeout(timeout)))
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text[:500]}
        if response.status_code == 401:
            error = requests.HTTPError("Wan 账号鉴权失败", response=response)
            raise error
        response.raise_for_status()
        if not isinstance(payload, dict):
            raise ValueError("Wan 上游响应无效")
        if payload.get("code") not in (None, 0, 200, "200"):
            raise ValueError(str(payload.get("message") or payload.get("error") or "Wan 上游请求失败"))
        return payload.get("data", payload)

    def wallet(self):
        data = self.request("GET", "/api/points/wallets")
        if not isinstance(data, dict):
            raise ValueError("钱包响应无效")
        return data

    def rewards(self):
        data = self.request("GET", "/api/engagement/tasks")
        return data if isinstance(data, list) else data.get("tasks", [])

    def engagement_overview(self):
        data = self.request("GET", "/api/engagement/overview")
        return data if isinstance(data, dict) else {}

    def engagement_achievements(self):
        data = self.request("GET", "/api/engagement/achievements")
        return data if isinstance(data, list) else []

    def engagement_timezone(self):
        data = self.request("GET", "/api/engagement/timezone")
        return data if isinstance(data, dict) else {}

    def warm_growth_center(self):
        for method in (self.engagement_overview, self.rewards, self.engagement_achievements, self.engagement_timezone):
            try:
                method()
            except Exception:
                continue

    def claim(self, key):
        data = self.request("POST", f"/api/engagement/tasks/{quote(key, safe='')}/claim")
        return data if isinstance(data, dict) else {}

    def experience_trial_summary(self):
        value = self.request("GET", "/api/canvas/experience/trial-summary")
        if not isinstance(value, dict):
            raise ValueError("免费体验额度响应无效")
        return value

    def submit_hidden_experience(self, payload):
        value = self.request("POST", "/api/canvas/experience/generate", json=payload)
        return value if isinstance(value, dict) else {}

    def login(self, email, password):
        value = self.request("POST", "/api/users/login", json={"email": email, "password": password},
                             headers={"Authorization": ""})
        token = str(value.get("access_token") or "").strip()
        if len(token) < 10:
            raise ValueError("登录响应缺少 access_token")
        user = value.get("user") if isinstance(value.get("user"), dict) else {}
        if user.get("email") and str(user["email"]).casefold() != email.casefold():
            raise ValueError("登录响应账号不匹配")
        return token

    def activity_details(self, activity_id):
        value = self.request("GET", f"/api/activities/{int(activity_id)}")
        return value if isinstance(value, dict) else {}

    def activity_offers(self):
        rows = self.request("GET", "/api/activities")
        if not isinstance(rows, list):
            return []
        offers = []
        for row in rows[:20]:
            if not isinstance(row, dict) or row.get("activity_type") != "registration":
                continue
            try:
                activity_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if activity_id <= 0 or row.get("is_active") is not True:
                continue
            detail = self.activity_details(activity_id)
            if not activity_eligible(detail):
                continue
            registration = self.activity_registration(activity_id)
            offers.append({
                "id": activity_id,
                "title": str(detail.get("title") or f"活动 {activity_id}")[:150],
                "description": str(detail.get("description") or detail.get("registration_end_at") or "")[:300],
                "reward": int(detail["reward"]["points_amount"]),
                "registered": registered(registration),
            })
        return offers

    def activity_registration(self, activity_id):
        try:
            value = self.request("GET", f"/api/activities/{int(activity_id)}/my-registration")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return value if isinstance(value, dict) else None

    def register_activity(self, activity_id):
        value = self.request("POST", f"/api/activities/{int(activity_id)}/registrations", json={"consent": True})
        return value if isinstance(value, dict) else {}

    def redeem(self, code):
        value = self.request("POST", "/api/redemption-codes/redeem", json={"code": code})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def wallet_amount(wallet):
        try:
            general = float(wallet.get("general_available"))
            targeted = float(wallet.get("targeted_available") or 0)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Wan 钱包响应缺少有效积分") from None
        if not math.isfinite(general + targeted) or general < 0 or targeted < 0:
            raise ValueError("Wan 钱包积分无效")
        return general + targeted

    def create_project(self):
        data = self.request("POST", "/api/canvas/new-projects", json={"name": "Zaowu video", "description": ""})
        return int(data["id"])

    def save_project(self, project_id, urls, prompt, model, duration, ratio):
        nonce = time.time_ns()
        image_nodes = []
        for index, url in enumerate(urls, 1):
            image_nodes.append({
                "id": f"imageReference-{nonce}-{index}",
                "type": "imageReference",
                "position": {"x": 207 if index == 1 else 215, "y": 109 if index == 1 else 340 * (index - 1)},
                "data": {
                    "displayIndex": index,
                    "uploading": False,
                    "remoteUrl": url,
                    "customName": Path(urlparse(url).path).name or f"参考素材 {index}",
                },
            })
        generation_id = f"videoGen-{nonce}-generation"
        generation_node = {
            "id": generation_id,
            "type": "generationResult",
            "position": {"x": 680, "y": 132},
            "data": {
                "sourceId": generation_id,
                "sourceLabel": "",
                "index": 1,
                "status": "composing",
                "resultKind": "video",
                "aspectRatio": ratio,
                "videoMode": "multi-image",
                "duration": duration,
                "modelId": model,
                "prompt": prompt.strip(),
            },
        }
        nodes = [image_nodes[0], generation_node, *image_nodes[1:]]
        edges = []
        for index, image_node in enumerate(image_nodes):
            edge = {"id": f"{image_node['id']}-{generation_id}", "source": image_node["id"], "target": generation_id}
            if index == 0:
                edge.update({"type": "referenceEdge", "data": {"sourceKind": "image"}})
            else:
                edge["data"] = {}
            edges.append(edge)
        graph = {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}
        return self.request(
            "PUT",
            f"/api/canvas/new-projects/{int(project_id)}/save",
            json={"content_json": json.dumps(graph, ensure_ascii=False, separators=(",", ":"))},
        )

    def submit(self, prompt, model, duration, ratio, urls, project_id):
        payload = {
            "model": model, "duration_seconds": duration, "aspect_ratio": ratio,
            "motion_instruction": prompt, "use_reference_image_video": True,
            "urls": urls, "new_project_id": project_id,
        }
        data = self.request("POST", "/api/canvas/video/generate", json=payload)
        task_id = str(data.get("task_id") or data.get("taskId") or "")
        if not task_id:
            raise ValueError("提交响应未返回任务 ID；不得重试提交")
        return task_id

    def upload_reference(self, source):
        if str(source).startswith("https://"):
            return public_https(str(source))
        from .wan_upload import upload_reference
        return upload_reference(self, Path(source))

    def poll(self, upstream_id):
        data = self.request("GET", f"/api/canvas/tasks/{quote(upstream_id, safe='')}")
        if not isinstance(data, dict):
            raise ValueError("任务响应无效")
        return {
            "status": str(data.get("status") or data.get("state") or "processing").lower(),
            "progress": max(0, min(100, float(data.get("progress") or data.get("percent") or 0))),
            "url": data.get("result_url") or data.get("video_url") or data.get("url") or data.get("output_url"),
        }

    def download(self, url, target: Path):
        public_https(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part")
        try:
            with self.media_session.get(url, timeout=self.phase_timeout(self.download_timeout), stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError("结果地址重定向被拒绝")
                if response.headers.get("Content-Type", "").split(";")[0] not in {"video/mp4", "application/octet-stream"}:
                    raise ValueError("结果不是视频文件")
                total = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(256 * 1024):
                        total += len(chunk)
                        if total > 2 * 1024**3:
                            raise ValueError("视频超过 2GB 限制")
                        output.write(chunk)
            if total < 1024:
                raise ValueError("视频文件不完整")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
