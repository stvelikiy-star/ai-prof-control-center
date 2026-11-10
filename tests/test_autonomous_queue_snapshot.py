import os
from pathlib import Path
import tempfile
import unittest

from orchestrator.autonomous_queue_snapshot import summarize_queue


QUEUE_DIRECTORIES = {
    "pending": "autonomous_pending",
    "active": "autonomous_active",
    "completed": "autonomous_completed",
    "blocked": "autonomous_blocked",
}


class SummarizeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def queue_directory(self, name):
        path = self.root / QUEUE_DIRECTORIES[name]
        path.mkdir()
        return path

    def test_missing_root_returns_zero_counts(self):
        self.assertEqual(
            summarize_queue(self.root / "missing"),
            {"pending": 0, "active": 0, "completed": 0, "blocked": 0},
        )

    def test_missing_queue_directories_return_zero_counts(self):
        self.assertEqual(
            summarize_queue(self.root),
            {"pending": 0, "active": 0, "completed": 0, "blocked": 0},
        )

    def test_counts_regular_files_in_each_queue(self):
        expected = {"pending": 2, "active": 1, "completed": 3, "blocked": 1}
        for queue_name, count in expected.items():
            directory = self.queue_directory(queue_name)
            for index in range(count):
                (directory / f"{index}.json").touch()

        self.assertEqual(summarize_queue(self.root), expected)

    def test_counts_regular_files_regardless_of_filename(self):
        directory = self.queue_directory("pending")
        (directory / "record.json").touch()
        (directory / ".hidden").touch()
        (directory / "no-extension").touch()

        self.assertEqual(summarize_queue(self.root)["pending"], 3)

    def test_rejects_non_path_root(self):
        with self.assertRaises(TypeError):
            summarize_queue(str(self.root))

    def test_rejects_root_that_is_a_regular_file(self):
        root_file = self.root / "root-file"
        root_file.touch()

        with self.assertRaises(ValueError):
            summarize_queue(root_file)

    def test_rejects_symlink_root_directory(self):
        link = self.root.parent / f"{self.root.name}-link"
        link.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(link.unlink)

        with self.assertRaises(ValueError):
            summarize_queue(link)

    def test_rejects_symlink_queue_directory(self):
        target = self.root / "target"
        target.mkdir()
        (self.root / "autonomous_pending").symlink_to(target, target_is_directory=True)

        with self.assertRaises(ValueError):
            summarize_queue(self.root)

    def test_rejects_file_in_place_of_queue_directory(self):
        (self.root / "autonomous_active").touch()

        with self.assertRaises(ValueError):
            summarize_queue(self.root)

    def test_rejects_subdirectory_queue_entry(self):
        (self.queue_directory("completed") / "nested").mkdir()

        with self.assertRaises(ValueError):
            summarize_queue(self.root)

    def test_rejects_symlink_file_queue_entry(self):
        target = self.root / "target.json"
        target.touch()
        (self.queue_directory("blocked") / "linked.json").symlink_to(target)

        with self.assertRaises(ValueError):
            summarize_queue(self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files are not supported")
    def test_rejects_fifo_queue_entry(self):
        fifo = self.queue_directory("pending") / "record.fifo"
        os.mkfifo(fifo)

        with self.assertRaises(ValueError):
            summarize_queue(self.root)


if __name__ == "__main__":
    unittest.main()
