import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from platform_backend import get_backend
from platform_backend.contracts import FrameImage
from platform_backend.paths import cache_paths
from overlay_model import parse_shortcut, shortcut_keys
from windows_backend_fixture import load_windows_module


class PlatformRoutingTest(unittest.TestCase):
    def tearDown(self):
        get_backend.cache_clear()

    def test_factory_loads_only_selected_platform(self):
        for system, name in (("win32", "windows"), ("darwin", "macos")):
            get_backend.cache_clear()
            with patch("platform_backend.sys.platform", system), patch("platform_backend.importlib.import_module") as load:
                get_backend()
                get_backend()
            load.assert_called_once_with(f"platform_backend.{name}")

    def test_unsupported_os_has_clear_error(self):
        with patch("platform_backend.sys.platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows 和 macOS"):
                get_backend()

    def test_native_backend_exports_real_entry_points(self):
        backend = get_backend()
        for name in ("visible_windows", "game_window", "client_bbox", "is_foreground", "pointer_position",
                     "pointer_over_window", "shortcut_pressed", "validate_shortcut", "escape_pressed",
                     "capture_screen", "WindowCaptureSession", "crop_client_frame", "placement_geometry",
                     "popup_bounds", "configure_popup", "MouseTrigger", "check_permissions"):
            self.assertTrue(callable(getattr(backend, name)), name)

    def test_windows_legacy_hotkey_check_registers_and_releases_both_keys(self):
        native = load_windows_module("window")
        with patch.object(native.user32, "RegisterHotKey", return_value=True) as register, \
             patch.object(native.user32, "UnregisterHotKey") as unregister:
            native.check_hotkeys()
        self.assertEqual([call.args[1] for call in register.call_args_list], [1, 2])
        self.assertEqual([call.args[1] for call in unregister.call_args_list], [1, 2])

    def test_windows_cache_paths_remain_unchanged(self):
        cards, database = cache_paths("win32", "/test")
        self.assertEqual(cards, Path("/test/AppData/LocalLow/Unity/pokemon_Pokemon TCG Live"))
        self.assertEqual(database, Path("/test/AppData/LocalLow/pokemon/Pokemon TCG Live/config-cache"))

    def test_mac_selects_existing_known_cache_without_windows_paths(self):
        with TemporaryDirectory() as home:
            cards = Path(home) / "Library/Caches/Unity/pokemon_Pokemon TCG Live"
            database = Path(home) / "Library/Application Support/unity.pokemon.Pokemon TCG Live/config-cache"
            cards.mkdir(parents=True)
            database.mkdir(parents=True)
            self.assertEqual(cache_paths("darwin", home), (cards, database))

    def test_shortcut_aliases_and_existing_windows_f24(self):
        self.assertEqual(parse_shortcut("Control+Option+L"), ("ctrl", "alt", "l"))
        self.assertEqual(shortcut_keys("Ctrl+F24"), (0x11, 0x87))
        with self.assertRaises(ValueError):
            parse_shortcut("Alt+Option")

    def test_mac_prefers_verified_bundle_cache_paths_over_legacy_candidates(self):
        with TemporaryDirectory() as home:
            cards = Path(home) / "Library/Caches/com.pokemon.pokemontcgl"
            database = Path(home) / "Library/Application Support/com.pokemon.pokemontcgl/config-cache"
            legacy = Path(home) / "Library/Caches/unity.pokemon.Pokemon TCG Live"
            for folder in (cards, database, legacy):
                folder.mkdir(parents=True)
            self.assertEqual(cache_paths("darwin", home), (cards, database))

    def test_windows_key_polling_requires_exact_modifiers(self):
        native = load_windows_module("window")
        with patch.object(native.user32, "GetAsyncKeyState", side_effect=lambda key: 0x8000 if key in (0x11, 0x12) else 0):
            self.assertTrue(native.shortcut_pressed("Ctrl+Alt"))
        with patch.object(native.user32, "GetAsyncKeyState", return_value=0x8000):
            self.assertFalse(native.shortcut_pressed("Ctrl+Alt"))


@unittest.skipUnless(sys.platform == "darwin", "macOS 原生绑定测试")
class MacCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = importlib.import_module("platform_backend.macos")

    def test_native_selectors_exist(self):
        b = self.backend
        self.assertTrue(hasattr(b.SC.SCScreenshotManager, "captureImageWithFilter_configuration_completionHandler_"))
        config = b.SC.SCStreamConfiguration.alloc().init()
        config.setWidth_(200)
        config.setHeight_(100)
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        self.assertEqual(config.width(), 200)

    def test_native_cgimage_decodes_to_bgr_without_color_changes(self):
        import cv2
        pixels = np.zeros((20,30,3), np.uint8)
        pixels[:,:,0] = 123
        ok, data = cv2.imencode(".png", pixels)
        image = self.backend.AppKit.NSBitmapImageRep.imageRepWithData_(bytes(data)).CGImage()
        self.assertTrue(np.array_equal(self.backend._image_array(image), pixels))

    def test_retina_crop_uses_frame_snapshot_and_preserves_pixels(self):
        b = self.backend
        full, client = (100,100,500,400), (100,128,500,400)
        pixels = np.zeros((600,800,3), np.uint8)
        pixels[56:,:,0] = 42
        frame = FrameImage(pixels, full, client, 1)
        result = b.crop_client_frame(frame, 10, bbox=client, strict=True)
        self.assertEqual(result.shape, (544,800,3))
        self.assertTrue(np.all(result[:,:,0] == 42))
        self.assertEqual(frame.desktop_bounds, full)

    def test_same_aspect_resize_still_rejects_old_frame(self):
        frame = FrameImage(np.zeros((600,800,3), np.uint8), (0,0,400,300), (0,0,400,300), 1)
        with self.assertRaisesRegex(RuntimeError, "尺寸已改变"):
            self.backend.crop_client_frame(frame, 10, bbox=(0,0,800,600), strict=True)

    def test_closed_session_does_not_return_old_frame(self):
        import threading
        b = self.backend
        session = object.__new__(b.WindowCaptureSession)
        session._closed, session._lock = threading.Event(), threading.Lock()
        session._sequence, session._frame = 1, np.zeros((2,2,3),np.uint8)
        self.assertIsNotNone(session.newest(0))
        session._closed.set()
        self.assertIsNone(session.newest(0))

    def test_mac_shortcuts_reject_unavailable_function_keys(self):
        self.backend.validate_shortcut("Ctrl+Option+L")
        with self.assertRaises(ValueError):
            self.backend.validate_shortcut("Ctrl+F24")

    def test_transparent_overlay_rectangle_does_not_block_actual_game_hit(self):
        b = self.backend
        windows = [{b.CG.kCGWindowNumber: wid, b.CG.kCGWindowOwnerPID: pid,
                    b.CG.kCGWindowLayer: layer, b.CG.kCGWindowAlpha: 1,
                    b.CG.kCGWindowBounds: {"X": 0, "Y": 0, "Width": 800, "Height": 600}}
                   for wid, pid, layer in ((70, 740, 27), (6185, 38179, 0))]
        with patch.object(b, "_windows", return_value=windows), \
             patch.object(b.AX, "AXIsProcessTrusted", return_value=True), \
             patch.object(b.AX, "AXUIElementCopyElementAtPosition", return_value=(0, object())), \
             patch.object(b.AX, "AXUIElementGetPid", return_value=(0, 38179)):
            self.assertTrue(b.pointer_over_window(6185, (462, 423)))

    def test_real_overlay_hit_and_failed_accessibility_remain_blocked(self):
        b = self.backend
        windows = [{b.CG.kCGWindowNumber: wid, b.CG.kCGWindowOwnerPID: pid,
                    b.CG.kCGWindowBounds: {"X": 0, "Y": 0, "Width": 800, "Height": 600}}
                   for wid, pid in ((70, 740), (6185, 38179))]
        with patch.object(b, "_windows", return_value=windows), \
             patch.object(b.AX, "AXIsProcessTrusted", return_value=True), \
             patch.object(b.AX, "AXUIElementCopyElementAtPosition", return_value=(0, object())) as hit, \
             patch.object(b.AX, "AXUIElementGetPid", return_value=(0, 740)):
            self.assertFalse(b.pointer_over_window(6185, (462, 423)))
            hit.return_value = (-25204, None)
            self.assertFalse(b.pointer_over_window(6185, (462, 423)))
