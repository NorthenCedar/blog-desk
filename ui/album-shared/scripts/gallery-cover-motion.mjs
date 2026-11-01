/** 正式封面与写作台预览读取同一组姿态，反向只换目标，不排队播放旧动作。 */
export function coverMotion(transition='slide',direction=1){
  const sign=direction<0?-1:1,visible={opacity:'1',transform:'translateX(0%) scale(1)',clipPath:'inset(0% 0% 0% 0%)'};
  if(transition==='reveal')return {enter:{opacity:'1',transform:`translateX(${sign*2}%) scale(1.025)`,clipPath:sign>0?'inset(0% 0% 0% 100%)':'inset(0% 100% 0% 0%)'},visible,exit:{opacity:'0',transform:`translateX(${-sign*2}%) scale(.995)`,clipPath:sign>0?'inset(0% 100% 0% 0%)':'inset(0% 0% 0% 100%)'}};
  if(transition==='perspective')return {enter:{opacity:'0',transform:`perspective(1200px) translateX(${sign*7}%) rotateY(${-sign*14}deg) scale(.97)`,clipPath:visible.clipPath},visible,exit:{opacity:'0',transform:`perspective(1200px) translateX(${-sign*6}%) rotateY(${sign*10}deg) scale(.98)`,clipPath:visible.clipPath}};
  return {enter:{opacity:'0',transform:`translateX(${sign*7}%) scale(.99)`,clipPath:visible.clipPath},visible,exit:{opacity:'0',transform:`translateX(${-sign*6}%) scale(.985)`,clipPath:visible.clipPath}};
}
