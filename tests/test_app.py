import json
import tempfile
import unittest
from pathlib import Path

from snapshot_backup.app import create, find_snapshot, load_state, verify


class SnapshotBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "a.txt").write_text("hello", encoding="utf-8")
        self.output = self.root / "backups"
        self.state = self.root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_create_manifest_and_state(self):
        manifest = create(self.source, self.output, self.state)
        snapshot = self.output / manifest["id"]
        self.assertEqual(len(manifest["files"]), 1)
        self.assertEqual(len(load_state(self.state)["snapshots"]), 1)
        self.assertEqual((snapshot / "data" / "a.txt").read_text(encoding="utf-8"), "hello")
        self.assertEqual(verify(snapshot), (True, []))

    def test_verify_detects_tampering(self):
        manifest = create(self.source, self.output, self.state)
        (self.output / manifest["id"] / "data" / "a.txt").write_text("changed", encoding="utf-8")
        valid, problems = verify(self.output / manifest["id"])
        self.assertFalse(valid)
        self.assertTrue(problems)

    def test_refuses_output_inside_source(self):
        with self.assertRaises(ValueError):
            create(self.source, self.source / "nested", self.state)

    def test_symlink_file_and_directory_are_skipped_and_recorded(self):
        target = self.root / "outside.txt"
        target.write_text("private", encoding="utf-8")
        linked_dir = self.root / "outside-dir"
        linked_dir.mkdir()
        (linked_dir / "secret.txt").write_text("secret", encoding="utf-8")
        try:
            (self.source / "link.txt").symlink_to(target)
            (self.source / "linked-dir").symlink_to(linked_dir, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"无法创建符号链接：{exc}")
        manifest = create(self.source, self.output, self.state)
        self.assertEqual(set(manifest["skipped_symlinks_or_nonregular"]), {"link.txt", "linked-dir"})
        snapshot = self.output / manifest["id"]
        self.assertFalse((snapshot / "data" / "linked-dir").exists())
        self.assertTrue(verify(snapshot)[0])

    def test_find_by_id(self):
        manifest = create(self.source, self.output, self.state)
        self.assertEqual(find_snapshot(manifest["id"], self.state), (self.output / manifest["id"]).resolve())

    def test_source_manifest_name_is_preserved_inside_data(self):
        source_manifest = self.source / "manifest.json"
        source_manifest.write_text("original user file", encoding="utf-8")
        manifest = create(self.source, self.output, self.state)
        snapshot = self.output / manifest["id"]
        self.assertEqual((snapshot / "data" / "manifest.json").read_text(encoding="utf-8"), "original user file")
        self.assertTrue(verify(snapshot)[0])

    def test_new_snapshot_never_removes_previous_snapshot(self):
        first = create(self.source, self.output, self.state)
        second = create(self.source, self.output, self.state)
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue((self.output / first["id"]).is_dir())
        self.assertTrue((self.output / second["id"]).is_dir())
        self.assertEqual(len(load_state(self.state)["snapshots"]), 2)

    def test_verify_rejects_manifest_path_traversal(self):
        manifest = create(self.source, self.output, self.state)
        snapshot = self.output / manifest["id"]
        malicious = {"format": 1, "files": [{"path": "../../outside.txt", "sha256": "", "size": 0}]}
        (snapshot / "manifest.json").write_text(json.dumps(malicious), encoding="utf-8")
        valid, problems = verify(snapshot)
        self.assertFalse(valid)
        self.assertTrue(any("路径越界" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
