import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from overlay_app import ReadingCard, configure_font


class ReadingCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_font(cls.app)

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
        self.assertIn("正在识别", self.popup.labels[0][0].text())
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
        self.assertEqual(self.popup.source_badge.text(), "中 · EN")
        self.popup.translated(2, 2, "中文效果")
        self.assertEqual(self.popup.text_labels[2].text(), "中文效果")
        self.assertEqual(self.popup.source_badge.text(), "中 · EN · 译")
        self.assertTrue(self.popup.buttons[2].isHidden())

    def test_unconfirmed_result_does_not_show_card_details(self):
        self.popup.show_result({"id": 2, "result": {"status": "tentative", "card_id": "test_en_001"}})
        self.assertEqual(self.popup.labels[0][0].text(), "待确认")
        self.assertEqual(len(self.popup.labels), 1)

    def assert_content_fully_visible(self):
        self.popup.show()
        self.app.processEvents()
        self.assertFalse(self.popup.findChildren(QScrollArea))
        for label,_ in self.popup.labels:
            self.assertGreaterEqual(label.height(),label.heightForWidth(label.width()))
            origin = label.mapTo(self.popup,label.rect().bottomRight())
            self.assertLess(origin.y(),self.popup.height())

    def test_long_rules_expand_past_old_height_limit_without_scrollbar(self):
        self.card["attacks"][1]["text_en"] = "Search your deck for a Pokemon, reveal it, and put it into your hand. "*16
        self.popup.show_result({"id":2,"card":self.card,"elapsed":200})
        self.assertGreater(self.popup.height(),620)
        self.assertEqual(self.popup.width(),350)
        self.assert_content_fully_visible()

    def test_translated_text_refits_height_and_small_window_refits_width(self):
        self.popup.show_result({"id":2,"card":self.card,"elapsed":200})
        initial = self.popup.height()
        self.popup.translated(2,2,"选择自己牌库中的一张宝可梦，在给对手看过之后加入手牌，然后重洗牌库。"*10)
        self.assertGreater(self.popup.height(),initial)
        self.assertTrue(self.popup.fit_to_area(700,500))
        self.assertLessEqual(self.popup.height(),500)
        self.assert_content_fully_visible()

    def test_popup_contains_only_card_content_and_compact_controls(self):
        self.popup.show_result({"id":2,"card":self.card,"elapsed":987})
        widgets = self.popup.findChildren(QLabel)+self.popup.findChildren(QPushButton)
        text = "\n".join(widget.text() for widget in widgets)
        for removed in ("Esc", "ESC", "中文阅读", "对照缺失", "本次识别", "987", "仅供参考", "本次查询"):
            self.assertNotIn(removed, text)
        self.assertEqual(self.popup.close_button.text(), "×")
        self.assertEqual(self.popup.buttons[2].text(), "翻译")

    def test_whole_card_keeps_edges_in_thumbnail_and_large_image(self):
        image = QImage(240,336,QImage.Format.Format_RGB32)
        image.fill(QColor("yellow"))
        image.setPixelColor(0,0,QColor("red"))
        image.setPixelColor(239,335,QColor("blue"))
        with patch("overlay_app.QPixmap",return_value=QPixmap.fromImage(image)):
            self.popup.show_result({"id":2,"card":self.card})
        self.assertEqual(self.popup.art_pixmap.size(),image.size())
        self.assertEqual(self.popup.art_pixmap.toImage().pixelColor(0,0),QColor("red"))
        self.assertEqual(self.popup.art_pixmap.toImage().pixelColor(239,335),QColor("blue"))
        self.popup.set_art_pixmap(QPixmap.fromImage(image.scaled(480,672)))
        self.assertTrue(self.popup.fit_to_area(900,840))
        displayed = self.popup.image.pixmap()
        self.assertAlmostEqual(displayed.width()/displayed.height(),240/336,places=2)
        self.assertGreaterEqual(displayed.height(),220)
        self.assert_content_fully_visible()

    def test_thumbnail_is_not_enlarged_on_125_percent_screen(self):
        self.popup.show_result({"id":2,"card":self.card})
        image = QPixmap(171,240)
        image.fill(QColor("yellow"))
        with patch.object(self.popup,"devicePixelRatioF",return_value=1.25):
            self.popup.set_art_pixmap(image)
            displayed = self.popup.image.pixmap()
            self.assertEqual(displayed.size(),image.size())
            self.assertEqual(displayed.devicePixelRatio(),1.25)
            self.assertEqual(self.popup.image.height(),192)
            self.assertIn("SD",self.popup.source_badge.text())

    def test_full_image_retains_physical_pixels_for_high_dpi(self):
        self.popup.show_result({"id":2,"card":self.card})
        image = QPixmap(686,962)
        image.fill(QColor("yellow"))
        with patch.object(self.popup,"devicePixelRatioF",return_value=1.25):
            self.popup.set_art_pixmap(image)
            displayed = self.popup.image.pixmap()
            self.assertGreater(displayed.width(),300)
            self.assertEqual(displayed.height(),525)
            self.assertEqual(displayed.devicePixelRatio(),1.25)
            self.assertEqual(self.popup.image.height(),420)
            self.assertIn("HD",self.popup.source_badge.text())

    def chinese_image_payload(self):
        image = QImage(600,825,QImage.Format.Format_RGB32)
        image.fill(QColor("yellow"))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer,"PNG")
        return {"bytes":bytes(buffer.data()),"language":"简中","set":"CSV","printing":"001/100",
                "chinese_effect":"选择自己牌库中的一张宝可梦，加入手牌。"}

    def test_chinese_card_effect_replaces_english_and_ignores_late_machine_translation(self):
        self.card.update(hp=0,attacks=[],card_text_en="English rule")
        self.popup.show_result({"id":2,"card":self.card})
        self.popup.set_art(2,self.chinese_image_payload())
        self.assertIn("选择自己牌库",self.popup.text_labels[0].text())
        self.assertTrue(self.popup.buttons[0].isHidden())
        self.assertIn("中图",self.popup.source_badge.text())
        self.assertEqual(self.popup.printing_label.text(),"CSV · 001/100")
        self.popup.translated(2,0,"晚到的机器译文")
        self.assertNotIn("机器译文",self.popup.text_labels[0].text())

    def test_old_image_cannot_replace_new_card_and_english_switch_restores_number(self):
        self.card.update(hp=0,attacks=[],card_text_en="English rule")
        self.popup.show_result({"id":2,"card":self.card})
        self.popup.set_art(1,self.chinese_image_payload())
        self.assertEqual(self.popup.text_labels[0].text(),"English rule")
        self.popup.set_art(2,self.chinese_image_payload())
        english = {**self.chinese_image_payload(),"language":"英文","chinese_effect":None}
        self.popup.set_art(2,english)
        self.assertEqual(self.popup.printing_label.text(),"TEST · 1")
        self.assertIn("EN图",self.popup.source_badge.text())


if __name__ == "__main__":
    unittest.main()
