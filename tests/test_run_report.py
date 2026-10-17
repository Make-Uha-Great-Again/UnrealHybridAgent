"""Offline report tests using recorded-schema fixtures and real RunLogger output."""

import contextlib
import io
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import uha
from src.core.log import RunLogger
from src.core.run_report import build_run_report, render_run_report


def step(**fields):
    return {"kind": "step", "run": "demo", "task": "actor_move", "selected_method": "UNREAL_MCP",
            "duration_ms": 10, "result": "success", **fields}


def verification(*checks):
    return {"passed": True, "checks": list(checks)}


class RunReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "run.jsonl"

    def write(self, *rows):
        self.path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def test_results_and_verification_are_independent(self):
        self.write(
            step(verification=verification({"passed": True})),
            step(verification=verification({"passed": False, "skipped": True})),
            step(result="unverified", verification=verification({"passed": False})),
            step(result="error", verification=None),
        )
        report = build_run_report(self.path)
        self.assertEqual(report["summary"]["results"], {"success": 2, "unverified": 1, "error": 1})
        self.assertEqual(report["summary"]["verification"], {"passed": 1, "skipped": 1, "failed": 1, "unknown": 1})
        self.assertEqual(report["issues"], [])

    def test_incomplete_checks_cannot_pass(self):
        values = [None, {}, {"passed": True}, verification(), verification({"passed": "true"}),
                  verification({"passed": True}, None), verification({"passed": True, "skipped": "false"})]
        self.write(*(step(verification=v) for v in values))
        report = build_run_report(self.path)
        self.assertEqual(report["summary"]["verification"], {"unknown": len(values)})

    def test_failed_check_is_visible_alongside_unknown_checks(self):
        self.write(step(verification=verification(None, {"passed": False})))
        self.assertEqual(build_run_report(self.path)["summary"]["verification"], {"failed": 1})

    def test_fallback_events_and_filters_follow_destination(self):
        self.write(step(result="error"),
                   {"kind": "fallback_skip", "method": "UE_PYTHON"},
                   {"kind": "fallback", "skill": "actor_move", "from_method": "UNREAL_MCP", "to_method": "UE_PYTHON"},
                   step(selected_method="UE_PYTHON"), step(task="level_save"))
        all_rows = build_run_report(self.path)
        self.assertEqual(all_rows["summary"]["fallbacks"], 1)
        report = build_run_report(self.path, task="actor_move", method="UE_PYTHON")
        self.assertEqual(report["valid_records"], 5)
        self.assertEqual(report["selected_records"], 2)
        self.assertEqual(report["summary"]["attempts"], 1)
        self.assertEqual(report["timeline"][0]["from_method"], "UNREAL_MCP")

    def test_mean_and_nearest_rank_p95(self):
        self.write(*(step(duration_ms=i) for i in range(1, 21)))
        report = build_run_report(self.path)
        method = report["methods"][0]
        self.assertAlmostEqual(method["mean_ms"], 10.5)
        self.assertEqual(method["p95_ms"], 19)
        self.assertEqual(method["timed_attempts"], 20)

    def test_invalid_duration_not_counted_as_zero(self):
        self.write(*(step(duration_ms=v) for v in (0, None, -1, "10", True)))
        report = build_run_report(self.path)
        self.assertEqual(report["methods"][0]["timed_attempts"], 1)
        self.assertEqual(report["methods"][0]["mean_ms"], 0)
        self.assertEqual(len(report["issues"]), 3)
        self.assertEqual(report["summary"]["attempts"], 5)

    def test_corrupt_and_truncated_rows_keep_line_numbers(self):
        self.path.write_text('\ufeff\n{}\n[]\n{"kind":"event"}\n{"kind":"step","ts":NaN}\n{"kind":', encoding="utf-8")
        report = build_run_report(self.path)
        self.assertEqual(report["valid_records"], 1)
        self.assertEqual([r["line"] for r in report["issues"]], [2, 3, 5, 6])
        self.assertEqual(report["timeline"][0]["line"], 4)

    def test_empty_no_match_and_unknown_result(self):
        self.path.write_text("", encoding="utf-8")
        report = build_run_report(self.path)
        self.assertEqual(report["summary"]["attempts"], 0)
        self.assertEqual(report["methods"], [])
        self.write(step(result="future-result"))
        self.assertEqual(build_run_report(self.path)["summary"]["results"], {"unknown": 1})
        self.assertEqual(build_run_report(self.path, method="missing")["selected_records"], 0)

    def test_file_order_is_retained_when_clocks_move_backwards(self):
        self.write(step(ts=100), step(ts=90))
        report = build_run_report(self.path)
        self.assertEqual([r["timestamp"] for r in report["timeline"]], [100, 90])

    def test_raw_arguments_and_error_messages_are_not_exported(self):
        self.write(step(arguments={"token": "do-not-export"}, metadata={"secret": "do-not-export"},
                        error={"code": "TIMEOUT", "message": "do-not-export"}))
        report = build_run_report(self.path)
        self.assertNotIn("do-not-export", json.dumps(report))
        self.assertNotIn("do-not-export", render_run_report(report))
        self.assertEqual(report["timeline"][0]["error_type"], "TIMEOUT")

    def test_html_escapes_log_text_and_has_no_active_content(self):
        self.write(step(task='<script>alert("x")</script>'))
        output = render_run_report(build_run_report(self.path))
        self.assertIn("&lt;script&gt;", output)
        self.assertNotIn("<script>", output)
        self.assertIn('lang="zh-CN"', output)

        class Inspector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                for name, _ in attrs:
                    if name in ("src", "href") or name.startswith("on"):
                        raise AssertionError("external or executable HTML attribute")

        inspector = Inspector()
        inspector.feed(output)
        self.assertEqual(inspector.tags.count("table"), 3)
        self.assertNotIn("script", inspector.tags)

    def test_real_logger_schema_is_supported(self):
        logger = RunLogger(self.root / "logs", run_name="report-test", console=False)
        try:
            logger.event("fallback", skill="actor_move", from_method="UE_PYTHON", to_method="UNREAL_MCP")
            with logger.step("actor_move", method="UNREAL_MCP", reason="offline fixture") as rec:
                rec.set(result="success", verification=verification({"passed": True}))
            report = build_run_report(logger.jsonl.path)
            self.assertEqual(report["summary"]["attempts"], 1)
            self.assertEqual(report["summary"]["fallbacks"], 1)
            self.assertEqual(report["summary"]["verification"], {"passed": 1})
            self.assertEqual(report["runs"], [logger.run_id])
        finally:
            for handler in logger._logger.handlers[:]:
                handler.close()
                logger._logger.removeHandler(handler)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(uha, "build_bundle", side_effect=AssertionError("provider connected")), \
             patch.object(uha, "_setup", side_effect=AssertionError("runtime started")), \
             patch.object(uha, "load_config", side_effect=AssertionError("config accessed")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = uha.main(["run-report", str(self.path), *args])
        return code, out.getvalue(), err.getvalue()

    def test_cli_json_and_html_preserve_source(self):
        self.write(step())
        before = self.path.read_bytes()
        output = self.root / "report.html"
        code, out, err = self.run_cli("--json", "--html", str(output))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["summary"]["attempts"], 1)
        self.assertIn("UHA 运行复盘报告", output.read_text(encoding="utf-8"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_error_paths_and_partial_report(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.write(step())
        original = self.path.read_bytes()
        self.assertEqual(self.run_cli("--html", str(self.path))[0], 2)
        self.assertEqual(self.path.read_bytes(), original)
        output = self.root / "existing.html"
        output.write_text("keep", encoding="utf-8")
        self.assertEqual(self.run_cli("--html", str(output))[0], 2)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write('{"kind":')
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["summary"]["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
