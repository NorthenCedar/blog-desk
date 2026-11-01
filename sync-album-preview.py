"""从站点同步受审计的相册预览模块。仅复制固定清单，不读取任意用户路径。"""
import argparse
import shutil
from pathlib import Path

PREVIEW_FILES = ('scripts/album-cover-preview.js','scripts/gallery-cover-motion.mjs',
    'lib/album-presentation.mjs','lib/album-cover-art.mjs','styles/album-cover-preview.css',
    'styles/gallery-stage.css','styles/tokens.css','styles/fonts.css')
FONT_FILES = ('archivo-latin.woff2','plex-sans-400.woff2','plex-sans-600.woff2',
              'plex-mono-400.woff2','homemade-apple.woff2')

def sync(source):
    source=Path(source).resolve()
    ui=Path(__file__).resolve().parent/'ui'
    for relative in PREVIEW_FILES:
        target=ui/'album-shared'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/'src'/relative,target)
    for name in FONT_FILES:
        shutil.copyfile(source/'public/fonts'/name,ui/'fonts'/name)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('site')
    sync(parser.parse_args().site)
