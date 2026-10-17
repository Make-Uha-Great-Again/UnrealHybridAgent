"""Snapshot comparison must not invent identity or missing transform evidence."""

import copy
import unittest

from src.core.checkpoint_diff import compare_checkpoints


def actor(label, z=0, **fields):
    return {"label": label, "location": [0, 0, z], **fields}


def checkpoint(*actors, level="Demo", task_id="task"):
    return {"task_id": task_id, "level": level, "actors": list(actors)}


class CheckpointDiffTests(unittest.TestCase):
    def test_transform_changes_and_signed_delta(self):
        old = checkpoint(actor("柱子", 20, rotation=[0, 0, 0], scale=[1, 1, 1]), task_id="old")
        new = checkpoint(actor("柱子", 5, rotation=[0, 90, 0], scale=[2, 1, 1]), task_id="new")
        originals = copy.deepcopy((old, new))
        out = compare_checkpoints(old, new)
        self.assertEqual(out["before_task_id"], "old")
        self.assertEqual(out["after_task_id"], "new")
        self.assertEqual(out["summary"], {"added": 0, "removed": 0, "changed": 1, "unchanged": 0})
        fields = out["changed"][0]["fields"]
        self.assertEqual(fields["location"]["delta"], [0, 0, -15])
        self.assertEqual(fields["rotation"]["after"], [0, 90, 0])
        self.assertEqual(fields["scale"]["before"], [1, 1, 1])
        self.assertEqual((old, new), originals)

    def test_added_removed_and_unchanged_are_sorted(self):
        old = checkpoint(actor("B"), actor("A"), actor("removed"))
        new = checkpoint(actor("new-Z"), actor("B"), actor("new-A"), actor("A"))
        out = compare_checkpoints(old, new)
        self.assertEqual(out["unchanged"], ["A", "B"])
        self.assertEqual([r["label"] for r in out["added"]], ["new-A", "new-Z"])
        self.assertEqual([r["label"] for r in out["removed"]], ["removed"])

    def test_missing_rotation_is_not_default_rotation(self):
        out = compare_checkpoints(checkpoint(actor("A")), checkpoint(actor("A", rotation=[0, 0, 0])))
        self.assertEqual(out["changed"][0]["fields"], {
            "rotation": {"before": None, "after": [0, 0, 0]}})

    def test_metadata_does_not_change_transforms(self):
        old, new = checkpoint(actor("A")), checkpoint(actor("A", 0.0))
        old["status"], new["status"] = "created", "done"
        self.assertEqual(compare_checkpoints(old, new)["unchanged"], ["A"])

    def test_different_levels_refused_unknown_level_warned(self):
        with self.assertRaisesRegex(ValueError, "不同关卡"):
            compare_checkpoints(checkpoint(level="One"), checkpoint(level="Two"))
        out = compare_checkpoints(checkpoint(level=None), checkpoint())
        self.assertFalse(out["level_verified"])
        self.assertEqual(len(out["warnings"]), 2)
        self.assertTrue(compare_checkpoints(checkpoint(), checkpoint())["level_verified"])

    def test_rename_is_only_record_appearance_and_disappearance(self):
        out = compare_checkpoints(checkpoint(actor("old")), checkpoint(actor("new")))
        self.assertEqual(out["summary"], {"added": 1, "removed": 1, "changed": 0, "unchanged": 0})
        self.assertIn("改名无法识别", out["warnings"][0])

    def test_duplicate_labels_refused_on_either_side(self):
        duplicate = checkpoint(actor("A"), actor("A", 10))
        for old, new in [(duplicate, checkpoint()), (checkpoint(), duplicate)]:
            with self.assertRaisesRegex(ValueError, "重复"):
                compare_checkpoints(old, new)

    def test_invalid_snapshots_rejected(self):
        bad_snapshots = [[], {}, {"actors": {}}, {"actors": [None]}, checkpoint(actor("")),
                         checkpoint(actor("A", location=[0, 1])), checkpoint(actor("A", location=[0, 0, True])),
                         checkpoint(actor("A", float("nan"))), checkpoint(actor("A", float("inf"))),
                         checkpoint(actor("A", rotation="0,0,0")), checkpoint(level=42)]
        for bad in bad_snapshots:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compare_checkpoints(bad, checkpoint())

    def test_delta_overflow_rejected(self):
        with self.assertRaises(ValueError):
            compare_checkpoints(checkpoint(actor("A", -1.7e308)), checkpoint(actor("A", 1.7e308)))


if __name__ == "__main__":
    unittest.main()
