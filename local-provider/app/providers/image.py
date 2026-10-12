from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .reference_image import upload_reference
from .vidu import ORIGIN, ViduClient, task_identifier

RESOLUTIONS = ("1080p", "2k", "4k")
RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")
MODELS = [
    {"id": "nano2 lite", "name": "Nano2 Lite", "api_model": "3.2_lite_m"},
    {"id": "3.2_fast_m", "name": "Nano Banana 2", "api_model": "3.2_fast_m"},
    {"id": "3.2_pro_m", "name": "Nano Banana Pro", "api_model": "3.2_pro_m"},
    {"id": "3.2_image_2", "name": "GPT Image 2", "api_model": "3.2_image_2"},
    {"id": "3.2_image_2_5_fast", "name": "GPT Image 2.5 Fast", "api_model": "3.2_image_2_5_fast"},
    {"id": "3.2_image_2_5_pro", "name": "GPT Image 2.5 Pro", "api_model": "3.2_image_2_5_pro"},
]


def _model(model: str) -> dict:
    selected = next((item for item in MODELS if item["id"] == model), None)
    if not selected:
        raise ValueError("图片模型不受支持")
    return selected


def _urls(value):
    if isinstance(value, dict):
        for key in ("creations", "data", "result", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    yield from _urls(item)
        for key in ("nomark_uri", "no_watermark_uri", "download_uri", "downloadUrl", "uri", "url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                yield candidate
    elif isinstance(value, list):
        for item in value:
            yield from _urls(item)


class ImageClient(ViduClient):
    upload_timeout = 600
    submit_timeout = 120
    download_timeout = 300

    def __init__(self, cookies: dict[str, str], timeout: int = 60):
        super().__init__(cookies, timeout, referer="text2image")

    def request(self, method: str, path: str, **kwargs):
        timeout = kwargs.pop("timeout", self.submit_timeout if method == "POST" else self.timeout)
        return super().request(method, path, timeout=timeout, **kwargs)

    def submit(self, prompt: str, model: str, resolution: str, ratio: str, samples: int, references: list[Path]):
        selected = _model(model)
        prompts = [{"type": "text", "content": prompt}]
        uploaded = []
        for path in references:
            asset = upload_reference(self, path)
            uri, width, height = asset["uri"], asset["width"], asset["height"]
            uploaded.append(asset)
            prompts.append({"type": "image", "content": uri, "src_imgs": [uri],
                            "selected_region": {
                                "top_left": {"x": 0, "y": 0},
                                "bottom_right": {"x": width, "y": height},
                            },
                            "name": path.name})
        task_type = "reference2image" if uploaded else "text2image"
        if len(uploaded) > 1:
            prompts[0]["content"] = "".join(f"[@图{i}]" for i in range(1, len(uploaded) + 1)) + f" {prompt}"
        body = {
            "type": task_type,
            "input": {"prompts": prompts, "editor_mode": "normal", "enhance": True},
            "settings": {
                "duration": 0, "resolution": resolution, "aspect_ratio": ratio,
                "sample_count": samples, "movement_amplitude": "auto",
                "schedule_mode": "normal", "model_version": selected["api_model"],
                "use_trial": model in {"3.2_image_2_5_fast", "3.2_image_2_5_pro"} and samples == 1,
            },
        }
        if uploaded:
            body["settings"]["image_count"] = len(uploaded)
        path = "/vidu/v1/tasks" if model in {"3.2_image_2_5_fast", "3.2_image_2_5_pro"} else "/vidu/v1/tasks/tool"
        headers = None
        if path.endswith("/tool"):
            headers = {key: None for key in self.session.headers}
            headers.update({
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "Referer": ORIGIN + "/zh/create/" + task_type,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": "; ".join(f"{key}={value}" for key, value in self.cookies.items()
                                    if key.lower() not in {"aws-waf-token", "aws_waf_token", "awswaftoken"}),
                "Accept-Language": "zh", "X-Platform": "web", "X-App-Version": "-",
            })
        if headers is None:
            headers = {"Referer": ORIGIN + "/zh/create/" + task_type}
        response = self.request("POST", path, json=body, headers=headers)
        task_id = task_identifier(response)
        if not task_id:
            raise ValueError("图片任务提交未返回任务 ID")
        return str(task_id)

    def poll(self, upstream_id: str):
        value = self.request("GET", f"/vidu/v1/tasks/{quote(upstream_id, safe='')}")
        state = str(value.get("state") or value.get("status") or "processing").lower()
        urls = list(_urls(value))
        return {"status": "completed" if state == "success" else state,
                "progress": {"preparation": 30, "queueing": 50, "processing": 70}.get(state, 85),
                "url": urls[0] if urls else None}

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
