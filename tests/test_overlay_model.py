import unittest

from overlay_model import (frame_point, intersect_rect, map_rect, popup_position,
                           relative_rect, restore_rect, shortcut_keys)


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

    def test_dpi_mapping_preserves_window_offset_on_secondary_monitor(self):
        physical = (-1920, 0, 0, 1080)
        logical = (-1920, 0, -384, 864)
        self.assertEqual(map_rect((-1700,125,-450,1000), physical, logical), (-1744,100,-744,800))

    def test_popup_stays_inside_offset_game_window_and_avoids_card(self):
        bounds = intersect_rect((400,200,1500,1000), (0,0,1920,1080), margin=8)
        x,y = popup_position((1350,850,1490,980), (350,700), bounds)
        self.assertGreaterEqual(x,bounds[0])
        self.assertGreaterEqual(y,bounds[1])
        self.assertLessEqual(x+350,bounds[2])
        self.assertLessEqual(y+700,bounds[3])
        self.assertLessEqual(x+350,1350)

    def test_game_move_and_resize_updates_card_anchor(self):
        relative = relative_rect((900,500,1080,750), (300,100,1500,1000))
        self.assertEqual(restore_rect(relative,(600,200,1800,1100)), (1200,600,1380,850))
        self.assertEqual(restore_rect(relative,(600,200,1400,800)), (1000,467,1120,633))


if __name__ == "__main__":
    unittest.main()
