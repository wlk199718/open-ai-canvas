import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.result_storage import configure_results
from app.store import Store


class ResultStorageTests(unittest.TestCase):
    def test_migration_rewrites_task_paths_preserves_backup_and_does_not_resurrect_deleted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "data")
            try:
                previous = store.root / "results"
                previous.mkdir()
                result = previous / "fixture.png"
                result.write_bytes(b"fixture-image")
                with store.transaction() as db:
                    db.execute("INSERT INTO tasks (id,task_type,model,prompt,input_json,status,result_path,created_at,updated_at) "
                               "VALUES ('fixture','image','fixture','fixture','{}','completed',?,'now','now')", (str(result),))
                target = root / "shared" / "results"
                with patch.dict(os.environ, {"ZAOWU_PROVIDER_RESULT_DIR": str(target)}):
                    self.assertEqual(configure_results(store), target)
                    self.assertEqual(store.task("fixture")["result_path"], str(target / "fixture.png"))
                    self.assertEqual((target / "fixture.png").read_bytes(), result.read_bytes())
                    self.assertTrue(result.exists())
                    # A second launch must not restore a deleted active result.
                    (target / "fixture.png").unlink()
                    configure_results(store)
                    self.assertFalse((target / "fixture.png").exists())
                self.assertEqual(store.setting("result_directory"), str(target))
                self.assertTrue((store.root / "provider.sqlite3").exists())
            finally:
                store.db.close()

    def test_conflict_failure_leaves_task_paths_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "data")
            try:
                previous = store.root / "results"
                previous.mkdir()
                (previous / "fixture.mp4").write_bytes(b"original")
                target = root / "new"
                target.mkdir()
                (target / "fixture.mp4").write_bytes(b"conflict")
                with patch.dict(os.environ, {"ZAOWU_PROVIDER_RESULT_DIR": str(target)}):
                    with self.assertRaisesRegex(ValueError, "同名"):
                        configure_results(store)
                self.assertIsNone(store.setting("result_directory"))
                self.assertEqual((previous / "fixture.mp4").read_bytes(), b"original")
                self.assertEqual((target / "fixture.mp4").read_bytes(), b"conflict")
                with patch.dict(os.environ, {"ZAOWU_PROVIDER_RESULT_DIR": str(previous / "nested")}):
                    with self.assertRaisesRegex(ValueError, "相互包含"):
                        configure_results(store)
            finally:
                store.db.close()

    def test_result_api_and_new_download_use_selected_directory(self):
        import test_task_lifecycle
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import os
from pathlib import Path
from fastapi.testclient import TestClient
from app.store import Store
root = Path(os.environ["ZAOWU_PROVIDER_DATA_DIR"])
old = root / "results"
old.mkdir()
(old / "fixture.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
store = Store(root)
with store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider) VALUES ('account','fixture',?,'vidu-cookie')", (b"fixture",))
    db.execute("INSERT INTO tasks (id,account_id,task_type,model,prompt,input_json,status,result_path,created_at,updated_at) "
               "VALUES ('fixture','account','image','fixture','fixture','{}','completed',?,'now','now')", (str(old / "fixture.png"),))
store.db.close()
os.environ["ZAOWU_PROVIDER_RESULT_DIR"] = str(root / "shared-results")
import app.main as main
main.license_active = lambda: True
main.store.set_setting("client_hash", main.digest("fixture-token"))
client = TestClient(main.app)
response = client.get("/api/v1/tasks/fixture/result", headers={"Authorization":"Bearer fixture-token"})
assert response.status_code == 200 and response.content == (old / "fixture.png").read_bytes()
class Downloader:
    def download(self, url, target):
        assert target.parent == main.result_directory
        target.write_bytes(b"\x89PNG\r\n\x1a\nnew")
main.store.update_task("fixture", status="downloading", result_url="https://example.invalid/result")
main.download_task("fixture", Downloader())
assert main.store.task("fixture")["status"] == "completed"
assert Path(main.store.task("fixture")["result_path"]).parent == main.result_directory
assert client.get("/api/health").json()["active_tasks"] == 0
""")
