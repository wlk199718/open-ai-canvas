import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ImageManagementTests(unittest.TestCase):
    def test_image_pool_actions_and_exhausted_cleanup(self):
        script = r"""
import json
from datetime import datetime, timedelta, timezone
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
secret = encode_cookie_record({"JWT": "jwt", "SSUID": "image"}).encode()
with main.store.transaction() as db:
    db.execute(
        "INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,image_balance,image_checked_at,image_trials_json) "
        "VALUES ('image','image',?,'vidu-cookie','image','ready','valid',20,'now','{}')",
        (secret,),
    )
    db.execute(
        "INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,image_balance,image_checked_at,image_trials_json) "
        "VALUES ('trial','trial',?,'vidu-cookie','image','ready','valid',20,'now',?)",
        (secret, json.dumps({"3.2_image_2_5_fast:text2image": {"remaining_times": 1}})),
    )
    db.execute(
        "INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,image_balance,image_checked_at) "
        "VALUES ('invalid','invalid',?,'vidu-cookie','image','expired','expired',20,'now')",
        (secret,),
    )
    for account_id, profile, status, amount, checked in [
        ("boundary", "image", "ready", 50, "now"),
        ("shared", "shared", "ready", 0, "now"),
        ("video", "video", "ready", 0, "now"),
        ("unverified", "image", "ready", 0, None),
        ("waf", "image", "check_error", 0, "now"),
        ("busy", "image", "ready", 10, "now"),
    ]:
        db.execute(
            "INSERT INTO accounts (id,name,secret,provider,credit_profile,status,auth_state,image_balance,image_checked_at) "
            "VALUES (?,?,?,'vidu-cookie',?,?,'valid',?,?)",
            (account_id, account_id, secret, profile, status, amount, checked),
        )
    db.execute("INSERT INTO tasks (id,account_id,model,prompt,input_json,status,created_at,updated_at) VALUES ('task','busy','image','fixture','{}','processing','now','now')")
main.store.set_setting("image_settings", {**main.IMAGE_DEFAULTS, "request_timeout": 41, "upload_timeout": 77, "download_timeout": 88})
client_instance = main.client_for(main.store.one("SELECT * FROM accounts WHERE id='image'"), "image")
assert client_instance.timeout == 41 and client_instance.upload_timeout == 77 and client_instance.download_timeout == 88
client_instance.close()
clean = client.post("/api/admin/image-accounts/clean-exhausted", headers=headers)
assert clean.status_code == 200, clean.text
assert clean.json()["count"] == 1 and {item["id"] for item in clean.json()["deleted"]} == {"image"}, clean.text
assert main.store.one("SELECT * FROM accounts WHERE id='trial'")
assert {item["id"] for item in clean.json()["skipped"]} == {"trial", "busy"}
for account_id in ("boundary", "shared", "video", "unverified", "waf"):
    assert main.store.one("SELECT * FROM accounts WHERE id=?", (account_id,))
assert main.store.setting("archived_account:image")["secret"]["encrypted"]
invalid = client.post("/api/admin/accounts/clean-invalid?provider=vidu-cookie", headers=headers)
assert invalid.status_code == 200 and invalid.json()["count"] == 1, invalid.text
assert main.store.one("SELECT * FROM accounts WHERE id='trial'")
assert client.post("/api/admin/image-accounts/clean-exhausted").status_code == 401
print(json.dumps({"ok": True}))
"""
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
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
