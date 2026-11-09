import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import control_plane_backup_health as h

class ControlPlaneBackupHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name)
        self.key=self.dir/"key"
        self.key.write_text("dummy not used for decryption")
        self.key.chmod(0o600)
        self.file=self.dir/"control-plane-20261109T080918Z.tar.gz.gpg"
        self.file.write_bytes(b"encrypted test fixture")
        digest=hashlib.sha256(self.file.read_bytes()).hexdigest()
        (self.dir/(self.file.name+".sha256")).write_text(digest+"  "+str(self.file)+"\n")
        os.utime(self.file,(100000,100000))
    def test_success(self):
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"PASS")
    def test_portable_manifest(self):
        digest=hashlib.sha256(self.file.read_bytes()).hexdigest()
        (self.dir/(self.file.name+".sha256")).write_text(digest+"  "+self.file.name+"\n")
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"PASS")
    def test_warning_age(self):
        self.assertEqual(h.evaluate(self.dir,self.key,now=100000+31*3600)[0],"WARNING")
    def test_critical_age(self):
        self.assertEqual(h.evaluate(self.dir,self.key,now=100000+55*3600)[0],"CRITICAL")
    def test_corruption(self):
        self.file.write_bytes(b"tampered data")
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"CRITICAL")
    def test_missing_key(self):
        self.key.unlink()
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"CRITICAL")
    def test_unsafe_key_permissions(self):
        self.key.chmod(0o644)
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"CRITICAL")
    def test_symlink_archive_not_accepted(self):
        self.file.unlink()
        self.file.symlink_to(self.dir/"absent")
        self.assertEqual(h.evaluate(self.dir,self.key,now=100050)[0],"CRITICAL")

if __name__=="__main__":unittest.main()
