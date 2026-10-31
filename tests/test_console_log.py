import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from console_log import configure_file_logging, log, _logger


class ConsoleLogTest(unittest.TestCase):
    def test_windowless_start_keeps_bounded_utf8_logs(self):
        previous = _logger.handlers[:]
        for handler in previous:
            _logger.removeHandler(handler)
        try:
            with TemporaryDirectory() as folder:
                path = Path(folder)/"logs/overlay.log"
                configure_file_logging(path)
                handler = _logger.handlers[0]
                self.assertEqual(handler.maxBytes, 2*1024*1024)
                self.assertEqual(handler.backupCount, 3)
                with patch("sys.stdout", None):
                    log("后台查询正常")
                self.assertIn("后台查询正常", path.read_text(encoding="utf-8"))
                handler.close()
                _logger.removeHandler(handler)
        finally:
            for handler in previous:
                _logger.addHandler(handler)

    def test_utf8_preserves_chinese_and_non_utf8_escapes_unrepresentable_text(self):
        for encoding in ("utf-8", "cp1252", "ascii"):
            with self.subTest(encoding=encoding):
                buffer = io.BytesIO()
                with io.TextIOWrapper(buffer, encoding=encoding) as stream:
                    with patch("sys.stdout", stream):
                        log("查询 1：matched")
                    text = buffer.getvalue().decode(encoding)
                expected = ("查询 1：matched" if encoding == "utf-8" else
                            r"\u67e5\u8be2 1\uff1amatched")
                self.assertEqual(text.rstrip(), expected)

    def test_missing_closed_or_broken_output_does_not_raise(self):
        class BrokenStream(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("console disconnected")
        closed = io.StringIO()
        closed.close()
        for stream in (None, closed, BrokenStream()):
            with self.subTest(stream=type(stream).__name__), patch("sys.stdout", stream):
                log("中文状态")
