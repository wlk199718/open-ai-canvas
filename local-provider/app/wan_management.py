import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .security import safe_error, digest
from .store import now
from .wan import activity_eligible, claimed, claimable, registered


SUCCESS = {"completed", "succeeded", "success", "done"}
TERMINAL = SUCCESS | {"failed", "error", "cancelled", "canceled"}
EXPERIENCES = json.loads(
    (Path(__file__).with_name("wan_experience_tasks.json")).read_text(encoding="utf-8-sig")
)["tasks"]


def period_key(row, overview):
    for key in ("period_key", "cycle_key", "cycle_id", "period_id", "period_start",
                "period_end", "date", "week", "week_start", "reset_at"):
        if row.get(key) not in (None, ""):
            return f"{key}:{row[key]}"
    category = str(row.get("category") or "daily").lower()
    period = overview.get("daily_period" if category == "daily" else "period")
    if isinstance(period, dict):
        for key in ("period_key", "key", "id", "start_at", "start", "end_at", "end"):
            if period.get(key) not in (None, ""):
                return f"{category}:{key}:{period[key]}"
    elif period:
        return f"{category}:{period}"
    current = datetime.now(timezone(timedelta(hours=8)))
    date = (current - timedelta(days=current.weekday())).date() if category == "weekly" else current.date()
    return f"{category}:{date.isoformat()}"


class WanManagement:
    def __init__(self, store, account, client):
        self.store = store
        self.account = account
        self.client = client
        self.results = []

    def error(self, operation, exc):
        message = safe_error(exc)
        self.results.append({"operation": operation, "status": "failed", "error": message})
        response = getattr(exc, "response", None)
        if response is not None and response.status_code == 401:
            with self.store.transaction() as db:
                db.execute("UPDATE accounts SET status = 'expired', auth_state = 'expired', "
                           "auth_error = '登录已失效' WHERE id = ?", (self.account["id"],))

    def claim_tasks(self, categories, keys=()):
        if not categories:
            return
        overview = self.client.engagement_overview()
        rows = self.client.rewards()
        requested = set(keys)
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("task_key") or "")
            category = str(row.get("category") or "").lower()
            if category not in categories or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
                continue
            if requested and key not in requested:
                continue
            period = period_key(row, overview)
            saved = self.store.one("SELECT status FROM claims WHERE account_id = ? AND task_key = ? AND period_key = ?",
                                   (self.account["id"], key, period))
            if claimed(row):
                with self.store.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO claims VALUES (?, ?, ?, 'claimed', ?)",
                               (self.account["id"], key, period, now()))
                continue
            if not claimable(row):
                continue
            if saved:
                self.results.append({"key": key, "status": "already_claimed" if saved["status"] == "claimed" else saved["status"], "category": category})
                continue
            # Persist the attempt before POST so restart/timeout cannot replay it.
            with self.store.transaction() as db:
                db.execute("INSERT INTO claims VALUES (?, ?, ?, 'unknown', ?)",
                           (self.account["id"], key, period, now()))
            status, error = "unknown", ""
            try:
                receipt = self.client.claim(key)
                status = "claimed" if claimed(receipt) else "unknown"
            except Exception as exc:
                error = safe_error(exc)
            if status == "unknown":
                try:
                    settled = next((item for item in self.client.rewards() if item.get("task_key") == key), {})
                    if claimed(settled):
                        status = "claimed"
                except Exception as exc:
                    error = error or safe_error(exc)
            with self.store.transaction() as db:
                db.execute("UPDATE claims SET status = ? WHERE account_id = ? AND task_key = ? AND period_key = ?",
                           (status, self.account["id"], key, period))
            self.results.append({"key": key, "category": category, "status": status,
                                 "error": error if status == "unknown" else "",
                                 "reward": row.get("reward_points") or row.get("points") or 0})

    def activities(self, ids, discover):
        if not ids and not discover:
            return
        selected = set(ids)
        if discover:
            selected.update(item["id"] for item in self.client.activity_offers())
        for activity_id in selected:
            try:
                # Recheck current eligibility at the write boundary, not stale UI data.
                if not activity_eligible(self.client.activity_details(activity_id)):
                    self.results.append({"activity_id": activity_id, "status": "not_eligible"})
                    continue
                existing = self.client.activity_registration(activity_id)
                if registered(existing):
                    self.results.append({"activity_id": activity_id, "status": "already_registered"})
                    continue
                if existing and not (
                    existing.get("status") in {"not_registered", "unregistered", "none"}
                    or existing.get("registered") is False or existing.get("is_registered") is False
                ):
                    raise ValueError("活动报名状态无法确认，已停止重复报名")
                receipt = None
                try:
                    receipt = self.client.register_activity(activity_id)
                except Exception:
                    receipt = self.client.activity_registration(activity_id)
                    if not registered(receipt):
                        raise
                if not registered(receipt):
                    receipt = self.client.activity_registration(activity_id)
                if not registered(receipt):
                    raise ValueError("活动报名结果待核实")
                self.results.append({"activity_id": activity_id, "status": "registered"})
            except Exception as exc:
                self.error(f"活动 {activity_id}", exc)

    def redeem(self, code):
        if not code:
            return
        # Never persist a raw redemption code; ambiguous requests must not be replayed.
        key = f"redemption_{digest(code)}"
        saved = self.store.one("SELECT status FROM claims WHERE account_id = ? AND task_key = ? AND period_key = 'once'",
                               (self.account["id"], key))
        if saved and saved["status"] != "rejected":
            self.results.append({"redemption": True, "status": saved["status"]})
            return
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO claims VALUES (?, ?, 'once', 'unknown', ?)",
                       (self.account["id"], key, now()))
        status, reward, error = "unknown", None, ""
        try:
            receipt = self.client.redeem(code)
            status = "claimed"
            reward = receipt.get("reward_points", receipt.get("points"))
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None and 400 <= response.status_code < 500:
                status = "rejected"
            error = safe_error(exc).replace(code, "[REDACTED]")
        with self.store.transaction() as db:
            db.execute("UPDATE claims SET status = ? WHERE account_id = ? AND task_key = ? AND period_key = 'once'",
                       (status, self.account["id"], key))
        self.results.append({"redemption": True, "status": status, "reward": reward, "error": error})

    def experience_rows(self):
        receipts = {row["experience_id"]: row for row in self.store.rows(
            "SELECT * FROM wan_experiences WHERE account_id = ?", (self.account["id"],))}
        return [{"id": item["id"], "title": item["title"],
                 "status": receipts.get(item["id"], {}).get("status", "available"),
                 "task_id": receipts.get(item["id"], {}).get("task_id")}
                for item in EXPERIENCES]

    def save_experience(self, experience_id, status, task_id, receipt):
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO wan_experiences VALUES (?, ?, ?, ?, ?, ?)",
                       (self.account["id"], experience_id, status, task_id, json.dumps(receipt), now()))

    def poll_experience(self, row, wait=False):
        deadline = time.monotonic() + (45 if wait else 0)
        receipt = json.loads(row["receipt_json"])
        state = {"status": row["status"]}
        while True:
            try:
                state = self.client.poll(row["task_id"])
                self.save_experience(row["experience_id"], state["status"], row["task_id"], receipt)
            except Exception as exc:
                self.error("免费体验状态复核", exc)
                break
            if state["status"] in TERMINAL or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        return state["status"] in SUCCESS

    def experiences(self, ids):
        completed = False
        for saved in self.store.rows("SELECT * FROM wan_experiences WHERE account_id = ? AND task_id IS NOT NULL",
                                    (self.account["id"],)):
            if saved["status"] not in TERMINAL:
                completed = self.poll_experience(saved) or completed
        if not ids:
            return completed
        quota = self.client.experience_trial_summary()
        remaining = int(quota.get("remaining") or 0)
        for experience_id in dict.fromkeys(ids):
            definition = next(item for item in EXPERIENCES if item["id"] == experience_id)
            saved = self.store.one("SELECT * FROM wan_experiences WHERE account_id = ? AND experience_id = ?",
                                  (self.account["id"], experience_id))
            if saved or remaining <= 0:
                self.results.append({"experience_id": experience_id, "status": "skipped"})
                continue
            self.save_experience(experience_id, "unknown", None, {})
            try:
                receipt = self.client.submit_hidden_experience(definition["payload"])
                task_id = str(receipt.get("task_id") or receipt.get("id") or "")
                if not task_id:
                    raise ValueError("免费体验提交响应缺少任务 ID，结果待核实")
                self.save_experience(experience_id, "submitted", task_id, receipt)
                remaining -= 1
                saved = self.store.one("SELECT * FROM wan_experiences WHERE account_id = ? AND experience_id = ?",
                                      (self.account["id"], experience_id))
                completed = self.poll_experience(saved, wait=True) or completed
                current = self.store.one("SELECT * FROM wan_experiences WHERE account_id = ? AND experience_id = ?",
                                        (self.account["id"], experience_id))
                self.results.append({"experience_id": experience_id, "status": current["status"], "task_id": task_id})
            except Exception as exc:
                self.error("免费体验提交（请核对官网记录）", exc)
        return completed

    def wallet(self):
        touched = any(item["status"] in {"claimed", "registered"} | SUCCESS for item in self.results)
        last_error = None
        amount = None
        previous = self.account.get("balance")
        for attempt in range(12 if touched else 1):
            try:
                self.client.warm_growth_center()
                amount = self.client.wallet_amount(self.client.wallet())
                with self.store.transaction() as db:
                    db.execute("UPDATE accounts SET balance = ?, checked_at = ?, status = 'ready', "
                               "auth_state = 'valid', auth_error = '' WHERE id = ?",
                               (amount, now(), self.account["id"]))
                last_error = None
                if previous is not None and amount != previous:
                    break
            except Exception as exc:
                last_error = exc
            if attempt < (11 if touched else 0):
                time.sleep(1.5)
        if last_error is not None:
            self.error("钱包刷新", last_error)
        return amount

    def snapshot(self):
        for saved in self.store.rows("SELECT * FROM wan_experiences WHERE account_id = ? AND task_id IS NOT NULL",
                                    (self.account["id"],)):
            if saved["status"] not in TERMINAL:
                self.poll_experience(saved)
        result = {"tasks": [], "activities": [], "experiences": self.experience_rows(),
                  "checked_at": now(), "errors": []}
        for key, read in (("tasks", self.client.rewards),
                          ("activities", self.client.activity_offers),
                          ("trial", self.client.experience_trial_summary)):
            try:
                value = read()
                if key == "tasks":
                    value = [{"key": str(row.get("task_key") or ""),
                              "title": str(row.get("title") or row.get("task_key") or "")[:120],
                              "category": str(row.get("category") or "").lower(),
                              "period": str(row.get("period_key") or row.get("cycle_key") or ""),
                              "reward": row.get("reward_points", row.get("points")),
                              "status": "claimed" if claimed(row) else "claimable" if claimable(row) else "pending"}
                             for row in value if isinstance(row, dict) and row.get("task_key")]
                result[key] = value
            except Exception as exc:
                result["errors"].append(f"{key}: {safe_error(exc)}")
        try:
            result["periods"] = self.client.engagement_overview()
            achievements = self.client.engagement_achievements()
            result["achievements"] = {"total": len(achievements),
                                      "unlocked": sum(item.get("status") == "unlocked" for item in achievements)}
        except Exception as exc:
            result["errors"].append(f"成长任务: {safe_error(exc)}")
        prior = json.loads(self.account.get("wan_state_json") or "{}")
        if prior.get("last_action"):
            result["last_action"] = prior["last_action"]
        self.persist_state(result)
        return result

    def persist_state(self, state):
        with self.store.transaction() as db:
            db.execute("UPDATE accounts SET wan_state_json = ? WHERE id = ?",
                       (json.dumps(state, ensure_ascii=False), self.account["id"]))

    def perform(self, body):
        for operation, run in (
            ("任务领取", lambda: self.claim_tasks(set(body.categories), body.task_keys)),
            ("活动报名", lambda: self.activities(body.activity_ids, body.include_activities)),
            ("兑换码", lambda: self.redeem(body.redemption_code.strip())),
        ):
            try:
                run()
            except Exception as exc:
                self.error(operation, exc)
        try:
            completed = self.experiences(body.experience_ids)
            if completed:
                # Engagement rewards can arrive shortly after the free video finishes.
                for attempt in range(11):
                    before = len(self.results)
                    self.claim_tasks({"daily", "weekly"})
                    if len(self.results) > before:
                        break
                    if attempt < 10:
                        time.sleep(2)
        except Exception as exc:
            self.error("免费体验", exc)
        amount = self.wallet()
        state = self.snapshot()
        bad = [item for item in self.results if item["status"] in {"failed", "unknown", "rejected", "error"}]
        messages = [item.get("error") or "部分结果待核实" for item in bad]
        messages += state["errors"]
        state["last_action"] = {"checked_at": now(), "results": self.results,
                                "error": "；".join(messages)[:600]}
        self.persist_state(state)
        return {"id": self.account["id"], "name": self.account["name"], "balance": amount,
                "results": self.results, "state": state, "ok": not bad and not state["errors"]}
