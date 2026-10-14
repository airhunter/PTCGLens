import unittest

from windows_backend_fixture import load_windows_module
MouseTrigger = load_windows_module("input").MouseTrigger


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


class NativeHookPolicyTest(unittest.TestCase):
    def test_hover_escape_hook_passes_mouse_clicks_and_filters_only_escape(self):
        import ctypes
        from unittest.mock import patch
        from types import SimpleNamespace
        native = load_windows_module("input")
        queries, dismissed, callbacks = [], [], []
        hook = native.MouseTrigger(lambda *p: queries.append(p), lambda *a: None,
                                   dismiss_callback=lambda: dismissed.append(True))
        hook.hwnd, hook.bbox = 123, (0,0,100,100)
        hook.query_enabled = False
        hook.dismiss_enabled = True
        def register(kind, callback, *args):
            callbacks.append(callback)
            return kind
        with patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(GetCurrentThreadId=lambda: 1), create=True), \
             patch.object(native, "set_dpi_awareness"), \
             patch.object(native.user32, "SetWindowsHookExW", side_effect=register), \
             patch.object(native.user32, "GetMessageW", return_value=0), \
             patch.object(native.user32, "GetForegroundWindow", return_value=123), \
             patch.object(native.user32, "CallNextHookEx", return_value=0):
            hook.run()
            mouse = native.MouseData()
            mouse.pt.x, mouse.pt.y = 50, 50
            self.assertEqual(callbacks[0](0,0x0204,ctypes.addressof(mouse)),0)
            self.assertEqual(queries,[])
            key = native.KeyData()
            key.vkCode = 0x1B
            self.assertEqual(callbacks[1](0,0x0100,ctypes.addressof(key)),1)
            self.assertEqual(callbacks[1](0,0x0101,ctypes.addressof(key)),1)
            key.vkCode = ord("A")
            self.assertEqual(callbacks[1](0,0x0100,ctypes.addressof(key)),0)
            self.assertEqual(dismissed,[True])
