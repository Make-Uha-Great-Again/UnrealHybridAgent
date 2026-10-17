"""Offline checkpoint inspection: no provider setup or scene mutation."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import uha
from src.core.config import Config


class CheckpointCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "checkpoints"
        self.store.mkdir()
        self.data = {
            "task_id": "task-a", "status": "done", "execution_mode": "CONFIRM",
            "level": "Demo", "actors": [{"label": "柱子", "location": [1, 2, 3],
                                         "rotation": [0, 0, 0], "scale": [1, 1, 1]}],
            "notes": ["saved snapshot"],
        }
        self.write("task-a", self.data)
        self.write("task-b", {**self.data, "task_id": "task-b", "status": "rollback_failed"})

    def write(self, name, data):
        (self.store / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def run_cli(self, *args):
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        cfg = Config({"workspace": {"state_dir": str(self.root)}}, self.root / "config.json")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(uha, "load_config", return_value=cfg), \
             patch.object(uha, "build_bundle", side_effect=AssertionError("must stay offline")), \
             patch.object(uha, "_setup", side_effect=AssertionError("must stay offline")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = uha.main(["checkpoints", *args])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})
        return code, out.getvalue(), err.getvalue()

    def test_json_list_and_status_filter(self):
        code, out, err = self.run_cli("--json", "--status", "rollback_failed")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        report = json.loads(out)
        self.assertEqual([r["task_id"] for r in report["checkpoints"]], ["task-b"])
        self.assertEqual(report["checkpoints"][0]["actor_count"], 1)
        self.assertEqual(report["errors"], [])

    def test_detail_json_preserves_full_snapshot(self):
        code, out, _ = self.run_cli("--task-id", "task-a", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.data)

    def test_human_detail_marks_snapshot(self):
        code, out, _ = self.run_cli("--task-id", "task-a")
        self.assertEqual(code, 0)
        self.assertIn("不代表当前 UE 场景状态", out)
        self.assertIn("柱子", out)
        self.assertIn("location=[1, 2, 3]", out)

    def test_missing_detail_and_conflicting_options(self):
        for args in [("--task-id", "missing"), ("--task-id", "task-a", "--status", "done")]:
            code, out, err = self.run_cli(*args)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertTrue(err)

    def test_corrupt_file_preserves_valid_list_results(self):
        (self.store / "broken.json").write_text("{", encoding="utf-8")
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 2)
        report = json.loads(out)
        self.assertEqual(len(report["checkpoints"]), 2)
        self.assertEqual(report["errors"][0]["task_id"], "broken")

    def test_invalid_detail_shapes(self):
        for data in [[], {"actors": [42]}]:
            self.write("broken", data)
            code, out, err = self.run_cli("--task-id", "broken")
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertTrue(err)

    def test_empty_list(self):
        code, out, _ = self.run_cli("--json", "--status", "nonexistent")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"checkpoints": [], "errors": []})

    def test_compare_json_and_human_report(self):
        self.write("task-b", {**self.data, "task_id": "task-b", "actors": [
            {"label": "柱子", "location": [1, 2, 13], "rotation": [0, 0, 0], "scale": [1, 1, 1]}]})
        code, out, err = self.run_cli("--compare", "task-a", "task-b", "--json")
        self.assertEqual((code, err), (0, ""))
        report = json.loads(out)
        self.assertEqual(report["summary"]["changed"], 1)
        self.assertEqual(report["changed"][0]["fields"]["location"]["delta"], [0, 0, 10])
        code, out, _ = self.run_cli("--compare", "task-a", "task-b")
        self.assertEqual(code, 0)
        self.assertIn("位移差 (cm): [0, 0, 10]", out)
        self.assertIn("变换变化=1", out)

    def test_compare_missing_invalid_and_conflicting_inputs(self):
        self.write("invalid", {"actors": [{"label": "bad", "location": [0]}]})
        for args in [
            ("--compare", "missing", "task-b"), ("--compare", "invalid", "task-b"),
            ("--compare", "task-a", "task-b", "--task-id", "task-a"),
            ("--compare", "task-a", "task-b", "--status", "done"),
        ]:
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
