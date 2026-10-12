import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from app.store import Store
from app.wan import WanClient, registered
from app.wan_management import WanManagement, period_key


class WanManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        with self.store.transaction() as db:
            db.execute("INSERT INTO accounts (id,name,secret,balance) VALUES ('wan','Wan',?,100)", (b"token",))
        self.account = self.store.one("SELECT * FROM accounts WHERE id = 'wan'")
        self.rows = [
            {"task_key": "daily_login", "category": "daily", "period_key": "daily-1", "claimable": True, "reward_points": 10},
            {"task_key": "weekly_login", "category": "weekly", "period_key": "weekly-1", "claimable": True, "reward_points": 20},
        ]
        self.client = Mock(spec=WanClient)
        self.client.engagement_overview.return_value = {}
        self.client.engagement_achievements.return_value = []
        self.client.rewards.side_effect = lambda: [dict(row) for row in self.rows]
        self.client.activity_offers.return_value = []
        self.client.redeem.return_value = {"points": 5}
        self.client.experience_trial_summary.return_value = {"remaining": 3}
        self.client.wallet.return_value = {"general_available": 130, "targeted_available": 0}
        self.client.wallet_amount.side_effect = WanClient.wallet_amount
        self.manager = WanManagement(self.store, self.account, self.client)

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def body(self, **kwargs):
        return SimpleNamespace(categories=["daily", "weekly"], task_keys=[], activity_ids=[],
                               include_activities=False, redemption_code="", experience_ids=[]) if not kwargs else SimpleNamespace(
            **({"categories": ["daily", "weekly"], "task_keys": [], "activity_ids": [],
                "include_activities": False, "redemption_code": "", "experience_ids": []} | kwargs))

    def accept_claim(self, key):
        next(row for row in self.rows if row["task_key"] == key)["claimed"] = True
        return {"status": "claimed"}

    def test_daily_and_weekly_filters_and_final_snapshot(self):
        self.client.claim.side_effect = self.accept_claim
        result = self.manager.perform(self.body(categories=["daily"]))
        self.client.claim.assert_called_once_with("daily_login")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["balance"], 130)
        stored = self.store.one("SELECT * FROM accounts WHERE id = 'wan'")
        self.assertEqual(stored["status"], "ready")
        state = json.loads(stored["wan_state_json"])
        self.assertEqual(state["tasks"][0]["status"], "claimed")
        self.assertEqual(state["tasks"][1]["status"], "claimable")
        self.assertEqual(len(state["last_action"]["results"]), 1)

    def test_claim_timeout_reconciles_and_never_replays_unknown(self):
        self.client.claim.side_effect = requests.Timeout("timeout")
        result = self.manager.perform(self.body(categories=["daily"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["status"], "unknown")
        self.manager.perform(self.body(categories=["daily"]))
        self.client.claim.assert_called_once()
        self.rows[0]["claimed"] = True
        self.manager.perform(self.body(categories=["daily"]))
        saved = self.store.one("SELECT status FROM claims WHERE task_key = 'daily_login'")
        self.assertEqual(saved["status"], "claimed")
        self.client.claim.assert_called_once()

    def test_timeout_after_accepted_claim_is_success(self):
        def accepted_timeout(key):
            self.accept_claim(key)
            raise requests.Timeout("lost response")
        self.client.claim.side_effect = accepted_timeout
        result = self.manager.perform(self.body(categories=["daily"]))
        self.assertTrue(result["ok"], result)
        self.client.claim.assert_called_once()

    def test_period_transition_permits_new_week_and_honors_explicit_task(self):
        self.client.claim.side_effect = self.accept_claim
        self.manager.perform(self.body(task_keys=["weekly_login"]))
        self.client.claim.assert_called_once_with("weekly_login")
        self.rows[1].update({"period_key": "weekly-2", "claimed": False})
        self.manager.perform(self.body(categories=["weekly"]))
        self.assertEqual(self.client.claim.call_count, 2)
        self.assertNotEqual(period_key({"category": "weekly"}, {"period": "week-1"}),
                            period_key({"category": "weekly"}, {"period": "week-2"}))

    def test_wallet_waits_for_delayed_reward_and_keeps_old_balance_on_error(self):
        self.client.claim.side_effect = self.accept_claim
        self.client.wallet.side_effect = [
            {"general_available": 100}, {"general_available": 100}, {"general_available": 120}
        ]
        with patch("app.wan_management.time.sleep") as sleep:
            result = self.manager.perform(self.body(categories=["daily"]))
        self.assertEqual(result["balance"], 120)
        self.assertEqual(sleep.call_count, 2)
        self.client.wallet.side_effect = requests.ConnectionError("offline")
        self.manager.results = []
        with patch("app.wan_management.time.sleep"):
            result = self.manager.perform(self.body(categories=[], redemption_code="new-code"))
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.one("SELECT balance FROM accounts WHERE id = 'wan'")["balance"], 120)

    def test_activity_revalidates_expiry_and_checks_timeout_without_second_post(self):
        self.client.activity_details.return_value = {"is_active": False}
        self.manager.activities([5], False)
        self.client.register_activity.assert_not_called()
        start = datetime.now(timezone.utc) - timedelta(days=1)
        end = start + timedelta(days=2)
        self.client.activity_details.return_value = {
            "is_active": True, "activity_type": "registration", "registration_enabled": True,
            "reward": {"reward_type": "general_points", "points_amount": 10},
            "registration_start_at": start.isoformat(), "registration_end_at": end.isoformat(),
        }
        self.client.activity_registration.side_effect = [None, {"id": 4, "status": "pending"}]
        self.client.register_activity.side_effect = requests.Timeout("lost response")
        self.manager.activities([5], False)
        self.client.register_activity.assert_called_once()
        self.assertEqual(self.manager.results[-1]["status"], "registered")
        self.assertFalse(registered({"id": 4, "registered": False}))

    def test_redemption_does_not_depend_on_engagement_read_and_never_replays_timeout(self):
        self.client.rewards.side_effect = requests.ConnectionError("task list offline")
        self.client.redeem.side_effect = requests.Timeout("redeem lost")
        result = self.manager.perform(self.body(categories=[], redemption_code="code"))
        self.assertFalse(result["ok"])
        self.manager.perform(self.body(categories=[], redemption_code="code"))
        self.client.redeem.assert_called_once_with("code")
        self.assertNotIn("code", json.dumps(self.store.rows("SELECT * FROM claims")))

    def test_free_experience_persists_task_and_does_not_replay_after_restart(self):
        self.client.submit_hidden_experience.return_value = {
            "task_id": "free-task-1", "is_free_trial": True, "billing_points": 0
        }
        self.client.poll.return_value = {"status": "completed", "progress": 100, "url": None}
        self.client.claim.side_effect = self.accept_claim
        result = self.manager.perform(self.body(categories=[], experience_ids=["hidden_free_1"]))
        self.assertTrue(result["ok"], result)
        row = self.store.one("SELECT * FROM wan_experiences")
        self.assertEqual(row["task_id"], "free-task-1")
        self.assertEqual(row["status"], "completed")
        restarted = WanManagement(self.store, self.store.one("SELECT * FROM accounts WHERE id = 'wan'"), self.client)
        restarted.perform(self.body(categories=[], experience_ids=["hidden_free_1"]))
        self.client.submit_hidden_experience.assert_called_once()
        self.assertEqual(json.loads(row["receipt_json"])["billing_points"], 0)

    def test_free_experience_requires_quota_and_unknown_response_is_persistent(self):
        self.client.experience_trial_summary.return_value = {"remaining": 0}
        self.manager.perform(self.body(categories=[], experience_ids=["hidden_free_2"]))
        self.client.submit_hidden_experience.assert_not_called()
        self.client.experience_trial_summary.return_value = {"remaining": 3}
        self.client.submit_hidden_experience.side_effect = requests.Timeout("lost receipt")
        self.manager.perform(self.body(categories=[], experience_ids=["hidden_free_2"]))
        self.manager.perform(self.body(categories=[], experience_ids=["hidden_free_2"]))
        self.client.submit_hidden_experience.assert_called_once()
        self.assertEqual(self.store.one("SELECT status FROM wan_experiences")["status"], "unknown")

    def test_batch_endpoints_isolate_providers_and_login_never_returns_password(self):
        script = r"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import app.main as main
main.encrypt_secret = lambda value: value.encode()
main.decrypt_secret = lambda value: value.decode()
main.store.set_setting("admin_session", {"hash": main.digest("session"),
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
with main.store.transaction() as db:
    db.execute("INSERT INTO accounts (id,name,secret,provider,status,wan_login_email) VALUES ('wan','wan',?,'wan-video','expired',?)",
               (b'old-token', b'account@example.test'))
    db.execute("INSERT INTO accounts (id,name,secret,provider,status) VALUES ('cookie','cookie',?,'vidu-cookie','expired')", (b'cookie',))
client = TestClient(main.app)
headers = {"Authorization": "Bearer session"}
mock = Mock()
mock.login.return_value = "new-token-123"
with patch.object(main, 'client_for', return_value=mock), patch.object(main, 'check_account', return_value={"id":"wan"}):
    result = client.post("/api/admin/wan/login", headers=headers, json={"password":"secret-password"})
    assert result.status_code == 200 and result.json()["succeeded"] == 1, result.text
    assert "secret-password" not in result.text
    assert main.store.one("SELECT secret FROM accounts WHERE id='wan'")["secret"] == b'new-token-123'
    assert main.store.one("SELECT status FROM accounts WHERE id='cookie'")["status"] == "expired"
    mock.login.side_effect = ValueError("failed secret-password")
    with main.store.transaction() as db:
        db.execute("UPDATE accounts SET status='expired' WHERE id='wan'")
    result = client.post("/api/admin/wan/login", headers=headers, json={"password":"secret-password"})
    assert "secret-password" not in result.text
    assert "secret-password" not in main.store.one("SELECT check_error FROM accounts WHERE id='wan'")["check_error"]
result = client.post("/api/admin/wan/claim", headers=headers, json={"experience_ids":["invalid"]})
assert result.status_code == 422
result = client.post("/api/admin/accounts/clean-invalid?provider=wan-video", headers=headers)
assert result.status_code == 200
assert main.store.one("SELECT * FROM accounts WHERE id='cookie'")
assert main.store.setting("archived_account:wan")["secret"]["encrypted"]
main.store.db.close()
print(json.dumps({"ok":True}))
"""
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment.update(ZAOWU_PROVIDER_DATA_DIR=directory,
                               ZAOWU_PROVIDER_DEV_MODE="1", ZAOWU_PROVIDER_ALLOW_DEV_MODE="1")
            result = subprocess.run([sys.executable, "-c", script], env=environment,
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
