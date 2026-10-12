import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DesktopConnectionTests(unittest.TestCase):
    def test_inline_references_idempotency_and_encrypted_connection(self):
        script = r"""
import base64, json
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
main.encrypt_secret = lambda value: b'encrypted:' + value.encode()
main.decrypt_secret = lambda value: value.removeprefix(b'encrypted:').decode()
main.license_active = lambda: True
main.wake_worker = lambda: None
session = main.issue_admin_session()
client = TestClient(main.app)
admin = {'Authorization': 'Bearer ' + session['token']}
assert client.post('/api/admin/connection').status_code == 401
first = client.post('/api/admin/connection', headers=admin)
assert first.status_code == 200, first.text
assert client.post('/api/admin/connection', headers=admin).json() == first.json()
assert first.json()['token'] not in main.store.setting('client_credential')
headers = {'Authorization': 'Bearer ' + first.json()['token'], 'Idempotency-Key': 'desktop-fixture-1'}
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,credit_profile) VALUES ('a','a',?,'vidu-cookie','shared')", (b'fixture',))
ref = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
body = {'provider':'image', 'model':'3.2_fast_m', 'prompt':'fixture', 'references':[ref]}
created = client.post('/api/v1/tasks', headers=headers, json=body)
assert created.status_code == 200, created.text
replayed = client.post('/api/v1/tasks', headers=headers, json=body)
assert replayed.status_code == 200 and replayed.json()['id'] == created.json()['id'], replayed.text
row = main.store.task(created.json()['id'])
params = json.loads(row['input_json'])
assert Path(params['references'][0]).read_bytes().startswith(b'\x89PNG')
assert ref not in row['input_json']
assert client.post('/api/v1/tasks', headers=headers, json={**body,'prompt':'other'}).status_code == 409
assert client.post('/api/v1/tasks', headers={'Authorization':headers['Authorization']}, json={**body,'references':['data:image/png;base64,eA==']}).status_code == 422
assert client.post('/api/v1/tasks', headers={'Authorization':headers['Authorization']}, json={**body,'sample_count':2}).status_code == 422
assert len(main.store.rows('SELECT id FROM tasks')) == 1
main.license_active = lambda: False
assert client.post('/api/admin/connection', headers=admin).status_code == 403
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", "from pathlib import Path\n" + script],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "ZAOWU_PROVIDER_DATA_DIR": directory},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
