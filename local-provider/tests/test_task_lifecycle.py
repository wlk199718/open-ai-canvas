import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TaskLifecycleTests(unittest.TestCase):
    def run_isolated(self, script):
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment.update({
                "ZAOWU_PROVIDER_DATA_DIR": directory,
                "ZAOWU_PROVIDER_DEV_MODE": "1",
                "ZAOWU_PROVIDER_ALLOW_DEV_MODE": "1",
            })
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_image_task_polls_with_image_client_contract_and_downloads(self):
        script = r"""
import json
from pathlib import Path
from unittest.mock import create_autospec, patch

import app.main as main
from app.providers.cookie_import import encode_cookie_record
from app.providers.image import ImageClient

main.decrypt_secret = lambda value: value.decode()
main.encrypt_secret = lambda value: value.encode()
main.license_active = lambda: True
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS, "poll_interval": 0, "task_timeout": 60})

secret = encode_cookie_record({"JWT": "jwt", "SSUID": "ssuid"}).encode()
with main.store.transaction() as db:
    db.execute(
        "INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,image_balance,image_checked_at) "
        "VALUES ('image','image',?,'vidu-cookie','image','ready','valid',100,'now')",
        (secret,),
    )
    db.execute(
        "INSERT INTO tasks "
        "(id,account_id,provider,task_type,model,prompt,input_json,status,upstream_id,created_at,updated_at) "
        "VALUES ('task','image','image','image','3.2_fast_m','test','{}','submitted','up-1','now','now')"
    )

client = create_autospec(ImageClient, instance=True)
client.poll.return_value = {"status": "completed", "progress": 100, "url": "https://cdn.example/image.png"}
client.download.side_effect = lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"\x89PNG\r\n\x1a\nfixture"))
client.close.return_value = None

with patch.object(main, "client_for", return_value=client):
    main.run_task("task")

row = main.store.task("task")
assert row["status"] == "completed", row
assert Path(row["result_path"]).is_file()
client.poll.assert_called_once_with("up-1")
client.download.assert_called_once()
print(json.dumps({"status": row["status"], "result": row["result_path"]}))
"""
        self.assertIn('"status": "completed"', self.run_isolated(script))

    def test_cookie_channels_create_and_complete_using_shared_pool(self):
        self.run_isolated(r"""
from pathlib import Path
from unittest.mock import create_autospec, patch
from fastapi.testclient import TestClient
import app.main as main
from app.providers.image import ImageClient
from app.providers.video import CookieVideoClient

main.license_active = lambda: True
main.store.set_setting("client_hash", main.digest("client-token"))
main.store.set_setting("wan_settings", {**main.WAN_DEFAULTS, "poll_interval": 0})
main.store.set_setting("video_settings", {**main.VIDEO_DEFAULTS, "poll_interval": 0})
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS, "poll_interval": 0})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,balance) "
               "VALUES ('cookie','cookie',?,'vidu-cookie','shared',300)", (b"fixture",))
client = TestClient(main.app)
headers = {"Authorization": "Bearer client-token"}
for provider, model, duration in [("q4-video", "q4", 5), ("claw-video", "claw", 10), ("image", "3.2_fast_m", 0)]:
    adapter = create_autospec(ImageClient if provider == "image" else CookieVideoClient, instance=True)
    if provider == "image":
        adapter.submit.return_value = "up-image"
    elif provider == "q4-video":
        adapter.submit_q4.return_value = "up-q4"
    else:
        adapter.submit_claw.return_value = ("session:message", "session")
    adapter.poll.return_value = {"status": "completed", "progress": 100, "url": "https://cdn.example/result"}
    result_bytes = b"\x89PNG\r\n\x1a\nfixture" if provider == "image" else b"fixture"
    adapter.download.side_effect = lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(result_bytes))
    with patch.object(main, "wake_worker"), patch.object(main, "client_for", return_value=adapter):
        response = client.post("/api/v1/tasks", headers=headers, json={
            "provider": provider, "model": model, "prompt": "fixture", "duration": duration,
        })
        assert response.status_code == 200, (provider, response.text)
        task_id = response.json()["id"]
        main.run_task(task_id)
        row = main.store.task(task_id)
        assert row["status"] == "completed", row
        assert row["account_id"] == "cookie"
        if provider == "image":
            adapter.poll.assert_called_once_with(row["upstream_id"])
        else:
            adapter.poll.assert_called_once_with(row["upstream_id"], provider)
        result = client.get(f"/api/v1/tasks/{task_id}/result", headers=headers)
        assert result.status_code == 200 and result.content == result_bytes
        assert result.headers["content-type"] == ("image/png" if provider == "image" else "video/mp4")
        adapter.close.assert_called_once()
assert client.post("/api/v1/tasks", json={"provider": "image", "model": "3.2_fast_m", "prompt": "fixture"}).status_code == 401
""")

    def test_image_download_retry_preserves_provider_and_upstream_task(self):
        self.run_isolated(r"""
from unittest.mock import create_autospec, patch
from fastapi.testclient import TestClient
import app.main as main
from app.providers.image import ImageClient
main.license_active = lambda: True
main.store.set_setting("client_hash", main.digest("client-token"))
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile) "
               "VALUES ('cookie','cookie',?,'vidu-cookie','image')", (b"fixture",))
    db.execute("INSERT INTO tasks (id,account_id,provider,task_type,model,prompt,input_json,status,upstream_id,result_url,created_at,updated_at) "
               "VALUES ('task','cookie','image','image','3.2_fast_m','fixture','{}','download_failed','up-image','https://cdn.example/result','now','now')")
adapter = create_autospec(ImageClient, instance=True)
adapter.download.side_effect = lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"\x89PNG\r\n\x1a\nfixture"))
client = TestClient(main.app)
with patch.object(main, "wake_worker"), patch.object(main, "client_for", return_value=adapter) as factory:
    response = client.post("/api/v1/tasks/task/resume", headers={"Authorization": "Bearer client-token"})
    assert response.status_code == 200, response.text
    main.schedule_worker()
    assert factory.call_args.args[1] == "image", factory.call_args
    assert main.store.task("task")["status"] == "completed"
    assert main.store.task("task")["upstream_id"] == "up-image"
    adapter.submit.assert_not_called()
    adapter.poll.assert_not_called()
    adapter.download.assert_called_once()
    adapter.close.assert_called_once()
""")

    def test_interrupted_poll_does_not_block_next_queued_task(self):
        self.run_isolated(r"""
import json
from unittest.mock import patch
import app.main as main
main.license_active = lambda: True
with main.store.transaction() as db:
    for task_id, stamp in [("first", "1"), ("second", "2")]:
        db.execute("INSERT INTO tasks (id,model,prompt,input_json,status,created_at,updated_at) "
                   "VALUES (?,'fixture','fixture','{}','queued',?,?)", (task_id, stamp, stamp))
def run(task_id):
    main.store.update_task(task_id, status="poll_failed" if task_id == "first" else "completed")
with patch.object(main, "run_task", side_effect=run) as worker:
    main.schedule_worker()
    assert [call.args[0] for call in worker.call_args_list] == ["first", "second"], worker.call_args_list
assert main.store.task("first")["status"] == "poll_failed"
assert main.store.task("second")["status"] == "completed"
""")

    def test_live_submission_is_not_exposed_as_unknown_until_post_returns(self):
        self.run_isolated(r"""
import threading
import time
from unittest.mock import create_autospec, patch
import app.main as main
from app.providers.image import ImageClient

main.license_active = lambda: True
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS, "poll_interval": 0, "task_timeout": 60})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,image_balance,status,auth_state) "
               "VALUES ('image','image',?,'vidu-cookie','image',100,'ready','valid')", (b"fixture",))
    db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) "
               "VALUES ('task','image','image','nano2 lite','fixture','{\"resolution\":\"1080p\",\"aspect_ratio\":\"1:1\",\"sample_count\":1,\"references\":[]}',"
               "'queued','now','now')")

entered = threading.Event()
release = threading.Event()
client = create_autospec(ImageClient, instance=True)
def submit(*args, **kwargs):
    entered.set()
    assert release.wait(5)
    return "up-image"
client.submit.side_effect = submit
client.poll.return_value = {"status": "completed", "progress": 100, "url": "https://cdn.example/result"}
client.download.side_effect = lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"\x89PNG\r\n\x1a\nfixture"))

with patch.object(main, "client_for", return_value=client):
    worker = threading.Thread(target=main.run_task, args=("task",))
    worker.start()
    assert entered.wait(5)
    assert main.store.task("task")["status"] == "submitting"
    release.set()
    worker.join(10)
assert not worker.is_alive()
assert main.store.task("task")["status"] == "completed"
""")

    def test_submission_exception_becomes_unknown_and_is_not_replayed(self):
        self.run_isolated(r"""
from unittest.mock import create_autospec, patch
import app.main as main
from app.providers.image import ImageClient

main.license_active = lambda: True
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS, "poll_interval": 0, "task_timeout": 60})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,image_balance,status,auth_state) "
               "VALUES ('image','image',?,'vidu-cookie','image',100,'ready','valid')", (b"fixture",))
    db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) "
               "VALUES ('task','image','image','nano2 lite','fixture','{\"resolution\":\"1080p\",\"aspect_ratio\":\"1:1\",\"sample_count\":1,\"references\":[]}',"
               "'queued','now','now')")
client = create_autospec(ImageClient, instance=True)
client.submit.side_effect = RuntimeError("connection reset")
with patch.object(main, "client_for", return_value=client):
    main.run_task("task")
row = main.store.task("task")
assert row["status"] == "submission_unknown", row
client.submit.assert_called_once()
main.schedule_worker()
client.submit.assert_called_once()
""")

    def test_restart_quarantines_interrupted_submission_without_replaying(self):
        self.run_isolated(r"""
from unittest.mock import patch
import app.main as main
main.license_active = lambda: True
with main.store.transaction() as db:
    db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) "
               "VALUES ('task','image','image','nano2 lite','fixture','{}','submitting','now','now')")
with patch.object(main.license_manager, "ensure_started"), patch.object(main, "wake_worker"):
    main.restore_tasks()
assert main.store.task("task")["status"] == "submission_unknown"
assert main.store.task("task")["error"]
with patch.object(main, "run_task") as run:
    main.schedule_worker()
    run.assert_not_called()
""")


if __name__ == "__main__":
    unittest.main()
