/* 独立相册会话。文件身份与图项对象保持稳定，晚到的上传不会进入另一本册。 */
(function(root){
  'use strict';
  const clone=x=>JSON.parse(JSON.stringify(x));
  function removeImage(fields,photo){
    const index=fields.images.indexOf(photo);if(index<0)return;
    fields.images.splice(index,1);
    if(fields.cover===photo.src){if(fields.images.length)fields.cover=fields.images[0].src;else delete fields.cover}
  }
  function createSession(post){
    let current=null,version=0,dirty=false,saving=false;
    return {
      get current(){return current},get dirty(){return dirty},get saving(){return saving},
      get version(){return version},
      open(value){current=clone(value);version++;dirty=false},
      edit(){version++;dirty=true},
      async save(){
        if(saving)throw Error('正在保存，请稍候');
        const target=current,revision=version;
        if(!target)return null;
        saving=true;
        try{
          const saved=await post('/api/album',clone({slug:target.slug,frontmatter:target.frontmatter,
            create:!!target.create,base_revision:target.revision}));
          if(current===target){
            target.revision=saved.revision;target.create=false;
            if(version===revision){Object.assign(target.frontmatter,saved.frontmatter);dirty=false}
            else {
              if(!target.frontmatter.cover&&saved.frontmatter.cover&&target.frontmatter.images.some(x=>x.src===saved.frontmatter.cover))target.frontmatter.cover=saved.frontmatter.cover;
              if(!target.frontmatter.presentation&&saved.frontmatter.presentation)target.frontmatter.presentation=saved.frontmatter.presentation;
            }
          }
          return saved;
        }finally{saving=false}
      },
      async upload(file,read){
        const target=current;
        const data=await read(file);
        const result=await post('/api/album/upload',{slug:target.slug,name:file.name,data});
        if(current!==target)return false;
        target.frontmatter.images.push({src:result.rel,alt:''});
        target.imageSizes??={};target.imageSizes[result.rel]={width:result.width,height:result.height};
        version++;dirty=true;return true;
      }
    }
  }
  function create({GET,POST,esc,toast,askForm,askConfirm,pickFiles,fileToDataURL}){
    const session=createSession(POST),$=s=>document.querySelector(s);
    let initialized=false,loadToken=0,previewToken=0,preview=null,thumbnails=[],module=null,config=null,prepared=null;
    const safe=fn=>async()=>{try{await fn()}catch(error){toast(error.message,true)}};
    const fields=()=>session.current.frontmatter;
    const previewFields=()=>({...fields(),images:fields().images.map(image=>({...image,...session.current.imageSizes?.[image.src]}))});
    const imageURL=src=>'/api/album/image?slug='+encodeURIComponent(session.current.slug)+'&src='+encodeURIComponent(src);
    const status=message=>{$('#album-status').textContent=message};
    function changed(){session.edit();prepared=null;status('有未保存的修改');preview?.update(previewFields());thumbnails.forEach(x=>x.controller.update({...previewFields(),presentation:x.preset}))}
    async function canSwitch(){return !session.dirty||await askConfirm('切换相册','有未保存的修改，继续会放弃当前输入。',{okText:'放弃并继续'})}
    function dispose(){previewToken++;preview?.destroy();preview=null;thumbnails.forEach(x=>x.controller.destroy());thumbnails=[]}
    async function list(){
      const result=await GET('/api/albums');
      const box=$('#album-list');box.replaceChildren();
      for(const album of result.items){
        const button=document.createElement('button');button.className='btn';button.textContent=(album.draft?'草稿 · ':'')+(album.title||album.slug);
        button.onclick=safe(()=>open(album.slug));box.append(button);
      }
    }
    async function open(slug){
      if(!await canSwitch())return;
      const token=++loadToken,value=await GET('/api/album?slug='+encodeURIComponent(slug));
      if(token!==loadToken)return;
      session.open(value);prepared=null;render();
    }
    async function save(){
      if(!session.current)return;
      const target=session.current;await session.save();
      if(session.current===target){status(session.dirty?'保存完成，仍有后续修改':'已保存草稿');preview?.update(previewFields());await list()}
    }
    function renderImages(){
      const box=$('#album-images');box.replaceChildren();
      const photos=fields().images;
      photos.forEach((photo,index)=>{
        const row=document.createElement('div');row.className='card row';row.draggable=true;
        row.innerHTML=`<img width="90" height="90" style="object-fit:cover" src="${esc(imageURL(photo.src))}" alt="${esc(photo.alt||'待填写图注')}">
          <label class="field grow"><span>图注 ${index+1}</span><input value="${esc(photo.alt||'')}"></label>
          <button class="btn sm" data-cover>${fields().cover===photo.src?'已选封面':'设为封面'}</button>
          <button class="btn sm" data-up ${index?'':'disabled'}>上移</button><button class="btn sm" data-down ${index<photos.length-1?'':'disabled'}>下移</button><button class="btn sm" data-remove>移除</button>`;
        row.querySelector('input').oninput=e=>{photo.alt=e.target.value;changed()};
        row.querySelector('[data-cover]').onclick=()=>{fields().cover=photo.src;changed();renderImages()};
        const move=target=>{const from=photos.indexOf(photo);if(from<0||target<0||target>=photos.length)return;photos.splice(from,1);photos.splice(target,0,photo);changed();renderImages()};
        row.querySelector('[data-up]').onclick=()=>move(photos.indexOf(photo)-1);
        row.querySelector('[data-down]').onclick=()=>move(photos.indexOf(photo)+1);
        row.querySelector('[data-remove]').onclick=()=>{removeImage(fields(),photo);changed();renderImages()};
        row.ondragstart=e=>{if(e.target.tagName==='INPUT'){e.preventDefault();return}row._drag=photo;box._drag=photo;e.dataTransfer.setData('text/plain',String(index))};
        row.ondragover=e=>e.preventDefault();
        row.ondrop=e=>{e.preventDefault();const dragged=box._drag,from=photos.indexOf(dragged),to=photos.indexOf(photo);if(from>=0&&to>=0){photos.splice(from,1);photos.splice(to,0,dragged);changed();renderImages()}box._drag=null};
        box.append(row);
      });
    }
    function render(){
      dispose();const f=fields();f.images??=[];
      $('#album-editor').innerHTML=`<div class="row spread"><b>${esc(session.current.slug)}</b><span id="album-status" role="status">${session.dirty?'有未保存的修改':session.current.create?'新草稿':'已读取'}</span></div>
      <div class="row"><label class="field grow"><span>相册名称</span><input data-field="title" value="${esc(f.title||'')}"></label><label class="field grow"><span>英文标题</span><input data-field="titleEn" value="${esc(f.titleEn||'')}"></label><label class="field"><span>日期</span><input type="date" data-field="date" value="${esc(f.date||'')}"></label></div>
      <div class="row"><label class="field"><span>册内布局</span><select id="album-layout" disabled></select></label><label><input type="checkbox" id="album-draft" ${f.draft?'checked':''}>草稿（不公开）</label><button class="btn" id="album-upload">批量上传照片</button></div>
      <div id="album-images" class="stack"></div><p class="hint">拖动或用按钮排序；封面独立选择。移除只解除引用，原图保留。</p>
      <h3>封面整套编排</h3><div id="album-presets" class="row"></div><details><summary>进一步调整</summary><div id="album-adjust" class="row"></div></details>
      <div class="row"><button class="btn" id="album-recommend">重新推荐</button><button class="btn primary" id="album-save">保存草稿 / 修改</button><button class="btn" id="album-prepare">准备本册发布</button><button class="btn" id="album-publish" disabled>确认发布本册</button></div>
      <div id="album-preview"></div><pre id="album-result" style="white-space:pre-wrap"></pre>`;
      for(const input of document.querySelectorAll('[data-field]'))input.oninput=()=>{f[input.dataset.field]=input.value;changed()};
      $('#album-draft').onchange=e=>{f.draft=e.target.checked;changed()};
      $('#album-save').onclick=safe(save);
      $('#album-upload').onclick=safe(async()=>{
        const target=session.current;
        for(const file of await pickFiles()){
          if(session.current!==target)break;
          if(fields().images.length>=100)throw Error('新增上传每册最多 100 张');
          if(await session.upload(file,fileToDataURL)){prepared=null;renderImages();changed()}
        }
      });
      $('#album-recommend').onclick=safe(async()=>{
        const target=session.current,version=session.version,result=await POST('/api/album/recommend',{slug:target.slug,frontmatter:clone(f)});
        if(session.current===target&&session.version===version){f.presentation={...f.presentation,...result};changed();render()}
      });
      $('#album-prepare').onclick=safe(async()=>{
        const target=session.current;await save();if(target!==session.current)return;
        if(session.dirty)throw Error('有保存期间的新修改，请再次保存');
        const version=session.version,result=await POST('/api/album/prepare',{slug:target.slug,base_revision:target.revision});
        if(target!==session.current)return;
        if(version!==session.version)throw Error('准备期间有新修改，请重新保存并准备');
        prepared=result;$('#album-result').textContent=result.message+'\n'+result.paths.join('\n')+'\n快照 '+result.tree;
        $('#album-publish').disabled=false;
      });
      $('#album-publish').onclick=safe(async()=>{
        if(!prepared||session.dirty)throw Error('请先重新准备本册发布');
        const snapshot=prepared,target=session.current;
        if(!await askConfirm('发布当前相册','将校验本册隔离快照并普通推送 main；只包含列出的本册正文与引用图片。',{okText:'校验并发布'}))return;
        if(target!==session.current||snapshot!==prepared||session.dirty)throw Error('编辑内容已变化，请重新准备本册');
        const result=await POST('/api/album/publish',{slug:target.slug,base_revision:target.revision,tree:snapshot.tree});
        if(target===session.current){$('#album-result').textContent=JSON.stringify(result,null,2);status('本册已发布；本地草稿文件保持原状，请同步后重新读取')}
      });
      renderImages();mount();
    }
    async function mount(){
      const target=session.current,token=previewToken;
      try{
        config??=await import('/album-shared/lib/album-presentation.mjs');
        if(target!==session.current||token!==previewToken)return;
        const layout=$('#album-layout');
        for(const [value,text] of config.ALBUM_OPTIONS.layout)layout.add(new Option(text,value));
        layout.value=fields().layout||'scatter';
        layout.disabled=false;
        layout.onchange=()=>{fields().layout=layout.value;changed()};
        module??=await import('/album-shared/scripts/album-cover-preview.js');
        if(target!==session.current||token!==previewToken)return;
        preview=module.mountCoverPreview($('#album-preview'),{fields:previewFields(),resolveImage:imageURL,index:target.presentationIndex||0});
        for(const preset of config.ALBUM_PRESETS){
          const card=document.createElement('div');card.style.cssText='flex:1;min-width:180px';
          const button=document.createElement('button');button.className='btn';button.textContent=preset.title;
          button.onclick=()=>{const {title,...presentation}=preset;fields().presentation={...fields().presentation,...presentation};changed();render()};
          const thumb=document.createElement('div');card.append(button,thumb);$('#album-presets').append(card);
          thumbnails.push({preset,controller:module.mountCoverPreview(thumb,{fields:{...previewFields(),presentation:preset},resolveImage:imageURL,index:target.presentationIndex||0})});
        }
        for(const key of ['arrangement','transition','stroke','weight']){
          const label=document.createElement('label');label.className='field';label.textContent={arrangement:'构图',transition:'翻页动作',stroke:'笔势',weight:'笔触'}[key];
          const select=document.createElement('select');for(const [value,text] of config.ALBUM_OPTIONS[key])if(value!=='auto')select.add(new Option(text,value));
          select.value=fields().presentation?.[key]||config.resolvePresentation(previewFields(),target.presentationIndex||0)[key];
          select.onchange=()=>{fields().presentation={...config.resolvePresentation(previewFields(),target.presentationIndex||0),[key]:select.value};changed()};label.append(select);$('#album-adjust').append(label);
        }
      }catch(error){status('预览模块不可用：'+error.message)}
    }
    function init(){if(initialized)return;initialized=true;
      root.addEventListener('beforeunload',event=>{if(session.dirty){event.preventDefault();event.returnValue=''}});
      $('.view[data-view="albums"]').innerHTML=`<div class="row"><button class="btn primary" id="album-new">＋ 新建相册</button><button class="btn" id="album-reload">重新读取</button></div><div class="split album-split"><div class="stack" id="album-list"></div><div id="album-editor" class="stack"><p>选择一本相册，或创建空草稿。</p></div></div>`;
      $('#album-new').onclick=safe(async()=>{
        if(!await canSwitch())return;
        const answer=await askForm('新建相册','标识可用中文，保存后固定。',[{k:'slug',label:'相册标识',placeholder:'冬日散步'}],{okText:'创建草稿'});
        if(!answer)return;
        const token=++loadToken,saved=await POST('/api/album',{slug:answer.slug,create:true,frontmatter:{title:'',titleEn:'',date:new Date().toLocaleDateString('sv-SE'),draft:true,images:[],layout:'scatter'}});
        if(token!==loadToken)return;
        session.open(saved);prepared=null;render();await list();
      });
      $('#album-reload').onclick=safe(async()=>{if(session.current&&!session.current.create)await open(session.current.slug);await list()});
    }
    return {init,enter:async()=>{init();await list()},save};
  }
  root.DeskGallery={create,createSession,removeImage};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.DeskGallery;
})(typeof window!=='undefined'?window:globalThis);
