from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import offsite_control_plane_sftp as offsite


class OffsiteSFTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "control-plane-20261109T090000Z.tar.gz.gpg"
        self.archive.write_bytes(b"encrypted snapshot" * 10)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum = Path(str(self.archive) + ".sha256")
        self.checksum.write_text(digest + "  " + self.archive.name + "\n")
        self.key = self.root / "identity"
        self.key.write_text("dummy")
        self.key.chmod(0o600)
        self.hosts = self.root / "known_hosts"
        self.hosts.write_text("host key")
        self.hosts.chmod(0o600)
        self.config = self.root / "config.json"
        self.data = {
            "host": "backup.example.org",
            "user": "aiprof",
            "port": 22,
            "remote_dir": "/backup/ai-prof",
            "identity_file": str(self.key),
            "known_hosts": str(self.hosts),
        }
        self.write_config()

    def write_config(self):
        self.config.write_text(json.dumps(self.data))
        self.config.chmod(0o600)

    def test_good_config(self):
        self.assertEqual(offsite.config_from_file(self.config)["host"], "backup.example.org")

    def test_reject_traversal(self):
        self.data["remote_dir"] = "/backup/../other"
        self.write_config()
        with self.assertRaises(offsite.OffsiteError):
            offsite.config_from_file(self.config)

    def test_reject_config_permissions(self):
        self.config.chmod(0o644)
        with self.assertRaises(offsite.OffsiteError):
            offsite.config_from_file(self.config)

    def test_reject_insecure_identity_key(self):
        self.key.chmod(0o644)
        with self.assertRaises(offsite.OffsiteError):
            offsite.config_from_file(self.config)

    def test_reject_untrusted_hosts(self):
        self.hosts.unlink()
        with self.assertRaises(offsite.OffsiteError):
            offsite.config_from_file(self.config)

    def test_sha_verified_archive(self):
        a,c,d=offsite.latest_verified(self.root)
        self.assertEqual((a,c),(self.archive,self.checksum))
        self.assertEqual(d,hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_modified_archive_denied(self):
        self.archive.write_bytes(b"different" * 30)
        with self.assertRaises(offsite.OffsiteError):
            offsite.latest_verified(self.root)

    def test_missing_archive_denied(self):
        self.archive.unlink()
        with self.assertRaises(offsite.OffsiteError):
            offsite.latest_verified(self.root)

    def test_batch_has_strict_transport_gates(self):
        recorded = []
        def fake_run(args, **kwargs):
            recorded.extend(args)
            class Response:
                returncode = 0
                stdout = ""
                stderr = ""
            return Response()
        with patch.object(offsite.subprocess,"run",side_effect=fake_run):
            offsite.sftp(self.data, ["pwd"])
        self.assertIn("-oBatchMode=yes",recorded)
        self.assertIn("-oStrictHostKeyChecking=yes",recorded)
        self.assertIn("-oUserKnownHostsFile=" + str(self.hosts), recorded)

    def test_readback_hash_mismatch_denied(self):
        with patch.object(offsite,"sftp",return_value=None):
            with self.assertRaises(offsite.OffsiteError):
                offsite.transfer(self.data,self.archive,self.checksum,"0"*64)

if __name__ == "__main__":
    unittest.main()
