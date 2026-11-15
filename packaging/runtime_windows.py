"""Windows 无控制台测试包：随包证书、容错输出及只读资源目录。"""

import os
import sys

import certifi

sys.dont_write_bytecode = True
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
# 部分第三方库会访问输出流；windowed EXE 的标准输出默认是 None。
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
