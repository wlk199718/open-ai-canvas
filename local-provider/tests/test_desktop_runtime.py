import unittest

import test_task_lifecycle


class DesktopRuntimeTests(unittest.TestCase):
    def test_management_restore_verifies_saved_card_and_rejects_failed_activation(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import base64
import json
import os
from pathlib import Path
from unittest.mock import patch
import requests
import run
from app.store import Store

store = Store(Path(os.environ["ZAOWU_PROVIDER_DATA_DIR"]))
card = "fixture-saved-card"
store.set_setting("license_credential", base64.b64encode(b"encrypted-card").decode())
store.db.close()
health = requests.Response()
health.status_code = 200
health._content = b'{"service":"zaowu","activated":false,"dev_mode":false}'
activated = requests.Response()
activated.status_code = 200
activated._content = b'{"activated":true,"token":"fixture-admin-session-123456","session_expires_at":"2099-01-01T00:00:00Z"}'
with patch.object(requests.Session, "get", return_value=health), patch.object(requests.Session, "post", return_value=activated) as post, patch("app.security.decrypt_secret", return_value=card):
    result = run.desktop_admin_session()
    assert result["token"] == "fixture-admin-session-123456"
    assert post.call_args.args == ("http://127.0.0.1:8787/api/admin/activate",)
    assert post.call_args.kwargs["json"] == {"license": card}
denied = requests.Response()
denied.status_code = 400
denied._content = b'{"detail":"expired"}'
with patch.object(requests.Session, "get", return_value=health), patch.object(requests.Session, "post", return_value=denied), patch("app.security.decrypt_secret", return_value=card):
    try:
        run.desktop_admin_session()
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("Rejected card must not restore a management session")
health._content = b'{"service":"zaowu","activated":true,"dev_mode":true}'
with patch.object(requests.Session, "get", return_value=health), patch.object(requests.Session, "post") as post:
    try:
        run.desktop_admin_session()
    except ValueError:
        pass
    else:
        raise AssertionError("Development mode must not restore a management session")
    post.assert_not_called()
health._content = b'{"service":"zaowu","activated":false,"dev_mode":false}'
store = Store(Path(os.environ["ZAOWU_PROVIDER_DATA_DIR"]))
store.set_setting("license_credential", "")
store.db.close()
with patch.object(requests.Session, "get", return_value=health), patch.object(requests.Session, "post") as post:
    assert run.desktop_admin_session() is None
    post.assert_not_called()
""")

    def test_management_restore_cli_does_not_expose_failure_diagnostics(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import contextlib
import io
import json
from unittest.mock import patch
import run
output = io.StringIO()
with patch("sys.argv", ["run.py", "--desktop-admin-session"]), patch.object(run, "desktop_admin_session", side_effect=RuntimeError("fixture-secret-card")), contextlib.redirect_stdout(output):
    try:
        run.main()
    except SystemExit as exc:
        assert exc.code == 1
assert "fixture-secret-card" not in output.getvalue()
assert "error" in json.loads(output.getvalue())
""")

    def test_connection_export_requires_active_license_and_reuses_encrypted_token(self):
        test_task_lifecycle.TaskLifecycleTests().run_isolated(r"""
import base64
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import requests
import run
from app.store import Store
from app.security import digest
import os

root = Path(os.environ["ZAOWU_PROVIDER_DATA_DIR"])
store = Store(root)
token = "fixture-generation-token-1234567890"
store.set_setting("client_hash", digest(token))
store.set_setting("client_credential", base64.b64encode(b"cipher:" + token.encode()).decode())
store.db.close()
response = requests.Response()
response.status_code = 200
for active, dev, allowed in ((True, False, True), (False, False, False), (True, True, False)):
    response._content = json.dumps({"service":"zaowu","activated":active,"dev_mode":dev}).encode()
    output = io.StringIO()
    with patch.object(requests.Session, "get", return_value=response), patch("app.security.decrypt_secret", side_effect=lambda value:value.removeprefix(b"cipher:").decode()), contextlib.redirect_stdout(output):
        try:
            run.desktop_connection()
        except ValueError:
            assert not allowed
        else:
            assert allowed
    if allowed:
        connection = json.loads(output.getvalue())
        assert connection["token"] == token
        assert connection["baseUrl"] == "http://127.0.0.1:8787"
    else:
        assert output.getvalue() == ""
with Store(root).transaction() as db:
    assert token not in db.execute("SELECT value FROM settings WHERE key='client_credential'").fetchone()[0]
""")
