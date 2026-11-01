"""独立包自检：不采集画面、不请求权限，可从任意工作目录执行。"""

import json
import os
from pathlib import Path
import platform
import ssl
import sys

from app_paths import DATA_DIR, RESOURCE_DIR
from atomic_json import write_json_atomic


def run(report_path, *, network=False):
    report = {"frozen": bool(getattr(sys, "frozen", False)), "architecture": platform.machine(),
              "executable": sys.executable, "resources": str(RESOURCE_DIR), "data": str(DATA_DIR), "checks": {}}

    def check(name, callback):
        try:
            report["checks"][name] = {"ok": True, "detail": callback()}
        except Exception as exc:
            report["checks"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def assets():
        from local_chinese import load_local_chinese
        layout = json.loads((RESOURCE_DIR/"battle_layout.sample.json").read_text(encoding="utf-8"))
        rules = json.loads((RESOURCE_DIR/"card-image-rules.json").read_text(encoding="utf-8"))
        assert layout["slots"] and load_local_chinese()
        build = RESOURCE_DIR/"build-info.json"
        return {"slots": len(layout["slots"]), "rules": len(rules),
                "build": json.loads(build.read_text()) if build.exists() else "source"}

    def recognition():
        import cv2
        import numpy as np
        pixels = np.random.default_rng(31).integers(0,256,(224,160,3),dtype=np.uint8)
        keypoints, descriptors = cv2.SIFT_create(nfeatures=100).detectAndCompute(
            cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY), None)
        assert keypoints and descriptors is not None
        encoded = cv2.imencode(".png",pixels)[1]
        assert np.array_equal(cv2.imdecode(encoded,cv2.IMREAD_COLOR), pixels)
        return {"opencv": cv2.__version__, "features": len(keypoints), "module": cv2.__file__}

    def qt():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from qt_runtime import prepare_qt_plugins
        from PySide6.QtGui import QImage, QPixmap
        from PySide6.QtWidgets import QApplication, QLabel
        plugins = prepare_qt_plugins()
        app = QApplication.instance() or QApplication([])
        label = QLabel("PTCGLens · 中英双语")
        label.resize(300,60)
        image = QImage(40,40,QImage.Format.Format_RGB32)
        image.fill(0xFF7DE5BD)
        assert not QPixmap.fromImage(image).isNull() and not label.grab().isNull()
        return {"platform": app.platformName(), "rendered": True}

    def native():
        from platform_backend import get_backend
        backend = get_backend()
        assert callable(backend.WindowCaptureSession) and callable(backend.MouseTrigger)
        return {"module": backend.__file__}

    def unity():
        import UnityPy
        from platform_backend.paths import DEFAULT_CARD_CACHE
        from verify_card import extract_card
        result = {"module": UnityPy.__file__, "texture_decode": "no cached sample"}
        manifest = DATA_DIR/"index/manifest.json"
        if manifest.is_file():
            for record in json.loads(manifest.read_text())["cards"][:5]:
                directory = DEFAULT_CARD_CACHE / (record["card_id"] + ("_t" if record["variant"] == "thumbnail" else ""))
                bundle = next(directory.rglob("__data"), None)
                if bundle is not None:
                    pixels = extract_card(bundle, directory.name)
                    assert pixels.size > 0
                    result.update(texture_decode=record["card_id"], shape=list(pixels.shape))
                    break
        return result

    def certificates():
        context = ssl.create_default_context()
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.cert_store_stats()["x509_ca"] > 0
        return {"ca_file": ssl.get_default_verify_paths().cafile, "store": context.cert_store_stats()}

    check("resources", assets)
    check("opencv", recognition)
    # 纹理库首次探测 CPU 可能启动系统工具，必须在 Qt/Cocoa 初始化之前完成。
    check("unity_texture", unity)
    check("qt", qt)
    check("native_backend", native)
    check("https_certificates", certificates)
    if network:
        from card_translation import translate_segment
        check("online_translation", lambda: translate_segment("Draw 2 cards."))
    report["ok"] = all(item["ok"] for item in report["checks"].values())
    write_json_atomic(Path(report_path).expanduser().resolve(), report)
    return 0 if report["ok"] else 1
