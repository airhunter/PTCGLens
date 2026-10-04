import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel

from overlay_app import ReadingCard


class ReadingCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.requests = []
        self.popup = ReadingCard(lambda *job: self.requests.append(job))
        self.popup.loading(2, True)
        self.card = {"card_id": "test_en_001", "name_en": "Test card", "name_zh": "测试卡", "hp": 100,
                     "set_code": "TEST", "number": "1", "attacks": [
                         {"kind": "ability", "name_en": "Ability", "name_zh": "特性名称", "text_zh": "特性效果"},
                         {"kind": "attack", "name_en": "Attack", "damage": "30", "text_en": "English rule text."}]}

    def tearDown(self):
        self.popup.close()
        self.popup.deleteLater()
        self.app.processEvents()

    def test_old_result_does_not_replace_new_query(self):
        self.popup.show_result({"id": 1, "card": self.card, "elapsed": 200})
        self.assertIn("正在识别", self.popup.findChildren(QLabel)[0].text())
        self.popup.set_art(1, b"not an image")

    def test_ability_and_attack_and_translation_are_available(self):
        self.popup.show_result({"id": 2, "card": self.card, "elapsed": 200})
        text = "\n".join(label.text() for label in self.popup.findChildren(QLabel))
        for expected in ("测试卡", "HP 100", "特性 · 特性名称", "招式 · Attack", "30", "特性效果"):
            self.assertIn(expected, text)
        self.popup.request_translation(2, "English rule text.")
        self.assertEqual(self.requests, [(2, 2, "English rule text.")])
        self.popup.translated(1, 2, "旧查询译文")
        self.assertEqual(self.popup.text_labels[2].text(), "English rule text.")
        self.popup.translated(2, 2, "翻译暂不可用：offline")
        self.assertEqual(self.popup.text_labels[2].text(), "English rule text.")
        self.assertTrue(self.popup.buttons[2].isEnabled())

    def test_unconfirmed_result_does_not_show_card_details(self):
        self.popup.show_result({"id": 2, "result": {"status": "tentative", "card_id": "test_en_001"}})
        self.assertEqual(self.popup.findChildren(QLabel)[0].text(), "待确认")


if __name__ == "__main__":
    unittest.main()
