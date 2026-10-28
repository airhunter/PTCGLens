"""平台无关的窗口采集入口。"""
from platform_backend import get_backend


def WindowCaptureSession(hwnd, interval, **options):
    return get_backend().WindowCaptureSession(hwnd, interval, **options)


def crop_client_frame(frame, hwnd, *, bbox=None, strict=False):
    return get_backend().crop_client_frame(frame, hwnd, bbox=bbox, strict=strict)
