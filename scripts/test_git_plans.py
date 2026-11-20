"""Exercise transport against real, synthetic Git repositories."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
import git_plans
import plans
from model import PlanError
from test_plans import STAMP, task


def args(*argv):
    return plans.parser().parse_args(["--stdin", *argv])


class GitTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name) / "data"
        self.repo.mkdir()
        remote = Path(directory.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "atlas.txt").write_text("shared data")
        self.git("add", "atlas.txt")
        self.git("commit", "-qm", "Fixture")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", "main")
        self.initial = self.snapshot()
        self.settings = {"settings": {"timezone": "Asia/Shanghai",
                         "calendar": {"mode": "pending"}, "vault_id": "fixture",
                         "recorded_at": STAMP}}

    def git(self, *argv):
        return subprocess.check_output(["git", "-C", str(self.repo), *argv]).decode().strip()

    def snapshot(self, ref="origin/main"):
        return git_plans.GitSnapshot(str(self.repo), ref)

    def seed(self):
        proposal = self.initial.run(self.settings, args("init"))
        result = self.initial.publish(proposal, "origin", "main")
        self.assertTrue(result["remote_confirmed"])
        return self.snapshot()

    def test_batch_write_exact_query_and_readback(self):
        snapshot = self.seed()
        op = {"plan": {"actor": "fixture", "operation_id": "op_test",
               "base_commit": snapshot.commit, "recorded_at": STAMP,
               "changes": [{"action": "create", "record": task()},
                           {"action": "create", "record": task("task_other", title="Coffee",
                                                               summary="Coffee", body="Coffee")}]}}
        proposal = snapshot.run(op, args("apply"))
        result = snapshot.publish(proposal, "origin", "main")
        latest = self.snapshot()
        self.assertEqual(latest.commit, result["commit"])
        self.assertEqual(self.git("show", f"{latest.commit}:atlas.txt"), "shared data")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(latest.run({}, args("verify"))["record_count"], 2)
        self.assertEqual(latest.run(op, args("apply"))["status"], "already_applied")
        paths = []
        load = latest.load

        def track(batch):
            paths.extend(batch)
            return load(batch)

        latest.load = track
        result = latest.run({"query": {"text": "生态设计"}}, args("query"))
        self.assertEqual([item["id"] for item in result["items"]], ["task_a"])
        self.assertIn("plans/tasks/task_a.json", paths)
        self.assertNotIn("plans/tasks/task_other.json", paths)
        self.assertNotIn(plans.MANIFEST, paths)
        op_receipt = self.git("show", f"{latest.commit}:plans/operations/op_test.json")
        self.assertEqual(json.loads(op_receipt)["id"], "op_test")

    def test_stale_snapshot_and_invalid_namespace_fail_closed(self):
        self.seed()
        with self.assertRaisesRegex(PlanError, "Branch advanced"):
            self.initial.publish(self.initial.run(self.settings, args("init")),
                                 "origin", "main")
        (self.repo / "plans").symlink_to("atlas.txt")
        self.git("add", "plans")
        self.git("commit", "-qm", "Invalid namespace")
        with self.assertRaisesRegex(PlanError, "regular files"):
            self.snapshot("HEAD").run(self.settings, args("init"))

    def test_missing_read_is_error(self):
        with self.assertRaisesRegex(PlanError, "Missing or invalid Git blob"):
            self.initial.run({}, args("status"))

    def test_different_remote_push_destination_is_rejected(self):
        self.git("remote", "set-url", "--push", "origin", "https://example.invalid/wrong")
        proposal = self.initial.run(self.settings, args("init"))
        with self.assertRaisesRegex(PlanError, "same fetch and push URL"):
            self.initial.publish(proposal, "origin", "main")

    def test_review_uses_vault_timezone_and_one_snapshot_for_metrics_and_feedback(self):
        snapshot = self.seed()
        stamp = "2026-10-19T02:00:00+08:00"
        base = {"actor": "fixture", "recorded_at": stamp, "base_commit": snapshot.commit}
        create = {**base, "operation_id": "op_review_setup", "changes": [
            {"action": "create", "record": task()},
            {"action": "create", "record": task("t_open")},
        ]}
        snapshot.publish(snapshot.run({"plan": create}, args("apply")), "origin", "main")
        snapshot = self.snapshot()
        sha = snapshot.run({}, args("read", "task_a"))["sha256"]
        complete = {**base, "base_commit": snapshot.commit, "operation_id": "op_review_complete",
                    "changes": [{"action": "complete", "id": "task_a", "expected_sha256": sha,
                                 "completed_at": "2026-10-18", "completion_precision": "date"}]}
        snapshot.publish(snapshot.run({"plan": complete}, args("apply")), "origin", "main")
        snapshot = self.snapshot()
        feedback = {**base, "base_commit": snapshot.commit, "operation_id": "op_review_feedback",
                    "kind": "period-note", "value": {"scope": {"kind": "day",
                    "start": "2026-10-18", "end": "2026-10-18", "label": "day",
                    "timezone": "Asia/Shanghai", "plan_id": None},
                    "text": "fixture feedback", "basis": "user_statement",
                    "completion": "partly completed"}}
        snapshot.publish(snapshot.run({"request": feedback}, args("configure")), "origin", "main")
        latest = self.snapshot()
        result = latest.run({}, git_plans.operation_args(
            ["review", "--as-of", "2026-10-18T19:00:00+00:00"]))
        self.assertEqual(result["snapshot"], latest.commit)
        self.assertEqual((result["from"], result["to"]), ("2026-10-17", "2026-10-19"))
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual((result["completed_count"], result["due_count"], result["unconfirmed"]),
                         (1, 2, 1))
        self.assertEqual(result["completion_rate"], 0.5)
        self.assertEqual(result["recent_completions"][0]["id"], "task_a")
        self.assertEqual([item["latest"]["text"] for item in result["daily_feedback"]],
                         ["fixture feedback"])

    def test_review_empty_period_has_no_completion_rate_or_feedback(self):
        latest = self.seed()
        result = latest.run({}, git_plans.operation_args(
            ["review", "--days", "1", "--as-of", STAMP]))
        self.assertEqual(result["due_count"], 0)
        self.assertIsNone(result["completion_rate"])
        self.assertEqual(result["recent_completions"], [])
        self.assertEqual(result["daily_feedback"], [])
