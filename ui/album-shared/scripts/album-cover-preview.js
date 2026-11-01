import {resolvePresentation,albumTitleLines} from '../lib/album-presentation.mjs';
import {coverArtwork,mobileArtwork} from '../lib/album-cover-art.mjs';
import {coverMotion} from './gallery-cover-motion.mjs';

const styles=[new URL('../styles/tokens.css',import.meta.url),new URL('../styles/fonts.css',import.meta.url),new URL('../styles/gallery-stage.css',import.meta.url)];
const namespace='http://www.w3.org/2000/svg';
const node=(document,tag,className,text)=>{const element=document.createElement(tag);if(className)element.className=className;if(text!==undefined)element.textContent=text;return element};
function svg(document,viewBox,paths,className){
  const element=document.createElementNS(namespace,'svg');element.setAttribute('class',className);element.setAttribute('viewBox',viewBox);element.setAttribute('preserveAspectRatio','none');element.setAttribute('aria-hidden','true');
  for(const [kind,d]of Object.entries(paths)){const path=document.createElementNS(namespace,'path');path.setAttribute('class',kind+'-ink');path.setAttribute('d',d);path.setAttribute('pathLength','1000');path.dataset.draw='';path.dataset.delay=String(kind==='main'?380:kind==='echo'?570:680);element.append(path)}
  return element;
}
function safeImage(source){
  if(typeof source!=='string')return null;
  try{const url=new URL(source,location.href);return url.protocol==='blob:'||url.origin===location.origin&&['http:','https:'].includes(url.protocol)||/^data:image\/(?:webp|png|jpeg);base64,[a-z0-9+/=]+$/i.test(source)?url.href:null}catch{return null}
}
function coverElement(document,fields,resolveImage,index=0){
  const images=Array.isArray(fields.images)?fields.images:[],image=images.find(image=>image.src===fields.cover)??images[0];
  const presentation=resolvePresentation(fields,index),kind=presentation.arrangement,art=coverArtwork(presentation.stroke,kind);
  const section=node(document,'section','slide kind-'+kind);section.dataset.transition=presentation.transition;section.dataset.stroke=presentation.stroke;section.dataset.weight=presentation.weight;section.dataset.slide='0';
  section.style.setProperty('--gallery-cover-ratio',`${image?.width||800}/${image?.height||1000}`);
  section.style.setProperty('--gallery-title-columns',String(Math.max(...albumTitleLines(String(fields.title||'相册名称')).map(line=>Array.from(line).length),1)));
  if(Array.from(String(fields.title||'')).length>4)section.dataset.longTitle='true';
  const copy=node(document,'div','copy'),folio=node(document,'p','folio');folio.dataset.reveal='';folio.dataset.order='-1';folio.append(node(document,'span','','01'),node(document,'i'),node(document,'span','','相册'));
  const heading=node(document,'h2','album-title');heading.setAttribute('aria-label',String(fields.title||'相册名称'));const title=node(document,'span','album-title-link');
  for(const [index,line]of albumTitleLines(String(fields.title||'相册名称')).entries()){
    const wrapper=node(document,'span','title-line'),text=node(document,'span','',line);text.dataset.reveal='';text.dataset.order=String(index);wrapper.append(text);title.append(wrapper);
    if(kind==='wide'&&index===albumTitleLines(String(fields.title||'相册名称')).length-1){const circle=svg(document,'0 0 260 110',{main:'M225 19 C160 -2 34 2 15 44 C-6 91 164 108 232 72 C277 49 215 9 132 13'},'word-circle');text.append(circle)}
  }
  heading.append(title);const meta=node(document,'div','album-meta');meta.dataset.reveal='';meta.dataset.order='3';meta.append(node(document,'span','',images.length+' 张照片'),node(document,'span','',String(fields.date||'日期待填写').replaceAll('-','.')));
  const caption=node(document,'span','caption',String(image?.alt||'照片图注'));caption.dataset.reveal='';caption.dataset.order='4';caption.append(node(document,'span','','↗'));
  copy.append(folio,heading,meta,caption,svg(document,'0 0 400 240',mobileArtwork(presentation.stroke),'copy-ink'));
  const visual=node(document,'div','visual'),frame=node(document,'figure','frame'),link=node(document,'span','cover-link'),source=image?safeImage(resolveImage(image.src)):null;
  if(source){const photo=node(document,'img');photo.src=source;photo.alt=String(image.alt||'相册封面');photo.width=image.width||800;photo.height=image.height||1000;photo.draggable=false;photo.addEventListener('load',()=>{if(!image.width||!image.height)section.style.setProperty('--gallery-cover-ratio',`${photo.naturalWidth}/${photo.naturalHeight}`)},{once:true});link.append(photo)}else link.append(node(document,'span','album-preview-empty','上传照片后，用真实封面预览'));
  frame.append(link);visual.append(frame);section.append(copy,visual,svg(document,'0 0 1400 800',art,'ink-layer desktop-ink'),svg(document,'0 0 400 700',{corner:'M65 24 Q202 8 329 32 L345 111'},'ink-layer mobile-ink'));
  return section;
}
/** 同源无脚本 iframe 使用正式封面 CSS；预览只读 fields，不保存或修改编辑器输入。 */
export function mountCoverPreview(host,{fields,resolveImage,index=0}){
  const document=host.ownerDocument,controls=node(document,'div','album-preview-controls'),viewport=node(document,'div','album-preview-viewport'),iframe=node(document,'iframe','album-preview-frame');
  iframe.title='相册封面真实动效预览';iframe.setAttribute('sandbox','allow-same-origin');iframe.setAttribute('scrolling','no');viewport.append(iframe);host.append(controls,viewport);
  let current=fields,phone=false,dark=document.documentElement.classList.contains('dark'),destroyed=false,ready=false,slide=null,revision=0,timer=null;
  const animations=new Map(),reduce=matchMedia('(prefers-reduced-motion:reduce)');
  function cancel(element){const animation=animations.get(element);if(!animation)return;const style=iframe.contentWindow.getComputedStyle(element);for(const key of Object.keys(animation.effect.getKeyframes().at(-1)))if(!['offset','computedOffset','easing','composite'].includes(key))element.style[key]=style[key];animation.cancel();animations.delete(element)}
  function animate(element,to,duration=720,delay=0){cancel(element);if(reduce.matches){Object.assign(element.style,to);return Promise.resolve()}const style=iframe.contentWindow.getComputedStyle(element),from=Object.fromEntries(Object.keys(to).map(key=>[key,style[key]])),animation=element.animate([from,to],{duration,delay,fill:'both',easing:'cubic-bezier(.18,.8,.18,1)'});animations.set(element,animation);return animation.finished.then(()=>{if(animations.get(element)!==animation)return;Object.assign(element.style,to);animation.cancel();animations.delete(element)}).catch(()=>{})}
  function scale(){if(destroyed)return;const width=phone?390:1400,height=phone?780:800,available=viewport.getBoundingClientRect().width,factor=Math.min(available/width,1);iframe.style.width=width+'px';iframe.style.height=height+'px';iframe.style.transform=`scale(${factor})`;viewport.style.height=height*factor+'px';viewport.style.maxWidth=width+'px'}
  function play(direction=1,fresh=true){if(!slide||destroyed)return;revision++;clearTimeout(timer);const motion=coverMotion(slide.dataset.transition,direction);if(fresh){for(const [element]of animations)cancel(element);Object.assign(slide.style,motion.enter)}animate(slide,motion.visible,760);
    for(const element of slide.querySelectorAll('[data-reveal]')){const order=Number(element.dataset.order);if(fresh){element.style.opacity=order<0||order>=3?'0':'1';element.style.transform=order<0?'translateY(12px)':order<3?'translateY(120%) rotate(2deg)':'translateY(20px)'}animate(element,{opacity:'1',transform:'translateY(0px) rotate(0deg)'},order<3?720:560,Math.max(80,180+order*80))}
    for(const path of slide.querySelectorAll('[data-draw]')){if(fresh)path.style.strokeDashoffset='1000';animate(path,{strokeDashoffset:'0'},path.classList.contains('main-ink')?1180:800,Number(path.dataset.delay)||0)}
  }
  function render(){if(!ready||destroyed)return;for(const [element]of animations)cancel(element);const doc=iframe.contentDocument;doc.documentElement.className='gallery-stage-ready'+(dark?' dark':'');doc.body.className='gallery-story';const ui=node(doc,'div','gallery-stage-ui'),stage=node(doc,'div','stage');slide=coverElement(doc,current,resolveImage,index);slide.classList.add('is-active');stage.append(slide);ui.append(stage);doc.body.replaceChildren(ui);scale();play()}
  function action(label,callback){const button=node(document,'button','',label);button.type='button';button.addEventListener('click',callback);controls.append(button);return button}
  const size=action('桌面',()=>{phone=!phone;size.textContent=phone?'手机':'桌面';size.setAttribute('aria-pressed',String(phone));scale()});size.setAttribute('aria-label','切换桌面或手机预览');size.setAttribute('aria-pressed','false');
  const theme=action(dark?'暗色':'亮色',()=>{dark=!dark;theme.textContent=dark?'暗色':'亮色';theme.setAttribute('aria-pressed',String(dark));iframe.contentDocument.documentElement.classList.toggle('dark',dark)});theme.setAttribute('aria-label','切换预览明暗');theme.setAttribute('aria-pressed',String(dark));
  action('播放一次',()=>play());action('快速往返',()=>{play(1,true);if(reduce.matches)return;const token=revision;clearTimeout(timer);timer=setTimeout(()=>{if(token!==revision||destroyed)return;animate(slide,coverMotion(slide.dataset.transition,-1).enter,360).then(()=>{if(token===revision&&!destroyed)play(-1,false)})},170)});
  const observer=new ResizeObserver(scale);observer.observe(viewport);
  function initialize(){if(destroyed)return;const doc=iframe.contentDocument;if(!doc?.head||ready)return;for(const url of styles){const link=doc.createElement('link');link.rel='stylesheet';link.href=url.href;doc.head.append(link)}const local=doc.createElement('style');local.textContent='.gallery-story .gallery-stage-ui{height:100svh!important;grid-template-rows:minmax(0,1fr)!important}.gallery-story .stage{height:100%}.album-preview-empty{display:grid;place-items:center;height:100%;padding:2rem;text-align:center;border:1px solid var(--gallery-hair);color:var(--gallery-muted)}';doc.head.append(local);ready=true;render()}
  iframe.addEventListener('load',initialize);initialize();
  return {update(value){current=value;render()},destroy(){destroyed=true;revision++;clearTimeout(timer);observer.disconnect();for(const [element]of animations)cancel(element);iframe.removeEventListener('load',initialize);controls.remove();viewport.remove()}};
}
