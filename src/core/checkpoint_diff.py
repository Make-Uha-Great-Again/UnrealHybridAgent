"""Read-only comparison of recorded actor transforms, matched by label."""

from __future__ import annotations

import math
from typing import Any, Mapping


def _actors(checkpoint: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = checkpoint.get("actors")
    if not isinstance(rows, list):
        raise ValueError("checkpoint actors 必须是对象列表")
    actors = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("checkpoint actors 必须是对象列表")
        label = row.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Actor label 必须是非空字符串")
        if label in actors:
            raise ValueError(f"Actor label 重复，无法可靠对比: {label}")
        actor = {"label": label}
        for field in ("location", "rotation", "scale"):
            value = row.get(field)
            if value is None and field != "location":
                actor[field] = None
                continue
            if (not isinstance(value, list) or len(value) != 3
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) for v in value)):
                raise ValueError(f"{label}.{field} 必须包含三个有限数值")
            actor[field] = list(value)
        actors[label] = actor
    return actors


def compare_checkpoints(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Compare saved snapshots only; never infer live state or actor identity."""
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise ValueError("checkpoint 必须是 JSON 对象")
    old_level, new_level = before.get("level"), after.get("level")
    for level in (old_level, new_level):
        if level is not None and not isinstance(level, str):
            raise ValueError("checkpoint level 必须是字符串或 null")
    if old_level and new_level and old_level != new_level:
        raise ValueError("检查点属于不同关卡，不能对比")
    old, new = _actors(before), _actors(after)
    changed = []
    unchanged = []
    for label in sorted(old.keys() & new.keys()):
        fields = {}
        for field in ("location", "rotation", "scale"):
            a, b = old[label][field], new[label][field]
            if a != b:
                fields[field] = {"before": a, "after": b}
                if field == "location":
                    delta = [y - x for x, y in zip(a, b)]
                    if not all(math.isfinite(v) for v in delta):
                        raise ValueError(f"{label}.location 位移差超出有限数值范围")
                    fields[field]["delta"] = delta
        if fields:
            changed.append({"label": label, "fields": fields})
        else:
            unchanged.append(label)
    added = [new[label] for label in sorted(new.keys() - old.keys())]
    removed = [old[label] for label in sorted(old.keys() - new.keys())]
    warnings = ["仅对比保存的快照，按 label 匹配；记录出现/消失不代表场景中创建/删除，改名无法识别。"]
    if not old_level or not new_level:
        warnings.append("至少一份快照缺少关卡信息，无法确认两份快照来自同一关卡。")
    return {
        "before_task_id": before.get("task_id"),
        "after_task_id": after.get("task_id"),
        "level": old_level or new_level,
        "level_verified": bool(old_level and new_level),
        "summary": {"added": len(added), "removed": len(removed),
                    "changed": len(changed), "unchanged": len(unchanged)},
        "added": added, "removed": removed, "changed": changed, "unchanged": unchanged,
        "warnings": warnings,
    }
