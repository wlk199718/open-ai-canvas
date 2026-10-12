import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AdminSessionTests(unittest.TestCase):
    def run_process(self, directory, script, token=""):
        environment = os.environ.copy()
        environment["ZAOWU_PROVIDER_DATA_DIR"] = directory
        environment["ZAOWU_PROVIDER_DEV_MODE"] = "0"
        environment["ZAOWU_PROVIDER_ALLOW_DEV_MODE"] = "0"
        environment["ZAOWU_PROVIDER_ADMIN_SESSION_HOURS"] = "24"
        environment["TEST_ADMIN_TOKEN"] = token
        bootstrap = """
from unittest.mock import patch
from app.main import license_manager
active_mock = patch.object(license_manager, 'active', return_value=True).start()
activate_mock = patch.object(license_manager, 'activate', return_value={
    'expires_at': '2099-01-01T00:00:00+00:00', 'heartbeat_interval': 60,
}).start()
patch('app.main.wake_worker').start()
"""
        result = subprocess.run(
            [sys.executable, "-c", bootstrap + script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_restart_rotation_expiry_and_logout(self):
        with tempfile.TemporaryDirectory() as directory:
            initial = self.run_process(directory, """
import json
from fastapi.testclient import TestClient
from app.main import app, store
client = TestClient(app)
assert client.get('/api/admin/overview').status_code == 401
login = client.post('/api/admin/activate', json={'license': 'test-license'})
assert login.status_code == 200
data = login.json()
activate_mock.assert_called_once_with('test-license')
assert data['activated'] is True
assert data['project'] == 'zaowu'
assert data['token'] not in json.dumps(store.setting('admin_session'))
assert client.get('/api/health').json()['dev_mode'] is False
headers = {'Authorization': 'Bearer ' + data['token']}
assert client.get('/api/admin/overview', headers=headers).status_code == 200
print(json.dumps(data))
""")
            rotated = self.run_process(directory, """
import json, os
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
headers = {'Authorization': 'Bearer ' + os.environ['TEST_ADMIN_TOKEN']}
assert client.get('/api/admin/overview', headers=headers).status_code == 200
login = client.post('/api/admin/activate', json={'license': 'replacement-test-license'})
assert login.status_code == 200
activate_mock.assert_called_once_with('replacement-test-license')
assert login.json()['token'] != os.environ['TEST_ADMIN_TOKEN']
assert client.get('/api/admin/overview', headers=headers).status_code == 401
replacement = {'Authorization': 'Bearer ' + login.json()['token']}
assert client.get('/api/admin/overview', headers=replacement).status_code == 200
print(json.dumps(login.json()))
""", initial["token"])
            self.run_process(directory, """
import json, os
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app, store
client = TestClient(app)
headers = {'Authorization': 'Bearer ' + os.environ['TEST_ADMIN_TOKEN']}
assert client.get('/api/admin/overview', headers=headers).status_code == 200
session = store.setting('admin_session')
session['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
store.set_setting('admin_session', session)
assert client.get('/api/admin/overview', headers=headers).status_code == 401
session['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
store.set_setting('admin_session', session)
assert client.post('/api/admin/logout', headers=headers).status_code == 200
assert store.setting('admin_session') == {}
assert client.get('/api/admin/overview', headers=headers).status_code == 401
print(json.dumps({'ok': True}))
""", rotated["token"])

    def test_license_required_and_failed_activation_does_not_issue_session(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_process(directory, """
import json
from fastapi.testclient import TestClient
from app.licensing import LicenseError
from app.main import app, store
client = TestClient(app)
active_mock.return_value = False
assert client.get('/api/admin/overview').status_code == 403
activate_mock.side_effect = LicenseError('invalid-test-license rejected')
rejected = client.post('/api/admin/activate', json={'license': 'invalid-test-license'})
assert rejected.status_code == 400
assert 'token' not in rejected.json()
assert 'invalid-test-license' not in rejected.text
assert store.setting('admin_session', {}) == {}
activate_mock.side_effect = None
active_mock.return_value = True
accepted = client.post('/api/admin/activate', json={'license': 'valid-test-license'})
assert accepted.status_code == 200
headers = {'Authorization': 'Bearer ' + accepted.json()['token']}
assert client.get('/api/admin/overview', headers=headers).status_code == 200
session = store.setting('admin_session')
activate_mock.side_effect = LicenseError('replacement rejected')
assert client.post('/api/admin/activate', json={'license': 'bad-replacement'}).status_code == 400
assert store.setting('admin_session') == session
active_mock.return_value = False
assert client.get('/api/admin/overview', headers=headers).status_code == 403
assert client.post('/api/admin/client-token', headers=headers).status_code == 403
print(json.dumps({'ok': True}))
""")

    def test_password_routes_removed_and_health_has_no_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_process(directory, """
import json
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
health = client.get('/api/health').json()
assert health['service'] == 'zaowu'
assert health['license']['project'] == 'zaowu'
assert 'setup' not in health
for path in ('/api/admin/setup', '/api/admin/login'):
    assert client.post(path, json={'password': 'unused'}).status_code in (404, 405)
    assert all(route.path != path for route in app.routes)
activate_mock.assert_not_called()
print(json.dumps({'ok': True}))
""")


if __name__ == "__main__":
    unittest.main()
