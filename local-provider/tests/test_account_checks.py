import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AccountCheckTests(unittest.TestCase):
    def run_isolated(self, script):
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["ZAOWU_PROVIDER_DATA_DIR"] = directory
            environment["ZAOWU_PROVIDER_DEV_MODE"] = "1"
            environment["ZAOWU_PROVIDER_ALLOW_DEV_MODE"] = "1"
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT, env=environment, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_imported_cookie_record_reaches_real_video_and_image_adapters(self):
        self.run_isolated(r"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import requests
from fastapi.testclient import TestClient
import app.main as main

main.encrypt_secret = lambda value: value.encode()
main.decrypt_secret = lambda value: value.decode()
main.store.set_setting("admin_session", {
    "hash": main.digest("test-session"),
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
})
calls = []
def upstream(session, method, url, **kwargs):
    cookie = session.headers["Cookie"]
    assert "JWT=jwt-value" in cookie and "SSUID=ssuid-" in cookie
    assert "cookies=" not in cookie and "{" not in cookie
    assert session.headers["X-Aws-Waf-Token"] == "waf-value"
    assert session.headers["Sec-Fetch-Site"] == "same-site"
    calls.append((cookie, url))
    response = requests.Response()
    response.status_code = 200
    if url.endswith("/credits/me"):
        wallet = {"credits": 285}
        data = {"data": wallet} if "ssuid-image" in cookie else wallet
    elif url.endswith("/claw/my"):
        data = {"claws": [{"id": "workspace-1", "status": "active"}]}
    elif url.endswith("/trials/me/all"):
        data = {"trials": [
            {"remaining_times": count, "model_versions": ["3.4"],
             "task_types": ["img2video", "character2video"], "resolutions": [resolution]}
            for resolution, count in [("540p", 1), ("720p", 0), ("1080p", 1)]
        ] + [{"remaining_times": 2, "model_versions": ["3.2_image_2_5_fast"],
              "task_types": ["text2image"]}]}
    else:
        raise AssertionError(url)
    response._content = json.dumps(data).encode()
    return response

client = TestClient(main.app)
headers = {"Authorization": "Bearer test-session"}
with patch("requests.Session.request", autospec=True, side_effect=upstream):
    for profile in ("shared", "video", "image"):
        cookies = [
            {"name": "JWT", "value": "jwt-value", "domain": ".vidu.com"},
            {"name": "SSUID", "value": "ssuid-" + profile, "domain": ".vidu.com"},
            {"name": "aws-waf-token", "value": "waf-value", "domain": ".vidu.com"},
        ]
        imported = client.post("/api/admin/cookie-accounts/import",
            headers=headers, data={"credit_profile": profile},
            files=[("files", (profile + ".json", json.dumps(cookies).encode(), "application/json"))])
        assert imported.status_code == 200, imported.text
        assert imported.json()["count"] == 1, imported.text
        account_id = imported.json()["imported"][0]["id"]
        checked = client.post("/api/admin/accounts/" + account_id + "/balance", headers=headers)
        assert checked.status_code == 200, checked.text
        account = checked.json()
        assert account["balance"] == 285 and account["auth_state"] == "valid"
        if profile in ("shared", "video"):
            assert account["video_balance"] == 285
            assert account["q4_trials"]["remaining_by_resolution"] == {"540p": 1, "720p": 0, "1080p": 1}
            assert account["claw_state"]["status"] == "active"
        if profile in ("shared", "image"):
            assert account["image_balance"] == 285
            assert account["image_trials"]["3.2_image_2_5_fast:text2image"]["remaining_times"] == 2
            assert "3.4:img2video" not in account["image_trials"]
        if profile == "image":
            assert not any("ssuid-image" in cookie and url.endswith("/claw/my") for cookie, url in calls)
        assert account["check_error"] == ""
print(json.dumps({"ok": True}))
""")

    def test_auth_expiry_is_distinct_from_forbidden_or_waf(self):
        self.run_isolated(r"""
import json
from unittest.mock import patch
import requests
import app.main as main
from app.providers.cookie_import import encode_cookie_record

main.decrypt_secret = lambda value: value.decode()
secret = encode_cookie_record({"JWT": "jwt", "SSUID": "ssuid"}).encode()
with main.store.transaction() as db:
    for account_id in ("expired", "forbidden", "waf"):
        db.execute("INSERT INTO accounts (id, name, secret, provider, credit_profile) VALUES (?, ?, ?, 'vidu-cookie', 'video')",
                   (account_id, account_id, secret))

for account_id, status, waf in [("expired", 401, ""), ("forbidden", 403, ""), ("waf", 401, "challenge")]:
    response = requests.Response()
    response.status_code = status
    response._content = b""
    if waf:
        response.headers["x-amzn-waf-action"] = waf
    with patch("requests.Session.request", return_value=response):
        try:
            main.check_account(account_id)
            raise AssertionError("expected upstream error")
        except main.HTTPException as exc:
            assert exc.status_code == 502
    stored = main.store.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    if account_id == "expired":
        assert stored["status"] == stored["auth_state"] == "expired"
    else:
        assert stored["status"] == "check_error"
        assert stored["auth_state"] == "unchecked"
        assert stored["auth_error"] == ""
        assert "未确认登录失效" in stored["check_error"]
print(json.dumps({"ok": True}))
""")

    def test_optional_checks_cannot_discard_valid_balance(self):
        self.run_isolated(r"""
import json
from unittest.mock import patch
import requests
import app.main as main
from app.providers.cookie_import import encode_cookie_record

main.decrypt_secret = lambda value: value.decode()
secret = encode_cookie_record({"JWT": "jwt", "SSUID": "ssuid"}).encode()
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id, name, secret, provider, credit_profile, status, auth_state) "
               "VALUES ('account', 'account', ?, 'vidu-cookie', 'shared', 'expired', 'expired')", (secret,))
def upstream(session, method, url, **kwargs):
    response = requests.Response()
    if url.endswith("/credits/me"):
        response.status_code = 200
        response._content = b'{"credits":285}'
    else:
        response.status_code = 403 if url.endswith("/claw/my") else 401
        response._content = b""
    return response
with patch("requests.Session.request", autospec=True, side_effect=upstream):
    account = main.check_account("account")
assert account["video_balance"] == account["image_balance"] == 285
assert account["status"] == "ready" and account["auth_state"] == "valid"
assert account["auth_error"] == ""
assert "Claw" in account["check_error"] and "试用额度" in account["check_error"]
assert account["q4_trials"] == {}
print(json.dumps({"ok": True}))
""")


if __name__ == "__main__":
    unittest.main()
