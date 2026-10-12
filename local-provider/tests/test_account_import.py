import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.account_import import decrypt_vault, extract_account


class AccountImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_path = self.root / "account_vault_private.pem"
        self.private_path.write_bytes(
            self.private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    def tearDown(self):
        self.directory.cleanup()

    def vault(self, record):
        from app.account_import import MAGIC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os

        prefix_nonce = os.urandom(12)
        content_key = os.urandom(32)
        wrapped = self.private_key.public_key().encrypt(
            content_key,
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        prefix = MAGIC + len(wrapped).to_bytes(2, "big") + prefix_nonce
        return prefix + wrapped + AESGCM(content_key).encrypt(
            prefix_nonce, json.dumps(record, separators=(",", ":")).encode(), prefix
        )

    def test_decrypts_vault_and_extracts_plugin_account_token(self):
        blob = self.vault({
            "schema_version": 1,
            "provider": "vivareel-canvas",
            "account": {"label": "批量主账号"},
            "auth": {"access_token": "Bearer IMPORT_TOKEN_123"},
        })
        name, token = extract_account("first.vault", blob, self.private_path)
        self.assertEqual(name, "批量主账号")
        self.assertEqual(token, "IMPORT_TOKEN_123")
        self.assertEqual(decrypt_vault(blob, self.private_path)["auth"]["access_token"], "Bearer IMPORT_TOKEN_123")

    def test_reads_legacy_json_and_rejects_wrong_key_or_tampering(self):
        record = {"account": {"email": "user@example.com"}, "auth": {"user_token": "JSON_TOKEN_123"}}
        name, token = extract_account("legacy.json", json.dumps(record).encode(), self.private_path)
        self.assertEqual(name, "user@example.com")
        self.assertEqual(token, "JSON_TOKEN_123")

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_path = self.root / "other.pem"
        other_path.write_bytes(
            other.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        blob = self.vault({"auth": {"access_token": "VAULT_TOKEN_123"}})
        with self.assertRaises(ValueError):
            decrypt_vault(blob, other_path)
        damaged = bytearray(blob)
        damaged[-1] ^= 1
        with self.assertRaises(ValueError):
            decrypt_vault(bytes(damaged), self.private_path)


if __name__ == "__main__":
    unittest.main()
