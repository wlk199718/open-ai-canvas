import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import requests

from app.providers.cookie_import import VIDEO_VAULT_MAGIC, decode_cookie_record, encode_cookie_record, extract_cookie_accounts
from app.providers.image import ImageClient, MODELS as IMAGE_MODELS
from app.providers.video import CookieVideoClient, CLAW_MODELS, Q4_MODELS
from app.wan import WanClient


class ProviderAdapterTests(unittest.TestCase):
    def test_cookie_json_requires_login_cookies(self):
        blob = json.dumps([
            {"name": "JWT", "value": "jwt-value", "domain": ".vidu.com"},
            {"name": "SSUID", "value": "ssuid-value", "domain": ".vidu.com"},
        ]).encode()
        result = extract_cookie_accounts("account.json", blob, "vidu.com")
        self.assertEqual(result[0][0], "account")
        self.assertEqual(result[0][1]["JWT"], "jwt-value")

    def test_custom_video_vault_can_be_decrypted(self):
        key = os.urandom(32)
        record = {
            "name": "video-account",
            "full_cookies": [
                {"name": "JWT", "value": "jwt-value", "domain": ".vidu.com"},
                {"name": "SSUID", "value": "ssuid-value", "domain": ".vidu.com"},
            ],
        }
        nonce = os.urandom(12)
        blob = VIDEO_VAULT_MAGIC + nonce + AESGCM(key).encrypt(
            nonce, json.dumps({"accounts": [record]}).encode(), VIDEO_VAULT_MAGIC
        )
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "account_vault.key"
            key_path.write_bytes(key)
            previous = os.environ.get("ZAOWU_PROVIDER_VIDEO_ACCOUNT_KEY")
            os.environ["ZAOWU_PROVIDER_VIDEO_ACCOUNT_KEY"] = str(key_path)
            try:
                result = extract_cookie_accounts("accounts.vault", blob, "vidu.com")
            finally:
                if previous is None:
                    os.environ.pop("ZAOWU_PROVIDER_VIDEO_ACCOUNT_KEY", None)
                else:
                    os.environ["ZAOWU_PROVIDER_VIDEO_ACCOUNT_KEY"] = previous
        self.assertEqual(result[0][0], "video-account")
        self.assertEqual(result[0][1]["SSUID"], "ssuid-value")

    def test_provider_catalogs_are_present(self):
        self.assertEqual(Q4_MODELS[0]["id"], "q4")
        self.assertEqual(CLAW_MODELS[0]["id"], "claw")
        self.assertGreaterEqual(len(IMAGE_MODELS), 6)

    def test_stored_cookie_envelope_is_unwrapped_and_invalid_shapes_rejected(self):
        cookies = {"JWT": "jwt", "SSUID": "ssuid"}
        self.assertEqual(decode_cookie_record(encode_cookie_record(cookies)), cookies)
        for invalid in ("null", "[]", '{"cookies":{"JWT":{},"SSUID":"ssuid"}}'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decode_cookie_record(invalid)
        with self.assertRaises(ValueError):
            CookieVideoClient({"cookies": cookies})

    def test_cookie_clients_share_credits_and_waf_aliases(self):
        for client_class in (CookieVideoClient, ImageClient):
            for alias in ("aws-waf-token", "aws_waf_token", "awsWafToken"):
                client = client_class({"JWT": "jwt", "SSUID": "ssuid", alias: "waf"})
                self.addCleanup(client.close)
                self.assertEqual(client.session.headers["X-Aws-Waf-Token"], "waf")
                response = requests.Response()
                response.status_code = 200
                response._content = b'{"data":{"credits":285}}'
                with patch.object(client.session, "request", return_value=response) as request:
                    self.assertEqual(client.credits()["credits"], 285)
                    self.assertEqual(client.credits()["credits"], 285)
                self.assertNotEqual(
                    request.call_args_list[0].kwargs["headers"]["X-Request-Id"],
                    request.call_args_list[1].kwargs["headers"]["X-Request-Id"],
                )

    def test_browser_challenge_cannot_be_parsed_as_success(self):
        client = CookieVideoClient({"JWT": "jwt", "SSUID": "ssuid"})
        self.addCleanup(client.close)
        for status, body in ((202, b""), (200, b"<html>challenge</html>")):
            response = requests.Response()
            response.status_code = status
            response._content = body
            with patch.object(client.session, "request", return_value=response), self.assertRaises(ValueError):
                client.credits()

    def test_wan_client_matches_canvas_headers_and_wallet_shape(self):
        client = WanClient("access-token")
        self.addCleanup(client.close)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"code": 0, "data": {"general_available": 200, "targeted_available": 85}}'
        with patch.object(client.session, "request", return_value=response) as request:
            wallet = client.wallet()
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["x-client-timezone"], "Asia/Shanghai")
        self.assertEqual(headers["x-locale"], "zh")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(wallet["general_available"], 200)
        self.assertEqual(client.wallet_amount(wallet), 285)

    def test_wan_401_keeps_response_for_auth_classification(self):
        client = WanClient("access-token")
        self.addCleanup(client.close)
        response = requests.Response()
        response.status_code = 401
        response._content = b'{"message":"expired"}'
        with patch.object(client.session, "request", return_value=response):
            with self.assertRaises(requests.HTTPError) as raised:
                client.wallet()
        self.assertEqual(raised.exception.response.status_code, 401)

    def test_wan_saves_canvas_graph_before_generation(self):
        client = WanClient("access-token")
        self.addCleanup(client.close)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"code": 0, "data": {}}'
        with patch.object(client.session, "request", return_value=response) as request:
            client.save_project(12, ["https://cdn.example/a.png", "https://cdn.example/b.png"],
                                "镜头向前推进", "dashscope-wan3.0-video", 10, "16:9")
        payload = request.call_args.kwargs["json"]["content_json"]
        graph = json.loads(payload)
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)
        self.assertEqual(graph["edges"][0]["type"], "referenceEdge")
        self.assertEqual(request.call_args.args[0], "PUT")
        self.assertIn("/api/canvas/new-projects/12/save", request.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
