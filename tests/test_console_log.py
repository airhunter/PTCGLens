import io
import unittest
from unittest.mock import patch

from console_log import log


class ConsoleLogTest(unittest.TestCase):
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
