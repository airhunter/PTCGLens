"""两个平台共用的源码摘要和构建来源；源码换行规范化，避免 Windows/Mac 摘要不同。"""

import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

ROOT = Path(__file__).resolve().parents[1]


def source_digest():
    files = (list(ROOT.glob("*.py")) + list((ROOT / "platform_backend").rglob("*.py"))
             + list((ROOT / "scripts").glob("*.py")) + list((ROOT / "packaging").glob("*.py"))
             + list((ROOT / "packaging").glob("*.spec"))
             + list((ROOT / ".github/workflows").glob("*.yml"))
             + [ROOT / name for name in ("battle_layout.sample.json", "card-image-rules.json",
                 "translations/local-zh.json", "docs/windows-portable-guide.txt", "pyproject.toml", "uv.lock", "LICENSE")])
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))
    return digest.hexdigest()


def build_info(version, architecture, platform_name):
    from repository_revision import repository_revision

    return {"version": version.public, "python_project_version": version.python,
            "base_version": version.base, "tag": version.tag, "build_number": version.build_number,
            "base_revision": repository_revision(ROOT), "source_sha256": source_digest(),
            "built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "architecture": architecture, "platform": platform_name, "python": platform.python_version(),
            "pyinstaller": importlib.metadata.version("pyinstaller"), "channel": "prerelease" if version.stage else "stable"}
