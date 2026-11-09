import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"orchestrator"))
import telegram_ru as ru

class RussianTelegramTests(unittest.TestCase):
    def test_help_is_russian(self):
        result=ru.localize("AI PROF Telegram Control Plane V2\n/ai help")
        self.assertIn("на русском",result)
        self.assertIn("/ai approvals",result)
        self.assertNotIn("No arbitrary shell",result)
    def test_task_notification(self):
        result=ru.localize("AI PROF task update\nProject: kol-travel-platform\nTask-ID: KOL_A1\nState: failed")
        self.assertIn("изменение статуса",result)
        self.assertIn("Статус: ошибка",result)
        self.assertIn("KOL_A1",result)
    def test_approvals_are_russian_without_publishing(self):
        result=ru.localize("AI PROF — owner approvals (read-only)\nAudit: 01B=PASS, 01C=PASS\nState: awaiting owner decision (not published)")
        self.assertIn("согласовании",result)
        self.assertIn("01B=PASS",result)
        self.assertIn("не опубликовано",result)
    def test_warning(self):
        result=ru.localize("[WARNING] AI PROF\nServer health problem detected.\nHost: ubuntu")
        self.assertIn("ПРЕДУПРЕЖДЕНИЕ",result)
        self.assertIn("Обнаружена проблема",result)
        self.assertIn("Сервер: ubuntu",result)
    def test_preserves_log_and_identifiers(self):
        source="AI PROF logs\nLog: task-01C.log\n---\nerror in package next-server: no such module\nToken: [REDACTED]"
        result=ru.localize(source)
        self.assertIn("error in package next-server: no such module",result)
        self.assertIn("[REDACTED]",result)
    def test_status_states(self):
        self.assertEqual(ru.localize("State: completed"),"Статус: завершено")
        self.assertEqual(ru.localize("State: blocked"),"Статус: заблокировано")
    def test_readonly_guard(self):
        result=ru.localize("Read-only request must not create a code-change task. For AI PROF diagnostics use /ai audit. No task was created.")
        self.assertIn("задача на изменение кода не создана",result)
        self.assertIn("/ai audit",result)
if __name__=="__main__":unittest.main()
