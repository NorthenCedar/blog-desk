/** 本站、两端写作台与预览共用的相册配置；不读取环境，也不持有相册内容。 */
export const ALBUM_OPTIONS = Object.freeze({
  arrangement:[['auto','自动推荐'],['left','竖幅靠左'],['right','竖幅靠右'],['wide','横幅展开']],
  transition:[['slide','侧向翻入'],['reveal','遮罩显影'],['perspective','轻微透视翻入']],
  stroke:[['arc','长弧引线'],['loop','回环圈注'],['corner','框角折线']],
  weight:[['regular','常规'],['bold','明显']],
  layout:[['scatter','散片陈列'],['slices','悬浮切片'],['wall','直接看照片墙']],
});
export const ALBUM_PRESETS = Object.freeze([
  {title:'长弧 · 侧翻',arrangement:'left',transition:'slide',stroke:'arc',weight:'bold'},
  {title:'圈注 · 显影',arrangement:'right',transition:'reveal',stroke:'loop',weight:'bold'},
  {title:'展幅 · 折角',arrangement:'wide',transition:'perspective',stroke:'corner',weight:'regular'},
]);
const fail=message=>{throw new Error(message)};
export function validateAlbumKey(key){
  if(typeof key!=='string'||!key||Array.from(key).length>100||!/[\p{L}\p{N}]/u.test(key)||! /^[\p{L}\p{N}_ -]+$/u.test(key)||key.trim()!==key||/^(?:spiral|index|con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(key))fail('相册网址须为文字、数字、空格、下划线或连字符，不能使用系统保留名');
  return key;
}
export const albumImageSource=value=>typeof value==='string'&&(/^writer-asset:[a-f0-9]{64}$/.test(value)||/^\.\/[\p{L}\p{N}_ .-]+\.(?:png|jpe?g|webp|avif)$/iu.test(value)&&!value.slice(2).includes('..'));
export function validateAlbumFields(fields,{publish=false}={}){
  if(!fields||typeof fields!=='object'||Array.isArray(fields))fail('相册资料格式不对');
  for(const key of ['title','titleEn','date'])if(fields[key]!==undefined&&typeof fields[key]!=='string')fail('相册名称、日期和英文标题须为文字');
  if(fields.draft!==undefined&&typeof fields.draft!=='boolean')fail('草稿状态须为布尔值');
  if(fields.layout!==undefined&&!ALBUM_OPTIONS.layout.some(([key])=>key===fields.layout))fail('layout 只能是 scatter、slices 或 wall');
  if(fields.presentation!==undefined){
    const value=fields.presentation;if(!value||typeof value!=='object'||Array.isArray(value))fail('封面编排须为一组配置');
    for(const key of ['arrangement','transition','stroke','weight'])if(value[key]!==undefined&&!ALBUM_OPTIONS[key].some(([option])=>option!=='auto'&&option===value[key]))fail('请选择已有的封面编排、翻页动作与笔触');
  }
  const images=fields.images??[];if(!Array.isArray(images))fail('照片须为列表');
  const sources=new Set();for(const image of images){
    if(!image||typeof image!=='object'||!albumImageSource(image.src)||typeof image.alt!=='string'||image.alt.length>2048)fail('照片须来自当前相册目录，并填写文字图注');
    if(sources.has(image.src))fail('相册存在重复图源');sources.add(image.src);
    if(publish&&!image.alt.trim())fail('请为每张照片填写图注');
  }
  if(fields.cover!==undefined&&(typeof fields.cover!=='string'||!sources.has(fields.cover)))fail('封面必须是本册照片之一');
  if(publish){
    if(typeof fields.title!=='string'||!fields.title.trim()||fields.title.length>200)fail('请填写相册名称（200 字以内）');
    const date=fields.date;if(typeof date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date+'T00:00:00Z'))||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date)fail('请填写有效日期');
    if(!images.length)fail('相册至少需要一张有图注的照片');
  }
  return fields;
}
export function recommendPresentation({title='',width=0,height=0}={},index=0){
  const arrangement=width>0&&height>0&&width/height>1.2?'wide':index%2?'right':'left';
  return {arrangement,transition:'slide',stroke:'arc',weight:'bold'};
}
export function resolvePresentation(album,index=0){
  const image=(album.images??[]).find(image=>image.src===album.cover)??album.images?.[0];
  const suggested=recommendPresentation({title:album.title,width:album.width??image?.width,height:album.height??image?.height},index);
  return {...suggested,...album.presentation};
}
export function albumTitleLines(title){
  const characters=Array.from(title),length=characters.length<=4?2:4;
  return characters.length<=2?[title]:Array.from({length:Math.ceil(characters.length/length)},(_,i)=>characters.slice(i*length,(i+1)*length).join(''));
}
