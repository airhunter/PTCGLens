import unittest

from overlay_model import frame_point, popup_position, shortcut_keys


class OverlayRulesTest(unittest.TestCase):
    def test_scaled_window_with_negative_monitor_origin(self):
        self.assertEqual(frame_point((-600, 550), (-1200, 100, 0, 1000), (600, 800)), (400, 300))
        self.assertIsNone(frame_point((0, 550), (-1200, 100, 0, 1000), (600, 800)))

    def test_popup_avoids_card_near_screen_right_edge(self):
        x, y = popup_position((1700, 300, 1890, 560), (350, 620), (0, 0, 1920, 1080))
        self.assertLessEqual(x+350, 1700)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(y+620, 1080)

    def test_popup_uses_above_if_both_sides_are_blocked(self):
        x, y = popup_position((200, 700, 600, 900), (350, 500), (0, 0, 800, 1000))
        self.assertLessEqual(y+500, 700)

    def test_shortcut_requires_modifier_and_accepts_keyboard_key(self):
        self.assertEqual(shortcut_keys("Ctrl+Shift+Q"), (0x11, 0x10, ord("Q")))
        self.assertEqual(shortcut_keys("Alt+F12"), (0x12, 0x7B))
        for text in ("Q", "Ctrl+Ctrl", "Ctrl+Space", "Ctrl+F25"):
            with self.assertRaises(ValueError):
                shortcut_keys(text)


if __name__ == "__main__":
    unittest.main()
