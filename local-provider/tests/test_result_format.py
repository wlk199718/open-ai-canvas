import unittest

import test_task_lifecycle


class ResultFormatTests(unittest.TestCase):
    def test_image_results_use_signature_and_preserve_strict_reference_checks(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import base64
import json
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main

main.license_active = lambda: True
main.store.set_setting("client_hash", main.digest("fixture-token"))
client = TestClient(main.app)
headers = {"Authorization": "Bearer fixture-token"}
session = main.issue_admin_session()
admin = {"Authorization": "Bearer " + session["token"]}
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile) "
               "VALUES ('cookie','fixture',?,'vidu-cookie','shared')", (b"fixture",))
    for task in ("legacy", "download", "invalid", "outside", "other-task"):
        db.execute("INSERT INTO tasks (id,account_id,provider,task_type,model,prompt,input_json,status,result_url,created_at,updated_at) "
                   "VALUES (?,'cookie','image','image','3.2_fast_m','fixture','{}','downloading','https://example.invalid/result','now','now')", (task,))
root = main.store.root / "results"
root.mkdir()
jpeg = b"\xff\xd8\xff\xe0fixture"
for name, content, expected_type, extension in (
    ("legacy", jpeg, "image/jpeg", ".jpg"),
    ("png", b"\x89PNG\r\n\x1a\nfixture", "image/png", ".png"),
    ("webp", b"RIFF\x00\x00\x00\x00WEBPVP8 fixture", "image/webp", ".webp"),
):
    if name != "legacy":
        with main.store.transaction() as db:
            db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) "
                       "VALUES (?,'image','image','3.2_fast_m','fixture','{}','completed','now','now')", (name,))
    target = root / (name + ".png")
    target.write_bytes(content)
    main.store.update_task(name, status="completed", result_path=str(target))
    for prefix, auth in (("/api/v1", headers), ("/api/admin", admin)):
        response = client.get(f"{prefix}/tasks/{name}/result", headers=auth)
        assert response.status_code == 200 and response.content == content, response.text
        assert response.headers["content-type"] == expected_type
        assert f"{name}{extension}" in response.headers["content-disposition"]
assert client.get("/api/v1/tasks/legacy/result").status_code == 401
class Downloader:
    def __init__(self, content): self.content = content
    def download(self, url, target): target.write_bytes(self.content)
main.download_task("download", Downloader(jpeg))
row = main.store.task("download")
assert row["status"] == "completed" and row["result_path"].endswith(".jpg"), row
assert Path(row["result_path"]).read_bytes() == jpeg
main.download_task("invalid", Downloader(b"not an image"))
assert main.store.task("invalid")["status"] == "download_failed"
outside = main.store.root / "outside.png"
outside.write_bytes(jpeg)
main.store.update_task("outside", status="completed", result_path=str(outside))
main.store.update_task("other-task", status="completed", result_path=str(root / "legacy.png"))
for task in ("outside", "other-task"):
    assert client.get(f"/api/v1/tasks/{task}/result", headers=headers).status_code == 404
body = {"provider":"q4-video", "model":"q4", "prompt":"fixture", "duration":5,
        "references":["data:image/jpeg;base64," + base64.b64encode(jpeg).decode()]}
with patch.object(main, "wake_worker"):
    accepted = client.post("/api/v1/tasks", headers=headers, json=body)
    assert accepted.status_code == 200, accepted.text
    params = json.loads(main.store.task(accepted.json()["id"])["input_json"])
    reference = Path(params["references"][0])
    assert reference.suffix == ".jpg" and reference.read_bytes() == jpeg
    body["references"] = ["data:image/png;base64," + base64.b64encode(jpeg).decode()]
    assert client.post("/api/v1/tasks", headers=headers, json=body).status_code == 422
""")
