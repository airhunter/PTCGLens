import unittest

from overlay_input import MouseTrigger


class OutsideClickTest(unittest.TestCase):
    def setUp(self):
        self.closed = []
        self.hook = MouseTrigger(lambda *args: None, lambda *args: None,
                                 dismiss_callback=lambda: self.closed.append(True))
        self.hook.popup_hwnd = 100
        self.hook.outside_dismiss_enabled = True

    def test_left_click_on_game_or_desktop_closes_without_swallowing(self):
        for window in (200, None):
            self.assertIsNone(self.hook.dismiss_outside_click(0x0201,window))
        self.assertEqual(len(self.closed),2)

    def test_popup_buttons_and_other_mouse_events_do_not_close(self):
        self.hook.dismiss_outside_click(0x0201,100)
        self.hook.dismiss_outside_click(0x0202,200)
        self.hook.dismiss_outside_click(0x0204,200)
        self.assertEqual(self.closed,[])

    def test_hidden_popup_does_not_handle_clicks(self):
        self.hook.outside_dismiss_enabled = False
        self.hook.dismiss_outside_click(0x0201,200)
        self.assertEqual(self.closed,[])


if __name__ == "__main__":
    unittest.main()
