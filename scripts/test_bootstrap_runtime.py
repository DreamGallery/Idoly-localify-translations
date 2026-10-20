"""Dependency refresh must preserve verified locks and require an explicit request."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_runtime as bootstrap


class PythonLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "bootstrap-lock.json"
        self.requirements = self.root / "requirements.lock"
        self.inputs = "fonttools==4.65.0\n"
        self.original = "fonttools==4.65.0 --hash=sha256:" + "a" * 64 + "\n"
        self.lock = {
            "python_requirements_input_sha256": hashlib.sha256(self.inputs.encode()).hexdigest(),
            "python_requirements_lock_sha256": hashlib.sha256(self.original.encode()).hexdigest(),
            "python_requirements_text": self.original,
            "sdk": {"unchanged": True},
        }
        self.requirements.write_text(self.original)
        bootstrap.atomic_json(self.path, self.lock)

    def resolve(self, text=None, refresh=False):
        return bootstrap.lock_python_requirements(
            self.root, Path("/uv"), self.inputs if text is None else text,
            self.lock, self.path, refresh=refresh)

    def test_unchanged_inputs_reuse_lock_even_with_refresh(self):
        with patch.object(bootstrap, "run") as run:
            self.assertEqual(self.resolve(refresh=True), self.requirements)
            run.assert_not_called()

    def test_changed_inputs_require_explicit_refresh(self):
        with patch.object(bootstrap, "run") as run:
            with self.assertRaisesRegex(ValueError, "refresh-python-dependencies"):
                self.resolve(self.inputs + "py7zr==1.1.4\n")
            run.assert_not_called()
        self.assertEqual(self.requirements.read_text(), self.original)

    def test_refresh_rejects_corrupt_lock(self):
        self.requirements.write_text("corrupt")
        with patch.object(bootstrap, "run") as run:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.resolve(self.inputs + "py7zr==1.1.4\n", refresh=True)
            run.assert_not_called()

    def test_refresh_seeds_existing_pins_and_backs_up_old_lock(self):
        updated_inputs = self.inputs + "py7zr==1.1.4\n"
        updated_lock = self.original + "py7zr==1.1.4 --hash=sha256:" + "b" * 64 + "\n"
        previous = dict(self.lock)

        def compile_lock(command, **_):
            self.assertIn("--generate-hashes", command)
            self.assertNotIn("--upgrade", command)
            output = command[command.index("-o") + 1]
            self.assertEqual(output.read_text(), self.original)
            self.assertEqual((output.parent / "requirements.in").read_text(), updated_inputs)
            output.write_text(updated_lock)

        with patch.object(bootstrap, "run", side_effect=compile_lock):
            self.resolve(updated_inputs, refresh=True)
        self.assertEqual(self.requirements.read_text(), updated_lock)
        self.assertEqual(json.loads(self.path.read_text()), self.lock)
        self.assertEqual(self.lock["python_requirements_lock_sha256"], bootstrap.sha(self.requirements))
        self.assertEqual(self.lock["sdk"], previous["sdk"])
        backup = self.root / ".agent-local/dependency-locks" / previous["python_requirements_lock_sha256"]
        self.assertEqual((backup / "requirements.lock").read_text(), self.original)
        self.assertEqual(json.loads((backup / "bootstrap-lock.json").read_text()), previous)
        with patch.object(bootstrap, "run") as run:
            self.resolve(updated_inputs)
            run.assert_not_called()

    def test_failed_resolution_preserves_both_locks(self):
        before = self.path.read_bytes()
        with patch.object(bootstrap, "run", side_effect=subprocess.CalledProcessError(1, "uv")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.resolve(self.inputs + "py7zr==1.1.4\n", refresh=True)
        self.assertEqual(self.requirements.read_text(), self.original)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_mirror_can_be_restored_from_verified_saved_text(self):
        self.requirements.unlink()
        with patch.object(bootstrap, "run") as run:
            self.resolve()
            run.assert_not_called()
        self.assertEqual(self.requirements.read_text(), self.original)

    def test_first_install_creates_a_lock_without_refresh(self):
        self.requirements.unlink()
        self.lock.clear()
        bootstrap.atomic_json(self.path, self.lock)

        def compile_lock(command, **_):
            output = command[command.index("-o") + 1]
            self.assertFalse(output.exists())
            output.write_text(self.original)

        with patch.object(bootstrap, "run", side_effect=compile_lock):
            self.resolve()
        self.assertEqual(self.requirements.read_text(), self.original)
        self.assertEqual(self.lock["python_requirements_lock_sha256"], bootstrap.sha(self.requirements))


if __name__ == "__main__":
    unittest.main()
