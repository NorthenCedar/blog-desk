"""独立相册编辑：受限路径、保留式字段 patch、乐观版本及单册 Git 准备。"""
from __future__ import annotations
import copy
import datetime
import difflib
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
import yaml
from PIL import Image
from lib.forge import Forge, RESERVED, patch_fields, parse_fields
from lib.content import ContentError, ContentConflict, split_frontmatter
from lib import gitops, proc

FIELDS = ('title', 'titleEn', 'date', 'draft', 'images', 'layout', 'cover', 'presentation')
OPTIONS = dict(arrangement=['left','right','wide'], transition=['slide','reveal','perspective'],
               stroke=['arc','loop','corner'], weight=['regular','bold'])
ALBUM_LAYOUTS = ('scatter', 'slices', 'dome', 'infinite', 'wall', 'willow')

def preserve_patch(original, fields):
    """复用 Forge YAML patch；仅变化行使用邻近行尾，其余行及正文原字节保留。"""
    normalized = original.replace('\r\n', '\n')
    rendered = patch_fields(normalized, fields, FIELDS)
    if fields.get('cover') == '':
        # 空册没有封面字段；先由 Forge 安全改成单行，再移除该已知行。
        frontmatter,body=split_frontmatter(rendered)
        document=yaml.compose(frontmatter)
        spans=[(key.start_mark.index,value.end_mark.index) for key,value in document.value if key.value=='cover']
        for start,end in sorted(spans,reverse=True):
            newline=frontmatter.find('\n',end)
            if newline>=0: end=newline+1
            else:
                end=len(frontmatter)
                if start>0 and frontmatter[start-1]=='\n': start-=1
            frontmatter=frontmatter[:start]+frontmatter[end:]
        rendered='---\n'+frontmatter+'\n---\n'+body
    old, new = original.splitlines(keepends=True), rendered.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=normalized.splitlines(keepends=True), b=new, autojunk=False)
    out = []
    default = '\r\n' if '\r\n' in original else '\n'
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == 'equal': out.extend(old[a:b])
        else:
            nl = ('\r\n' if old[a].endswith('\r\n') else '\n') if a < len(old) else default
            out.extend(line.rstrip('\n') + (nl if line.endswith('\n') else '') for line in new[c:d])
    return ''.join(out)

class Gallery(Forge):
    def __init__(self, repo):
        super().__init__(repo)
        self._lock = threading.RLock()

    def album_dir(self, slug):
        if (not isinstance(slug,str) or not slug or len(slug)>100
                or slug in ('.','..') or slug.strip()!=slug or slug.startswith('.') or slug.endswith((' ','.'))
                or not re.fullmatch(r'[\w -]+',slug) or not any(char.isalnum() for char in slug)
                or slug.lower() in ('spiral','index') or slug.split('.')[0].lower() in RESERVED):
            raise ContentError('相册标识不能含路径、系统保留名或末尾空格/点')
        return self._inside(self.repo/'content/gallery'/slug)

    project_dir = album_dir

    def upload(self,slug,name,data,max_kb=300):
        from lib.images import real_ext
        if real_ext(data) not in ('.jpg','.jpeg','.png','.webp','.avif'):
            raise ContentError('相册支持 JPEG、PNG、WebP、AVIF 图片')
        with self._lock:
            result=super().upload(slug,name,data,max_kb)
            with Image.open(self.asset_path(slug,result['rel'])) as image: result['width'],result['height']=image.size
            return result

    def read_album(self, slug):
        value=self._read(self._inside(self.album_dir(slug)/'index.md'), slug)
        value['presentationIndex']=self._legacy_index(slug)
        value['imageSizes']={}
        for photo in value['frontmatter'].get('images',[]):
            with Image.open(self.asset_path(slug,photo['src'])) as image:
                value['imageSizes'][photo['src']]=dict(width=image.width,height=image.height)
        return value

    def _legacy_index(self,slug):
        root=self._inside(self.repo/'content/gallery')
        published=[]
        if root.is_dir():
            for directory in root.iterdir():
                if directory.is_dir() and (directory/'index.md').is_file():
                    path=self._inside(self.album_dir(directory.name)/'index.md')
                    fm=self._read(path)['frontmatter']
                    if fm.get('draft') is not True: published.append((directory.name,str(fm.get('date',''))))
        published.sort(key=lambda item:item[0])
        published.sort(key=lambda item:item[1],reverse=True)
        return next((index for index,item in enumerate(published) if item[0]==slug),0)

    def list_albums(self):
        root = self._inside(self.repo/'content/gallery')
        result=[]
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir() and (path/'index.md').is_file():
                    value=self.read_album(path.name)
                    result.append(dict(**value['frontmatter'],slug=path.name))
        return sorted(result,key=lambda x:x['slug'])

    def recommend(self, slug, fields, index=None):
        width=height=0
        src=fields.get('cover') or next((x.get('src') for x in fields.get('images',[]) if isinstance(x,dict)),None)
        if src:
            with Image.open(self.asset_path(slug,src)) as image: width,height=image.size
        # 与站点纯函数保持一致；跨语言对照回归见 _test_gallery.py。
        if index is None: index=self._legacy_index(slug)
        return dict(arrangement='wide' if width>0 and height>0 and width/height>1.2 else ('right' if index%2 else 'left'),
                    transition='slide', stroke='arc', weight='bold')

    def validate(self,slug,fields,publish=False):
        if fields.get('layout','scatter') not in ALBUM_LAYOUTS: raise ContentError('请选择已有相册布局')
        if 'draft' in fields and type(fields['draft']) is not bool: raise ContentError('draft 必须为布尔值')
        for key in ('title','titleEn','date','cover'):
            if key in fields and not isinstance(fields[key],str): raise ContentError(f'{key} 必须是文字')
        presentation=fields.get('presentation',{})
        if not isinstance(presentation,dict): raise ContentError('编排字段无效')
        for key, choices in OPTIONS.items():
            if key in presentation and presentation[key] not in choices: raise ContentError('编排选项无效：'+key)
        photos=fields.get('images',[])
        if not isinstance(photos,list): raise ContentError('图片须为列表')
        seen=set()
        for item in photos:
            if not isinstance(item,dict) or not isinstance(item.get('src'),str): raise ContentError('图片引用无效')
            # 草稿允许暂缺图注，不接受跨册路径或失效图片引用。
            if Path(item['src']).suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.avif'):
                raise ContentError('相册图片格式不受支持')
            self.read_image(slug,item['src'])
            if item['src'] in seen: raise ContentError('相册存在重复图源')
            seen.add(item['src'])
            if 'alt' in item and not isinstance(item['alt'],str): raise ContentError('图注必须是文字')
            if len(item.get('alt','').encode('utf-16-le','surrogatepass'))//2>2048: raise ContentError('图注最多 2048 字')
            if publish and not item.get('alt','').strip(): raise ContentError('发布前请补全每张图片的图注')
        if fields.get('cover') and fields['cover'] not in [x['src'] for x in photos]: raise ContentError('封面必须属于本册图片')
        if publish:
            if not str(fields.get('title','')).strip(): raise ContentError('发布前请填写标题')
            if len(fields['title'].encode('utf-16-le','surrogatepass'))//2>200: raise ContentError('相册名称最多 200 字')
            try:
                date=fields.get('date','')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): raise ValueError()
                datetime.date.fromisoformat(date)
            except (ValueError,TypeError): raise ContentError('发布前请填写有效日期 YYYY-MM-DD')
            if not photos: raise ContentError('发布至少需要一张图片')

    def save_album(self,slug,fields,*,base_revision=None,create=False):
        with self._lock:
            path=self._inside(self.album_dir(slug)/'index.md')
            if not isinstance(fields,dict): raise ContentError('相册字段无效')
            if create:
                if path.exists(): raise ContentConflict('同名相册已存在，没有覆盖')
                original='---\n{}\n---\n'
                old={}
            else:
                self._revision(base_revision)
                self.content._check_revision(path,base_revision)
                original=path.read_bytes().decode('utf-8')
                old=parse_fields(split_frontmatter(original)[0])
            fm={**old,**{k:copy.deepcopy(v) for k,v in fields.items() if k in FIELDS}}
            if isinstance(fm.get('images'),list) and isinstance(old.get('images'),list):
                previous={item.get('src'):item for item in old['images'] if isinstance(item,dict) and isinstance(item.get('src'),str)}
                fm['images']=[{**previous.get(item.get('src'),{}),**item} if isinstance(item,dict) else item for item in fm['images']]
            if ('images' in fields and 'cover' not in fields and fm.get('cover')
                    and fm['cover'] not in [item.get('src') for item in fm.get('images',[]) if isinstance(item,dict)]):
                fm['cover']=''
            if create: fm.setdefault('draft',True)
            if isinstance(old.get('presentation'),dict) and isinstance(fm.get('presentation'),dict):
                fm['presentation']={**old['presentation'],**fm['presentation']}
            fm.setdefault('images',[])
            self.validate(slug,fm)
            # 固定封面和推荐只发生在写操作；显式完整编排不重新计算。
            if not fm.get('cover') and fm['images']: fm['cover']=fm['images'][0]['src']
            if not fm.get('presentation') and fm['images']:
                fm['presentation']=self.recommend(slug,fm,index=0 if create else None)
            raw=preserve_patch(original,fm).encode('utf-8')
            path.parent.mkdir(parents=True,exist_ok=True)
            if create:
                try:
                    with path.open('xb') as stream: stream.write(raw)
                except FileExistsError as e: raise ContentConflict('同名相册已存在') from e
            else:
                fd,name=tempfile.mkstemp(prefix='.gallery-',suffix='.tmp',dir=path.parent)
                pending=Path(name)
                try:
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    self.content._check_revision(path,base_revision)
                    os.replace(pending,path)
                finally: pending.unlink(missing_ok=True)
            return self._value(raw,path,slug)

    def prepare_publish(self,slug,base_revision):
        """只写不可变 Git 对象；不提交、推送、改真实索引或本地 draft。"""
        with self._lock:
            self._revision(base_revision)
            path=self._inside(self.album_dir(slug)/'index.md')
            self.content._check_revision(path,base_revision)
            original=path.read_bytes().decode('utf-8')
            fields=parse_fields(split_frontmatter(original)[0])
            self.validate(slug,fields,publish=True)
            prefix='content/gallery/'+slug+'/'
            blobs={prefix+'index.md':preserve_patch(original,dict(fields,draft=False)).encode('utf-8')}
            for photo in fields['images']: blobs[prefix+photo['src'][2:]]=self.read_image(slug,photo['src'])[0]
            base=gitops.git(self.repo,'rev-parse','HEAD')
            # 临时索引从 HEAD 创建，只覆盖正文与引用图。
            # 已入库但本次解除引用的原图保留 HEAD 原字节；未引用的新文件不纳入。
            with tempfile.TemporaryDirectory(prefix='gallery-index-') as temporary:
                env=dict(os.environ,GIT_INDEX_FILE=str(Path(temporary)/'index'))
                def run(*args,input=None):
                    result=proc.run(['git',*args],cwd=str(self.repo),env=env,input=input,capture_output=True)
                    if result.returncode: raise gitops.GitError('准备本册失败：'+result.stderr.decode('utf-8','replace')[:300])
                    return result.stdout
                run('read-tree',base)
                for name,data in blobs.items():
                    oid=run('hash-object','-w','--path='+name,'--stdin',input=data).decode().strip()
                    run('update-index','--add','--cacheinfo','100644',oid,name)
                tree=run('write-tree').decode().strip()
            self.content._check_revision(path,base_revision)
            if gitops.git(self.repo,'rev-parse','HEAD')!=base: raise ContentConflict('准备期间 HEAD 已变化，请重新准备')
            return dict(ok=True,slug=slug,base=base,tree=tree,revision=base_revision,paths=sorted(blobs),
                        message='本册发布快照已准备，未提交或推送；其他未提交内容未包含。')
