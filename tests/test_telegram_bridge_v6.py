from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"orchestrator"))
import telegram_bridge_v6 as v6

TASK="KOL_TRAVEL_PLATFORM_20261109T063719Z_7899A5"
KOL=("approved","kol-travel-platform","")
FAIL=("failed","kol-travel-platform","error: token=PRIVATE_DO_NOT_SEND")

class Client:
    def __init__(self, fails=0):
        self.messages=[]
        self.fails=fails
    def send(self, text):
        if self.fails:
            self.fails-=1
            raise v6.legacy.BridgeError("simulated network loss")
        self.messages.append(text)

class DurableWatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"delivery.json"

    def test_bootstrap_does_not_spam_historical_tasks(self):
        cl=Client()
        self.assertEqual(v6.notify_once(cl,path=self.path,snapshot={TASK:KOL}),0)
        self.assertEqual(cl.messages,[])
        self.assertEqual(v6.read_state(self.path)[TASK],v6.fingerprint(KOL))
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_new_status_sent_once_and_survives_restart(self):
        cl=Client()
        v6.notify_once(cl,path=self.path,snapshot={TASK:KOL})
        self.assertEqual(v6.notify_once(cl,path=self.path,snapshot={TASK:FAIL}),1)
        self.assertEqual(v6.notify_once(Client(),path=self.path,snapshot={TASK:FAIL}),0)
        self.assertEqual(len(cl.messages),1)
        self.assertIn("State: failed",cl.messages[0])
        self.assertNotIn("PRIVATE_DO_NOT_SEND",cl.messages[0])

    def test_failed_send_not_checkpointed_then_retried(self):
        v6.notify_once(Client(),path=self.path,snapshot={TASK:KOL})
        bad=Client(fails=1)
        with self.assertRaises(v6.legacy.BridgeError):
            v6.notify_once(bad,path=self.path,snapshot={TASK:FAIL})
        self.assertEqual(v6.read_state(self.path)[TASK],v6.fingerprint(KOL))
        self.assertEqual(v6.notify_once(bad,path=self.path,snapshot={TASK:FAIL}),1)

    def test_new_task_after_bootstrap_delivered(self):
        v6.notify_once(Client(),path=self.path,snapshot={TASK:KOL})
        cl=Client()
        task2="KOL_TRAVEL_PLATFORM_20261109T070000Z_AABBCC"
        self.assertEqual(v6.notify_once(cl,path=self.path,snapshot={TASK:KOL,task2:KOL}),1)
        self.assertIn(task2,cl.messages[0])

    def test_corrupt_state_fails_closed(self):
        self.path.write_text('not-json')
        with self.assertRaises(v6.DeliveryStateError):
            v6.notify_once(Client(),path=self.path,snapshot={TASK:KOL})
        self.assertEqual(self.path.read_text(),'not-json')

    def test_symlink_state_rejected(self):
        target=Path(self.tmp.name)/"other"
        target.write_text('{}')
        self.path.symlink_to(target)
        with self.assertRaises(v6.DeliveryStateError):
            v6.notify_once(Client(),path=self.path,snapshot={TASK:KOL})
        self.assertEqual(target.read_text(),'{}')

    def test_main_patches_only_passive_watcher(self):
        old=v6.v2.watch_task_changes
        try:
            with patch.object(v6.v5,"main",return_value=0):
                self.assertEqual(v6.main(),0)
            self.assertIs(v6.v2.watch_task_changes,v6.watch_task_changes_v6)
        finally:
            v6.v2.watch_task_changes=old

if __name__=="__main__":
    unittest.main()
