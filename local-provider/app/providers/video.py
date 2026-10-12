from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .reference_image import upload_reference
from .vidu import ViduClient

Q4_MODELS = [
    {"id": "q4", "name": "Q4", "rate": 10, "duration_min": 4, "duration_max": 10},
]
CLAW_MODELS = [
    {"id": "claw", "name": "Claw", "rate": 20, "duration_min": 5, "duration_max": 15},
]
RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4")
RESOLUTIONS = ("540p", "720p", "1080p")


def _first_url(value):
    if isinstance(value, dict):
        for key in (
            "nomark_uri", "no_watermark_uri", "video_url", "videoUrl",
            "result_url", "resultUrl", "output_url", "outputUrl",
            "download_url", "downloadUrl", "download_uri", "downloadUri",
            "uri", "url",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate
        for key in ("data", "result", "creations", "creation", "outputs", "output", "videos", "video", "items"):
            found = _first_url(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    return None


def _state(value) -> str:
    if not isinstance(value, dict):
        return "processing"
    return str(value.get("state") or value.get("status") or "processing").strip().lower()


class CookieVideoClient(ViduClient):
    upload_timeout = 600
    submit_timeout = 120
    download_timeout = 300

    def request(self, method: str, path: str, **kwargs):
        timeout = kwargs.pop("timeout", self.submit_timeout if method == "POST" else self.timeout)
        return super().request(method, path, timeout=timeout, **kwargs)

    def _upload(self, path: Path) -> dict:
        return upload_reference(self, path)

    def submit_q4(
        self,
        prompt: str,
        duration: int,
        ratio: str,
        resolution: str,
        references: list[Path],
        use_trial: bool = True,
    ) -> str:
        assets = []
        for path in references:
            assets.append({"primary": self._upload(path), "source": self._upload(path)})
        task_type = "text2video" if not assets else "img2video" if len(assets) == 1 else "character2video"
        prompts = [{"type": "text", "content": prompt}]
        if len(assets) == 1:
            asset = assets[0]
            prompts.append(_image_prompt(asset, name="参考图"))
        elif assets:
            prompts[0]["content"] = "".join(f"[@图{i}]" for i in range(1, len(assets) + 1)) + f" {prompt}"
            prompts.extend(
                _image_prompt(item, name=f"图{i}")
                for i, item in enumerate(assets, 1)
            )
        body = {
            "type": task_type,
            "input": {"prompts": prompts, "editor_mode": "normal", "enhance": True},
            "settings": {
                "duration": duration, "resolution": resolution, "movement_amplitude": "auto",
                "aspect_ratio": ratio, "sample_count": 1, "schedule_mode": "normal",
                "codec": "h265", "model_version": "3.4", "use_trial": use_trial,
            },
        }
        value = self.request("POST", "/vidu/v1/tasks", json=body)
        task_id = value.get("id") or (value.get("task") or {}).get("id")
        if not task_id:
            raise ValueError("Q4 任务提交未返回任务 ID")
        return str(task_id)

    def _claw_workspace(self):
        value = self.request("GET", "/claw/v1/claw/my")
        for item in value.get("claws", []) if isinstance(value, dict) else []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status in {"active", "claw_status_active", "enabled"}:
                return str(item.get("id") or item.get("claw_id") or ""), status
        return "", ""

    def claw_workspace(self):
        workspace_id, status = self._claw_workspace()
        return {"id": workspace_id, "status": status or "inactive"}

    def send_claw_test_message(self):
        workspace_id, status = self._claw_workspace()
        if not workspace_id:
            value = self.request("POST", "/claw/v1/claw/by-code", json={})
            workspace_id = str(value.get("claw_id") or value.get("id") or "")
            status = str(value.get("status") or "active").lower()
        if not workspace_id or status not in {"active", "claw_status_active", "enabled"}:
            raise ValueError("Claw 工作区尚未激活")
        session = self.request(
            "POST", "/claw/v1/claw/session",
            json={"claw_id": workspace_id, "name": "Zaowu Test", "mode": "yolo"},
        )
        session_id = str(session.get("session_id") or session.get("id") or "")
        if not session_id:
            raise ValueError("Claw 测试会话创建失败")
        response = self.request(
            "POST", "/claw/v1/claw/chat",
            json={
                "session_id": session_id, "model_version": "auto",
                "resolution": "720p", "billing_mode": "credits",
                "msg": {"role": "user", "contents": [{"type": "text", "content": "你好"}]},
            },
        )
        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "message_id": response.get("id") or response.get("message_id"),
        }

    def submit_claw(self, prompt: str, resolution: str = "720p") -> tuple[str, str]:
        workspace_id, status = self._claw_workspace()
        if not workspace_id:
            value = self.request("POST", "/claw/v1/claw/by-code", json={})
            workspace_id = str(value.get("claw_id") or value.get("id") or "")
            status = str(value.get("status") or "active").lower()
        if not workspace_id or status not in {"active", "claw_status_active", "enabled"}:
            raise ValueError("Claw 工作区尚未激活或仍在创建")
        session = self.request(
            "POST", "/claw/v1/claw/session",
            json={"claw_id": workspace_id, "name": "Zaowu", "mode": "yolo"},
        )
        session_id = str(session.get("session_id") or session.get("id") or "")
        if not session_id:
            raise ValueError("Claw 会话创建未返回 session_id")
        response = self.request(
            "POST", "/claw/v1/claw/chat",
            json={
                "session_id": session_id, "model_version": "auto",
                "resolution": resolution, "billing_mode": "credits",
                "msg": {"role": "user", "contents": [{"type": "text", "content": prompt}]},
            },
        )
        message_id = str(response.get("id") or response.get("message_id") or "")
        return f"{session_id}:{message_id}", session_id

    def poll(self, upstream_id: str, provider: str):
        if provider == "q4-video":
            value = self.request("GET", f"/vidu/v1/tasks/{quote(upstream_id, safe='')}")
            state = _state(value)
            return {
                "status": "completed" if state == "success" else state,
                "progress": 95 if state == "success" else {"queueing": 30, "processing": 70}.get(state, 15),
                "url": _first_url(value),
            }
        session_id = upstream_id.split(":", 1)[0]
        value = self.request(
            "GET", "/claw/v1/claw/chat/history",
            params={"session_id": session_id, "pager.page": 0, "pager.pagesz": 100},
        )
        url = _first_url(value)
        if url:
            return {"status": "completed", "progress": 95, "url": url}
        return {"status": "processing", "progress": 50, "url": None}

    def download(self, url: str, target: Path):
        response = self.session.get(url, stream=True, timeout=self.phase_timeout(self.download_timeout), verify=False)
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part")
        with temporary.open("wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if chunk:
                    output.write(chunk)
        temporary.replace(target)


def _image_prompt(asset: dict, *, name: str) -> dict:
    primary = asset["primary"]
    source = asset["source"]
    return {
        "type": "image",
        "content": primary["uri"],
        "src_imgs": [source["uri"]],
        "selected_region": {
            "top_left": {"x": 0, "y": 0},
            "bottom_right": {"x": int(source["width"]), "y": int(source["height"])},
        },
        "name": name,
    }
