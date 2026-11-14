"""相册回归只在 work 下临时仓库操作，不访问用户博客。"""
import copy
import importlib.util
import io
import tempfile
import unittest
import json
import base64
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path
from PIL import Image
from lib.content import ContentError, ContentConflict
from lib import gitops
from unittest.mock import patch
import importlib.util
import subprocess

Gallery = None
if importlib.util.find_spec('lib.gallery'):
    from lib.gallery import Gallery

class GalleryTests(unittest.TestCase):
    def test_shared_preview_and_recommendation_parity(self):
        spec=importlib.util.spec_from_file_location('sync_album_preview',Path(__file__).parent/'sync-album-preview.py')
        sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
        source=Path(__file__).resolve().parents[1] / 'blog'
        ui=Path(__file__).parent/'ui'
        if (source / '.git').exists():
            # 封面预览来自 521b9a9，布局列表随当前主站同步。
            for relative in sync.PREVIEW_FILES:
                if relative == 'lib/album-presentation.mjs':
                    expected = (source/'src'/relative).read_text(encoding='utf-8')
                else:
                    expected = gitops.git_bin(source, 'show', f'521b9a9:src/{relative}').decode('utf-8').replace('\r\n', '\n')
                self.assertEqual(expected, (ui/'album-shared'/relative).read_text(encoding='utf-8'), relative)
            for name in sync.FONT_FILES:
                self.assertEqual(gitops.git_bin(source, 'show', f'521b9a9:public/fonts/{name}'),
                                 (ui/'fonts'/name).read_bytes(), name)
        fields=self.fields()
        self.gallery.save_album('中文相册',fields,create=True)
        module=(ui/'album-shared/lib/album-presentation.mjs').resolve().as_uri()
        code=f"import {{recommendPresentation,ALBUM_OPTIONS}} from '{module}'; console.log(JSON.stringify({{recommend:recommendPresentation({{width:20,height:10,title:'旧日'}}),options:ALBUM_OPTIONS}}));"
        result=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True,encoding='utf-8',check=True)
        from lib.gallery import ALBUM_LAYOUTS, OPTIONS
        parsed=json.loads(result.stdout)
        self.assertEqual(self.gallery.recommend('中文相册',fields),parsed['recommend'])
        for key,values in OPTIONS.items():self.assertEqual(values,[x[0] for x in parsed['options'][key] if x[0]!='auto'])
        self.assertEqual(list(ALBUM_LAYOUTS), [x[0] for x in parsed['options']['layout']])

    def test_layout_roundtrip_keeps_old_and_new_options(self):
        saved = self.gallery.save_album('中文相册', self.fields(), create=True)
        for layout in ('dome', 'infinite', 'scatter', 'slices', 'wall', 'willow'):
            with self.subTest(layout=layout):
                saved = self.gallery.save_album('中文相册', {'layout': layout}, base_revision=saved['revision'])
                self.assertEqual(self.gallery.read_album('中文相册')['frontmatter']['layout'], layout)
    def setUp(self):
        self.assertIsNotNone(Gallery, '尚无独立相册服务')
        root = Path('D:/router/work/gallery-writer-20261101/desktop-tests')
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.gallery = Gallery(self.repo)

    def fields(self):
        data = io.BytesIO()
        Image.new('RGB', (20, 10), 'green').save(data, 'PNG')
        src = self.gallery.upload('中文相册', '图.png', data.getvalue())['rel']
        return dict(title='旧日', date='2026-11-01', draft=True, layout='wall', images=[dict(src=src, alt='绿')])

    def test_empty_draft_and_publish_validation(self):
        saved = self.gallery.save_album('空册', {}, create=True)
        self.assertTrue(saved['frontmatter']['draft'])
        with self.assertRaises(ContentError): self.gallery.prepare_publish('空册', saved['revision'])
        for slug in ['../x','x/y','x\\y','spiral','index','CON','nul.txt','a.','a ','.git']:
            with self.subTest(slug=slug), self.assertRaises(ContentError): self.gallery.album_dir(slug)

    def test_empty_draft_waits_for_first_real_cover_before_pin(self):
        empty=self.gallery.save_album('中文相册',{},create=True)
        self.assertFalse(empty['frontmatter'].get('presentation'))
        fields=self.fields()
        saved=self.gallery.save_album('中文相册',{'images':fields['images']},base_revision=empty['revision'])
        self.assertEqual(saved['frontmatter']['presentation']['arrangement'],'wide')
        self.assertEqual(saved['frontmatter']['cover'],fields['images'][0]['src'])
        chosen=dict(arrangement='right',transition='reveal',stroke='loop',weight='regular')
        manual=self.gallery.save_album('手动空册',{'presentation':chosen},create=True)
        self.assertEqual(manual['frontmatter']['presentation'],chosen)

    def test_legacy_first_save_preserves_second_portrait_right_and_wide(self):
        for slug,date,size in [('a','2026-11-03',(10,20)),('b','2026-11-02',(10,20)),('c','2026-11-01',(30,10))]:
            data=io.BytesIO();Image.new('RGB',size,'green').save(data,'PNG')
            self.gallery.upload(slug,'photo.png',data.getvalue())
            path=self.gallery.album_dir(slug)/'index.md'
            path.write_text(f'---\ntitle: old\ndate: {date}\nimages:\n- src: ./photo.png\n  alt: old\n---\nbody\n',encoding='utf-8')
        for slug,index,expected in [('b',1,'right'),('c',2,'wide')]:
            path=self.gallery.album_dir(slug)/'index.md';raw=path.read_bytes()
            value=self.gallery.read_album(slug)
            self.assertEqual(value['presentationIndex'],index)
            self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(value['imageSizes']['./photo.png']['width'],10 if slug=='b' else 30)
            saved=self.gallery.save_album(slug,{'title':'changed'},base_revision=value['revision'])
            self.assertEqual(saved['frontmatter']['presentation']['arrangement'],expected)
            self.assertNotIn('imageSizes',saved['frontmatter'])

    def test_pinned_cover_reorder_unknown_and_conflict(self):
        fields = self.fields()
        saved = self.gallery.save_album('中文相册', fields, create=True)
        fixed = copy.deepcopy(saved['frontmatter']['presentation'])
        self.assertEqual(saved['frontmatter']['cover'], fields['images'][0]['src'])
        fields = copy.deepcopy(saved['frontmatter'])
        data=io.BytesIO(); Image.new('RGB',(10,20),'blue').save(data,'PNG')
        src=self.gallery.upload('中文相册','second.png',data.getvalue())['rel']
        fields['images'].append(dict(src=src, alt='另一张', custom='keep'))
        fields['images'].reverse()
        saved2 = self.gallery.save_album('中文相册', fields, base_revision=saved['revision'])
        self.assertEqual(saved2['frontmatter']['presentation'], fixed)
        self.assertEqual(saved2['frontmatter']['images'][0]['custom'], 'keep')
        patch_images=copy.deepcopy(fields['images']);patch_images[0].pop('custom')
        keep=self.gallery.save_album('中文相册',{'images':patch_images},base_revision=saved2['revision'])
        self.assertEqual(keep['frontmatter']['images'][0]['custom'],'keep')
        saved2=keep
        duplicate=copy.deepcopy(fields); duplicate['images'].append(copy.deepcopy(fields['images'][0]))
        with self.assertRaises(ContentError): self.gallery.save_album('中文相册',duplicate,base_revision=saved2['revision'])
        with self.assertRaises(ContentConflict): self.gallery.save_album('中文相册', fields, base_revision=saved['revision'])
        fields['images'] = []
        del fields['cover']
        empty=self.gallery.save_album('中文相册', fields, base_revision=saved2['revision'])
        self.assertNotIn('cover',empty['frontmatter'])
        self.assertTrue((self.gallery.album_dir('中文相册') / '图.png').is_file())

    def test_mixed_line_endings_body_unknown_preserved(self):
        fields = self.fields()
        self.gallery.save_album('中文相册', fields, create=True)
        p = self.gallery.album_dir('中文相册') / 'index.md'
        raw = b'---\r\ntitle: old\nunknown: "opaque" # keep\r\ndraft: true\nimages: []\r\npresentation: {arrangement: left, transition: slide, stroke: arc, weight: regular}\n---\r\nbody\nlast\r\n'
        p.write_bytes(raw)
        saved = self.gallery.read_album('中文相册')
        self.gallery.save_album('中文相册', {'title':'new'}, base_revision=saved['revision'])
        self.assertEqual(p.read_bytes(), raw.replace(b'title: old', b'title: new'))

    def test_removing_final_photo_keeps_body_cover_lines_and_code_bytes(self):
        saved=self.gallery.save_album('中文相册',self.fields(),create=True)
        path=self.gallery.album_dir('中文相册')/'index.md'
        body=b"Example:\r\ncover: ''\nend\r\n\n```yaml\r\ncover: ''\n```\r\n"
        raw=path.read_bytes().replace(b'\n---\n',b'\r\n---\r\n',1)+body;path.write_bytes(raw)
        before=self.gallery.read_album('中文相册')
        result=self.gallery.save_album('中文相册',{'images':[]},base_revision=before['revision'])
        self.assertNotIn('cover',result['frontmatter'])
        self.assertEqual(result['body'].encode('utf-8'),body)
        self.assertTrue(path.read_bytes().endswith(b'---\r\n'+body))

    def test_real_git_scoped_snapshot_keeps_index_head_other_content(self):
        gitops.git(self.repo,'init','-b','main')
        gitops.git(self.repo,'config','user.name','Gallery test')
        gitops.git(self.repo,'config','user.email','gallery@example.invalid')
        (self.repo/'other.md').write_text('original')
        gitops.git(self.repo,'add','.')
        gitops.git(self.repo,'commit','-m','fixture')
        head = gitops.git(self.repo,'rev-parse','HEAD')
        (self.repo/'other.md').write_text('unrelated staged change')
        gitops.git(self.repo,'add','other.md')
        index = gitops.git_bin(self.repo,'ls-files','--stage','-z')
        fields = self.fields()
        saved = self.gallery.save_album('中文相册',fields,create=True)
        (self.repo/'content/gallery/中文相册/orphan.png').write_bytes(b'not published')
        snapshot = self.gallery.prepare_publish('中文相册',saved['revision'])
        self.assertEqual(gitops.git(self.repo,'rev-parse','HEAD'),head)
        self.assertEqual(gitops.git_bin(self.repo,'ls-files','--stage','-z'),index)
        self.assertEqual(gitops.git_bin(self.repo,'show',snapshot['tree']+':other.md'),b'original')
        text = gitops.git_bin(self.repo,'show',snapshot['tree']+':content/gallery/中文相册/index.md').decode()
        self.assertIn('draft: false',text)
        self.assertTrue(self.gallery.read_album('中文相册')['frontmatter']['draft'])
        names=gitops.git(self.repo,'ls-tree','-r','--name-only',snapshot['tree'])
        self.assertNotIn('orphan.png',names)
        self.assertEqual(snapshot['paths'],['content/gallery/中文相册/index.md','content/gallery/中文相册/图.png'])

    def test_real_git_publish_only_album_ordinary_push_and_hooks(self):
        gitops.git(self.repo,'init','-b','main')
        gitops.git(self.repo,'config','user.name','Gallery test')
        gitops.git(self.repo,'config','user.email','gallery@example.invalid')
        other=self.repo/'content/posts/other/index.md';other.parent.mkdir(parents=True)
        other.write_text('original')
        gitops.git(self.repo,'add','.')
        gitops.git(self.repo,'commit','-m','fixture')
        remote=self.repo/'remote/NorthenCedar/personal.git';remote.mkdir(parents=True)
        gitops.git(remote,'init','--bare','-b','main')
        gitops.git(self.repo,'remote','add','origin',str(remote))
        gitops.git(self.repo,'push','origin','main')
        head=gitops.git(self.repo,'rev-parse','HEAD')
        original=self.repo/'content/gallery/中文相册/historical.png';original.parent.mkdir(parents=True)
        original.write_bytes(b'historical original remains')
        gitops.git(self.repo,'add',str(original));gitops.git(self.repo,'commit','-m','historical original')
        gitops.git(self.repo,'push','origin','main')
        head=gitops.git(self.repo,'rev-parse','HEAD')
        other.write_text('unrelated staged draft');gitops.git(self.repo,'add',str(other))
        index=gitops.git_bin(self.repo,'ls-files','--stage','-z')
        saved=self.gallery.save_album('中文相册',self.fields(),create=True)
        snapshot=self.gallery.prepare_publish('中文相册',saved['revision'])
        hook=self.repo/'.git/hooks/pre-commit'
        marker=self.repo/'.git/gallery-hook-fired'
        hook.write_text('#!/bin/sh\necho yes > "'+marker.as_posix()+'"\n',encoding='utf-8')
        checked=[]
        def verify(folder):
            checked.append(gitops.git(folder,'write-tree'))
            self.assertEqual((folder/'content/posts/other/index.md').read_text(),'original')
            return {'ok':True}
        with self.assertRaises(gitops.GitError):
            gitops.publish_album(self.repo,snapshot,verify=lambda folder:{'ok':False})
        self.assertEqual(gitops.git(remote,'rev-parse','main'),head)
        def tamper(folder):
            (folder/'content/posts/other/index.md').write_text('different validated source')
            return {'ok':True}
        with self.assertRaises(gitops.GitError):gitops.publish_album(self.repo,snapshot,verify=tamper)
        self.assertEqual(gitops.git(remote,'rev-parse','main'),head)
        result=gitops.publish_album(self.repo,snapshot,verify=verify)
        self.assertTrue(result['ok']);self.assertTrue(marker.is_file())
        self.assertEqual(checked,[snapshot['tree']])
        self.assertEqual(gitops.git(self.repo,'rev-parse','HEAD'),head)
        self.assertEqual(gitops.git_bin(self.repo,'ls-files','--stage','-z'),index)
        self.assertEqual(other.read_text(),'unrelated staged draft')
        self.assertEqual(gitops.git(remote,'rev-parse','main'),result['sha'])
        self.assertEqual(gitops.git(remote,'rev-parse','main^{tree}'),snapshot['tree'])
        self.assertEqual(gitops.git(remote,'rev-parse','main^'),head)
        self.assertEqual(gitops.git_bin(remote,'show','main:content/gallery/中文相册/historical.png'),b'historical original remains')

    def test_scoped_snapshot_uses_git_clean_filters_without_rewriting_local_crlf(self):
        gitops.git(self.repo,'init','-b','main')
        gitops.git(self.repo,'config','user.name','Gallery test')
        gitops.git(self.repo,'config','user.email','gallery@example.invalid')
        gitops.git(self.repo,'config','core.autocrlf','true')
        gitops.git(self.repo,'commit','--allow-empty','-m','fixture')
        saved=self.gallery.save_album('中文相册',self.fields(),create=True)
        path=self.gallery.album_dir('中文相册')/'index.md'
        raw=path.read_bytes().replace(b'\n',b'\r\n');path.write_bytes(raw)
        current=self.gallery.read_album('中文相册')
        snapshot=self.gallery.prepare_publish('中文相册',current['revision'])
        published=gitops.git_bin(self.repo,'show',snapshot['tree']+':content/gallery/中文相册/index.md')
        self.assertNotIn(b'\r\n',published)
        self.assertIn(b'draft: false',published)
        self.assertEqual(path.read_bytes(),raw)

    def test_paths_missing_images_and_incomplete_publish(self):
        fields=self.fields()
        for src in ['../outside.png','./../outside.png','/tmp/x.png','./missing.png']:
            bad=copy.deepcopy(fields);bad['images'][0]['src']=src
            with self.assertRaises(ContentError): self.gallery.save_album('中文相册',bad,create=True)
        for change in [{'title':''},{'date':'2026-02-30'},{'images':[]},{'cover':'./no.png'}]:
            bad={**fields,**change}
            with self.assertRaises(ContentError):self.gallery.validate('中文相册',bad,publish=True)
        fields['images'][0]['alt']=''
        self.gallery.validate('中文相册',fields)
        with self.assertRaises(ContentError):self.gallery.validate('中文相册',fields,publish=True)

    def test_http_album_upload_read_save_conflict_and_image_scope(self):
        import server
        app=server.App(self.repo)
        handler=type('GalleryHandler',(server.Handler,),{'app':app})
        http=ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
        origin='http://127.0.0.1:'+str(http.server_port)
        def post(path,payload):
            request=Request(origin+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            with urlopen(request) as response:return json.load(response)
        saved=post('/api/album',dict(slug='demo',create=True,frontmatter={}))
        data=io.BytesIO();Image.new('RGB',(10,20),'green').save(data,'PNG')
        photo=post('/api/album/upload',dict(slug='demo',name='image.png',data=base64.b64encode(data.getvalue()).decode()))
        saved['frontmatter']['images']=[dict(src=photo['rel'],alt='')]
        saved2=post('/api/album',dict(slug='demo',frontmatter=saved['frontmatter'],base_revision=saved['revision']))
        with urlopen(origin+'/api/album/image?slug=demo&src=./image.png') as response:self.assertEqual(response.read(),data.getvalue())
        with self.assertRaises(HTTPError) as conflict:post('/api/album',dict(slug='demo',frontmatter={},base_revision=saved['revision']))
        self.assertEqual(conflict.exception.code,409)
        conflict.exception.close()
        with self.assertRaises(HTTPError) as traversal:urlopen(origin+'/api/album/image?slug=demo&src=../x.png')
        self.assertEqual(traversal.exception.code,400)
        traversal.exception.close()
        with urlopen(origin+'/api/albums') as response:self.assertEqual(json.load(response)['items'][0]['slug'],'demo')

if __name__ == '__main__': unittest.main()
