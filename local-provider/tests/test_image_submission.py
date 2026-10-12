import unittest
from unittest.mock import patch

import requests

from app.providers.image import ImageClient
from app.providers.vidu import SubmissionRejected
import test_task_lifecycle


def response(status, body, headers=None):
    value = requests.Response()
    value.status_code = status
    value._content = body
    value.headers.update(headers or {})
    return value


class ImageSubmissionTests(unittest.TestCase):
    def client(self):
        client = ImageClient({"JWT": "fixture-jwt", "SSUID": "fixture-ssuid", "aws-waf-token": "fixture-waf"})
        self.addCleanup(client.close)
        return client

    def test_nano2_lite_tool_payload_matches_plugin_for_2k(self):
        client = self.client()
        with patch.object(client.session, "request", return_value=response(200, b'{"data":{"task":{"id":"up-image"}}}')) as call:
            identifier = client.submit("fixture", "nano2 lite", "2k", "16:9", 1, [])
        self.assertEqual(identifier, "up-image")
        self.assertTrue(call.call_args.args[1].endswith("/vidu/v1/tasks/tool"))
        payload = call.call_args.kwargs["json"]
        self.assertEqual(payload["settings"]["model_version"], "3.2_lite_m")
        self.assertEqual(payload["settings"]["resolution"], "2k")
        self.assertEqual(payload["settings"]["aspect_ratio"], "16:9")
        self.assertNotIn("image_count", payload["settings"])
        prepared = client.session.prepare_request(requests.Request("POST", call.call_args.args[1], headers=call.call_args.kwargs["headers"]))
        self.assertNotIn("X-Aws-Waf-Token", prepared.headers)
        self.assertNotIn("fixture-waf", prepared.headers["Cookie"])
        self.assertNotIn("Sec-CH-UA", prepared.headers)
        self.assertEqual(prepared.headers["Referer"], "https://www.vidu.com/zh/create/text2image")

    def test_gpt_image_2_5_retains_website_endpoint_and_trial(self):
        client = self.client()
        with patch.object(client.session, "request", return_value=response(200, b'{"task_id":"up-image"}')) as call:
            self.assertEqual(client.submit("fixture", "3.2_image_2_5_fast", "2k", "16:9", 1, []), "up-image")
        self.assertTrue(call.call_args.args[1].endswith("/vidu/v1/tasks"))
        self.assertTrue(call.call_args.kwargs["json"]["settings"]["use_trial"])
        self.assertEqual(client.session.headers["X-Aws-Waf-Token"], "fixture-waf")

    def test_rejections_keep_code_without_echoing_response_body(self):
        client = self.client()
        for status, body in (
            (400, b'{"code":"SubsplanNotSupportedForTask","message":"fixture-jwt"}'),
            (200, b'{"code":"InsufficientCredits","message":"fixture-jwt"}'),
            (422, b'{"error":{"code":"InvalidResolution","detail":"fixture-jwt"}}'),
        ):
            with self.subTest(status=status), patch.object(client.session, "request", return_value=response(status, body)):
                with self.assertRaises(SubmissionRejected) as raised:
                    client.submit("fixture", "nano2 lite", "2k", "16:9", 1, [])
                self.assertNotIn("fixture-jwt", str(raised.exception))
                self.assertIsNotNone(raised.exception.response)

    def test_accepted_header_id_avoids_losing_created_task(self):
        client = self.client()
        for headers, body in (({"X-Task-Id": "up-image"}, b""), ({"Location": "/vidu/v1/tasks/up-image"}, b"not-json")):
            with self.subTest(headers=headers), patch.object(client.session, "request", return_value=response(202, body, headers)):
                self.assertEqual(client.submit("fixture", "nano2 lite", "2k", "16:9", 1, []), "up-image")

    def test_error_code_cannot_echo_account_credential(self):
        client = self.client()
        with patch.object(client.session, "request", return_value=response(400, b'{"code":"fixture-jwt"}')):
            with self.assertRaises(SubmissionRejected) as raised:
                client.submit("fixture", "nano2 lite", "2k", "16:9", 1, [])
        self.assertNotIn("fixture-jwt", str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))

    def test_worker_distinguishes_rejection_from_ambiguous_submission(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
from unittest.mock import create_autospec, patch
import requests
import app.main as main
from app.providers.image import ImageClient
from app.providers.vidu import SubmissionRejected
main.license_active = lambda: True
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile,image_balance,status,auth_state) "
               "VALUES ('image','image',?,'vidu-cookie','image',100,'ready','valid')", (b"fixture",))
for name, failure, expected in (
    ("rejected", SubmissionRejected("上游请求失败（HTTP 400），原因：InvalidResolution"), "failed"),
    ("transport", requests.ConnectionError("connection reset"), "submission_unknown"),
    ("server", requests.HTTPError("上游请求失败（HTTP 502）", response=type("Response", (), {"status_code":502})()), "submission_unknown"),
):
    with main.store.transaction() as db:
        db.execute("INSERT INTO tasks (id,provider,task_type,model,prompt,input_json,status,created_at,updated_at) "
                   "VALUES (?,'image','image','nano2 lite','fixture','{\"resolution\":\"2k\",\"aspect_ratio\":\"16:9\",\"sample_count\":1,\"references\":[]}',"
                   "'queued','now','now')", (name,))
    adapter = create_autospec(ImageClient, instance=True)
    adapter.submit.side_effect = failure
    with patch.object(main, "client_for", return_value=adapter):
        main.run_task(name)
        main.schedule_worker()
    row = main.store.task(name)
    assert row["status"] == expected, row
    assert ("InvalidResolution" if name == "rejected" else "connection reset" if name == "transport" else "502") in row["error"], row
    adapter.submit.assert_called_once()
    adapter.poll.assert_not_called()
""")
