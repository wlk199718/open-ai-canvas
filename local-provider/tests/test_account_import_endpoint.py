import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AccountImportEndpointTests(unittest.TestCase):
    def test_batch_import_decrypts_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as key_directory:
            environment = os.environ.copy()
            environment["ZAOWU_PROVIDER_DATA_DIR"] = directory
            environment["ZAOWU_PROVIDER_ACCOUNT_PRIVATE_KEY"] = str(Path(key_directory) / "private.pem")
            environment["ZAOWU_PROVIDER_DEV_MODE"] = "1"
            environment["ZAOWU_PROVIDER_ALLOW_DEV_MODE"] = "1"
            script = r"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from app.account_import import MAGIC
from app.main import app, store
from app.security import digest

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
Path(os.environ["ZAOWU_PROVIDER_ACCOUNT_PRIVATE_KEY"]).write_bytes(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
))
store.set_setting("admin_session", {
    "hash": digest("endpoint-test-token"),
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
})
record = {
    "schema_version": 1, "provider": "vivareel-canvas",
    "account": {"label": "导入账号"},
    "auth": {"access_token": "ENDPOINT_TOKEN_123"},
}
nonce = os.urandom(12)
content_key = os.urandom(32)
wrapped = key.public_key().encrypt(
    content_key,
    padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
)
prefix = MAGIC + len(wrapped).to_bytes(2, "big") + nonce
vault = prefix + wrapped + AESGCM(content_key).encrypt(
    nonce, json.dumps(record, separators=(",", ":")).encode(), prefix,
)
headers = {"Authorization": "Bearer endpoint-test-token"}
client = TestClient(app)
files = [
    ("files", ("one.vault", vault, "application/octet-stream")),
    ("files", ("two.json", json.dumps(record).encode(), "application/json")),
]
first = client.post("/api/admin/accounts/import", files=files, headers=headers)
assert first.status_code == 200, first.text
assert first.json()["count"] == 1, first.text
assert len(first.json()["skipped"]) == 1, first.text
assert client.get("/api/admin/overview", headers=headers).json()["accounts"][0]["name"] == "导入账号"
print(json.dumps({"ok": True}))
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout)["ok"], True)


if __name__ == "__main__":
    unittest.main()
