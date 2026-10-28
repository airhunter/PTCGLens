"""在游戏中开启悬停查询，显示中英双语阅读浮卡。"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path
import cv2

from PySide6.QtCore import QLockFile, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QBoxLayout, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                              QSystemTrayIcon, QVBoxLayout, QWidget, QScrollArea, QFrame, QDoubleSpinBox, QStyle,
                              QMessageBox)

from battle_live import is_battle_screen, load_resources
from battle_multi import adaptive_layout, recognise_slot, scaled_box
from cache_watch import DEFAULT_CARD_CACHE, DEFAULT_DATABASE_CACHE, metadata_is_stale, refresh_once
from card_data import build_card_data
from card_translation import TranslationCache, readable_text
from card_images import CardImageSources
from grid_cards import GridCardFinder
from live_capture import client_bbox, game_window, set_dpi_awareness, check_permissions
from overlay_input import MouseTrigger, HoverTrigger
from platform_backend.paths import chinese_font_path
from platform_backend import get_backend
from qt_runtime import prepare_qt_plugins
from overlay_geometry import placement_geometry
from overlay_model import frame_point, popup_position, relative_rect, restore_rect
from latest_jobs import LatestJobQueue
from query_diagnostics import QueryFailureRecorder
from atomic_json import read_json_retry, write_json_atomic
from window_capture import WindowCaptureSession, crop_client_frame
from query_control import QUERY_TIMEOUT, TIMEOUT_MESSAGE, QueryCancelled, QueryTimedOut, check_query, query_scope
from window_monitor import GameWindowMonitor
from console_log import log


ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "output/overlay-settings.json"


def configure_font(app):
    font_file = chinese_font_path()
    if font_file:
        QFontDatabase.addApplicationFont(str(font_file))
    app.setFont(QFont("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI", 10))


class Events(QObject):
    clicked = Signal(object)
    dismissed = Signal()
    ready = Signal()
    status = Signal(str)
    answer = Signal(object)
    art = Signal(int, object)
    translated = Signal(int, object, str)
    cache_checked = Signal(object)
    capture_state = Signal(str)


class Recognizer(threading.Thread):
    def __init__(self, events, args):
        super().__init__(daemon=True, name="PTCGLens recognition")
        self.events, self.args = events, args
        self.jobs = LatestJobQueue()
        self.stop_event = threading.Event()
        self.refresh_requested = threading.Event()
        self.index_revision = 0
        self.last_capture_state = None
        self.session = None
        self.capture_active = True
        self.ready = False
        self.latest_request = 0
        self.translator = TranslationCache(ROOT / "output/translation-cache.json")
        self.art_jobs = LatestJobQueue()
        self.translation_jobs = queue.Queue()
        self.last_art = None
        self.query_card = None
        self.diagnostics = QueryFailureRecorder(ROOT / "output/query-failures")
        self.image_sources = CardImageSources(ROOT / "output/card-sources",args.cache_root,
                                              ROOT / "output/card-thumbnails",ROOT / "output/card-large")

    def translate(self, request_id, number, source):
        if request_id == self.latest_request:
            cached = self.translator.cached(source)
            if cached:
                self.events.translated.emit(request_id, number, cached)
            else:
                self.translation_jobs.put((request_id, number, source))

    def cancel(self, request_id):
        self.latest_request = request_id
        self.query_card = None
        self.jobs.cancel(request_id)
        self.art_jobs.cancel(request_id)
        while True:
            try:
                self.translation_jobs.get_nowait()
            except queue.Empty:
                break

    def background_jobs(self, jobs, kind):
        while not self.stop_event.is_set():
            try:
                job = jobs.get(timeout=.2)
            except queue.Empty:
                continue
            request_id = job[0]
            if request_id != self.latest_request:
                continue
            if kind == "translate":
                _, number, source = job
                try:
                    text, _ = self.translator.translate(source,
                        cancelled=lambda: request_id != self.latest_request or self.stop_event.is_set())
                    if request_id == self.latest_request and not self.stop_event.is_set():
                        self.events.translated.emit(request_id, number, text)
                except Exception as exc:
                    if request_id == self.latest_request and not self.stop_event.is_set():
                        self.events.translated.emit(request_id, number, f"翻译暂不可用：{exc}")
            else:
                _, card_id = job
                try:
                    snapshot = self.query_card
                    card = (snapshot[1] if snapshot and snapshot[0] == request_id
                            and snapshot[1]["card_id"] == card_id else self.resources[1][card_id])
                    art = self.image_sources.resolve(card,lambda:self.latest_request != request_id or self.stop_event.is_set())
                    if (request_id == self.latest_request and not self.stop_event.is_set()
                            and self.last_art != (request_id,art)):
                        self.last_art = (request_id,art)
                        self.events.art.emit(request_id, art)
                        log(f"卡图已更新：{card_id}，{art['language']}，{art['provider']}")
                except Exception as exc:
                    log(f"卡图加载暂不可用：{card_id}，{exc}")

    def refresh_cache(self):
        last_refresh = float("-inf")
        while not self.stop_event.is_set():
            self.refresh_requested.wait(5)
            self.refresh_requested.clear()
            if self.stop_event.is_set():
                break
            if self.stop_event.wait(max(0, 1-(time.monotonic()-last_refresh))):
                break
            last_refresh = time.monotonic()
            try:
                refreshed = refresh_once(self.args.cache_root, ROOT / "output/index",
                    ROOT / "output/card-thumbnails", self.args.game_cache,
                    self.args.translation_root, ROOT / "output/card-data.json")
                if self.stop_event.is_set():
                    break
                if any(refreshed[key] for key in ("added", "updated", "visual_rebuilt", "card_data_refreshed")):
                    self.reload_resources(refreshed)
                    self.events.status.emit("本地卡牌索引已更新")
                self.events.cache_checked.emit(refreshed)
                if refreshed["pending"]:
                    self.refresh_requested.set()
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.events.status.emit(f"缓存更新暂未完成：{exc}")
                self.events.cache_checked.emit({"error": str(exc)})

    def reload_resources(self, refreshed):
        previous = self.resources
        if any(refreshed[key] for key in ("added", "updated", "visual_rebuilt")):
            replacement = load_resources(ROOT / "output/index", ROOT / "output/card-thumbnails",
                                         ROOT / "output/card-data.json", previous=previous)
        else:
            cards = read_json_retry(ROOT / "output/card-data.json")["cards"]
            replacement = (previous[0], cards, previous[2], previous[3])
        self.resources = replacement
        self.index_revision += 1

    def query(self, request_id, request):
        self.latest_request = request_id
        self.jobs.put((request_id, request))

    def prepare_metadata(self):
        path = ROOT / "output/card-data.json"
        manifest = ROOT / "output/index/manifest.json"
        last_status = None
        while (not manifest.is_file() or not path.is_file() or not read_json_retry(manifest).get("cards")
               or not read_json_retry(path).get("cards")):
            if self.stop_event.is_set():
                return
            try:
                if last_status is None:
                    self.events.status.emit("首次准备：正在检查游戏缓存并建立卡牌索引……")
                result = refresh_once(self.args.cache_root, ROOT / "output/index",
                    ROOT / "output/card-thumbnails", self.args.game_cache, self.args.translation_root, path)
                if result["cards"] and path.is_file() and read_json_retry(path).get("cards"):
                    break
                message = ("已经找到卡图，正在等待英文卡牌资料。请让游戏完成数据下载，程序会自动继续。"
                           if result["cards"] else
                           "还没有可识别的卡图。请启动游戏并打开卡牌页面，下载完成后会自动继续。")
            except Exception as error:
                message = f"正在等待游戏缓存。请启动游戏并打开卡牌页面；也可在运行状态中核对缓存目录。详情：{error}"
            if message != last_status:
                self.events.status.emit(message)
                last_status = message
            for _ in range(50):
                if self.stop_event.wait(.1):
                    return
                if self.refresh_requested.is_set():
                    self.refresh_requested.clear()
                    break
        if metadata_is_stale(path, self.args.game_cache, self.args.translation_root):
            self.events.status.emit("正在更新本地中文资料……")
            try:
                build_card_data(ROOT / "output/index", self.args.game_cache, self.args.translation_root, path)
            except Exception as exc:
                if not path.is_file():
                    raise
                self.events.status.emit(f"资料更新暂未完成，继续使用上次完整资料：{exc}")

    def run(self):
        set_dpi_awareness()
        try:
            self.events.status.emit("正在加载本地卡牌索引……")
            self.prepare_metadata()
            if self.stop_event.is_set():
                return
            self.resources = load_resources(ROOT / "output/index", ROOT / "output/card-thumbnails",
                                            ROOT / "output/card-data.json")
            self.layout = json.loads((ROOT / "battle_layout.sample.json").read_text(encoding="utf-8"))
            self.ready = True
            self.events.status.emit("已就绪，等待游戏内快捷查询")
            self.events.ready.emit()
            for jobs, kind in ((self.art_jobs, "art"), (self.translation_jobs, "translate")):
                threading.Thread(target=self.background_jobs, args=(jobs, kind), daemon=True).start()
            threading.Thread(target=self.refresh_cache, daemon=True).start()
            while not self.stop_event.is_set():
                try:
                    self.connect_game()
                except Exception as exc:
                    self.events.status.emit(f"窗口采集连接失败，将重试：{exc}")
                    self.stop_event.wait(1)
                try:
                    job = self.jobs.get(timeout=.1)
                except queue.Empty:
                    continue
                request_id, request = job
                if request_id != self.latest_request:
                    continue
                dequeued = time.perf_counter()
                self.query_phase = "读取查询画面"
                try:
                    screenshot = request["screenshot"]
                    point, bbox, started = request["point"], request["bbox"], request["started"]
                    if request.get("capture_error"):
                        raise RuntimeError(request["capture_error"])
                    if screenshot is None:
                        raise RuntimeError("暂未收到游戏画面，请保持窗口展开并稍后重试")
                    local = frame_point(point, bbox, screenshot.shape[:2])
                    if local is None:
                        raise RuntimeError("鼠标不在游戏画面中")
                    # 一次查询使用同一份索引及卡牌资料；后台更新在下次查询生效。
                    self.query_resources = self.resources
                    index_revision = self.index_revision
                    with query_scope(lambda: request_id != self.latest_request or self.stop_event.is_set(),
                                     started+QUERY_TIMEOUT):
                        result = self.recognize(screenshot, local)
                        check_query()
                    if request_id != self.latest_request or self.stop_event.is_set():
                        continue
                    _, cards, _, _ = self.query_resources
                    card = cards.get(result.get("card_id")) if result.get("status") == "matched" else None
                    self.query_card = (request_id, card) if card else None
                    if card is None:
                        self.refresh_requested.set()
                    elapsed = round((time.perf_counter()-started)*1000)
                    self.events.answer.emit({"id": request_id, "card": card, "result": result,
                                             "bbox": bbox, "shape": screenshot.shape[:2], "elapsed": elapsed,
                                             "checking_cache": card is None, "index_revision": index_revision})
                    log(f"查询 {request_id}：{result.get('status')} {result.get('card_id', '')}，{elapsed} ms")
                    if self.args.diagnose_input or result.get("status") != "matched":
                        try:
                            self.diagnostics.save(screenshot, {"request_id": request_id,
                                "point": local, "bbox": bbox, "shape": screenshot.shape[:2], "result": result})
                        except Exception as exc:
                            log(f"查询诊断保存失败：{exc}")
                    if card and request_id == self.latest_request:
                        self.art_jobs.put((request_id, card["card_id"]))
                except QueryCancelled:
                    # 界面可能先触发超时并推进编号；仍保存这次慢查询，避免
                    # 只留下“已取消”提示而没有可复现画面及耗时阶段。
                    if time.perf_counter()-request["started"] >= QUERY_TIMEOUT:
                        self.record_timeout(request_id, request, dequeued)
                    continue
                except Exception as exc:
                    if isinstance(exc, QueryTimedOut):
                        self.record_timeout(request_id, request, dequeued)
                    if request_id == self.latest_request and not self.stop_event.is_set():
                        self.events.answer.emit({"id": request_id, "error": str(exc),
                                                 "retry_capture": request.get("screenshot") is None
                                                     or bool(request.get("capture_error"))})
                finally:
                    self.query_resources = None
        except Exception as exc:
            self.events.status.emit(f"识别启动失败：{exc}")
        finally:
            self.ready = False
            self.stop_event.set()
            if self.session:
                self.session.stop()

    def record_timeout(self, request_id, request, dequeued):
        elapsed = round((time.perf_counter()-request["started"])*1000)
        queued = round((dequeued-request["started"])*1000)
        log(f"查询 {request_id} 超时：{elapsed} ms，排队 {queued} ms，阶段：{self.query_phase}")
        try:
            image = request.get("screenshot")
            shape = image.shape[:2] if image is not None else None
            point = frame_point(request["point"], request["bbox"], shape) if shape else None
            self.diagnostics.save(image, {
                "request_id": request_id, "point": point, "bbox": request["bbox"], "shape": shape,
                "elapsed_ms": elapsed, "queued_ms": queued,
                "phase": self.query_phase, "result": {"status": "timeout"}})
        except Exception as exc:
            log(f"查询诊断保存失败：{exc}")

    def connect_game(self):
        try:
            hwnd, _ = game_window(None)
        except RuntimeError:
            hwnd = None
        failure = getattr(self.session, "error", None) if self.session and self.session.closed else None
        if self.session and (self.session.hwnd != hwnd or self.session.closed):
            self.session.stop()
            self.session = None
        if failure:
            raise RuntimeError(failure)
        if hwnd and not self.session:
            self.session = WindowCaptureSession(hwnd, .25 if sys.platform == "darwin" else .1)
        if self.session is not None and sys.platform == "darwin":
            self.session.set_active(self.capture_active)
        state = ("查询关闭，已暂停采集" if self.session and sys.platform == "darwin" and not self.capture_active else
                 "正在接收游戏画面" if self.session and self.session.newest(0) else
                 "正在等待第一帧游戏画面" if hwnd else
                 "等待游戏窗口：请启动或恢复游戏，并切换到游戏所在桌面")
        if state != self.last_capture_state:
            self.last_capture_state = state
            self.events.capture_state.emit(state)

    def set_capture_active(self, active):
        self.capture_active = active
        session = self.session
        if session is not None and sys.platform == "darwin":
            session.set_active(active)

    def recognize(self, screenshot, point):
        check_query()
        resources = getattr(self, "query_resources", None) or self.resources
        visuals, cards, index, finder = resources
        if getattr(self,"grid_visuals",None) is not visuals:
            if getattr(self,"grid_finder",None) is None:
                self.grid_finder=GridCardFinder(visuals,cards)
            else:
                self.grid_finder.update_visuals(visuals,cards)
            self.grid_visuals=visuals
        else:
            self.grid_finder.cards=cards
        self.query_phase = "卡框检测与图像复核"
        grid=self.grid_finder.find_at(screenshot,point,finder)
        check_query()
        if grid:
            return grid
        # SIFT 优先确认鼠标下的具体卡图，适用于出战、备战、手牌、牌库及特写。
        self.query_phase = "局部特征检索"
        result = finder.find_at(screenshot, cards, point)
        check_query()
        result = self.grid_finder.check_energy_result(screenshot, result)
        if result and result["status"] == "tentative" and result.get("polygon"):
            verified = self.grid_finder.verify_projected(screenshot,point,result["polygon"])
            if verified:
                return verified
            if result.get("verification") == "geometry-only":
                result = None  # 几何提议未确认，继续原有卡位及局部卡面回退。
        if result:
            return result
        # 动画横幅可能遮住牌桌边框和卡牌上沿。布局只提议卡框，
        # 必须由鼠标所在卡面的严格图像复核确认，不能直接沿用卡位答案。
        self.query_phase = "牌桌卡位复核"
        layout = adaptive_layout(screenshot, self.layout)
        check_query()
        x, y = point
        for slot in layout["slots"]:
            check_query()
            if slot["key"] == "preview":
                continue
            left, top, right, bottom = scaled_box(slot["box"], layout["reference_size"], screenshot.shape[:2])
            if left <= x < right and top <= y < bottom:
                verified = self.grid_finder.verify_box(screenshot, point,
                    [left, top, right, max(bottom, top+round((right-left)*1.4))])
                if verified:
                    return verified
        if is_battle_screen(screenshot):
            x, y = point
            for slot in layout["slots"]:
                check_query()
                if slot["key"] == "preview":
                    continue  # 特写只能由实际检测到的卡框确认。
                left, top, right, bottom = scaled_box(slot["box"], layout["reference_size"], screenshot.shape[:2])
                if left <= x < right and top <= y < bottom:
                    fallback = recognise_slot(screenshot, slot, layout["reference_size"], visuals, cards, index)
                    if fallback["status"] in ("unknown","tentative"):
                        # 手牌下沿可能在画面外；按卡宽恢复完整参考图尺寸后逐卡复核。
                        full_box=[left,top,right,max(bottom,top+round((right-left)*1.4))]
                        verified=self.grid_finder.verify_box(screenshot,point,full_box)
                        if verified:
                            return verified
                    if fallback["status"] == "matched":
                        printings = fallback.get("possible_printings", [])
                        rules = {finder.rules_signature(cards.get(card_id,{})) for card_id in printings}
                        if len(rules) > 1:
                            fallback["status"] = "tentative"
                            fallback.pop("card_id", None)
                    if fallback["status"] == "unknown":
                        partial = self.grid_finder.find_partial(screenshot, point)
                        if partial:
                            return partial
                    return fallback
        self.query_phase = "截断卡面复核"
        partial = self.grid_finder.find_partial(screenshot, point)
        if partial:
            return partial
        return grid or result or {"status": "unknown", "box": [point[0]-10, point[1]-10, point[0]+10, point[1]+10]}


class ReadingCard(QWidget):
    dismissed = Signal()
    geometry_changed = Signal()

    def __init__(self, translate):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.area_size = (1000, 2000)
        self.art_pixmap = None
        self.body = None
        self.labels = []
        self.body_widgets = []
        self.building = False
        self.layout_revision = 0
        self.translate = translate
        self.request_id = 0
        self.card_id = None
        self.image_language = None
        self.printing_label = None
        self.original_printing = ""
        self.pending_translations = set()
        self.cache_hint = None
        self.loading_label = None
        self.compact_media = False
        self.text_labels = {}
        self.buttons = {}
        self.sources = {}
        self.outer = QVBoxLayout(self)
        self.setObjectName("readingCard")
        self.outer.setContentsMargins(10, 8, 10, 8)
        self.outer.setSpacing(4)
        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.source_badge = QLabel()
        self.source_badge.setObjectName("sourceBadge")
        header_layout.addWidget(self.source_badge)
        header_layout.addStretch()
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("closeCard")
        self.close_button.setFixedSize(24,24)
        self.close_button.clicked.connect(self.dismissed.emit)
        header_layout.addWidget(self.close_button)
        self.outer.addWidget(self.header)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.outer.addWidget(self.scroll)
        get_backend().configure_popup(int(self.winId()))

    def content(self):
        self.loading_label = None
        self.cache_hint = None
        self.image = None
        self.art_pixmap = None
        self.card_id = None
        self.image_language = None
        self.printing_label = None
        self.original_printing = ""
        if self.body:
            self.scroll.takeWidget()
            self.body.hide()
            self.body.setParent(None)
            self.body.deleteLater()
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(4)
        self.scroll.setWidget(self.body)
        self.media = None
        self.rules = None
        self.rules_layout = None
        self.text_labels, self.buttons = {}, {}
        self.english_labels = {}
        self.pending_translations = set()
        self.sources = {}
        self.source_badge.setText("")
        self.setToolTip("")
        self.labels, self.body_widgets = [], []
        self.building = True

    def label(self, text, bold=False, role=None):
        label = QLabel(text)
        label.setProperty("role", role or ("section" if bold else "effect"))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        self.labels.append((label, bold))
        self.body_widgets.append(label)
        (self.rules_layout or self.body_layout).addWidget(label)
        # 可见浮卡替换内容时，Qt 会延后显示新子控件；先标为显示再测量，
        # 否则错误/加载提示可能被算成零高度，只剩下浮卡标题栏。
        label.show()
        return label

    def finish_content(self):
        self.building = False
        self.fit_to_area(*self.area_size)
        self.layout_revision += 1
        self.geometry_changed.emit()

    def measure(self, width, image_height, font_size, reserve=0):
        self.setFixedWidth(width)
        self.setStyleSheet(f"QWidget {{background:transparent;color:#dce6ed;font-size:{font_size}px;}}"
                          "QWidget#readingCard {background:#141f28;border:1px solid #344956;border-radius:10px;}"
                          "QLabel#sourceBadge {color:#8fa8b4;font-size:10px;}"
                          "QPushButton {background:#1e3540;color:#a6dace;border:1px solid #37525d;"
                          "border-radius:5px;padding:3px 9px;font-size:11px;}"
                          "QPushButton:hover {background:#2c4c57;border-color:#5b8b88;}"
                          "QPushButton#closeCard {background:transparent;color:#95aab7;border:0;"
                          "padding:0;font-size:19px;}"
                          "QPushButton#closeCard:hover {background:#293d49;color:#ffffff;}")
        self.scroll.setStyleSheet("QScrollBar:vertical {background:#182831;width:8px;border:0;}"
                                  "QScrollBar::handle:vertical {background:#4b626e;min-height:24px;border-radius:4px;}"
                                  "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}"
                                  "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:none;}")
        content_width = width-20-reserve
        image_width = 0
        has_rules = bool(self.text_labels or any(bold for label,bold in self.labels
                                                if label.parent() is self.rules))
        side_by_side = bool(self.rules and has_rules and content_width>=300)
        image_limit = min(140,content_width-160) if side_by_side else min(300,content_width)
        if self.image is not None:
            if self.art_pixmap is not None and not self.art_pixmap.isNull():
                ratio = self.devicePixelRatioF()
                scaled = self.art_pixmap.scaled(
                    min(round(image_limit*ratio),self.art_pixmap.width()),
                    min(round(image_height*ratio),self.art_pixmap.height()),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(ratio)
                self.image.setPixmap(scaled)
                image_width = math.ceil(scaled.width()/ratio)
                self.image.setFixedSize(image_width,math.ceil(scaled.height()/ratio))
            else:
                self.image.setFixedSize(0,0)
        # 排版由文字和可用宽度决定，不能因后台图源升级而切回纵向大图。
        self.compact_media = bool(image_width and side_by_side)
        rules_width = content_width-image_width-10 if self.compact_media else content_width
        for label, bold in self.labels:
            label.setMinimumHeight(0)
            label.setMaximumHeight(16777215)
            role = label.property("role")
            styles = {
                "title": f"font-size:{font_size+5}px;font-weight:700;color:#f0f5f8;",
                "subtitle": f"font-size:{max(11,font_size-2)}px;color:#90a6b5;",
                "english": f"font-size:{max(12,font_size-1)}px;color:#90a6b5;",
                "section": f"font-size:{font_size}px;font-weight:600;color:#a9dece;",
                "footer": "font-size:10px;color:#78919f;",
            }
            label.setStyleSheet(styles.get(role, ""))
            label_width = rules_width if self.rules and label.parent() is self.rules else content_width
            label.setFixedWidth(label_width)
            label.ensurePolished()
            label.setFixedHeight(max(label.fontMetrics().height(), label.heightForWidth(label_width))+2)
        for button in self.buttons.values():
            button.setMinimumHeight(0)
            button.setMaximumHeight(16777215)
            button.ensurePolished()
            button.setFixedHeight(button.sizeHint().height())
            button.setFixedWidth(min(rules_width,button.sizeHint().width()))
        visible = [widget for widget in self.body_widgets if not widget.isHidden()]
        if self.media is not None:
            rule_widgets = [widget for widget in visible if widget.parent() is self.rules]
            rules_height = sum(widget.height() for widget in rule_widgets)+max(0,len(rule_widgets)-1)*5
            self.rules.setFixedSize(rules_width,rules_height)
            self.media_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.compact_media
                                           else QBoxLayout.Direction.TopToBottom)
            self.media_layout.setSpacing(10 if self.compact_media else 6)
            media_height = max(self.image.height(),rules_height) if self.compact_media else (
                self.image.height()+rules_height+(6 if self.image.height() and rules_height else 0))
            self.media.setFixedHeight(media_height)
            top_widgets = [widget for widget in visible if widget.parent() is self.body]
            body_height = sum(widget.height() for widget in top_widgets)+media_height+len(top_widgets)*4
        else:
            body_height = sum(widget.height() for widget in visible)+max(0,len(visible)-1)*4
        self.body.setFixedSize(content_width, body_height)
        self.header.setFixedHeight(self.close_button.height())
        self.scroll.setFixedSize(width-20, body_height)
        self.setFixedHeight(16+self.header.height()+4+body_height)
        self.outer.invalidate()
        self.outer.activate()
        self.body_layout.activate()
        if self.media is not None:
            self.media_layout.activate()
            self.rules_layout.activate()
        return self.height()

    def fit_to_area(self, max_width, max_height):
        """优先保留约 350 宽及正常字号，长内容先增高，空间不够时适当扩宽。"""
        self.area_size = (max_width, max_height)
        widths = list(range(min(350, max_width), min(700, max_width)+1, 40))
        if not widths or widths[-1] != min(700, max_width):
            widths.append(min(700, max_width))
        # 完整卡图较高，优先保持 350 宽；缩放整张卡面后才扩宽文字区。
        for width in widths:
            for image_height in (420,350,280):
                if self.measure(width,image_height,14) <= max_height:
                    return True
        # 双语长文本保留正常字号，通过触控板/滚轮阅读，不再无限缩小文字。
        width = min(500, max_width)
        reserve = self.scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)+4
        self.measure(max(100, width), 220, 14, reserve=reserve)
        viewport_height = max(40, max_height-16-self.header.height()-4)
        self.scroll.setFixedHeight(viewport_height)
        self.setFixedHeight(16+self.header.height()+4+viewport_height)
        return self.height() <= max_height

    def loading(self, request_id, ready):
        self.request_id = request_id
        self.content()
        self.loading_label = self.label("正在识别……" if ready else "正在准备……", True)
        self.label("可随时关闭浮卡，取消本次查询。")
        self.finish_content()

    def loading_status(self, elapsed):
        if self.loading_label is not None:
            dots = "."*(int(elapsed*3)%3+1)
            self.loading_label.setText(f"正在识别 {elapsed:.1f} 秒{dots}")

    def show_result(self, payload):
        if payload["id"] != self.request_id:
            return
        self.content()
        card = payload.get("card")
        if not card:
            self.source_badge.setText("?")
            self.label("暂时无法查询" if payload.get("error") else "待确认", True)
            if payload.get("error"):
                self.label(payload["error"])
            elif payload.get("checking_cache"):
                self.cache_hint = self.label("正在检查本地卡图；缓存更新后会自动重试。")
            self.setToolTip(payload.get("error", ""))
            self.finish_content()
            return
        self.card_id = card["card_id"]
        self.add_name("name", card.get("name_zh"), card.get("name_en"), "", role="title")
        self.sources["name"] = self.chinese_mark(card.get("name_zh"), card.get("name_zh_source"))
        if card.get("hp"):
            self.label(f"HP {card['hp']}", role="subtitle")
        self.media = QWidget(self.body)
        self.media_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom,self.media)
        self.media_layout.setContentsMargins(0,0,0,0)
        self.body_layout.addWidget(self.media)
        self.image = QLabel()
        self.image.hide()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_layout.addWidget(self.image,0,Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignHCenter)
        self.body_widgets.append(self.image)
        self.rules = QWidget(self.media)
        self.rules_layout = QVBoxLayout(self.rules)
        self.rules_layout.setContentsMargins(0,0,0,0)
        self.rules_layout.setSpacing(5)
        self.media_layout.addWidget(self.rules,0,Qt.AlignmentFlag.AlignTop)
        thumbnail = QPixmap(str(ROOT / "output/card-thumbnails" / f"{card['card_id']}.png"))
        if not thumbnail.isNull():
            self.set_art_pixmap(thumbnail)
        self.add_text(0, card.get("card_text_zh"), card.get("card_text_en"), card.get("card_text_zh_source"))
        for number, attack in enumerate(card.get("attacks", []), 1):
            self.sources[f"attack-{number}"] = self.chinese_mark(attack.get("name_zh"), attack.get("name_zh_source"))
            kind = "特性" if attack.get("kind") == "ability" else "招式"
            self.add_name(f"attack-{number}", attack.get('name_zh'), attack.get('name_en'),
                          f"{kind} · ", suffix=f"   {attack.get('damage') or ''}")
            self.add_text(number, attack.get("text_zh"), attack.get("text_en"), attack.get("text_zh_source"))
        self.original_printing = f"{card['set_code']} · {card['number']}"
        self.printing_label = self.label(self.original_printing,role="footer")
        self.update_badge()
        self.finish_content()

    def cache_status(self, result):
        if self.cache_hint is None:
            return
        if result.get("error"):
            text = "缓存检查暂不可用，可从托盘打开运行状态查看原因。"
        elif result.get("pending"):
            text = "游戏正在写入新卡缓存，完成后会自动重试。"
        else:
            text = "尚未确认卡牌。可将指针移到插画中央，或打开卡牌特写后重新悬停。"
        if self.cache_hint.text() != text:
            self.cache_hint.setText(text)
            self.finish_content()

    @staticmethod
    def chinese_mark(chinese, source):
        return ("译" if source == "local" else "中") if chinese else "EN"

    def add_name(self, key, chinese, english, prefix, suffix="", role="section"):
        self.text_labels[key] = self.label(prefix+(chinese or "翻译中……")+suffix, True, role)
        self.english_labels[key] = self.label(english or "", role="subtitle")
        if english and not chinese:
            self.pending_translations.add(key)
            self.translate(self.request_id, key, english)
        self.text_labels[key].setProperty("prefix", prefix)
        self.text_labels[key].setProperty("suffix", suffix)

    def add_text(self, number, chinese, english, source=None):
        if chinese or english:
            self.text_labels[number] = self.label(readable_text(chinese) if chinese else "翻译中……")
            if english:
                self.english_labels[number] = self.label(readable_text(english, language="en"), role="english")
            self.sources[number] = self.chinese_mark(chinese, source)
        if english and not chinese:
            self.pending_translations.add(number)
            self.translate(self.request_id, number, english)

    def update_badge(self):
        present = set(self.sources.values())
        marks = [source for source in ("中", "EN", "译") if source in present]
        if self.art_pixmap is not None and not self.art_pixmap.isNull():
            # 标记纹理来源质量，不以当前因空间不足而缩小的显示尺寸判断高清。
            marks.append("HD" if self.art_pixmap.width() >= round(300*self.devicePixelRatioF()) else "SD")
        if self.image_language == "简中":
            marks.append("中图")
        elif self.image_language == "英文":
            marks.append("EN图")
        self.source_badge.setText(" · ".join(marks))

    def request_translation(self, number, source):
        self.translate(self.request_id, number, source)

    def translated(self, request_id, number, text):
        if request_id == self.request_id and number in self.pending_translations:
            self.pending_translations.remove(number)
            if text.startswith("翻译暂不可用"):
                self.text_labels[number].setText("中文翻译暂不可用")
                self.text_labels[number].setToolTip(text)
            else:
                label = self.text_labels[number]
                label.setText((label.property("prefix") or "")+text+(label.property("suffix") or ""))
                self.sources[number] = "译"
                self.update_badge()
            self.finish_content()

    def set_art_pixmap(self, pixmap):
        self.art_pixmap = pixmap
        if self.image is not None:
            self.image.show()
        self.update_badge()
        if not self.building:
            self.finish_content()

    def set_art(self, request_id, data):
        if request_id == self.request_id and self.image is not None:
            payload = data if isinstance(data,dict) else {"bytes":data}
            pixmap = QPixmap()
            if pixmap.loadFromData(payload["bytes"]):
                self.image_language = payload.get("language")
                if payload.get("chinese_effect") and 0 in self.text_labels:
                    self.text_labels[0].setText(readable_text(payload["chinese_effect"]))
                    self.sources[0] = "中"
                    self.pending_translations.discard(0)
                if self.printing_label and payload.get("language")=="简中":
                    self.printing_label.setText(f"{payload.get('set','')} · {payload.get('printing','')}")
                elif self.printing_label:
                    self.printing_label.setText(self.original_printing)
                self.set_art_pixmap(pixmap)


class Overlay(QObject):
    def __init__(self, app, args):
        super().__init__()
        self.app, self.args = app, args
        try:
            self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
            get_backend().validate_shortcut(self.settings["shortcut"])
            if self.settings["button"] not in ("left", "right"):
                raise ValueError("鼠标键无效")
        except (OSError, ValueError, KeyError, TypeError):
            self.settings = {"shortcut": "Ctrl+Option" if sys.platform == "darwin" else "Ctrl+Alt", "button": "right"}
        self.events = Events()
        self.worker = Recognizer(self.events, args)
        self.image_preference = self.settings.get("image_preference","zh")
        if self.image_preference not in ("zh","en","local"):
            self.image_preference = "zh"
        self.worker.image_sources.preference = self.image_preference
        self.popup = ReadingCard(self.worker.translate)
        self.popup.dismissed.connect(self.dismiss)
        self.popup.geometry_changed.connect(self.reposition)
        self.events.clicked.connect(self.query, Qt.ConnectionType.QueuedConnection)
        self.events.dismissed.connect(self.dismiss, Qt.ConnectionType.QueuedConnection)
        self.events.answer.connect(self.answer, Qt.ConnectionType.QueuedConnection)
        self.events.art.connect(self.popup.set_art, Qt.ConnectionType.QueuedConnection)
        self.events.translated.connect(self.popup.translated, Qt.ConnectionType.QueuedConnection)
        self.events.cache_checked.connect(self.cache_checked, Qt.ConnectionType.QueuedConnection)
        self.events.capture_state.connect(self.capture_state, Qt.ConnectionType.QueuedConnection)
        self.events.status.connect(self.status, Qt.ConnectionType.QueuedConnection)
        self.events.ready.connect(self.start_hook, Qt.ConnectionType.QueuedConnection)
        self.keep_in_game = bool(self.settings.get("keep_in_game", True))
        self.mode = self.settings.get("mode", "toggle")
        if self.mode not in ("toggle", "hover", "click"):
            self.mode = "toggle"
        try:
            self.hover_delay = max(.3, min(2., float(self.settings.get("hover_delay", .6))))
        except (TypeError, ValueError):
            self.hover_delay = .6
        self.settings = {key: self.settings[key] for key in ("shortcut", "button")}
        self.hook = self.make_hook()
        self.worker.set_capture_active(self.mode == "click")
        self.hook.popup_hwnd = int(self.popup.winId())
        self.request_id = 0
        self.hold_epoch = None
        self.wanted = False
        self.recognition_pending = False
        self.waiting_for_cache = False
        self.capture_retry_at = None
        self.capture_retries = 0
        self.popup_box = None
        self.target_relative = None
        self.positioning = False
        self.last_placement = None
        self.last_input_diagnostic = None
        self.next_art_refresh = 0
        self.last_pixel_ratio = None
        self.current_status = "正在启动……"
        self.last_toggle_enabled = None
        self.current_capture_state = "等待准备完成"
        self.status_dialog = None
        icon = QPixmap(32, 32)
        icon.fill(QColor("#18232e"))
        painter = QPainter(icon)
        painter.setPen(QColor("#7de5bd"))
        painter.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "中")
        painter.end()
        self.idle_icon = QIcon(icon)
        icon = QPixmap(32, 32)
        icon.fill(QColor("#7de5bd"))
        painter = QPainter(icon)
        painter.setPen(QColor("#18232e"))
        painter.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "查")
        painter.end()
        self.active_icon = QIcon(icon)
        self.tray = QSystemTrayIcon(self.idle_icon, app)
        self.menu = QMenu()
        self.mode_status = self.menu.addAction("悬停查询：已关闭")
        self.mode_status.setEnabled(False)
        self.menu.addAction("设置查询快捷键", self.configure)
        self.menu.addAction("运行状态与准备", self.show_status)
        self.menu.addAction("关闭阅读浮卡", self.dismiss)
        self.menu.addSeparator()
        self.menu.addAction("退出 PTCGLens", app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.window_monitor = GameWindowMonitor()
        self.window_monitor.start()
        self.timer = QTimer()
        self.last_ui_poll = time.perf_counter()
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        app.aboutToQuit.connect(self.stop)
        self.worker.start()
        self.status("正在准备游戏内快捷查询")
        self.tray.showMessage("PTCGLens", self.instruction(), QSystemTrayIcon.MessageIcon.Information, 5000)
        if not (ROOT / "output/index/manifest.json").is_file() or not (ROOT / "output/card-data.json").is_file():
            QTimer.singleShot(0, self.show_status)

    def make_hook(self):
        if self.mode in ("hover", "toggle"):
            hook = HoverTrigger(self.freeze_hover, self.events.status.emit,
                shortcut=self.settings["shortcut"], delay=self.hover_delay,
                dismiss_callback=self.events.dismissed.emit, toggle=self.mode == "toggle")
            # valid() 也在界面线程调用；这里复用有时效限制的后台快照，
            # 避免通过输入状态校验再次同步枚举系统窗口。
            hook.foreground = lambda: self.window_monitor.snapshot()[2]
        else:
            hook = MouseTrigger(self.freeze_click, self.events.status.emit, **self.settings,
                dismiss_callback=self.events.dismissed.emit)
        hook.popup_contains = lambda point: bool(self.popup_box and self.popup_box[0] <= point[0] < self.popup_box[2]
                                                 and self.popup_box[1] <= point[1] < self.popup_box[3])
        return hook

    def instruction(self):
        if self.mode == "toggle":
            return f"按一下 {self.settings['shortcut']} 开启／关闭，悬停 {self.hover_delay:g} 秒查询"
        if self.mode == "hover":
            return f"按住 {self.settings['shortcut']}，悬停 {self.hover_delay:g} 秒查询；松开关闭"
        return self.settings["shortcut"] + "+鼠标" + ("右键" if self.settings["button"] == "right" else "左键")

    @Slot()
    def start_hook(self):
        if not self.worker.stop_event.is_set() and self.hook.ident is None and not self.hook.stop_event.is_set():
            self.hook.start()

    def status(self, text):
        self.current_status = text
        if self.status_dialog is not None:
            self.status_label.setText(text)
        self.tray.setToolTip(f"PTCGLens · {self.instruction()}\n{text}"[:127])
        log(text)

    @Slot(str)
    def capture_state(self, text):
        self.current_capture_state = text
        if text == "正在接收游戏画面" and self.current_status.startswith("窗口采集连接失败"):
            self.status("游戏画面已恢复，可以继续悬停查询")
        if self.status_dialog is not None:
            self.capture_label.setText(text)

    def show_status(self):
        if self.status_dialog is None:
            self.status_dialog = QDialog()
            self.status_dialog.setWindowTitle("PTCGLens · 运行状态与准备")
            self.status_dialog.resize(580, 330)
            layout = QVBoxLayout(self.status_dialog)
            self.status_label = QLabel(self.current_status)
            self.status_label.setWordWrap(True)
            layout.addWidget(self.status_label)
            self.capture_label = QLabel(self.current_capture_state)
            self.capture_label.setWordWrap(True)
            layout.addWidget(self.capture_label)
            hint = QLabel("先启动游戏并打开卡牌页面，程序会自动建立和更新索引。\n"
                          "就绪后："+self.instruction()+"。\n"
                          "缺少中文对照时会自动翻译，英文始终保留。")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            for name, path in (("卡图目录", self.args.cache_root), ("英文资料目录", self.args.game_cache),
                               ("简中对照目录（可选）", self.args.translation_root)):
                label = QLabel(f"{name}：{path}")
                label.setWordWrap(True)
                layout.addWidget(label)
            refresh = QPushButton("检查新卡缓存")
            refresh.clicked.connect(self.worker.refresh_requested.set)
            layout.addWidget(refresh)
            close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
            close.rejected.connect(self.status_dialog.close)
            layout.addWidget(close)
        self.status_label.setText(self.current_status)
        self.capture_label.setText(self.current_capture_state)
        self.status_dialog.show()
        self.status_dialog.raise_()

    def poll(self):
        now = time.perf_counter()
        gap = now-self.last_ui_poll
        self.last_ui_poll = now
        if gap > .75:
            log(f"界面响应延迟：{round(gap*1000)} ms，查询阶段：{getattr(self.worker,'query_phase','等待查询')}")
        self.hook.hwnd, self.hook.bbox, active = self.window_monitor.snapshot()
        self.worker.set_capture_active(active and (self.mode == "click" or self.hook.enabled))
        if self.args.diagnose_input and self.hook.last_click != self.last_input_diagnostic:
            self.last_input_diagnostic = self.hook.last_click
            log(f"输入诊断：{self.last_input_diagnostic}")
        if self.mode in ("hover", "toggle") and self.wanted and (not active or not self.hook.valid(self.hold_epoch)):
            self.dismiss()
        elif self.mode == "click" and not active and self.wanted:
            # 组合点击保留原有切回游戏后恢复浮卡的行为。
            self.popup.hide()
        self.hook.dismiss_enabled = self.wanted and active
        if self.wanted and active and self.target_relative:
            self.reposition()
            self.popup.show()
        elif not active:
            self.popup.hide()
        elif not self.popup.isVisible():
            self.wanted = False
        if self.popup.isVisible():
            # 后端输入线程不读取 Qt 对象，只读取主线程发布的不可变坐标。
            r = self.popup.geometry()
            self.popup_box = get_backend().popup_bounds(int(self.popup.winId()),
                (r.x(), r.y(), r.x()+r.width(), r.y()+r.height()))
        else:
            self.popup_box = None
        self.hook.outside_dismiss_enabled = self.mode == "click" and self.wanted and self.popup.isVisible()
        if self.wanted and self.popup.isVisible() and self.popup.card_id:
            now = time.monotonic()
            if now >= self.next_art_refresh:
                self.next_art_refresh = now+3
                self.worker.art_jobs.put((self.request_id,self.popup.card_id))
        if self.wanted and self.capture_retry_at is not None and time.monotonic() >= self.capture_retry_at:
            self.capture_retry_at = None
            if self.mode in ("hover", "toggle") and self.hook.valid(self.hold_epoch):
                self.hook.retry(self.hold_epoch)
        self.query_progress()
        self.update_query_indicator()

    def update_query_indicator(self):
        self.mode_status.setVisible(self.mode == "toggle")
        enabled = self.hook.enabled if self.mode == "toggle" else None
        if enabled == self.last_toggle_enabled:
            return
        previous = self.last_toggle_enabled
        self.last_toggle_enabled = enabled
        self.tray.setIcon(self.active_icon if enabled else self.idle_icon)
        if enabled is not None:
            state = "已开启" if enabled else "已关闭"
            self.mode_status.setText(f"悬停查询：{state}（{self.settings['shortcut']} 切换）")
            if previous is not None:
                self.status(f"悬停查询{state}" + ("，可以松手并悬停换牌" if enabled else ""))

    def query_progress(self):
        if not self.wanted or not self.recognition_pending:
            return
        elapsed = time.perf_counter()-self.query_started
        if elapsed < QUERY_TIMEOUT:
            self.popup.loading_status(elapsed)
            return
        # 主线程的超时反馈独立于匹配线程。推进编号，迟到结果不能覆盖提示。
        self.recognition_pending = False
        self.request_id += 1
        self.worker.cancel(self.request_id)
        self.popup.request_id = self.request_id
        self.waiting_for_cache = False
        self.capture_retry_at = None
        self.popup.show_result({"id": self.request_id, "error": TIMEOUT_MESSAGE})
        self.reposition()
        self.status("本次识别超时，已取消；可重新查询")

    def dismiss(self):
        self.recognition_pending = False
        self.request_id += 1
        self.worker.cancel(self.request_id)
        self.popup.request_id = self.request_id
        self.hold_epoch = None
        if self.mode in ("hover", "toggle"):
            self.hook.invalidate()
        self.popup_box = None
        self.wanted = False
        self.waiting_for_cache = False
        self.capture_retry_at = None
        self.capture_retries = 0
        self.hook.dismiss_enabled = False
        self.hook.outside_dismiss_enabled = False
        self.popup.hide()

    def freeze_hover(self, x, y, epoch):
        self.freeze_click(x, y, hold_epoch=epoch)

    def freeze_click(self, x, y, hold_epoch=None):
        """钩子线程只取不可变帧引用和坐标，匹配及 UI 交给各自线程。"""
        started = time.perf_counter()
        hwnd = self.hook.hwnd
        current_hwnd, bbox, _ = self.window_monitor.snapshot()
        if current_hwnd != hwnd or hwnd is None or bbox is None:
            return
        screenshot, error = None, None
        try:
            session = self.worker.session
            latest = session.newest(0) if session and session.hwnd == hwnd and not session.closed else None
            screenshot = crop_client_frame(latest[1], hwnd, bbox=bbox, strict=True) if latest else None
            current_hwnd, current_box, _ = self.window_monitor.snapshot()
            if current_hwnd != hwnd or current_box != bbox:
                screenshot = None
                error = "窗口位置或尺寸已改变，请稍后再查询"
        except (RuntimeError, OSError) as exc:
            error = str(exc)
        if bbox is not None:
            self.events.clicked.emit({"point": (x, y), "bbox": bbox, "screenshot": screenshot,
                                      "started": started, "capture_error": error, "hold_epoch": hold_epoch})

    @Slot(object)
    def query(self, request):
        if self.mode in ("hover", "toggle") and not self.hook.valid(request.get("hold_epoch")):
            return
        self.hold_epoch = request.get("hold_epoch")
        self.waiting_for_cache = False
        self.capture_retry_at = None
        x, y = request["point"]
        self.query_started = request["started"]
        self.recognition_pending = True
        self.request_id += 1
        self.target_relative = relative_rect((x-10,y-10,x+10,y+10), request["bbox"])
        self.popup.loading(self.request_id, self.worker.ready)
        self.reposition()
        self.wanted = True
        self.hook.dismiss_enabled = True
        self.popup.show()
        self.hook.outside_dismiss_enabled = self.mode == "click"
        self.worker.query(self.request_id, request)

    @Slot(object)
    def answer(self, payload):
        if payload["id"] != self.request_id or not self.wanted:
            return
        if self.mode in ("hover", "toggle") and not self.hook.valid(self.hold_epoch):
            self.dismiss()
            return
        self.recognition_pending = False
        if "result" in payload:
            height, width = payload["shape"]
            box = payload["result"]["box"]
            self.target_relative = relative_rect(box, (0,0,width,height))
            if self.mode in ("hover", "toggle") and self.hook.bbox:
                self.hook.set_target(restore_rect(self.target_relative, self.hook.bbox))
        self.popup.show_result(payload)
        self.waiting_for_cache = not payload.get("card") and "error" not in payload
        if payload.get("card"):
            self.capture_retries = 0
        elif payload.get("retry_capture") and self.capture_retries < 2:
            self.capture_retries += 1
            self.capture_retry_at = time.monotonic()+1
        elif self.waiting_for_cache and payload.get("index_revision", 0) < self.worker.index_revision:
            self.cache_checked({"card_data_refreshed": True})
        self.reposition()
        self.status(f"查询未完成：{payload['error']}" if "error" in payload else
                    f"本次查询已完成：{payload.get('elapsed', '—')} ms")
        if "elapsed" in payload:
            log(f"浮卡已显示：查询 {payload['id']}，点击至界面更新 "
                f"{round((time.perf_counter()-self.query_started)*1000)} ms")

    @Slot(object)
    def cache_checked(self, result):
        if not self.waiting_for_cache or not self.wanted:
            return
        changed = any(result.get(key) for key in ("added", "updated", "visual_rebuilt", "card_data_refreshed"))
        if changed and self.mode in ("hover", "toggle") and self.hook.valid(self.hold_epoch):
            if self.hook.retry(self.hold_epoch):
                self.waiting_for_cache = False
                self.popup.loading(self.request_id, True)
        else:
            self.popup.cache_status(result)

    @Slot()
    def reposition(self):
        if self.positioning or self.target_relative is None or not self.hook.bbox:
            return
        self.positioning = True
        try:
            game = self.hook.bbox
            card = restore_rect(self.target_relative, game)
            geometry = placement_geometry(game, card, self.keep_in_game)
            if not geometry:
                self.popup.hide()
                return
            box, bounds = geometry
            ratio = self.popup.devicePixelRatioF()
            signature = (game, box, bounds, self.keep_in_game, self.popup.layout_revision,ratio)
            if signature == self.last_placement:
                return
            area_size = (bounds[2]-bounds[0], bounds[3]-bounds[1])
            fits = self.popup.height() <= area_size[1] and self.popup.width() <= area_size[0]
            if self.popup.area_size != area_size or self.last_pixel_ratio != ratio:
                fits = self.popup.fit_to_area(*area_size)
            if not fits:
                # 窗口小于完整阅读内容时，用当前显示器的可见范围保持内容完整。
                box, bounds = placement_geometry(game, card, False)
                area_size = (bounds[2]-bounds[0], bounds[3]-bounds[1])
                self.popup.fit_to_area(*area_size)
            pos = popup_position(box, (self.popup.width(), self.popup.height()), bounds)
            self.popup.move(*pos)
            self.last_pixel_ratio = self.popup.devicePixelRatioF()
            self.popup.update_badge()
            if self.last_pixel_ratio != ratio:
                self.popup.fit_to_area(*area_size)
                self.popup.move(*popup_position(box,(self.popup.width(),self.popup.height()),bounds))
            self.last_placement = signature
        except OSError:
            self.popup.hide()
        finally:
            self.positioning = False

    def configure(self):
        dialog = QDialog()
        dialog.setWindowTitle("PTCGLens 查询设置")
        form = QFormLayout(dialog)
        shortcut = QLineEdit(self.settings["shortcut"])
        form.addRow("查询快捷键", shortcut)
        buttons = QComboBox()
        buttons.addItems(["鼠标右键", "鼠标左键"])
        buttons.setCurrentIndex(0 if self.settings["button"] == "right" else 1)
        modes = QComboBox()
        modes.addItems(["按一下开启／关闭，悬停换牌", "按住快捷键并悬停（松开关闭）", "组合键加点击（再次查询或关闭）"])
        modes.setCurrentIndex(("toggle", "hover", "click").index(self.mode))
        form.addRow("查询方式", modes)
        delay = QDoubleSpinBox()
        delay.setRange(.3, 2.)
        delay.setSingleStep(.1)
        delay.setValue(self.hover_delay)
        delay.setSuffix(" 秒")
        form.addRow("悬停时间", delay)
        form.addRow("点击模式的鼠标键", buttons)
        inside = QCheckBox("优先保持在游戏窗口内")
        inside.setChecked(self.keep_in_game)
        form.addRow(inside)
        images = QComboBox()
        images.addItems(["优先简中卡面","优先英文高清原卡","仅本地游戏缓存"])
        images.setCurrentIndex(("zh","en","local").index(self.image_preference))
        form.addRow("卡图来源",images)
        hint = QLabel("例如 Ctrl+Alt（Mac 为 Ctrl+Option）或 Ctrl+Shift+Q。\n"
                      "开关模式无需长按；Esc、关闭浮卡或切走游戏也会关闭查询模式。")
        form.addRow(hint)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(actions)
        def save():
            try:
                get_backend().validate_shortcut(shortcut.text())
            except ValueError as exc:
                hint.setText(str(exc))
                return
            self.dismiss()
            previous_hook = self.hook
            previous_hook.stop()
            if previous_hook.ident is not None:
                previous_hook.join(timeout=2)
            self.mode = ("toggle", "hover", "click")[modes.currentIndex()]
            self.hover_delay = delay.value()
            self.settings = {"shortcut": shortcut.text().strip(), "button": "right" if buttons.currentIndex() == 0 else "left"}
            self.keep_in_game = inside.isChecked()
            self.image_preference = ("zh","en","local")[images.currentIndex()]
            self.worker.image_sources.preference = self.image_preference
            self.next_art_refresh = 0
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(SETTINGS, {**self.settings, "keep_in_game": self.keep_in_game,
                "image_preference": self.image_preference, "mode": self.mode, "hover_delay": self.hover_delay})
            self.hook = self.make_hook()
            self.hook.popup_hwnd = int(self.popup.winId())
            if self.worker.ready:
                self.hook.start()
            self.reposition()
            self.status("查询快捷键已更新")
            dialog.accept()
        actions.accepted.connect(save)
        actions.rejected.connect(dialog.reject)
        dialog.exec()

    def stop(self):
        self.timer.stop()
        self.window_monitor.stop()
        self.hook.stop()
        self.worker.stop_event.set()
        self.worker.refresh_requested.set()
        if self.hook.ident is not None:
            self.hook.join(timeout=2)
        self.worker.join(timeout=3)
        self.window_monitor.join(timeout=.2)
        self.tray.hide()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CARD_CACHE)
    parser.add_argument("--game-cache", type=Path, default=DEFAULT_DATABASE_CACHE)
    parser.add_argument("--translation-root", type=Path, default=ROOT / ".tmp/ptcg-live-zh-mod/databases_zh-CN")
    parser.add_argument("--diagnose-input", action="store_true", help="在终端输出悬停或点击查询输入诊断")
    args = parser.parse_args(argv)
    set_dpi_awareness()
    plugin_directory = prepare_qt_plugins()
    app = QApplication(sys.argv[:1])
    configure_font(app)
    app.setQuitOnLastWindowClosed(False)
    # 在后台采集和匹配启动前设置；给游戏及界面保留 CPU，避免占满所有核心。
    cv2.setNumThreads(2)
    if cv2.getNumThreads() > 2:
        # Mac 的 GCD 后端忽略正数限制；0 可关闭其内部并行区域。
        cv2.setNumThreads(0)
    missing = check_permissions(input_required=True, request=True)
    if missing:
        QMessageBox.warning(None, "PTCGLens · 需要系统权限", "\n\n".join(missing)+
                            "\n\n授权实际启动程序的终端或应用后，重启该应用并重新运行 PTCGLens。")
        return 1
    (ROOT / "output").mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(ROOT / "output/overlay.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        log("游戏内快捷查询已经运行，请使用系统托盘中的 PTCGLens 图标。")
        return 1
    overlay = Overlay(app, args)
    try:
        return app.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
