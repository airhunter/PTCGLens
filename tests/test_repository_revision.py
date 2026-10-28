from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repository_revision import repository_revision


class RepositoryRevisionTest(unittest.TestCase):
    def test_detached_loose_and_packed_head(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);git=root/'.git';git.mkdir()
            first,second='a'*40,'b'*64
            (git/'HEAD').write_text(first+'\n')
            self.assertEqual(repository_revision(root),first)
            (git/'HEAD').write_text('ref: refs/heads/main\n')
            (git/'packed-refs').write_text('# packed refs\n'+second+' refs/heads/main\n')
            self.assertEqual(repository_revision(root),second)
            (git/'refs/heads').mkdir(parents=True)
            (git/'refs/heads/main').write_text(first+'\n')
            self.assertEqual(repository_revision(root),first)

    def test_worktree_uses_common_refs_without_git_executable(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);work=root/'checkout';work.mkdir()
            common=root/'common';private=common/'worktrees/one';private.mkdir(parents=True)
            (work/'.git').write_text('gitdir: ../common/worktrees/one\n')
            (private/'HEAD').write_text('ref: refs/heads/test\n')
            (private/'commondir').write_text('../..\n')
            (common/'packed-refs').write_text('c'*40+' refs/heads/test\n')
            self.assertEqual(repository_revision(work),'c'*40)

    def test_missing_or_invalid_metadata_does_not_block_build(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertIsNone(repository_revision(root))
            git=root/'.git';git.mkdir()
            for value in ('not a revision','ref: ../../elsewhere','ref: refs/../bad','ref: refs/heads/missing'):
                (git/'HEAD').write_text(value)
                self.assertIsNone(repository_revision(root))
