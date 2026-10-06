"""回放真实截图的连续查询压力测试；可同时只读观察游戏后台采集。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QCoreApplication, QEvent, Slot
from PySide6.QtWidgets import QApplication

from atomic_json import read_json_retry, write_json_atomic
from cache_search import read_image
from cache_watch import DEFAULT_CARD_CACHE
from global_cards import LargeCardFinder
from live_capture import client_bbox, game_window, set_dpi_awareness
from overlay_app import Events, ReadingCard, Recognizer, configure_font
from query_diagnostics import QueryFailureRecorder
from query_regression import normal_name
from window_capture import WindowCaptureSession, crop_client_frame


ROOT = Path(__file__).resolve().parent


def private_megabytes():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("peak_working", "working", "peak_paged", "paged",
                "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
    result = Counters()
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(result), ctypes.sizeof(result)):
        raise OSError("无法读取进程内存统计")
    return round(result.private / 1024**2, 1)


class Observer(QObject):
    def __init__(self, popup):
        super().__init__()
        self.popup, self.latest, self.answer = popup, 0, None

    @Slot(object)
    def receive(self, payload):
        self.popup.show_result(payload)
        if payload["id"] == self.latest:
            self.answer = payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--burst-size", type=int, default=30)
    parser.add_argument("--live-capture", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/stability-check.json")
    args = parser.parse_args()
    if args.duration < 1 or not 1 <= args.burst_size <= 1000:
        parser.error("测试时长至少为 1 秒，突发查询数须在 1 至 1000 之间")
    set_dpi_awareness()
    app = QApplication([])
    configure_font(app)
    events = Events()
    worker = Recognizer(events, SimpleNamespace(cache_root=DEFAULT_CARD_CACHE, diagnose_input=False))
    # 只读回放不改动正在运行程序的缓存，不发出鼠标输入或网络请求。
    worker.prepare_metadata = lambda: None
    worker.connect_game = lambda: None
    worker.background_jobs = lambda *unused: None
    worker.refresh_cache = lambda: None
    worker.diagnostics = QueryFailureRecorder(ROOT / "output/stability-failures")
    popup = ReadingCard(worker.translate)
    observer = Observer(popup)
    events.answer.connect(observer.receive)
    worker.start()
    ready_deadline = time.monotonic() + 60
    while not worker.ready and worker.is_alive() and time.monotonic() < ready_deadline:
        app.processEvents()
        time.sleep(.01)
    if not worker.ready:
        raise RuntimeError("识别线程未就绪")
    cards = read_json_retry(ROOT / "output/card-data.json")["cards"]
    cases, images = [], {}
    for scene in read_json_retry(ROOT / "tests/scenes/query-cases.json")["scenes"]:
        path = ROOT / "output/query-scenes" / scene["image"]
        images[scene["id"]] = read_image(path)
        for point in scene["points"]:
            if point.get("rules_card_id") and point["rules_card_id"] not in cards:
                raise ValueError(f"本地卡牌资料缺少期望规则：{point['rules_card_id']}")
        cases.extend((scene["id"], point) for point in scene["points"])
    session = None
    live = {"frames":0, "changed_frames":0, "dimension_rejections":0,
            "sizes":[], "positions":[], "closed":False}
    if args.live_capture:
        hwnd, _ = game_window(None)
        session = WindowCaptureSession(hwnd, .1)
    sequence = 0
    last_signature = None
    def pump():
        nonlocal sequence, last_signature
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if session:
            live["closed"] = session.closed
            latest = session.newest(sequence)
            if latest:
                sequence = latest[0]
                box = client_bbox(session.hwnd)
                try:
                    frame = crop_client_frame(latest[1], session.hwnd, bbox=box, strict=True)
                    live["frames"] += 1
                    signature = cv2.resize(frame,(64,36),interpolation=cv2.INTER_AREA)
                    if last_signature is not None and np.any(signature != last_signature):
                        live["changed_frames"] += 1
                    last_signature = signature
                    size = [frame.shape[1], frame.shape[0]]
                    if size not in live["sizes"]:live["sizes"].append(size)
                    if list(box) not in live["positions"]:live["positions"].append(list(box))
                except RuntimeError:
                    live["dimension_rejections"] += 1
    start = time.monotonic()
    rows, memory = [], []
    request_id, burst, reloads = 0, 0, 0
    last_report = last_reload = start
    maximum_queue = 0
    try:
        while time.monotonic() - start < args.duration:
            for number in range(args.burst_size):
                scene, case = cases[(burst + number - args.burst_size + 1) % len(cases)]
                image = images[scene]
                # 每次使用新像素缓冲，检验被覆盖的请求能否释放截图。
                screenshot = image.copy()
                x, y = case["point"]
                left, top = (burst % 5 - 2)*160, (burst % 4)*100
                bbox = (left, top, left+image.shape[1], top+image.shape[0])
                request_id += 1
                observer.latest, observer.answer = request_id, None
                popup.loading(request_id, True)
                worker.query(request_id, {"point":(left+x,top+y), "bbox":bbox,
                    "screenshot":screenshot, "started":time.perf_counter()})
                maximum_queue = max(maximum_queue, worker.jobs.qsize())
            deadline = time.monotonic()+15
            while observer.answer is None and worker.is_alive() and time.monotonic()<deadline:
                pump()
                time.sleep(.01)
            payload = observer.answer or {"error":"query timeout"}
            actual = payload.get("card")
            expected = cards.get(case.get("rules_card_id"))
            passed = not payload.get("error")
            if case["name_en"] is None:
                passed = passed and actual is None
            else:
                passed = passed and bool(actual and normal_name(actual["name_en"]) == normal_name(case["name_en"]))
                if actual and expected:
                    passed = passed and LargeCardFinder.rules_signature(actual) == LargeCardFinder.rules_signature(expected)
            if actual and popup.card_id != actual["card_id"]:passed = False
            rows.append({"request_id":request_id, "scene":scene, "case":case["id"], "passed":passed,
                "expected":case["name_en"], "actual":actual["name_en"] if actual else None,
                "rules_match":LargeCardFinder.rules_signature(actual) == LargeCardFinder.rules_signature(expected)
                    if actual and expected else None,
                "error":payload.get("error"), "ms":payload.get("elapsed")})
            if not passed:print("失败："+json.dumps(rows[-1],ensure_ascii=False), flush=True)
            burst += 1
            now = time.monotonic()
            if now-last_reload >= 15:
                worker.reload_resources({"added":0,"updated":0,"visual_rebuilt":0,"card_data_refreshed":True})
                reloads += 1
                last_reload = now
            if now-last_report >= 10:
                memory.append({"seconds":round(now-start,1), "private_mb":private_megabytes()})
                print(f"回放 {len(rows)} 轮，提交 {request_id} 次查询，失败 {sum(not r['passed'] for r in rows)}，内存 {memory[-1]['private_mb']} MB",flush=True)
                last_report = now
            while time.monotonic()<now+.15:
                pump()
                time.sleep(.01)
    finally:
        worker.stop_event.set()
        worker.join(15)
        if session:
            session.stop()
            session = None
        popup.close()
        pump()
    summary = {"seconds":round(time.monotonic()-start,1), "submitted":request_id, "checked_latest":len(rows),
        "failed":sum(not row["passed"] for row in rows), "max_pending_frames":maximum_queue,
        "metadata_refreshes":reloads, "worker_stopped":not worker.is_alive(), "live_capture":live}
    write_json_atomic(args.output, {"summary":summary, "memory":memory, "rows":rows,
        "boundaries":"Offline screenshot replay with actual recognition and Qt rendering; live capture is read-only. No physical hotkey or automatic game resolution changes."})
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    raise SystemExit(0 if summary["failed"]==0 and summary["worker_stopped"] else 1)


if __name__ == "__main__":
    main()
