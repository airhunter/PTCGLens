import threading
import time
import unittest
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from overlay_app import Recognizer
from query_control import check_query


class SignalRecorder:
    def __init__(self):
        self.values = []
        self.emitted = threading.Event()

    def emit(self, *values):
        self.values.append(values)
        self.emitted.set()


class QueryQueueTest(unittest.TestCase):
    def test_non_utf8_logging_keeps_single_answer_and_enqueues_card_art(self):
        for encoding in ("cp1252", "ascii"):
            with self.subTest(encoding=encoding):
                worker = self.worker()
                worker.args.diagnose_input = True
                worker.connect_game = lambda: None
                worker.background_jobs = lambda *args: None
                worker.refresh_cache = lambda: None
                worker.recognize = lambda *args: {"status": "matched", "card_id": "correct"}
                worker.diagnostics.save = lambda *args: (_ for _ in ()).throw(PermissionError("sharing conflict"))
                resources = ([], {"correct": {"card_id": "correct"}}, None, None)
                request = {"screenshot": np.zeros((10,10,3),np.uint8), "point": (5,5),
                           "bbox": (0,0,10,10), "started": time.perf_counter()}
                art_enqueued = threading.Event()
                put = worker.art_jobs.put
                def enqueue(job):
                    put(job)
                    art_enqueued.set()
                worker.art_jobs.put = enqueue
                with io.TextIOWrapper(io.BytesIO(), encoding=encoding) as stream, \
                     patch("sys.stdout", stream), patch("overlay_app.set_dpi_awareness"), \
                     patch("overlay_app.load_resources", return_value=resources):
                    worker.start()
                    try:
                        self.assertTrue(worker.events.ready.emitted.wait(2))
                        worker.query(1, request)
                        self.assertTrue(art_enqueued.wait(2))
                    finally:
                        worker.stop_event.set()
                        worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(worker.events.answer.values), 1)
                self.assertEqual(worker.events.answer.values[0][0]["card"]["card_id"], "correct")
                self.assertEqual(worker.art_jobs.get_nowait(), (1, "correct"))

    def test_running_query_stops_at_checkpoint_when_new_query_arrives(self):
        worker = self.worker()
        entered = threading.Event()
        def recognize(image, point):
            if int(image[0, 0, 0]) == 1:
                entered.set()
                while True:
                    time.sleep(.005)
                    check_query()
            return {"status": "matched", "card_id": "2", "box": [0,0,10,10]}
        worker.recognize = recognize
        worker.connect_game = lambda: None
        worker.background_jobs = lambda *args: None
        worker.refresh_cache = lambda: None
        resources = ([], {"2": {"card_id": "2"}}, None, None)
        def request(number):
            return {"screenshot": np.full((10,10,3), number, np.uint8), "point": (5,5),
                    "bbox": (0,0,10,10), "started": time.perf_counter()}
        with patch("overlay_app.set_dpi_awareness"), patch("overlay_app.load_resources", return_value=resources):
            worker.start()
            try:
                self.assertTrue(worker.events.ready.emitted.wait(2))
                worker.query(1, request(1))
                self.assertTrue(entered.wait(2))
                worker.query(2, request(2))
                self.assertTrue(worker.events.answer.emitted.wait(1))
            finally:
                worker.stop_event.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([v[0]["id"] for v in worker.events.answer.values], [2])

    def test_timeout_returns_error_without_stopping_worker(self):
        worker = self.worker()
        worker.connect_game = lambda: None
        worker.background_jobs = lambda *args: None
        worker.refresh_cache = lambda: None
        def recognize(image, point):
            while True:
                time.sleep(.005)
                check_query()
        worker.recognize = recognize
        with patch("overlay_app.set_dpi_awareness"), \
             patch("overlay_app.load_resources", return_value=([], {}, None, None)), \
             patch("overlay_app.QUERY_TIMEOUT", .05):
            worker.start()
            try:
                self.assertTrue(worker.events.ready.emitted.wait(2))
                worker.query(1, {"screenshot": np.zeros((10,10,3),np.uint8), "point": (5,5),
                                 "bbox": (0,0,10,10), "started": time.perf_counter()})
                self.assertTrue(worker.events.answer.emitted.wait(1))
                self.assertTrue(worker.is_alive())
            finally:
                worker.stop_event.set()
                worker.join(2)
        self.assertIn("用时过长", worker.events.answer.values[0][0]["error"])

    def worker(self):
        events = SimpleNamespace(**{name: SignalRecorder() for name in
                                  ("status", "ready", "answer", "art", "translated")})
        args = SimpleNamespace(cache_root=Path("output/unused-test-cache"), diagnose_input=False)
        worker = Recognizer(events, args)
        worker.prepare_metadata = lambda: None
        return worker

    def test_quick_queries_skip_queued_card_and_discard_running_old_result_or_error(self):
        for old_fails in (False, True):
            with self.subTest(old_fails=old_fails):
                worker = self.worker()
                entered, release = threading.Event(), threading.Event()
                recognized = []
                def recognize(image, point):
                    number = int(image[0, 0, 0])
                    recognized.append(number)
                    if number == 1:
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError("test timed out")
                        if old_fails:
                            raise RuntimeError("old query failed")
                    return {"status": "matched", "card_id": str(number), "box": [0,0,10,10]}
                worker.recognize = recognize
                worker.connect_game = lambda: None
                worker.background_jobs = lambda *args: None
                worker.refresh_cache = lambda: None
                resources = ([], {str(n): {"card_id": str(n)} for n in (1,2,3)}, None, None)
                def request(number):
                    return {"screenshot": np.full((10,10,3), number, np.uint8),
                            "point": (5,5), "bbox": (0,0,10,10), "started": time.perf_counter()}
                with patch("overlay_app.set_dpi_awareness"), patch("overlay_app.load_resources", return_value=resources):
                    worker.start()
                    try:
                        self.assertTrue(worker.events.ready.emitted.wait(3))
                        worker.query(1, request(1))
                        self.assertTrue(entered.wait(3))
                        worker.query(2, request(2))
                        worker.query(3, request(3))
                        release.set()
                        self.assertTrue(worker.events.answer.emitted.wait(3))
                    finally:
                        release.set()
                        worker.stop_event.set()
                        worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(recognized, [1,3])
                self.assertEqual([v[0]["id"] for v in worker.events.answer.values], [3])
                self.assertEqual(worker.art_jobs.get_nowait(), (3, "3"))
                self.assertTrue(worker.art_jobs.empty())

    def test_old_background_art_and_translation_are_discarded_on_completion(self):
        for kind in ("art", "translate"):
            with self.subTest(kind=kind):
                worker = self.worker()
                worker.resources = ([], {key: {"card_id":key} for key in ("old", "new")}, None, None)
                worker.latest_request = 1
                entered, release = threading.Event(), threading.Event()
                def slow(*args, **kwargs):
                    old = args[0] == "old text" if kind == "translate" else args[0]["card_id"] == "old"
                    if old:
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError("test timed out")
                    return ("translation", False) if kind == "translate" else {"language":"英文","provider":"test"}
                if kind == "translate":
                    worker.translator.translate = slow
                    jobs = worker.translation_jobs
                    jobs.put((1,0,"old text"))
                else:
                    worker.image_sources.resolve = slow
                    jobs = worker.art_jobs
                    jobs.put((1,"old"))
                thread = threading.Thread(target=worker.background_jobs, args=(jobs,kind))
                thread.start()
                try:
                    self.assertTrue(entered.wait(3))
                    worker.latest_request = 2
                    jobs.put((2,0,"new text") if kind == "translate" else (2,"new"))
                    release.set()
                    signal = worker.events.translated if kind == "translate" else worker.events.art
                    self.assertTrue(signal.emitted.wait(3))
                finally:
                    release.set()
                    worker.stop_event.set()
                    thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual([values[0] for values in signal.values], [2])

    def test_art_uses_queried_rules_when_metadata_refreshes_in_background(self):
        worker = self.worker()
        worker.latest_request = 1
        old_card = {"card_id":"same", "card_text_en":"original rules"}
        worker.query_card = (1, old_card)
        worker.resources = ([], {"same":{"card_id":"same", "card_text_en":"refreshed rules"}}, None, None)
        received = []
        def resolve(card, cancelled):
            received.append(card)
            worker.stop_event.set()
            return {"language":"英文", "provider":"test"}
        worker.image_sources.resolve = resolve
        worker.art_jobs.put((1, "same"))
        worker.background_jobs(worker.art_jobs, "art")
        self.assertEqual(received, [old_card])

    def test_metadata_only_refresh_reuses_visuals_and_sift_index(self):
        worker = self.worker()
        visuals, index, finder = [], object(), object()
        worker.resources = (visuals, {"old":{}}, index, finder)
        with patch("overlay_app.read_json_retry", return_value={"cards":{"new":{}}}), \
                patch("overlay_app.load_resources") as load:
            for _ in range(50):
                worker.reload_resources({"added":0,"updated":0,"visual_rebuilt":0,"card_data_refreshed":True})
            load.assert_not_called()
        self.assertIs(worker.resources[0], visuals)
        self.assertIs(worker.resources[2], index)
        self.assertIs(worker.resources[3], finder)
        self.assertEqual(worker.resources[1], {"new":{}})

    def test_failed_metadata_reload_keeps_current_resources(self):
        worker = self.worker()
        previous = ([], {"old":{}}, object(), object())
        worker.resources = previous
        with patch("overlay_app.read_json_retry", side_effect=ValueError("incomplete")):
            with self.assertRaises(ValueError):
                worker.reload_resources({"added":0,"updated":0,"visual_rebuilt":0,"card_data_refreshed":True})
        self.assertIs(worker.resources, previous)

    def test_diagnostic_write_failure_does_not_replace_a_correct_answer(self):
        worker = self.worker()
        worker.args.diagnose_input = True
        worker.connect_game = lambda: None
        worker.background_jobs = lambda *args: None
        worker.refresh_cache = lambda: None
        worker.recognize = lambda *args: {"status":"matched", "card_id":"correct"}
        worker.diagnostics.save = lambda *args: (_ for _ in ()).throw(PermissionError("sharing conflict"))
        resources = ([], {"correct":{"card_id":"correct"}}, None, None)
        request = {"screenshot":np.zeros((10,10,3), np.uint8), "point":(5,5),
                   "bbox":(0,0,10,10), "started":time.perf_counter()}
        with patch("overlay_app.set_dpi_awareness"), patch("overlay_app.load_resources", return_value=resources):
            worker.start()
            try:
                self.assertTrue(worker.events.ready.emitted.wait(3))
                worker.query(1, request)
                self.assertTrue(worker.events.answer.emitted.wait(3))
            finally:
                worker.stop_event.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(worker.events.answer.values), 1)
        self.assertEqual(worker.events.answer.values[0][0]["card"]["card_id"], "correct")
        self.assertIsNone(worker.query_resources)
