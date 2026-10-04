"""在游戏中按住查询键点击卡牌，显示中文阅读浮卡。"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                              QSystemTrayIcon, QVBoxLayout, QWidget)

from battle_live import is_battle_screen, load_resources
from battle_multi import adaptive_layout, recognise_slot, scaled_box
from cache_watch import DEFAULT_CARD_CACHE, DEFAULT_DATABASE_CACHE, refresh_once
from card_translation import TranslationCache, readable_text
from card_images import CardImageSources
from live_capture import client_bbox, game_window, set_dpi_awareness, user32
from overlay_input import MouseTrigger
from overlay_geometry import placement_geometry
from overlay_model import frame_point, popup_position, relative_rect, restore_rect, shortcut_keys
from window_capture import WindowCaptureSession, crop_client_frame


ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "output/overlay-settings.json"


def configure_font(app):
    font_file = Path("C:/Windows/Fonts/msyh.ttc")
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    app.setFont(QFont("Microsoft YaHei UI", 10))


class Events(QObject):
    clicked = Signal(object)
    dismissed = Signal()
    ready = Signal()
    status = Signal(str)
    answer = Signal(object)
    art = Signal(int, object)
    translated = Signal(int, int, str)


class Recognizer(threading.Thread):
    def __init__(self, events, args):
        super().__init__(daemon=True, name="PTCGLens recognition")
        self.events, self.args = events, args
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.session = None
        self.ready = False
        self.latest_request = 0
        self.translator = TranslationCache(ROOT / "output/translation-cache.json")
        self.art_jobs = queue.Queue()
        self.translation_jobs = queue.Queue()
        self.last_art = None
        self.image_sources = CardImageSources(ROOT / "output/card-sources",args.cache_root,
                                              ROOT / "output/card-thumbnails",ROOT / "output/card-large")

    def translate(self, request_id, number, source):
        self.translation_jobs.put((request_id, number, source))

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
                    text, _ = self.translator.translate(source)
                    self.events.translated.emit(request_id, number, text)
                except Exception as exc:
                    self.events.translated.emit(request_id, number, f"翻译暂不可用：{exc}")
            else:
                _, card_id = job
                try:
                    card = self.resources[1][card_id]
                    art = self.image_sources.resolve(card,lambda:self.latest_request != request_id or self.stop_event.is_set())
                    if self.last_art != (request_id,art):
                        self.last_art = (request_id,art)
                        self.events.art.emit(request_id, art)
                        print(f"卡图已更新：{card_id}，{art['language']}，{art['provider']}",flush=True)
                except Exception as exc:
                    print(f"卡图加载暂不可用：{card_id}，{exc}",flush=True)

    def refresh_cache(self):
        while not self.stop_event.wait(30):
            try:
                refreshed = refresh_once(self.args.cache_root, ROOT / "output/index",
                    ROOT / "output/card-thumbnails", self.args.game_cache,
                    self.args.translation_root, ROOT / "output/card-data.json")
                if any(refreshed[key] for key in ("added", "updated", "visual_rebuilt", "card_data_refreshed")):
                    resources = load_resources(ROOT / "output/index", ROOT / "output/card-thumbnails",
                                               ROOT / "output/card-data.json")
                    self.resources = resources
                    self.events.status.emit("本地卡牌索引已更新")
            except Exception as exc:
                self.events.status.emit(f"缓存更新暂未完成：{exc}")

    def query(self, request_id, request):
        self.latest_request = request_id
        self.jobs.put((request_id, request))

    def run(self):
        set_dpi_awareness()
        try:
            self.events.status.emit("正在加载本地卡牌索引……")
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
                try:
                    screenshot = request["screenshot"]
                    point, bbox, started = request["point"], request["bbox"], request["started"]
                    if screenshot is None:
                        raise RuntimeError("暂未收到游戏画面，请保持窗口展开并稍后重试")
                    local = frame_point(point, bbox, screenshot.shape[:2])
                    if local is None:
                        raise RuntimeError("鼠标不在游戏画面中")
                    result = self.recognize(screenshot, local)
                    _, cards, _, _ = self.resources
                    card = cards.get(result.get("card_id")) if result.get("status") == "matched" else None
                    elapsed = round((time.perf_counter()-started)*1000)
                    self.events.answer.emit({"id": request_id, "card": card, "result": result,
                                             "bbox": bbox, "shape": screenshot.shape[:2], "elapsed": elapsed})
                    print(f"查询 {request_id}：{result.get('status')} {result.get('card_id', '')}，{elapsed} ms", flush=True)
                    if self.args.diagnose_input:
                        import cv2
                        diagnostic = ROOT / "output/overlay-diagnostics"
                        diagnostic.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(diagnostic / f"query-{request_id}.png"), screenshot)
                        (diagnostic / f"query-{request_id}.json").write_text(json.dumps(
                            {"point": local, "bbox": bbox, "shape": screenshot.shape[:2], "result": result},
                            ensure_ascii=False, indent=2), encoding="utf-8")
                    if card and request_id == self.latest_request:
                        self.art_jobs.put((request_id, card["card_id"]))
                except Exception as exc:
                    self.events.answer.emit({"id": request_id, "error": str(exc)})
        except Exception as exc:
            self.events.status.emit(f"识别启动失败：{exc}")
        finally:
            self.ready = False
            self.stop_event.set()
            if self.session:
                self.session.stop()

    def connect_game(self):
        try:
            hwnd, _ = game_window(None)
        except RuntimeError:
            hwnd = None
        if self.session and (self.session.hwnd != hwnd or self.session.closed):
            self.session.stop()
            self.session = None
        if hwnd and not self.session:
            self.session = WindowCaptureSession(hwnd, .1)

    def recognize(self, screenshot, point):
        visuals, cards, index, finder = self.resources
        # SIFT 优先确认鼠标下的具体卡图，适用于出战、备战、手牌、牌库及特写。
        result = finder.find_at(screenshot, cards, point)
        if result:
            return result
        if is_battle_screen(screenshot):
            layout = adaptive_layout(screenshot, self.layout)
            x, y = point
            for slot in layout["slots"]:
                if slot["key"] == "preview":
                    continue  # 特写只能由实际检测到的卡框确认。
                left, top, right, bottom = scaled_box(slot["box"], layout["reference_size"], screenshot.shape[:2])
                if left <= x < right and top <= y < bottom:
                    fallback = recognise_slot(screenshot, slot, layout["reference_size"], visuals, cards, index)
                    if fallback["status"] == "matched":
                        printings = fallback.get("possible_printings", [])
                        rules = {json.dumps({key: cards.get(card_id, {}).get(key) for key in
                                             ("hp", "attacks", "card_text_en")}, sort_keys=True)
                                 for card_id in printings}
                        if len(rules) > 1:
                            fallback["status"] = "tentative"
                            fallback.pop("card_id", None)
                    return fallback
        return result or {"status": "unknown", "box": [point[0]-10, point[1]-10, point[0]+10, point[1]+10]}


class ReadingCard(QWidget):
    dismissed = Signal()
    geometry_changed = Signal()

    def __init__(self, translate):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
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
        self.text_labels = {}
        self.buttons = {}
        self.sources = {}
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(10, 10, 10, 10)
        self.outer.setSpacing(6)
        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.source_badge = QLabel()
        self.source_badge.setObjectName("sourceBadge")
        header_layout.addWidget(self.source_badge)
        header_layout.addStretch()
        self.close_button = QPushButton("×")
        self.close_button.setFixedWidth(30)
        self.close_button.clicked.connect(self.dismissed.emit)
        header_layout.addWidget(self.close_button)
        self.outer.addWidget(self.header)

    def content(self):
        self.image = None
        self.art_pixmap = None
        self.card_id = None
        self.image_language = None
        self.printing_label = None
        self.original_printing = ""
        if self.body:
            self.outer.removeWidget(self.body)
            self.body.hide()
            self.body.setParent(None)
            self.body.deleteLater()
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(3, 3, 3, 3)
        self.body_layout.setSpacing(6)
        self.outer.addWidget(self.body)
        self.text_labels, self.buttons = {}, {}
        self.sources = {}
        self.source_badge.setText("")
        self.setToolTip("")
        self.labels, self.body_widgets = [], []
        self.building = True

    def label(self, text, bold=False):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        self.labels.append((label, bold))
        self.body_widgets.append(label)
        self.body_layout.addWidget(label)
        return label

    def finish_content(self):
        self.building = False
        self.fit_to_area(*self.area_size)
        self.layout_revision += 1
        self.geometry_changed.emit()

    def measure(self, width, image_height, font_size):
        self.setFixedWidth(width)
        self.setStyleSheet(f"QWidget {{background:#18232e;color:#edf4fa;font-size:{font_size}px;}}"
                          "QLabel#sourceBadge {color:#9cccb7;font-size:12px;}"
                          "QPushButton {background:#2b4657;border:0;border-radius:5px;padding:6px;}"
                          "QPushButton:hover {background:#3a6274;}")
        content_width = width-26
        for label, bold in self.labels:
            label.setMinimumHeight(0)
            label.setMaximumHeight(16777215)
            label.setStyleSheet(f"font-size:{font_size+3}px;font-weight:600;color:#f7d991;" if bold else "")
            label.ensurePolished()
            label.setFixedHeight(max(label.fontMetrics().height(), label.heightForWidth(content_width))+2)
        if self.image is not None:
            if self.art_pixmap is not None and not self.art_pixmap.isNull():
                ratio = self.devicePixelRatioF()
                # 在物理像素中只缩小，不把缩略图插值成大图；让 Qt 使用正确的屏幕比例。
                scaled = self.art_pixmap.scaled(
                    min(round(min(300,content_width)*ratio),self.art_pixmap.width()),
                    min(round(image_height*ratio),self.art_pixmap.height()),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(ratio)
                self.image.setPixmap(scaled)
                self.image.setFixedHeight(math.ceil(scaled.height()/ratio))
            else:
                self.image.setFixedHeight(0)
        for button in [self.close_button, *self.buttons.values()]:
            button.setMinimumHeight(0)
            button.setMaximumHeight(16777215)
            button.ensurePolished()
            button.setFixedHeight(button.sizeHint().height())
        visible = [widget for widget in self.body_widgets if not widget.isHidden()]
        body_height = 6+sum(widget.height() for widget in visible)+max(0,len(visible)-1)*6
        self.body.setFixedHeight(body_height)
        self.header.setFixedHeight(self.close_button.height())
        self.setFixedHeight(20+self.header.height()+6+body_height)
        self.outer.invalidate()
        self.outer.activate()
        self.body_layout.activate()
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
        for font_size, image_height in ((13,280), (12,220)):
            for width in widths:
                height = self.measure(width, image_height, font_size)
                if height <= max_height:
                    return True
        # 极小窗口无法容纳完整内容时仍保持文字完整，由摆放层使用屏幕范围。
        return False

    def loading(self, request_id, ready):
        self.request_id = request_id
        self.content()
        self.label("正在识别……" if ready else "正在准备……", True)
        self.finish_content()

    def show_result(self, payload):
        if payload["id"] != self.request_id:
            return
        self.content()
        card = payload.get("card")
        if not card:
            self.source_badge.setText("?")
            self.label("待确认", True)
            self.setToolTip(payload.get("error", ""))
            self.finish_content()
            return
        self.card_id = card["card_id"]
        self.label(card.get("name_zh") or card["name_en"], True)
        self.sources["name"] = "中" if card.get("name_zh") else "EN"
        self.label(f"{card['name_en']}   " + (f"HP {card['hp']}" if card.get("hp") else ""))
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self.image)
        self.body_widgets.append(self.image)
        thumbnail = QPixmap(str(ROOT / "output/card-thumbnails" / f"{card['card_id']}.png"))
        if not thumbnail.isNull():
            self.set_art_pixmap(thumbnail)
        self.add_text(0, card.get("card_text_zh"), card.get("card_text_en"))
        for number, attack in enumerate(card.get("attacks", []), 1):
            self.sources[f"attack-{number}"] = "中" if attack.get("name_zh") else "EN"
            kind = "特性" if attack.get("kind") == "ability" else "招式"
            self.label(f"{kind} · {attack.get('name_zh') or attack['name_en']}   {attack.get('damage') or ''}", True)
            self.add_text(number, attack.get("text_zh"), attack.get("text_en"))
        self.original_printing = f"{card['set_code']} · {card['number']}"
        self.printing_label = self.label(self.original_printing)
        self.update_badge()
        self.finish_content()

    def add_text(self, number, chinese, english):
        if chinese or english:
            self.text_labels[number] = self.label(readable_text(chinese or english))
            self.sources[number] = "中" if chinese else "EN"
        if english and not chinese:
            button = QPushButton("翻译")
            button.clicked.connect(lambda _=False, n=number, source=english: self.request_translation(n, source))
            self.body_layout.addWidget(button)
            self.body_widgets.append(button)
            self.buttons[number] = button

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
        self.buttons[number].setText("翻译中……")
        self.buttons[number].setEnabled(False)
        self.finish_content()
        self.translate(self.request_id, number, source)

    def translated(self, request_id, number, text):
        if request_id == self.request_id and number in self.text_labels and self.sources.get(number) != "中":
            if text.startswith("翻译暂不可用"):
                self.buttons[number].setText("重试")
                self.buttons[number].setToolTip(text)
                self.buttons[number].setEnabled(True)
            else:
                self.text_labels[number].setText(text)
                self.sources[number] = "译"
                self.update_badge()
                self.buttons[number].hide()
            self.finish_content()

    def set_art_pixmap(self, pixmap):
        self.art_pixmap = pixmap
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
                    if 0 in self.buttons:
                        self.buttons[0].hide()
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
            shortcut_keys(self.settings["shortcut"])
            if self.settings["button"] not in ("left", "right"):
                raise ValueError("鼠标键无效")
        except (OSError, ValueError, KeyError, TypeError):
            self.settings = {"shortcut": "Ctrl+Alt", "button": "right"}
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
        self.events.status.connect(self.status, Qt.ConnectionType.QueuedConnection)
        self.events.ready.connect(self.start_hook, Qt.ConnectionType.QueuedConnection)
        self.keep_in_game = bool(self.settings.get("keep_in_game", True))
        self.settings = {key: self.settings[key] for key in ("shortcut", "button")}
        self.hook = MouseTrigger(self.freeze_click, self.events.status.emit, **self.settings,
                                 dismiss_callback=self.events.dismissed.emit)
        self.hook.popup_hwnd = int(self.popup.winId())
        self.request_id = 0
        self.wanted = False
        self.target_relative = None
        self.positioning = False
        self.last_placement = None
        self.last_input_diagnostic = None
        self.next_art_refresh = 0
        self.last_pixel_ratio = None
        icon = QPixmap(32, 32)
        icon.fill(QColor("#18232e"))
        painter = QPainter(icon)
        painter.setPen(QColor("#7de5bd"))
        painter.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "中")
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(icon), app)
        self.menu = QMenu()
        self.menu.addAction("设置查询快捷键", self.configure)
        self.menu.addAction("关闭阅读浮卡", self.dismiss)
        self.menu.addSeparator()
        self.menu.addAction("退出 PTCGLens", app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        app.aboutToQuit.connect(self.stop)
        self.worker.start()
        self.status("正在准备游戏内快捷查询")
        self.tray.showMessage("PTCGLens", self.instruction(), QSystemTrayIcon.MessageIcon.Information, 5000)

    def instruction(self):
        return self.settings["shortcut"] + "+鼠标" + ("右键" if self.settings["button"] == "right" else "左键")

    @Slot()
    def start_hook(self):
        if not self.worker.stop_event.is_set():
            self.hook.start()

    def status(self, text):
        self.tray.setToolTip(f"PTCGLens · {self.instruction()}\n{text}"[:127])
        print(text, flush=True)

    def poll(self):
        try:
            hwnd, _ = game_window(None)
            self.hook.hwnd, self.hook.bbox = hwnd, client_bbox(hwnd)
        except (RuntimeError, OSError):
            self.hook.hwnd, self.hook.bbox = None, None
        if self.args.diagnose_input and self.hook.last_click != self.last_input_diagnostic:
            self.last_input_diagnostic = self.hook.last_click
            print(f"输入诊断：{self.last_input_diagnostic}", flush=True)
        active = user32.GetForegroundWindow() == self.hook.hwnd and self.hook.hwnd is not None
        self.hook.dismiss_enabled = self.wanted and active
        if self.wanted and active and self.target_relative:
            self.reposition()
            self.popup.show()
        elif not active:
            self.popup.hide()
        elif not self.popup.isVisible():
            self.wanted = False
        self.hook.outside_dismiss_enabled = self.wanted and self.popup.isVisible()
        if self.wanted and self.popup.isVisible() and self.popup.card_id:
            now = time.monotonic()
            if now >= self.next_art_refresh:
                self.next_art_refresh = now+3
                self.worker.art_jobs.put((self.request_id,self.popup.card_id))

    def dismiss(self):
        self.wanted = False
        self.hook.dismiss_enabled = False
        self.hook.outside_dismiss_enabled = False
        self.popup.hide()

    def freeze_click(self, x, y):
        """钩子线程只取不可变帧引用和坐标，匹配及 UI 交给各自线程。"""
        started = time.perf_counter()
        hwnd = self.hook.hwnd
        bbox = client_bbox(hwnd)
        session = self.worker.session
        latest = session.newest(0) if session and session.hwnd == hwnd and not session.closed else None
        screenshot = crop_client_frame(latest[1], hwnd) if latest else None
        self.events.clicked.emit({"point": (x, y), "bbox": bbox, "screenshot": screenshot, "started": started})

    @Slot(object)
    def query(self, request):
        x, y = request["point"]
        self.query_started = request["started"]
        self.request_id += 1
        self.target_relative = relative_rect((x-10,y-10,x+10,y+10), request["bbox"])
        self.popup.loading(self.request_id, self.worker.ready)
        self.reposition()
        self.wanted = True
        self.hook.dismiss_enabled = True
        self.popup.show()
        self.hook.outside_dismiss_enabled = True
        self.worker.query(self.request_id, request)

    @Slot(object)
    def answer(self, payload):
        if payload["id"] != self.request_id:
            return
        if "result" in payload:
            height, width = payload["shape"]
            box = payload["result"]["box"]
            self.target_relative = relative_rect(box, (0,0,width,height))
        self.popup.show_result(payload)
        self.reposition()
        self.status(f"本次查询已完成：{payload.get('elapsed', '—')} ms")
        if "elapsed" in payload:
            print(f"浮卡已显示：查询 {payload['id']}，点击至界面更新 "
                  f"{round((time.perf_counter()-self.query_started)*1000)} ms", flush=True)

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
        form.addRow("按住这些键", shortcut)
        buttons = QComboBox()
        buttons.addItems(["鼠标右键", "鼠标左键"])
        buttons.setCurrentIndex(0 if self.settings["button"] == "right" else 1)
        form.addRow("同时点击", buttons)
        inside = QCheckBox("优先保持在游戏窗口内")
        inside.setChecked(self.keep_in_game)
        form.addRow(inside)
        images = QComboBox()
        images.addItems(["优先简中卡面","优先英文高清原卡","仅本地游戏缓存"])
        images.setCurrentIndex(("zh","en","local").index(self.image_preference))
        form.addRow("卡图来源",images)
        hint = QLabel("例如 Ctrl+Alt 或 Ctrl+Shift+Q。\n查询点击会被拦截，普通点击仍交给游戏。")
        form.addRow(hint)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(actions)
        def save():
            try:
                shortcut_keys(shortcut.text())
            except ValueError as exc:
                hint.setText(str(exc))
                return
            self.settings = {"shortcut": shortcut.text().strip(), "button": "right" if buttons.currentIndex() == 0 else "left"}
            self.keep_in_game = inside.isChecked()
            self.image_preference = ("zh","en","local")[images.currentIndex()]
            self.worker.image_sources.preference = self.image_preference
            self.next_art_refresh = 0
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({**self.settings, "keep_in_game": self.keep_in_game,
                                           "image_preference":self.image_preference},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
            self.hook.configure(**self.settings)
            self.reposition()
            self.status("查询快捷键已更新")
            dialog.accept()
        actions.accepted.connect(save)
        actions.rejected.connect(dialog.reject)
        dialog.exec()

    def stop(self):
        self.timer.stop()
        self.hook.stop()
        self.worker.stop_event.set()
        if self.hook.ident is not None:
            self.hook.join(timeout=2)
        self.worker.join(timeout=3)
        self.tray.hide()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CARD_CACHE)
    parser.add_argument("--game-cache", type=Path, default=DEFAULT_DATABASE_CACHE)
    parser.add_argument("--translation-root", type=Path, default=ROOT / ".tmp/ptcg-live-zh-mod/databases_zh-CN")
    parser.add_argument("--diagnose-input", action="store_true", help="在终端输出查询点击诊断")
    args = parser.parse_args(argv)
    if not (ROOT / "output/index/manifest.json").is_file() or not (ROOT / "output/card-data.json").is_file():
        parser.error("尚未建立索引；请先运行 uv run python cache_watch.py --once")
    set_dpi_awareness()
    app = QApplication(sys.argv[:1])
    configure_font(app)
    app.setQuitOnLastWindowClosed(False)
    instance_lock = QLockFile(str(ROOT / "output/overlay.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        print("游戏内快捷查询已经运行，请使用系统托盘中的 PTCGLens 图标。", flush=True)
        return 1
    overlay = Overlay(app, args)
    try:
        return app.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
