"""构建快照与实际提交之间的检查，只使用临时本地 Git 仓库。"""
import tempfile
import unittest
from pathlib import Path
from lib import gitops, proc
from server import App


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='desk-snapshot-')
        self.repo = Path(self.temp.name)
        self.run_git('init', '-b', 'main')
        (self.repo / 'content').mkdir()
        self.path = self.repo / 'content/a.md'
        self.path.write_bytes(b'base\n')
        self.commit('初始')
        head = self.run_git('rev-parse', 'HEAD')
        self.path.write_bytes(b'verified\n')
        blob = self.run_git('hash-object', '--', 'content/a.md')
        self.snapshot = {'head':head, 'files':[{'path':'content/a.md', 'clean_blob_sha':blob}]}

    def tearDown(self):
        self.temp.cleanup()

    def run_git(self, *args):
        result = proc.run(['git', *args], cwd=str(self.repo), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        return result.stdout.decode('utf-8').strip()

    def commit(self, message):
        self.run_git('add', '--', 'content')
        self.run_git('-c', 'user.name=验收', '-c', 'user.email=test@example.test', 'commit', '-m', message)
        return self.run_git('rev-parse', 'HEAD')

    def test_verified_content_accepted(self):
        sha = self.commit('已校验内容')
        App(self.repo)._check_verified_commit(self.snapshot, sha)

    def test_changed_bytes_after_build_rejected(self):
        self.path.write_bytes(b'unverified late edit\n')
        sha = self.commit('模拟提交前变化')
        with self.assertRaises(gitops.GitError):
            App(self.repo)._check_verified_commit(self.snapshot, sha)
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), sha)

    def test_extra_file_rejected(self):
        (self.repo / 'content/extra.md').write_bytes(b'not verified')
        sha = self.commit('模拟新增文件')
        with self.assertRaises(gitops.GitError):
            App(self.repo)._check_verified_commit(self.snapshot, sha)

    def test_verified_change_cannot_be_omitted_from_commit(self):
        self.path.write_bytes(b'base\n')
        self.run_git('-c', 'user.name=验收', '-c', 'user.email=test@example.test',
                     'commit', '--allow-empty', '-m', '模拟已校验改动被撤回')
        sha = self.run_git('rev-parse', 'HEAD')
        with self.assertRaises(gitops.GitError):
            App(self.repo)._check_verified_commit(self.snapshot, sha)

    def test_new_parent_rejected_without_resetting_history(self):
        self.commit('模拟 AI 抢先提交')
        self.path.write_bytes(b'next\n')
        sha = self.commit('后续提交')
        with self.assertRaises(gitops.GitError):
            App(self.repo)._check_verified_commit(self.snapshot, sha)
        self.assertEqual(self.run_git('rev-parse', 'HEAD'), sha)


if __name__ == '__main__':
    unittest.main()
