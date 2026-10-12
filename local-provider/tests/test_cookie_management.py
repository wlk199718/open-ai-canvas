import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CookieManagementTests(unittest.TestCase):
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
                cwd=Path(__file__).resolve().parents[1], env=environment,
                text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_batch_messages_preserve_receipts_and_block_unknown_retries(self):
        self.run_isolated(r"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import requests
from fastapi.testclient import TestClient
import app.main as main
from app.providers.cookie_import import encode_cookie_record

main.decrypt_secret = lambda value: value.decode()
main.store.set_setting("admin_session", {
    "hash": main.digest("test-session"),
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
})
headers = {"Authorization": "Bearer test-session"}
client = TestClient(main.app)
with main.store.transaction() as db:
    for account_id, profile in [("normal", "video"), ("low", "video"), ("uncertain", "video"), ("disabled", "video"), ("image", "image"), ("busy", "video")]:
        secret = encode_cookie_record({"JWT": "jwt", "SSUID": account_id}).encode()
        db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,enabled) VALUES (?,?,?,'vidu-cookie',?,?)",
                   (account_id, account_id, secret, profile, int(account_id != "disabled")))
    db.execute("INSERT INTO tasks (id,account_id,model,prompt,input_json,status,created_at,updated_at) VALUES ('task','busy','q4','fixture','{}','processing','now','now')")

messages = []
timeouts = []
def upstream(session, method, url, **kwargs):
    account_id = session.cookies.get("SSUID") or session.headers["Cookie"].split("SSUID=")[-1].split(";")[0]
    timeouts.append((method, kwargs["timeout"]))
    if url.endswith("/credits/me"):
        data = {"credits": 180 if account_id == "low" else 285}
    elif url.endswith("/claw/my"):
        data = {"claws": [{"id": "workspace", "status": "active"}]}
    elif url.endswith("/trials/me/all"):
        data = {"trials": []}
    elif url.endswith("/claw/session"):
        data = {"session_id": "session-" + account_id}
    elif url.endswith("/claw/chat"):
        messages.append(account_id)
        assert kwargs["json"]["msg"]["contents"][0]["content"] == "你好"
        if account_id == "uncertain":
            raise requests.Timeout("fixture response lost")
        data = {"message_id": "message"}
    else:
        raise AssertionError(url)
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(data).encode()
    return response

settings = {**main.VIDEO_DEFAULTS, "claw_message_workers": 2, "submit_timeout": 99, "request_timeout": 44}
assert client.put("/api/admin/video-settings", headers=headers, json=settings).status_code == 200
assert client.put("/api/admin/video-settings", headers=headers, json={**settings, "claw_message_workers": -1}).status_code == 422
with patch("requests.Session.request", autospec=True, side_effect=upstream):
    response = client.post("/api/admin/claw/test-messages", headers=headers,
        json={"account_ids": ["normal", "normal", "low", "uncertain", "disabled", "image", "busy"]})
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 1 and response.json()["failed"] == 5, response.text
    assert sorted(messages) == ["normal", "uncertain"], messages
    second = client.post("/api/admin/claw/test-messages", headers=headers,
        json={"account_ids": ["normal", "uncertain"]})
    assert second.json()["skipped"] == 1 and second.json()["failed"] == 1, second.text
    assert len(messages) == 2
    normal = client.post("/api/admin/accounts/normal/balance", headers=headers).json()
    assert normal["claw_state"]["test_message_status"] == "success"
    assert normal["claw_state"]["session_id"] == "session-normal"
    uncertain = main.store.one("SELECT * FROM accounts WHERE id='uncertain'")
    assert json.loads(uncertain["claw_state_json"])["test_message_status"] == "unknown"
    reset = client.post("/api/admin/cookie-accounts/reset", headers=headers)
    assert "busy" in reset.json()["skipped"]
    assert json.loads(main.store.one("SELECT claw_state_json FROM accounts WHERE id='normal'")["claw_state_json"])["test_message_status"] == "success"
    assert client.post("/api/admin/claw/test-message", headers=headers, json={"account_id": "normal"}).json()["skipped"]
    assert client.post("/api/admin/claw/test-message", headers=headers, json={"account_id": "uncertain"}).status_code == 409
assert ("GET", 44) in timeouts and ("POST", 99) in timeouts, timeouts
assert client.post("/api/admin/claw/test-messages", json={"account_ids":["normal"]}).status_code == 401
assert client.post("/api/admin/claw/test-messages", headers=headers, json={"account_ids":[]}).status_code == 422
print(json.dumps({"ok": True}))
""")

    def test_cleanup_is_scoped_and_browser_receives_only_requested_account(self):
        self.run_isolated(r"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
from app.providers.cookie_import import encode_cookie_record

main.decrypt_secret = lambda value: value.decode()
main.store.set_setting("admin_session", {
    "hash": main.digest("test-session"),
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
})
headers = {"Authorization": "Bearer test-session"}
client = TestClient(main.app)
with main.store.transaction() as db:
    for account_id, provider, profile, status, amount in [
        ("wan", "wan-video", "shared", "expired", 100),
        ("expired", "vidu-cookie", "video", "expired", 100),
        ("zero", "vidu-cookie", "video", "ready", 0),
        ("shared", "vidu-cookie", "shared", "ready", 0),
        ("image", "vidu-cookie", "image", "ready", 0),
        ("waf", "vidu-cookie", "video", "check_error", 0),
        ("busy", "vidu-cookie", "video", "expired", 0),
        ("active", "vidu-cookie", "video", "ready", 100),
    ]:
        secret = encode_cookie_record({"JWT": "jwt", "SSUID": account_id}).encode()
        db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,video_balance,video_checked_at) VALUES (?,?,?,?,?,?,'valid',?,'now')",
                   (account_id, account_id, secret, provider, profile, status, amount))
    db.execute("INSERT INTO tasks (id,account_id,model,prompt,input_json,status,created_at,updated_at) VALUES ('task','busy','q4','fixture','{}','processing','now','now')")
with patch.object(main, "open_cookie_browser", return_value={"ok": True}) as opened:
    response = client.post("/api/admin/cookie-accounts/active/browser?channel=claw-video", headers=headers)
    assert response.status_code == 200, response.text
    opened.assert_called_once_with({"JWT": "jwt", "SSUID": "active"}, "claw-video")
    assert "jwt" not in response.text
    assert client.post("/api/admin/cookie-accounts/wan/browser", headers=headers).status_code == 404
    assert client.post("/api/admin/cookie-accounts/active/browser?channel=other", headers=headers).status_code == 422
assert client.post("/api/admin/cookie-accounts/active/browser").status_code == 401
lock = main.account_message_lock("zero")
lock.acquire()
try:
    assert client.delete("/api/admin/accounts/zero", headers=headers).status_code == 409
finally:
    lock.release()
clean = client.post("/api/admin/accounts/clean-invalid?provider=vidu-cookie&include_exhausted=true", headers=headers)
assert clean.status_code == 200, clean.text
assert clean.json()["count"] == 2, clean.text
assert {item["id"] for item in clean.json()["deleted"]} == {"zero", "expired"}
assert main.store.setting("archived_account:zero")["secret"]["encrypted"]
remaining = {row["id"] for row in main.store.rows("SELECT id FROM accounts")}
assert {"wan", "shared", "image", "waf", "busy", "active"} == remaining
main.store.set_setting("video_settings", {**main.VIDEO_DEFAULTS, "check_concurrency": 3})
assert client.get("/api/admin/overview", headers=headers).json()["video_settings"]["check_concurrency"] == 3
print(json.dumps({"ok": True}))
""")


if __name__ == "__main__":
    unittest.main()
