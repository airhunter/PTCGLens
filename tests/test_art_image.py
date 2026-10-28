import gc
import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from art_image import prepare_art
from qt_runtime import prepare_qt_plugins


class ArtImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugins = prepare_qt_plugins()
        cls.app = QApplication.instance() or QApplication([])

    def test_rgb_alpha_and_pixel_ownership_survive_background_preparation(self):
        pixels = np.zeros((7,9,4),np.uint8)
        pixels[:] = (30,60,210,128)
        body = cv2.imencode('.png',pixels)[1].tobytes()
        prepared = []
        worker = threading.Thread(target=lambda: prepared.append(prepare_art(
            {'bytes':body,'language':'英文','provider':'test'})))
        worker.start(); worker.join(2)
        self.assertFalse(worker.is_alive())
        image = prepared[0]['image']
        pixels[:] = 0
        gc.collect()
        self.assertEqual(image.pixelColor(3,4),QColor(210,60,30,128))
        self.assertEqual(prepared[0]['language'],'英文')
        self.assertNotIn('bytes',prepared[0])

    def test_large_and_grayscale_images_keep_aspect_with_bounded_upload_size(self):
        pixels = np.full((3000,1500),91,np.uint8)
        image = prepare_art({'bytes':cv2.imencode('.png',pixels)[1].tobytes()})['image']
        self.assertEqual((image.width(),image.height()),(1024,2048))
        self.assertEqual(image.pixelColor(500,1000),QColor(91,91,91))

    def test_slow_background_decode_does_not_block_qt_timer(self):
        entered, release = threading.Event(),threading.Event()
        def decode(*unused):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('decode test timed out')
            return np.zeros((4,4,3),np.uint8)
        timer = QTimer()
        ticks = []
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(10)
        result = []
        with patch('art_image.cv2.imdecode',side_effect=decode):
            worker = threading.Thread(target=lambda:result.append(prepare_art({'bytes':b'png'})))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                deadline = time.monotonic()+.15
                while time.monotonic()<deadline:
                    self.app.processEvents()
                    time.sleep(.002)
                self.assertGreaterEqual(len(ticks),5)
                self.assertTrue(worker.is_alive())
            finally:
                release.set();worker.join(2);timer.stop()
        self.assertEqual(len(result),1)

    def test_invalid_image_keeps_failure_in_worker(self):
        with self.assertRaises(ValueError):
            prepare_art({'bytes':b'not an image'})
