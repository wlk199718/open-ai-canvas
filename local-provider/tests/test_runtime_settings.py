import base64
import io
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

import test_task_lifecycle
from app.proxy_settings import proxy_nodes, resolve_proxy
from app.runtime_settings import migrate_settings, public_settings, settings_for
from app.store import Store
from app.wan import WanClient
from app.wan_upload import image_payload, upload_reference


class RuntimeSettingsTests(unittest.TestCase):
    def test_existing_settings_split_once_and_remain_independent(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.set_setting("wan_settings", {"poll_interval": 9, "task_timeout": 900, "min_balance": 700})
            store.set_setting("cookie_settings", {"request_timeout": 42, "task_timeout": 1200, "claw_message_workers": 2})
            migrate_settings(store)
            self.assertEqual(settings_for(store, "image")["request_timeout"], 42)
            self.assertEqual(settings_for(store, "video")["task_timeout"], 1200)
            self.assertEqual(settings_for(store, "wan")["min_balance"], 700)
            self.assertEqual(settings_for(store, "video")["min_balance"], 0)
            store.set_setting("image_settings", {"request_timeout": 111})
            migrate_settings(store)
            self.assertEqual(settings_for(store, "image")["request_timeout"], 111)
            self.assertEqual(settings_for(store, "video")["request_timeout"], 42)
            self.assertNotIn("claw_message_workers", public_settings(store, "image"))
            store.db.close()

    def test_channel_settings_write_validate_encrypt_and_reach_clients(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import base64, json
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
from app.providers.cookie_import import encode_cookie_record
main.license_active = lambda: True
headers = {"Authorization": "Bearer " + main.issue_admin_session()["token"]}
client = TestClient(main.app)
main.decrypt_secret = lambda value: value.decode()
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,image_balance,video_balance) VALUES ('pool','pool',?,'vidu-cookie','shared',200,200)",
               (encode_cookie_record({"JWT":"fixture-jwt","SSUID":"fixture-ssuid"}).encode(),))
    db.execute("INSERT INTO accounts (id,name,secret,provider) VALUES ('wan','wan',?,'wan-video')", (b"fixture-wan",))
proxy = "http://fixture-user:fixture-password@127.0.0.1:8888"
with patch("app.runtime_settings.encrypt_secret", side_effect=lambda value: b"encrypted:" + value.encode()), patch("app.runtime_settings.decrypt_secret", side_effect=lambda value: value.removeprefix(b"encrypted:").decode()):
    for kind, timeout in (("wan",31), ("video",45), ("image",77)):
        assert client.put("/api/admin/"+kind+"-settings", json={"request_timeout":timeout,"proxy_server":proxy}, headers=headers).status_code == 200
    assert proxy not in json.dumps(main.store.rows("SELECT * FROM settings"))
    assert client.put("/api/admin/image-settings", json={"request_timeout":78}, headers=headers).json()["proxy_configured"]
    info = client.get("/api/admin/overview", headers=headers).json()
    assert proxy not in json.dumps(info)
    assert info["video_settings"]["request_timeout"] == 45
    assert info["image_settings"]["request_timeout"] == 78
    assert client.put("/api/admin/image-settings", json={"claw_message_workers":5}, headers=headers).status_code == 422
    assert client.put("/api/admin/wan-settings", json={"retry_count":11}, headers=headers).status_code == 422
    assert client.put("/api/admin/video-settings", json={"proxy_server":"file://bad"}, headers=headers).status_code == 422
    assert client.put("/api/admin/image-settings", json={"request_timeout":100}, headers={}).status_code == 401
    row = main.store.one("SELECT * FROM accounts WHERE id='pool'")
    image = main.client_for(row,"image")
    video = main.client_for(row,"q4-video")
    assert image.timeout == 78 and video.timeout == 45
    assert image.session.proxies["https"] == proxy
    image.close(); video.close()
    assert client.put("/api/admin/video-settings", json={"browser_assist_enabled":True,"browser_window_count":2}, headers=headers).status_code == 200
    q4 = main.client_for(row,"q4-video")
    claw = main.client_for(row,"claw-video")
    assert q4.browser_assist_enabled and q4.browser_window_count == 2
    assert not claw.browser_assist_enabled
    q4.close(); claw.close()
    assert client.put("/api/admin/image-settings", json={"proxy_server":""}, headers=headers).json()["proxy_configured"] is False
    assert main.store.setting("image_proxy_credential") == ""
""")

    def test_wan_threshold_does_not_exclude_image_account(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import json
from unittest.mock import create_autospec, patch
import app.main as main
from app.providers.image import ImageClient
main.license_active = lambda: True
main.store.set_setting("wan_settings", {**main.WAN_DEFAULTS, "min_balance":1000000})
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS,"min_balance":10,"poll_interval":0})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,image_balance) VALUES ('account','account',?,'vidu-cookie','image',100)", (b"fixture",))
    db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) VALUES ('task','image','image','nano2 lite','fixture',?,'queued','now','now')",
        (json.dumps({"resolution":"1080p","aspect_ratio":"1:1","sample_count":1,"references":[]}),))
adapter = create_autospec(ImageClient, instance=True)
adapter.submit.return_value = "accepted"
adapter.poll.return_value = {"status":"completed","progress":100,"url":"https://cdn.example/image"}
adapter.download.side_effect = lambda url,target: (target.parent.mkdir(parents=True,exist_ok=True),target.write_bytes(b"\x89PNG\r\n\x1a\nfixture"))
with patch.object(main,"client_for",return_value=adapter):
    main.run_task("task")
assert main.store.task("task")["status"] == "completed"
""")

    def test_wan_inline_reference_is_saved_idempotently_and_uploaded_before_submission(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import base64,io,json
from pathlib import Path
from unittest.mock import create_autospec,patch
from fastapi.testclient import TestClient
from PIL import Image
import app.main as main
from app.wan import WanClient
main.license_active = lambda: True
main.store.set_setting("client_hash",main.digest("fixture-client"))
main.store.set_setting("wan_settings",{**main.WAN_DEFAULTS,"poll_interval":0})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,balance) VALUES ('wan','wan',?,'wan-video',200)",(b"fixture",))
output=io.BytesIO();Image.new("RGB",(8,8),"red").save(output,format="PNG")
body={"provider":"wan-video","model":"dashscope-wan3.0-video","duration":5,"prompt":"fixture",
      "references":["data:image/png;base64,"+base64.b64encode(output.getvalue()).decode()]}
headers={"Authorization":"Bearer fixture-client","Idempotency-Key":"wan-inline-fixture"}
adapter=create_autospec(WanClient,instance=True)
adapter.upload_reference.return_value="https://cdn.example/uploaded.png"
adapter.create_project.return_value=1
adapter.submit.return_value="up-wan"
adapter.poll.return_value={"status":"completed","progress":100,"url":"https://cdn.example/video"}
adapter.download.side_effect=lambda url,target:(target.parent.mkdir(parents=True,exist_ok=True),target.write_bytes(b"fixture-video"))
client=TestClient(main.app)
with patch.object(main,"wake_worker"),patch.object(main,"client_for",return_value=adapter):
    created=client.post("/api/v1/tasks",headers=headers,json=body)
    assert created.status_code==200,created.text
    task_id=created.json()["id"]
    replay=client.post("/api/v1/tasks",headers=headers,json=body)
    assert replay.status_code==200 and replay.json()["id"]==task_id
    params=json.loads(main.store.task(task_id)["input_json"])
    assert Path(params["references"][0]).read_bytes()==output.getvalue()
    main.run_task(task_id)
assert main.store.task(task_id)["status"]=="completed"
adapter.upload_reference.assert_called_once_with(params["references"][0])
assert adapter.submit.call_args.args[4]==["https://cdn.example/uploaded.png"]
""")

    def test_wan_read_retries_but_submission_never_replays(self):
        client = WanClient("fixture")
        client.retry_count = 2
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"data":{"points":100}}'
        with patch.object(client.session, "request", side_effect=[requests.ConnectionError(), response]) as call, patch("app.wan.time.sleep"):
            client.wallet()
            self.assertEqual(call.call_count, 2)
        with patch.object(client.session, "request", side_effect=requests.ConnectionError()) as call:
            with self.assertRaises(requests.ConnectionError):
                client.submit("fixture", "model", 5, "16:9", [], 1)
            self.assertEqual(call.call_count, 1)
        client.close()

    def test_wan_compression_and_signed_upload_isolate_account_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.png"
            Image.new("RGB", (3000, 2000), "blue").save(path)
            original = path.read_bytes()
            content, media = image_payload(path, True)
            with Image.open(io.BytesIO(content)) as image:
                self.assertLessEqual(max(image.size), 2048)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(image_payload(path, False)[0], original)
            client = WanClient("fixture-account-token")
            presign = {"bucket":"bucket","object_key":"reference.jpg","access_key_id":"fixture-id",
                       "secret_access_key":"fixture-secret","session_token":"fixture-sts",
                       "upload_url":"https://bucket.oss.example/reference.jpg","file_url":"https://cdn.example/reference.jpg"}
            response = requests.Response()
            response.status_code = 200
            with patch.object(client, "request", return_value=presign), patch.object(client.media_session, "request", return_value=response) as upload, patch("app.wan_upload.public_https", side_effect=lambda value:value):
                self.assertEqual(upload_reference(client, path), presign["file_url"])
                headers = upload.call_args.kwargs["headers"]
                self.assertTrue(headers["Authorization"].startswith("OSS fixture-id:"))
                self.assertNotIn("fixture-account-token", json.dumps(headers))
                self.assertNotIn("Authorization", client.media_session.headers)
            client.close()

    def test_proxy_subscription_filters_protocols_and_preserves_credentials(self):
        nodes = proxy_nodes('proxies:\n- type: http\n  server: localhost\n  port: 8888\n  username: a\n  password: p@ss\n- type: vmess\n  server: example.com\n  port: 443', 6)
        self.assertEqual(nodes, ["http://a:p%40ss@localhost:8888"])
        encoded = base64.b64encode(b"socks5h://127.0.0.1:9999\nhttp://localhost:8888").decode()
        self.assertEqual(len(proxy_nodes(encoded, 6)), 2)
        with self.assertRaises(ValueError):
            proxy_nodes("vmess://unsupported", 6)
        settings = {"proxy_server":"http://localhost:8888"}
        self.assertEqual(resolve_proxy(settings,"account"), "http://localhost:8888")

    def test_q4_browser_transport_never_applies_to_claw_or_image(self):
        from app.providers.video import CookieVideoClient
        client = CookieVideoClient({"JWT":"jwt","SSUID":"ssuid"})
        client.browser_assist_enabled = True
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"id":"fixture-task"}'
        with patch("app.video_browser.browser_pool.request", return_value=response) as browser, patch.object(client.session,"request",return_value=response) as api:
            client.request("POST","/vidu/v1/tasks",json={"fixture":True})
            self.assertEqual(browser.call_count, 1)
            api.assert_not_called()
            client.request("GET","/claw/v1/sessions")
            self.assertEqual(api.call_count, 1)
        client.close()

    def test_real_browser_helper_submits_once_to_isolated_fixture(self):
        from app.video_browser import BrowserSession, browser_available
        if not browser_available():
            self.skipTest("Chrome / Edge is not installed")
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                body = b"<html><body>isolated browser fixture</body></html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = b'{"id":"isolated-task"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        browser = None
        try:
            with patch("app.video_browser.VIDEO_PAGE", base), patch("app.video_browser.VIDEO_ORIGIN", base):
                browser = BrowserSession({"JWT":"fixture","SSUID":"fixture"}, "")
                response = browser.request("POST", base + "/submit", {"Content-Type":"application/json"}, {"fixture":True}, 10)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"id":"isolated-task"})
                self.assertEqual(observed, [{"fixture":True}])
        finally:
            if browser:
                browser.close()
            server.shutdown()
            server.server_close()
