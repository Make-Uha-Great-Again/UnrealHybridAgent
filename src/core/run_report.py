"""Offline run-log summaries. Attempt success and verification stay separate."""

from __future__ import annotations

import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _verification(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks:
        return "unknown"
    executed = []
    uncertain = False
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("skipped", False), bool):
            uncertain = True
        elif check.get("skipped") is True:
            continue
        elif isinstance(check.get("passed"), bool):
            executed.append(check["passed"])
        else:
            uncertain = True
    if False in executed:
        return "failed"
    if uncertain:
        return "unknown"
    if not executed:
        return "skipped"
    return "passed"


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def build_run_report(path: Path | str, *, task: str | None = None,
                     method: str | None = None) -> dict[str, Any]:
    """Read JSONL in file order; retain partial results with line diagnostics."""
    source = Path(path)
    timeline = []
    issues = []
    valid_records = 0
    with source.open("r", encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line, parse_constant=_reject_constant)
                if not isinstance(raw, dict) or not _text(raw.get("kind")).strip():
                    raise ValueError("record must have a nonempty kind")
            except (ValueError, RecursionError):
                issues.append({"line": line_no, "reason": "无效 JSON 或缺少事件类型"})
                continue
            valid_records += 1
            kind = raw["kind"]
            row_task = _text(raw.get("task")) or _text(raw.get("skill"))
            row_method = _text(raw.get("selected_method")) or _text(raw.get("method"))
            # A fallback belongs to the destination method for filtering.
            if kind == "fallback":
                row_method = _text(raw.get("to_method")) or row_method
            if task is not None and row_task != task:
                continue
            if method is not None and row_method != method:
                continue
            duration = _number(raw.get("duration_ms")) if kind == "step" else None
            if kind == "step" and raw.get("duration_ms") is not None and duration is None:
                issues.append({"line": line_no, "reason": "耗时无效，已从耗时统计中排除"})
            result = _text(raw.get("result"), "unknown") if kind == "step" else ""
            if kind == "step" and result not in ("success", "error", "unverified"):
                result = "unknown"
            error = raw.get("error")
            error_type = (_text(error.get("code")) or _text(error.get("type"))) if isinstance(error, dict) else ""
            timeline.append({
                "line": line_no, "run": _text(raw.get("run")), "kind": kind,
                "task": row_task, "method": row_method, "timestamp": _number(raw.get("ts")),
                "result": result, "duration_ms": duration, "error_type": error_type,
                "verification": _verification(raw.get("verification")) if kind == "step" else "",
                "from_method": _text(raw.get("from_method")) if kind == "fallback" else "",
            })

    steps = [row for row in timeline if row["kind"] == "step"]
    methods = []
    for name in sorted({row["method"] or "UNKNOWN" for row in steps}):
        rows = [row for row in steps if (row["method"] or "UNKNOWN") == name]
        durations = sorted(row["duration_ms"] for row in rows if row["duration_ms"] is not None)
        # Mean and nearest-rank p95 describe logged attempts, not network RTT.
        mean = sum(v / len(durations) for v in durations) if durations else None
        methods.append({"method": name, "attempts": len(rows),
                        "results": dict(Counter(row["result"] for row in rows)),
                        "verification": dict(Counter(row["verification"] for row in rows)),
                        "timed_attempts": len(durations), "mean_ms": mean,
                        "p95_ms": durations[math.ceil(len(durations) * .95) - 1] if durations else None})
    return {
        "source": str(source), "filters": {"task": task, "method": method},
        "valid_records": valid_records, "selected_records": len(timeline),
        "runs": sorted({row["run"] for row in timeline if row["run"]}),
        "summary": {"attempts": len(steps), "fallbacks": sum(row["kind"] == "fallback" for row in timeline),
                    "results": dict(Counter(row["result"] for row in steps)),
                    "verification": dict(Counter(row["verification"] for row in steps))},
        "events": dict(sorted(Counter(row["kind"] for row in timeline).items())),
        "methods": methods, "timeline": timeline, "issues": issues,
        "notes": ["统计对象是日志中的执行尝试，同一任务可能有多次尝试；不能据此推断整个任务成功。",
                  "验证状态从逐项 checks 重新汇总：全部跳过记 skipped，缺失或格式不明记 unknown。",
                  "时间线按文件行序排列；耗时是尝试耗时，p95 使用最近秩算法。",
                  "报告仅展示选定字段，不导出工具参数、返回载荷或完整错误消息。"],
    }


def render_run_report(report: dict[str, Any]) -> str:
    """Self-contained HTML; all log-provided text is escaped, with no scripts."""
    def cell(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return html.escape(str(value), quote=True)

    def table(headers: list[str], rows: list[list[Any]]) -> str:
        head = "".join(f"<th>{cell(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>" for row in rows)
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    summary = report["summary"]
    methods = table(["通道", "尝试", "执行结果", "验证结果", "有效耗时数", "平均 ms", "p95 ms"], [
        [r["method"], r["attempts"], r["results"], r["verification"], r["timed_attempts"],
         round(r["mean_ms"], 2) if r["mean_ms"] is not None else None, r["p95_ms"]] for r in report["methods"]])
    timeline = table(["行", "运行", "事件", "任务", "通道", "执行结果", "验证", "耗时 ms", "错误类型"], [
        [r["line"], r["run"], r["kind"], r["task"],
         f'{r["from_method"]} → {r["method"]}' if r["from_method"] else r["method"],
         r["result"], r["verification"], r["duration_ms"], r["error_type"]] for r in report["timeline"]])
    issues = table(["行", "问题"], [[r["line"], r["reason"]] for r in report["issues"]])
    notes = "".join(f"<li>{cell(note)}</li>" for note in report["notes"])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UHA 运行复盘报告</title><style>
body{{font:15px/1.65 system-ui,sans-serif;margin:0;background:#f3f6fa;color:#172b45}}
main{{max-width:1320px;margin:auto;padding:32px}}h1,h2{{color:#173f6a}}
.cards{{display:flex;flex-wrap:wrap;gap:16px}}.card{{background:white;padding:18px 24px;border-radius:12px}}
.card strong{{display:block;font-size:30px}}.scroll{{overflow:auto;background:white;border-radius:10px}}
table{{border-collapse:collapse;width:100%;text-align:left}}th,td{{padding:10px 14px;border-bottom:1px solid #e5ebf1;vertical-align:top}}
th{{background:#e7eff8;white-space:nowrap}}td{{overflow-wrap:anywhere}}.meta{{overflow-wrap:anywhere}}
@media print{{main{{padding:0}}.scroll{{overflow:visible}}body{{background:white}}}}
</style></head><body><main><h1>UHA 运行复盘报告</h1>
<p class="meta">日志：{cell(report["source"])}<br>筛选：{cell(report["filters"])}</p>
<div class="cards"><div class="card">执行尝试<strong>{summary["attempts"]}</strong></div>
<div class="card">实际降级事件<strong>{summary["fallbacks"]}</strong></div>
<div class="card">日志问题<strong>{len(report["issues"])}</strong></div></div>
<p>执行结果：{cell(summary["results"])}<br>验证结果：{cell(summary["verification"])}</p>
<h2>通道统计</h2>{methods}<h2>事件统计</h2><p>{cell(report["events"])}</p>
<h2>执行时间线</h2>{timeline}<h2>日志问题</h2>{issues}<h2>统计说明</h2><ul>{notes}</ul>
</main></body></html>'''
