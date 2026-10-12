import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

from app.providers.image import ImageClient
from app.providers.reference_image import prepare_reference, upload_reference
from app.providers.video import CookieVideoClient


def response(status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    return result


class ReferenceImageTests(unittest.TestCase):
    def test_upload_contract_finishes_with_put_and_keeps_credentials_off_media(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.png"
            Image.new("RGB", (1024, 512), "blue").save(path)
            client = ImageClient({"JWT": "fixture-jwt", "SSUID": "fixture-ssuid"})
            self.addCleanup(client.close)
            client.session.proxies["https"] = "http://localhost:8888"
            with patch.object(client, "request", side_effect=[
                {"id": "asset", "put_url": "https://cdn.example/upload"}, {},
            ]) as api, patch("app.providers.reference_image.requests.Session") as session:
                media = session.return_value.__enter__.return_value
                media.put.return_value = response()
                asset = upload_reference(client, path)
            self.assertEqual(asset, {"uri": "ssupload:?id=asset", "width": 1024, "height": 512})
            self.assertEqual(api.call_args_list[0].kwargs["json"], {
                "metadata": {"image-height": "512", "image-width": "1024"}, "scene": "vidu",
            })
            self.assertEqual(api.call_args_list[1].args, ("PUT", "/tools/v1/files/uploads/asset/finish"))
            uploaded = media.put.call_args.kwargs
            self.assertEqual(uploaded["headers"], {
                "Content-Type": "image/webp", "x-amz-meta-image-height": "512", "x-amz-meta-image-width": "1024",
            })
            self.assertNotIn("Cookie", uploaded["headers"])
            self.assertNotIn("Authorization", uploaded["headers"])
            self.assertEqual(api.call_args_list[1].kwargs["json"]["etag"], hashlib.md5(uploaded["data"]).hexdigest())
            media.proxies.update.assert_called_once_with(client.session.proxies)
            with Image.open(io.BytesIO(uploaded["data"])) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.size, (1024, 512))

    def test_upload_rejection_does_not_finish_or_submit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.png"
            Image.new("RGB", (16, 8), "blue").save(path)
            client = ImageClient({"JWT": "fixture-jwt", "SSUID": "fixture-ssuid"})
            self.addCleanup(client.close)
            with patch.object(client, "request", return_value={
                "id": "asset", "put_url": "https://cdn.example/upload",
            }) as api, patch("app.providers.reference_image.requests.Session") as session:
                session.return_value.__enter__.return_value.put.return_value = response(403)
                with self.assertRaises(requests.HTTPError):
                    client.submit("fixture", "nano2 lite", "1080p", "1:1", 1, [path])
            self.assertEqual(api.call_count, 1)

    def test_reference_image_payload_carries_dimensions_and_referer(self):
        client = ImageClient({"JWT": "fixture-jwt", "SSUID": "fixture-ssuid"})
        self.addCleanup(client.close)
        with patch("app.providers.image.upload_reference", return_value={
            "uri": "ssupload:?id=asset", "width": 1024, "height": 512,
        }), patch.object(client, "request", return_value={"id": "image"}) as api:
            self.assertEqual(client.submit("fixture", "3.2_image_2_5_fast", "1080p", "16:9", 1, [Path("fixture.png")]), "image")
        body = api.call_args.kwargs["json"]
        self.assertEqual(body["type"], "reference2image")
        self.assertEqual(body["input"]["prompts"][1]["selected_region"]["bottom_right"], {"x": 1024, "y": 512})
        self.assertEqual(api.call_args.kwargs["headers"]["Referer"], "https://www.vidu.com/zh/create/reference2image")

    def test_q4_five_second_reference_uses_separate_primary_and_source(self):
        client = CookieVideoClient({"JWT": "fixture-jwt", "SSUID": "fixture-ssuid"})
        self.addCleanup(client.close)
        with patch.object(client, "_upload", side_effect=[
            {"uri": "ssupload:?id=primary", "width": 1024, "height": 512},
            {"uri": "ssupload:?id=source", "width": 1024, "height": 512},
        ]) as upload, patch.object(client, "request", return_value={"id": "video"}) as api:
            self.assertEqual(client.submit_q4("fixture", 5, "16:9", "720p", [Path("fixture.png")]), "video")
        self.assertEqual(upload.call_count, 2)
        body = api.call_args.kwargs["json"]
        self.assertEqual(body["settings"]["duration"], 5)
        self.assertEqual(body["type"], "img2video")
        image = body["input"]["prompts"][1]
        self.assertEqual(image["content"], "ssupload:?id=primary")
        self.assertEqual(image["src_imgs"], ["ssupload:?id=source"])
        self.assertEqual(image["selected_region"]["bottom_right"], {"x": 1024, "y": 512})

    def test_large_image_is_bounded_without_changing_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.png"
            Image.new("RGB", (4096, 3072), "blue").save(path)
            original = path.read_bytes()
            content, width, height = prepare_reference(path)
            self.assertLessEqual(width * height, 3840 * 2160)
            self.assertLessEqual(len(content), 4 * 1024 * 1024)
            self.assertEqual(path.read_bytes(), original)
