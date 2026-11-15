"""统一项目版本、公开版号和两个平台的数字版本；不依赖打包工具。"""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


@dataclass(frozen=True)
class ReleaseVersion:
    python: str
    core: tuple[int, int, int]
    stage: str | None
    sequence: int

    @classmethod
    def parse(cls, text):
        match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:(a|b|rc)([1-9][0-9]{0,2}))?", text)
        if not match:
            raise ValueError("项目版本必须为 X.Y.Z、X.Y.ZaN、X.Y.ZbN 或 X.Y.ZrcN，N 为 1–999")
        core = tuple(int(match[i]) for i in (1, 2, 3))
        if any(part > 65535 for part in core):
            raise ValueError("版本分量不能超过 Windows 16 位版本字段范围")
        return cls(text, core, match[4], int(match[5] or 0))

    @property
    def base(self):
        return ".".join(map(str, self.core))

    @property
    def public(self):
        phase = {"a": "alpha", "b": "beta", "rc": "rc"}.get(self.stage)
        return f"{self.base}-{phase}.{self.sequence}" if phase else self.base

    @property
    def tag(self):
        return "v" + self.public

    @property
    def build_number(self):
        return {"a": 1000, "b": 2000, "rc": 3000, None: 4000}[self.stage] + self.sequence

    @property
    def windows(self):
        return self.core + (self.build_number,)


def project_version(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent
    return ReleaseVersion.parse(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
