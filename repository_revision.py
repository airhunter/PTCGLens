"""只读获取可选上游仓库版本，不在原生 GUI 后台线程派生 Git 进程。"""

from pathlib import Path
import re


def repository_revision(root):
    try:
        git = Path(root)/'.git'
        if git.is_file():
            entry = git.read_text(encoding='utf-8').strip()
            if not entry.startswith('gitdir: '):
                return None
            git = (git.parent/entry[8:]).resolve()
        head = (git/'HEAD').read_text(encoding='utf-8').strip()
        def valid(value):
            return bool(re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})',value))
        if valid(head):
            return head
        if not head.startswith('ref: '):
            return None
        reference = head[5:]
        if (not reference.startswith('refs/') or '\\' in reference
                or any(part in ('','.','..') for part in reference.split('/'))):
            return None
        roots = [git]
        if (git/'commondir').is_file():
            roots.append((git/(git/'commondir').read_text(encoding='utf-8').strip()).resolve())
        for directory in roots:
            path = directory/reference
            if path.is_file():
                revision = path.read_text(encoding='utf-8').strip()
                if valid(revision):
                    return revision
            packed = directory/'packed-refs'
            if packed.is_file():
                for line in packed.read_text(encoding='utf-8').splitlines():
                    parts = line.split()
                    if len(parts)==2 and parts[1]==reference and valid(parts[0]):
                        return parts[0]
    except (OSError,UnicodeError):
        pass  # 上游版本只用于来源标记，缺少元数据不影响卡牌内容。
    return None
