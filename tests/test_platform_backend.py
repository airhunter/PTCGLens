import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

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
        for name in ("visible_windows", "game_window", "window_state", "client_bbox", "is_foreground", "pointer_position",
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

    def test_fullscreen_titlebar_and_animation_windows_do_not_replace_game(self):
        b = self.backend
        windows = [{b.CG.kCGWindowNumber: wid, b.CG.kCGWindowName: title,
                    b.CG.kCGWindowOwnerName: "Pokemon TCG Live", b.CG.kCGWindowLayer: 0,
                    b.CG.kCGWindowBounds: {"X": 0, "Y": 0, "Width": 1470, "Height": height}}
                   for wid, title, height in ((10529, "", 44), (10528, "", 956),
                                              (6185, "Pokemon TCG Live", 923))]
        with patch.object(b, "_windows", return_value=windows), \
             patch.object(b, "is_foreground", return_value=True):
            self.assertEqual(b.game_window(), (6185, "Pokemon TCG Live"))
        with patch.object(b, "_windows", return_value=windows[:1]):
            with self.assertRaisesRegex(RuntimeError, "未找到"):
                b.game_window()

    def test_native_selectors_exist(self):
        b = self.backend
        self.assertTrue(hasattr(b.SC.SCScreenshotManager, "captureImageWithFilter_configuration_completionHandler_"))
        config = b.SC.SCStreamConfiguration.alloc().init()
        config.setWidth_(200)
        config.setHeight_(100)
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        self.assertEqual(config.width(), 200)

    def test_popup_can_join_other_apps_fullscreen_without_conflicting_roles(self):
        b = self.backend
        window = Mock()
        window.styleMask.return_value = 14
        application = Mock()
        application.activationPolicy.return_value = b.AppKit.NSApplicationActivationPolicyRegular
        window.collectionBehavior.return_value = (b.AppKit.NSWindowCollectionBehaviorPrimary |
            b.AppKit.NSWindowCollectionBehaviorAuxiliary |
            b.AppKit.NSWindowCollectionBehaviorMoveToActiveSpace |
            b.AppKit.NSWindowCollectionBehaviorFullScreenPrimary |
            b.AppKit.NSWindowCollectionBehaviorFullScreenNone)
        view = Mock()
        view.window.return_value = window
        with patch("PySide6.QtGui.QGuiApplication.platformName", return_value="cocoa"), \
             patch.object(b.AppKit, "NSApplication", Mock(sharedApplication=Mock(return_value=application))), \
             patch.object(b.objc, "objc_object", return_value=view):
            b.configure_popup(123)
        application.setActivationPolicy_.assert_called_once_with(b.AppKit.NSApplicationActivationPolicyAccessory)
        window.setStyleMask_.assert_called_once_with(14 | b.AppKit.NSWindowStyleMaskNonactivatingPanel)
        window.setCollectionBehavior_.assert_called_once_with(
            b.AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            b.AppKit.NSWindowCollectionBehaviorCanJoinAllApplications |
            b.AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        window.setHidesOnDeactivate_.assert_called_once_with(False)
        window.makeKeyAndOrderFront_.assert_not_called()
        application.activateIgnoringOtherApps_.assert_not_called()
        application.activationPolicy.return_value = b.AppKit.NSApplicationActivationPolicyAccessory
        with patch("PySide6.QtGui.QGuiApplication.platformName", return_value="cocoa"), \
             patch.object(b.AppKit, "NSApplication", Mock(sharedApplication=Mock(return_value=application))), \
             patch.object(b.objc, "objc_object", return_value=view):
            b.configure_popup(123)
        application.setActivationPolicy_.assert_called_once()

    def test_native_cgimage_decodes_to_bgr_without_color_changes(self):
        import cv2
        pixels = np.zeros((20,30,3), np.uint8)
        pixels[:,:,0] = 123
        ok, data = cv2.imencode(".png", pixels)
        image = self.backend.AppKit.NSBitmapImageRep.imageRepWithData_(bytes(data)).CGImage()
        self.assertTrue(np.array_equal(self.backend._image_array(image), pixels))

    def test_cgimage_conversion_preserves_odd_stride_orientation_and_channels(self):
        import cv2
        b = self.backend
        pixels = np.random.default_rng(42).integers(0, 256, (21, 31, 3), dtype=np.uint8)
        _, data = cv2.imencode(".png", pixels)
        image = b.AppKit.NSBitmapImageRep.imageRepWithData_(bytes(data)).CGImage()
        self.assertTrue(np.array_equal(b._image_array(image), pixels))
        with patch.object(b.CG, "CGBitmapContextCreate", return_value=None):
            self.assertTrue(np.array_equal(b._image_array(image), pixels))

    def test_transparent_window_corners_keep_png_conversion_colors(self):
        import cv2
        b = self.backend
        pixels = np.random.default_rng(7).integers(0, 256, (31, 21, 4), dtype=np.uint8)
        _, data = cv2.imencode(".png", pixels)
        image = b.AppKit.NSBitmapImageRep.imageRepWithData_(bytes(data)).CGImage()
        self.assertTrue(np.array_equal(b._image_array(image), b._image_array_png(image)))
        pixels[:, :, :3] = (pixels[:, :, :3].astype(np.uint16)*pixels[:, :, 3, None]//255).astype(np.uint8)
        raw = bytearray(pixels.tobytes())
        context = b.CG.CGBitmapContextCreate(raw, 21, 31, 8, 21*4, b.CG.CGColorSpaceCreateDeviceRGB(),
            b.CG.kCGImageAlphaPremultipliedLast | b.CG.kCGBitmapByteOrder32Big)
        try:
            image = b.CG.CGBitmapContextCreateImage(context)
            self.assertTrue(np.array_equal(b._image_array(image), b._image_array_png(image)))
        finally:
            del context

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
        session._active = threading.Event()
        session._active.set()
        session._sequence, session._frame = 1, np.zeros((2,2,3),np.uint8)
        self.assertIsNotNone(session.newest(0))
        session._closed.set()
        self.assertIsNone(session.newest(0))

    def test_paused_mac_capture_discards_old_frame_and_requires_fresh_resume(self):
        import threading
        b=self.backend
        session=object.__new__(b.WindowCaptureSession)
        session._closed,session._lock=threading.Event(),threading.Lock()
        session._active,session._wake=threading.Event(),threading.Event()
        session._active.set()
        session._generation=0
        session._sequence,session._frame=1,np.ones((2,2,3),np.uint8)
        session.set_active(False)
        self.assertIsNone(session.newest(0))
        self.assertIsNone(session._frame)
        paused_generation=session._generation
        session.set_active(True)
        self.assertGreater(session._generation,paused_generation)
        self.assertIsNone(session.newest(0))
        session._sequence,session._frame=2,np.full((2,2,3),2,np.uint8)
        self.assertEqual(session.newest(0)[0],2)

    def test_mac_capture_drops_inflight_frame_across_pause_and_resume(self):
        import threading
        import time
        b=self.backend
        session=object.__new__(b.WindowCaptureSession)
        session.hwnd,session.error=1,None
        session._closed,session._lock=threading.Event(),threading.Lock()
        session._active,session._wake=threading.Event(),threading.Event()
        session._active.set()
        session._generation,session._sequence,session._frame=0,0,None
        first_started,second_started=threading.Event(),threading.Event()
        first_release,second_release=threading.Event(),threading.Event()
        calls=[]
        def capture(*args):
            calls.append(1)
            if len(calls)==1:
                first_started.set()
                first_release.wait(2)
                return np.ones((20,20,3),np.uint8)
            second_started.set()
            second_release.wait(2)
            return np.full((20,20,3),2,np.uint8)
        session._thread=threading.Thread(target=session._run,args=(.1,),daemon=True)
        with patch.object(b,"_filter",return_value=object()), \
             patch.object(b,"_info",return_value={}), patch.object(b,"_rect",return_value=(0,0,20,20)), \
             patch.object(b,"client_bbox",return_value=(0,0,20,20)), patch.object(b,"_capture",side_effect=capture):
            session._thread.start()
            try:
                self.assertTrue(first_started.wait(2))
                session.set_active(False)
                session.set_active(True)
                first_release.set()
                self.assertTrue(second_started.wait(2))
                self.assertIsNone(session.newest(0))  # 第一帧属于旧会话，不能复用。
                second_release.set()
                deadline=time.monotonic()+2
                while session.newest(0) is None and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertEqual(int(session.newest(0)[1][0,0,0]),2)
                session.set_active(False)
                time.sleep(.03)  # 允许已在途的采集退出；不能继续启动新采集。
                count=len(calls)
                time.sleep(.15)
                self.assertEqual(len(calls),count)
            finally:
                first_release.set()
                second_release.set()
                session.stop()
        self.assertFalse(session._thread.is_alive())

    def test_mac_paused_start_does_not_initialize_capture_until_resumed(self):
        import threading
        import time
        b=self.backend
        captured=threading.Event()
        def capture(*unused):
            captured.set()
            return np.zeros((20,20,3),np.uint8)
        with patch.object(b,'check_permissions',return_value=[]), \
             patch.object(b,'_filter',return_value=object()) as source, \
             patch.object(b,'_info',return_value={}), patch.object(b,'_rect',return_value=(0,0,20,20)), \
             patch.object(b,'client_bbox',return_value=(0,0,20,20)),patch.object(b,'_capture',side_effect=capture):
            session=b.WindowCaptureSession(1,.1,active=False)
            try:
                time.sleep(.15)
                source.assert_not_called()
                self.assertIsNone(session.newest(0))
                session.set_active(True)
                self.assertTrue(captured.wait(2))
                source.assert_called_once_with(1)
            finally:
                session.stop()
        self.assertFalse(session._thread.is_alive())

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
