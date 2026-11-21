#!/usr/bin/env node
/**
 * 把云端写作台的可视化正文编辑器打包给桌面端。
 *
 * 为什么要打包：桌面写作台是 Python + 原生 JS，不能直接 import TypeScript，
 * 而正文编辑器牵着 markdown-it + prosemirror 一大串依赖，逐文件抄进 ui/ 会
 * 立刻分叉。这里用 blog 仓库里已有的 esbuild 打成单文件 IIFE，产物落到
 * ui/vendor/writer-body.js，并写一份 ui/vendor/manifest.json（把 sha256 钉住，
 * 手改产物会被 _test_writer_bundle.py 抓到）。
 *
 * 用法（在写作台仓库根目录下）：
 *   node _tools/bundle-writer.mjs
 * 博客仓库改了 src/scripts/writer/* 之后要重跑一次，再重打包应用。
 */
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const deskRoot = resolve(here, '..');
const blogRoot = resolve(process.env.BLOG_REPO || join(deskRoot, '..', 'blog'));
const requireFromBlog = createRequire(join(blogRoot, 'package.json'));
const esbuild = requireFromBlog('esbuild');

const outFile = join(deskRoot, 'ui', 'vendor', 'writer-body.js');
mkdirSync(dirname(outFile), { recursive: true });

const result = await esbuild.build({
  entryPoints: [join(blogRoot, 'src', 'scripts', 'writer', 'desk.ts')],
  bundle: true,
  format: 'iife',
  globalName: 'WriterDesk',
  platform: 'browser',
  target: ['chrome110'],
  legalComments: 'none',
  // 保留可读性：出问题时站主能直接在 DevTools 里看到真实函数名。
  minify: false,
  write: false,
  logLevel: 'warning',
});

const code = result.outputFiles[0].contents;
writeFileSync(outFile, code);
const sha256 = createHash('sha256').update(code).digest('hex');
writeFileSync(
  join(deskRoot, 'ui', 'vendor', 'manifest.json'),
  JSON.stringify({ file: 'writer-body.js', bytes: code.byteLength, sha256 }, null, 2) + '\n',
  'utf8',
);
console.log(`writer-body.js ${code.byteLength} bytes  sha256=${sha256}`);
