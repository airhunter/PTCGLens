"""保留既有 Windows 原生实现。"""
from .window import (visible_windows, game_window, client_bbox, set_dpi_awareness,
                     is_foreground, window_state, pointer_position, pointer_over_window,
                     shortcut_pressed, capture_screen, check_permissions, popup_bounds, validate_shortcut, escape_pressed,
                     watch_capture, check_hotkeys)
from .capture import WindowCaptureSession, crop_client_frame
from .geometry import placement_geometry, configure_popup
from .input import MouseTrigger
