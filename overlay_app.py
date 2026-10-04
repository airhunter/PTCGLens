"""在游戏中按住查询键点击卡牌，显示中文阅读浮卡。"""

from __future__ import annotations

import argparse
import ctypes
import json
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QPoint, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
                              QSystemTrayIcon, QVBoxLayout, QWidget)

from battle_live import is_battle_screen, load_resources
from battle_multi import adaptive_layout, recognise_slot, scaled_box
from cache_watch import DEFAULT_CARD_CACHE, DEFAULT_DATABASE_CACHE, refresh_once
from card_translation import TranslationCache, readable_text
from live_capture import client_bbox, game_window, set_dpi_awareness, user32
from overlay_input import MouseTrigger
from overlay_model import frame_point, popup_position, shortcut_keys
from viewer import card_art_bytes
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
    art = Signal(int, bytes)
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
                    self.events.translated.emit(request_id, number, "机器翻译（仅供参考）\n" + text)
                except Exception as exc:
                    self.events.translated.emit(request_id, number, f"翻译暂不可用：{exc}")
            else:
                _, card_id = job
                try:
                    art = card_art_bytes(card_id, self.args.cache_root, ROOT / "output/card-large",
                                         ROOT / "output/card-thumbnails", ROOT / "output/card-data.json")
                    self.events.art.emit(request_id, art)
                except Exception:
                    pass  # 缩略插画已经显示，完整插画提取失败不影响阅读。

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

    def __init__(self, translate):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(350)
        self.setStyleSheet("QWidget {background:#18232e;color:#edf4fa;font-size:14px;}"
                           "QPushButton {background:#2b4657;border:0;border-radius:5px;padding:6px;}"
                           "QPushButton:hover {background:#3a6274;} QScrollArea {border:0;}")
        self.translate = translate
        self.request_id = 0
        self.text_labels = {}
        self.buttons = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        close = QPushButton("关闭  ×   ·   Esc")
        close.clicked.connect(self.dismissed.emit)
        outer.addWidget(close)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        outer.addWidget(self.scroll)

    def content(self):
        self.image = None
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(3, 3, 3, 3)
        self.scroll.setWidget(body)
        self.text_labels, self.buttons = {}, {}

    def label(self, text, bold=False):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if bold:
            label.setStyleSheet("font-weight:600;color:#f7d991;font-size:17px;")
        self.body_layout.addWidget(label)
        return label

    def loading(self, request_id, ready):
        self.request_id = request_id
        self.content()
        self.label("正在识别鼠标指向的卡牌……" if ready else "正在准备本地卡牌索引……", True)
        self.label("本次查询会锁定这一张牌。")
        self.setFixedHeight(170)

    def show_result(self, payload):
        if payload["id"] != self.request_id:
            return
        self.content()
        card = payload.get("card")
        if not card:
            self.label("待确认", True)
            self.label(payload.get("error") or "暂时无法可靠确认这张牌。请指向清晰插画，或在游戏中放大卡牌后再次查询。")
            self.setFixedHeight(210)
            return
        self.label(card.get("name_zh") or card["name_en"], True)
        self.label(f"{card['name_en']}   " + (f"HP {card['hp']}" if card.get("hp") else ""))
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self.image)
        thumbnail = QPixmap(str(ROOT / "output/card-thumbnails" / f"{card['card_id']}.png"))
        if not thumbnail.isNull():
            first, last = (.07, .48) if card.get("hp") else (.115, .52)
            art = thumbnail.copy(round(thumbnail.width()*.05), round(thumbnail.height()*first),
                                 round(thumbnail.width()*.9), round(thumbnail.height()*(last-first)))
            self.set_art_pixmap(art)
        self.add_text(0, card.get("card_text_zh"), card.get("card_text_en"))
        for number, attack in enumerate(card.get("attacks", []), 1):
            kind = "特性" if attack.get("kind") == "ability" else "招式"
            self.label(f"{kind} · {attack.get('name_zh') or attack['name_en']}   {attack.get('damage') or ''}", True)
            self.add_text(number, attack.get("text_zh"), attack.get("text_en"))
        self.label(f"{card['set_code']} · {card['number']}   本次识别 {payload['elapsed']} ms")
        self.label("中文阅读版 · 本地简中对照缺失时显示英文")
        self.body_layout.addStretch()
        self.body_layout.activate()
        content_height = self.body_layout.totalHeightForWidth(310)
        self.setFixedHeight(min(620, max(220, content_height+70)))

    def add_text(self, number, chinese, english):
        if chinese or english:
            self.text_labels[number] = self.label(readable_text(chinese or english))
        if english and not chinese:
            button = QPushButton("翻译这段英文")
            button.clicked.connect(lambda _=False, n=number, source=english: self.request_translation(n, source))
            self.body_layout.addWidget(button)
            self.buttons[number] = button

    def request_translation(self, number, source):
        self.buttons[number].setText("翻译中……")
        self.buttons[number].setEnabled(False)
        self.translate(self.request_id, number, source)

    def translated(self, request_id, number, text):
        if request_id == self.request_id and number in self.text_labels:
            if text.startswith("翻译暂不可用"):
                self.buttons[number].setText("翻译失败，点击重试")
                self.buttons[number].setToolTip(text)
                self.buttons[number].setEnabled(True)
            else:
                self.text_labels[number].setText(text)
                self.buttons[number].hide()

    def set_art_pixmap(self, pixmap):
        self.image.setPixmap(pixmap.scaled(300, 190, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))

    def set_art(self, request_id, data):
        if request_id == self.request_id and self.image is not None:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
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
        self.popup = ReadingCard(self.worker.translate)
        self.popup.dismissed.connect(self.dismiss)
        self.events.clicked.connect(self.query, Qt.ConnectionType.QueuedConnection)
        self.events.dismissed.connect(self.dismiss, Qt.ConnectionType.QueuedConnection)
        self.events.answer.connect(self.answer, Qt.ConnectionType.QueuedConnection)
        self.events.art.connect(self.popup.set_art, Qt.ConnectionType.QueuedConnection)
        self.events.translated.connect(self.popup.translated, Qt.ConnectionType.QueuedConnection)
        self.events.status.connect(self.status, Qt.ConnectionType.QueuedConnection)
        self.events.ready.connect(self.start_hook, Qt.ConnectionType.QueuedConnection)
        self.settings = {key: self.settings[key] for key in ("shortcut", "button")}
        self.hook = MouseTrigger(self.freeze_click, self.events.status.emit, **self.settings,
                                 dismiss_callback=self.events.dismissed.emit)
        self.request_id = 0
        self.wanted = False
        self.popup_anchor = None
        self.last_input_diagnostic = None
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
        if self.wanted and active and self.popup_anchor:
            self.popup.show()
        elif not active:
            self.popup.hide()
        elif not self.popup.isVisible():
            self.wanted = False

    def dismiss(self):
        self.wanted = False
        self.hook.dismiss_enabled = False
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
        cursor = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        current = QCursor.pos()
        screen = QApplication.screenAt(current) or QApplication.primaryScreen()
        ratio = screen.devicePixelRatio()
        self.popup_anchor = current + QPoint(round((x-cursor.x)/ratio), round((y-cursor.y)/ratio))
        self.physical_anchor = (x, y)
        self.screen = QApplication.screenAt(self.popup_anchor) or QApplication.primaryScreen()
        self.ratio = self.screen.devicePixelRatio()
        self.popup.loading(self.request_id, self.worker.ready)
        self.position((self.popup_anchor.x()-10, self.popup_anchor.y()-10,
                       self.popup_anchor.x()+10, self.popup_anchor.y()+10))
        self.wanted = True
        self.hook.dismiss_enabled = True
        self.popup.show()
        self.worker.query(self.request_id, request)

    @Slot(object)
    def answer(self, payload):
        if payload["id"] != self.request_id:
            return
        self.popup.show_result(payload)
        if "result" in payload:
            left, top, right, bottom = payload["bbox"]
            height, width = payload["shape"]
            box = payload["result"]["box"]
            physical = (left+box[0]*(right-left)/width, top+box[1]*(bottom-top)/height,
                        left+box[2]*(right-left)/width, top+box[3]*(bottom-top)/height)
            logical = tuple(round((v-self.physical_anchor[i%2])/self.ratio+
                                  (self.popup_anchor.x() if i%2 == 0 else self.popup_anchor.y()))
                            for i, v in enumerate(physical))
            self.position(logical)
        self.status(f"本次查询已完成：{payload.get('elapsed', '—')} ms")
        if "elapsed" in payload:
            print(f"浮卡已显示：查询 {payload['id']}，点击至界面更新 "
                  f"{round((time.perf_counter()-self.query_started)*1000)} ms", flush=True)

    def position(self, box):
        rect = self.screen.availableGeometry()
        self.popup.setFixedHeight(min(self.popup.height(), rect.height()))
        pos = popup_position(box, (self.popup.width(), self.popup.height()),
                             (rect.left(), rect.top(), rect.right()+1, rect.bottom()+1))
        self.popup.move(*pos)

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
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
            self.hook.configure(**self.settings)
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
