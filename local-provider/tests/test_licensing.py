import base64
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import licensing
from app.security import decrypt_secret, encrypt_secret
from app.store import Store


class LicensingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.signer.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.manager = licensing.LicenseManager(self.store)
        self.key_patch = patch.object(licensing, "LICENSE_PUBLIC_KEY_PEM", self.public_key)
        self.key_patch.start()
        self.expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    def tearDown(self):
        self.manager.close()
        self.store.db.close()
        self.key_patch.stop()
        self.directory.cleanup()

    def payload(self, **values):
        return {
            "ok": True, "project": "zaowu", "device_id": self.manager.device_id,
            "expires_at": self.expiry, "heartbeat_interval": 60, **values,
        }

    def signed(self, payload):
        signature = self.signer.sign(
            licensing.canonical(payload), padding.PKCS1v15(), hashes.SHA256(),
        )
        return {**payload, "signature": base64.b64encode(signature).decode("ascii")}

    def activate(self, key="test-license"):
        with patch.object(self.manager, "_request", return_value=self.payload()):
            return self.manager.activate(key, persist=False)

    def test_signature_tampering_and_unsigned_denial(self):
        signed = self.signed(self.payload())
        self.assertTrue(licensing.verify_response(signed)["ok"])
        with self.assertRaises(licensing.LicenseError):
            licensing.verify_response({**signed, "project": "other-project"})
        with self.assertRaises(licensing.LicenseError):
            licensing.verify_response({"ok": False, "error": "拒绝"})

    def test_http_verify_and_heartbeat_match_reference_protocol(self):
        response = Mock(status_code=200)
        response.json.return_value = self.signed(self.payload())
        with patch.object(self.manager.session, "post", return_value=response) as post:
            self.manager.activate("test-license", persist=False)
            verification = post.call_args.kwargs
            self.assertTrue(post.call_args.args[0].endswith("/api/verify"))
            self.assertEqual(verification["json"]["project"], "zaowu")
            self.assertEqual(verification["json"]["device_id"], self.manager.device_id)
            self.assertEqual(verification["json"]["license_key"], "test-license")
            self.assertTrue(verification["verify"])
            self.assertFalse(verification["allow_redirects"])
            self.manager._heartbeat_once("test-license")
            self.assertTrue(post.call_args.args[0].endswith("/api/heartbeat"))
        self.assertTrue(self.manager.active())
        self.assertNotIn("license_key", self.manager.state())

    def test_wrong_project_device_expiry_and_interval_rejected(self):
        for values in (
            {"project": "wrong"}, {"device_id": "wrong"},
            {"expires_at": "bad-date"}, {"heartbeat_interval": "invalid"},
            {"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
        ):
            with self.subTest(values=values), patch.object(
                self.manager, "_request", return_value=self.payload(**values),
            ):
                with self.assertRaises(licensing.LicenseError):
                    self.manager.activate("test-license", persist=False)
        self.assertFalse(self.manager.active())

    def test_grace_expires_even_before_next_heartbeat(self):
        self.activate()
        with patch.object(self.manager, "_request", side_effect=licensing.LicenseConnectionError("断网")):
            self.assertTrue(self.manager._heartbeat_step("test-license", self.manager.stop))
        self.assertTrue(self.manager.active())
        self.assertTrue(self.manager.state()["in_grace_period"])
        self.manager._last_success = time.monotonic() - 901
        self.assertFalse(self.manager.active())
        self.manager._last_success = time.monotonic() - 1
        self.manager._save(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.assertFalse(self.manager.active())

    def test_denial_invalidates_immediately_and_old_worker_cannot_overwrite(self):
        self.activate("old-license")
        old_stop = self.manager.stop
        self.activate("new-license")
        self.assertTrue(old_stop.is_set())
        with patch.object(self.manager, "_request") as request:
            self.assertFalse(self.manager._heartbeat_step("old-license", old_stop))
            request.assert_not_called()
        self.assertTrue(self.manager.active())
        with patch.object(self.manager, "_request", return_value={"ok": False, "error": "已停用"}):
            self.assertFalse(self.manager._heartbeat_step("new-license", self.manager.stop))
        self.assertFalse(self.manager.active())
        self.assertFalse(self.manager.state()["in_grace_period"])

    def test_rate_limit_and_invalid_signature_never_retry_other_route(self):
        for response in (Mock(status_code=429), Mock(status_code=200)):
            response.json.return_value = {"ok": True, "signature": "invalid"}
            with self.subTest(status=response.status_code), patch.object(
                self.manager.session, "post", return_value=response,
            ), patch.object(self.manager.direct_session, "post") as direct:
                with self.assertRaises(licensing.LicenseError):
                    self.manager._request("/api/verify", {}, 10)
                direct.assert_not_called()

    def test_cached_state_does_not_authorize_after_restart(self):
        self.store.set_setting("license_state", {"ok": True, "license_key": "fake"})
        self.assertFalse(self.manager.active())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_encrypted_key_restores_only_after_online_validation(self):
        key = "synthetic-license-for-dpapi-test"
        with patch.object(self.manager, "_request", return_value=self.payload()):
            self.manager.activate(key)
        credential = self.store.setting("license_credential")
        self.assertNotIn(key, credential)
        self.assertEqual(decrypt_secret(base64.b64decode(credential)), key)
        self.assertEqual(decrypt_secret(encrypt_secret(key)), key)
        restarted = licensing.LicenseManager(self.store)
        ready = threading.Event()
        try:
            self.assertFalse(restarted.active())
            with patch.object(restarted, "_request", return_value=self.payload()):
                restarted.ensure_started(on_ready=ready.set)
                self.assertTrue(ready.wait(5))
            self.assertTrue(restarted.active())
        finally:
            restarted.close()


if __name__ == "__main__":
    unittest.main()
