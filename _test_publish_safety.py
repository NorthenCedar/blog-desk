# -*- coding: utf-8 -*-
"""发布安全回归：真实临时 Git/bare 远端；不出网、不读凭据、不动用户配置。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import gitops


class PublishSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="desk-publish-safety-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.root / "empty-global-config"),
            "GIT_TERMINAL_PROMPT": "0",
        })
        env.start()
        self.addCleanup(env.stop)
        # 发布不能再依赖 API、凭据读取或会改写 HEAD 的旧对齐函数。
        for name in ("get_token", "_gh", "_sync_with_remote"):
            patch = mock.patch.object(gitops, name, side_effect=AssertionError(
                f"普通 Git 发布不应调用 {name}"))
            patch.start()
            self.addCleanup(patch.stop)
        self.origin = self.root / "NorthenCedar" / "personal.git"
        self.origin.parent.mkdir()
        self.run_git(self.root, "init", "--bare", "-b", "main", str(self.origin))
        self.work = self.clone("work")
        self.write("content/posts/one/index.md", "base\n")
        self.write("src/app.py", "version = 1\n")
        self.commit("initial")
        self.run_git(self.work, "push", "origin", "main")
        self.initial = self.head()

    def run_git(self, cwd, *args, check=True):
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if check and p.returncode:
            self.fail(f"fixture git {' '.join(args)}: {p.stderr}")
        return p

    def clone(self, name):
        work = self.root / name
        self.run_git(self.root, "clone", self.origin.as_posix(), str(work))
        for key, value in (("user.name", "Offline test"),
                           ("user.email", "test@example.invalid"),
                           ("core.autocrlf", "false"),
                           ("rebase.autostash", "false")):
            self.run_git(work, "config", key, value)
        return work

    def write(self, path, text, work=None):
        p = (work or self.work) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, message, work=None):
        work = work or self.work
        paths = ['content', 'src']
        if (work / 'docs').is_dir():
            paths.append('docs')
        self.run_git(work, "add", '--', *paths)
        self.run_git(work, "commit", "-m", message)
        return self.head(work)

    def head(self, work=None):
        return self.run_git(work or self.work, "rev-parse", "HEAD").stdout.strip()

    def remote_head(self):
        return self.run_git(self.origin, "rev-parse", "refs/heads/main").stdout.strip()

    def preflight(self):
        self.assertTrue(callable(getattr(gitops, "preflight", None)),
                        "构建前需要公开 preflight(repo) 安全检查")
        return gitops.preflight(self.work)

    def test_tracked_content_edit_commits_without_stash_or_rebase(self):
        self.write("content/posts/one/index.md", "desk edit\n")
        try:
            result = gitops.commit_all(self.work, "desk tracked edit")
        except gitops.GitError as e:
            self.fail(f"正常已跟踪内容修改应该能提交：{e}")
        self.assertTrue(result["ok"])
        self.assertEqual(self.run_git(self.work, "show", "HEAD:content/posts/one/index.md").stdout,
                         "desk edit\n")
        self.assertEqual(self.run_git(self.work, "rev-parse", "HEAD^").stdout.strip(), self.initial)
        self.assertEqual(self.run_git(self.work, "status", "--porcelain").stdout, "")

    def test_unknown_untracked_source_is_not_swept_into_content_commit(self):
        self.write("content/posts/new/index.md", "desk new\n")
        self.write("src/ai-wip.py", "unfinished = True\n")
        with self.assertRaisesRegex(gitops.GitError, "src/ai-wip.py"):
            gitops.commit_all(self.work, "desk")
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.run_git(self.work, "diff", "--cached", "--name-only").stdout, "")

    def test_unknown_staged_source_blocks_preflight_and_commit(self):
        self.write("src/app.py", "version = 2\n")
        self.run_git(self.work, "add", "src/app.py")
        self.write("content/posts/one/index.md", "desk edit\n")
        before = self.run_git(self.work, "diff", "--cached").stdout
        with self.assertRaisesRegex(gitops.GitError, "src/app.py"):
            self.preflight()
        with self.assertRaisesRegex(gitops.GitError, "src/app.py"):
            gitops.commit_all(self.work, "desk")
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.run_git(self.work, "diff", "--cached").stdout, before)

    def test_remote_ahead_preserves_dirty_content_and_head(self):
        other = self.clone("other")
        self.write("content/remote.md", "remote first\n", other)
        remote = self.commit("remote ahead", other)
        self.run_git(other, "push", "origin", "main")
        self.write("content/posts/one/index.md", "desk dirty\n")
        with self.assertRaises(gitops.GitError):
            self.preflight()
        with self.assertRaises(gitops.GitError):
            gitops.commit_all(self.work, "must not commit")
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.remote_head(), remote)
        self.assertEqual((self.work / "content/posts/one/index.md").read_text(), "desk dirty\n")
        self.assertFalse((self.work / ".git/rebase-merge").exists())

    def test_divergence_is_rejected_without_replaying_local_commits(self):
        other = self.clone("other")
        self.write("content/local.md", "local\n")
        local = self.commit("local pending")
        self.write("content/remote.md", "remote\n", other)
        remote = self.commit("remote concurrent", other)
        self.run_git(other, "push", "origin", "main")
        self.write("content/posts/one/index.md", "dirty after local commit\n")
        with self.assertRaises(gitops.GitError):
            gitops.commit_all(self.work, "must not rebase")
        self.assertEqual(self.head(), local)
        self.assertEqual(self.remote_head(), remote)

    def test_normal_push_preserves_each_existing_commit_and_head(self):
        self.write("src/app.py", "version = 2\n")
        ai = self.commit("AI existing source commit")
        self.write("content/posts/one/index.md", "desk content\n")
        desk = self.commit("desk existing content commit")
        state = gitops.status(self.work)
        self.assertEqual(state["sync"], "ahead")
        self.assertEqual(state["pending"], 2)
        self.assertEqual(state["remote_err"], "")
        result = gitops.push(self.work)
        self.assertTrue(result["ok"])
        self.assertEqual(self.head(), desk)
        self.assertEqual(self.remote_head(), desk)
        self.assertEqual(self.run_git(self.origin, "rev-parse", "main^").stdout.strip(), ai)
        self.assertEqual(self.run_git(self.origin, "rev-list", "--count", "main").stdout.strip(), "3")
        self.assertEqual(gitops.compare(self.work)["mode"], "synced")

    def test_non_fast_forward_race_preserves_remote_ai_commit(self):
        other = self.clone("other")
        self.write("content/posts/one/index.md", "desk candidate\n")
        desk = self.commit("desk candidate")
        real_run = gitops.proc.run
        raced = []

        def advance_before_push(args, **kwargs):
            if "push" in args and not raced:
                self.write("content/remote.md", "AI remote concurrent\n", other)
                raced.append(self.commit("AI remote commit", other))
                self.run_git(other, "push", "origin", "main")
            return real_run(args, **kwargs)

        with mock.patch.object(gitops.proc, "run", side_effect=advance_before_push):
            with self.assertRaises(gitops.GitError):
                gitops.push(self.work)
        self.assertEqual(len(raced), 1, "race must occur at the real Git transport boundary")
        self.assertEqual(self.remote_head(), raced[0])
        self.assertEqual(self.head(), desk)

    def test_local_commit_during_push_keeps_its_history_and_pins_published_sha(self):
        self.write("content/posts/one/index.md", "verified snapshot\n")
        verified = self.commit("verified content")
        real_run = gitops.proc.run
        raced = []

        def commit_before_push(args, **kwargs):
            if "push" in args and not raced:
                self.write("src/app.py", "version = 3\n")
                raced.append(self.commit("AI local concurrent"))
            return real_run(args, **kwargs)

        with mock.patch.object(gitops.proc, "run", side_effect=commit_before_push):
            result = gitops.push(self.work)
        self.assertEqual(self.remote_head(), verified)
        self.assertEqual(self.head(), raced[0])
        self.assertEqual(self.run_git(self.work, "rev-parse", "HEAD^").stdout.strip(), verified)
        self.assertTrue(result["head_changed"])

    def test_content_change_during_staging_refuses_commit_and_keeps_new_bytes(self):
        self.write("content/posts/one/index.md", "checked content\n")
        real_run = gitops.proc.run
        raced = []

        def change_before_add(args, **kwargs):
            if "add" in args and not raced:
                raced.append(True)
                self.write("content/posts/one/index.md", "AI concurrent content\n")
            return real_run(args, **kwargs)

        with mock.patch.object(gitops.proc, "run", side_effect=change_before_add):
            with self.assertRaises(gitops.GitError):
                gitops.commit_all(self.work, "must refuse changed snapshot")
        self.assertEqual(raced, [True])
        self.assertEqual(self.head(), self.initial)
        self.assertEqual((self.work / "content/posts/one/index.md").read_text(),
                         "AI concurrent content\n")

    def test_foreign_staging_at_commit_boundary_is_not_included(self):
        self.write("content/posts/one/index.md", "desk checked content\n")
        real_run = gitops.proc.run
        raced = []

        def stage_before_commit(args, **kwargs):
            if "commit" in args and not raced:
                raced.append(True)
                self.write("src/app.py", "AI staged = True\n")
                self.run_git(self.work, "add", "src/app.py")
            return real_run(args, **kwargs)

        with mock.patch.object(gitops.proc, "run", side_effect=stage_before_commit):
            gitops.commit_all(self.work, "only desk content")
        self.assertEqual(self.run_git(self.work, "show", "HEAD:src/app.py").stdout, "version = 1\n")
        self.assertIn("src/app.py", self.run_git(self.work, "diff", "--cached", "--name-only").stdout)
        with self.assertRaisesRegex(gitops.GitError, "src/app.py"):
            gitops.push(self.work)

    def test_rename_from_source_into_content_is_still_rejected(self):
        self.run_git(self.work, "mv", "src/app.py", "content/copied-source.py")
        with self.assertRaisesRegex(gitops.GitError, "src/app.py"):
            self.preflight()
        self.assertEqual(self.head(), self.initial)

    def test_non_main_branch_cannot_publish_to_main(self):
        self.run_git(self.work, "switch", "-c", "ai-feature")
        with self.assertRaises(gitops.GitError):
            self.preflight()
        self.assertEqual(self.remote_head(), self.initial)

    def test_preflight_handles_unicode_spaces_deletions_and_allowed_content_files(self):
        self.write("content/posts/风景 一/index.md", "new\n")
        self.write("src/assets/talks/配图.bin", "image fixture\n")
        self.write("src/tag-registry.ts", "export const tags = [];\n")
        self.write("docs/content-model.md", "content model\n")
        self.run_git(self.work, "rm", "content/posts/one/index.md")
        state = self.preflight()
        self.assertTrue(state["ok"])
        self.assertIn("content/posts/风景 一/index.md", state["files"])
        gitops.commit_all(self.work, "allowed edits")
        self.assertEqual(self.run_git(self.work, "status", "--porcelain").stdout, "")
        self.assertNotIn("content/posts/one/index.md", self.run_git(
            self.work, "ls-tree", "-r", "--name-only", "HEAD").stdout)

    def test_network_commands_read_proxy_each_time_without_changing_git_config(self):
        self.assertTrue(callable(getattr(gitops, "_system_proxy", None)),
                        "网络命令需要动态读取系统代理")
        real_run = gitops.proc.run
        proxies = []

        def record_transport(args, **kwargs):
            if "fetch" in args or "push" in args:
                proxies.append(next((a for a in args if a.startswith("http.proxy=")), ""))
                self.assertNotIn("http.sslVerify=false", args)
            return real_run(args, **kwargs)

        config_before = (self.work / ".git/config").read_bytes()
        with mock.patch.object(gitops, "_system_proxy", side_effect=[
                "http://127.0.0.1:11111", "http://127.0.0.1:22222"]):
            with mock.patch.object(gitops.proc, "run", side_effect=record_transport):
                self.preflight()
                self.preflight()
        self.assertEqual(proxies, ["http.proxy=http://127.0.0.1:11111",
                                   "http.proxy=http://127.0.0.1:22222"])
        self.assertEqual((self.work / ".git/config").read_bytes(), config_before)

    def test_snapshot_blob_matches_real_commit_after_crlf_conversion(self):
        self.run_git(self.work, 'config', 'core.autocrlf', 'true')
        (self.work / 'content/posts/one/index.md').write_bytes('新内容\r\n第二行\r\n'.encode())
        binary = self.work / 'content/片段.mp4'
        binary.write_bytes(b'\0\xffftyp\r\n\x80')
        before = gitops.capture_publish_state(self.work)
        result = gitops.commit_all(self.work, '验证文本归一化与二进制')
        for item in before['files']:
            blob = self.run_git(self.work, 'rev-parse', 'HEAD:' + item['path']).stdout.strip()
            self.assertIn(blob, (item['raw_blob_sha'], item['clean_blob_sha']))
        from server import App
        App(self.work)._check_verified_commit(before, result['sha'])
        committed_binary = gitops.git_bin(self.work, 'show', 'HEAD:content/片段.mp4')
        self.assertEqual(committed_binary, binary.read_bytes())

    def test_unverified_new_head_is_not_published(self):
        self.write('content/posts/one/index.md', 'verified\n')
        verified = self.commit('verified')
        self.write('src/app.py', 'unverified source\n')
        current = self.commit('AI new local commit')
        with self.assertRaisesRegex(gitops.GitError, 'HEAD'):
            gitops.push(self.work, expected_head=verified)
        self.assertEqual(self.remote_head(), self.initial)
        self.assertEqual(self.head(), current)


if __name__ == "__main__":
    unittest.main(verbosity=2)
