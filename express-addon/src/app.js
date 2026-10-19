import { connectExpress } from './host.js';

const $ = (id) => document.getElementById(id);
const PRESETS = [
  ['Post',1080,1080],['Story',1080,1920],['Portrait',1080,1350],
  ['Thumbnail',1280,720],['Banner',1600,400],['Leaderboard',728,90]
];
const state = {tasks:[],custom:null,source:null,assets:{},result:null,version:0,busy:false,compare:false,host:null};
let service = location.origin;
$('serviceUrl').value = service;
const message = (text,success=false) => {
  $('notice').textContent = text;
  $('notice').classList.toggle('success',success);
  $('notice').hidden = !text;
};
function dimensions(){
  const width=Number($('targetW').value),height=Number($('targetH').value);
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1) throw Error('Enter positive whole numbers for width and height.');
  if(width>8192||height>8192||width*height>20000000) throw Error('Choose a canvas up to 8192 px per side and 20 megapixels.');
  return {width,height};
}
function updatePresets(){
  for(const button of $('presets').children){
    button.classList.toggle('active',Number(button.dataset.width)===Number($('targetW').value)&&Number(button.dataset.height)===Number($('targetH').value));
  }
}
function setTarget(w,h){$('targetW').value=w;$('targetH').value=h;updatePresets();invalidate();}
function invalidate(){
  state.version++;
  state.result=null;
  $('qualityPanel').hidden=true;
  for(const id of ['pngBtn','jsonBtn','expressBtn']) $(id).disabled=true;
  $('batchBtn').disabled=!state.source||state.busy;
  $('previewStatus').textContent='Settings changed · reflow to update';
}
function setSource(source){
  if(!source?.canvas||!Array.isArray(source.elements)) throw Error('Choose a Chameleon design JSON with canvas and elements.');
  state.source=structuredClone(source);
  $('sourceInfo').textContent=`${source.canvas.width} × ${source.canvas.height} · ${source.elements.length} elements · ${Object.keys(state.assets).length} added images`;
  $('runBtn').disabled=false;
  invalidate();
}
function busy(value,label='Reflowing…'){
  state.busy=value;document.body.classList.toggle('busy',value);
  $('runBtn').disabled=value||!state.source;
  $('runBtn').textContent=value?label:'Reflow design ↗';
  for(const id of ['pngBtn','jsonBtn'])$(id).disabled=value||!state.result;
  $('batchBtn').disabled=value||!state.source;
  $('expressBtn').disabled=value||!state.result||!state.host;
}
function payload(target){return {source:state.source,target_canvas:target,mode:$('modeSelect').value,assets:state.assets};}
async function request(path,body){
  const response=await fetch(service+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){let error;try{error=(await response.json()).error;}catch{}throw Error(error||`Service returned ${response.status}`);}
  return response;
}
function setView(){
  $('sourceFrame').hidden=!state.compare||!state.result;
  $('resultTab').setAttribute('aria-selected',String(!state.compare));
  $('compareTab').setAttribute('aria-selected',String(state.compare));
  $('previewStage').classList.toggle('compare',state.compare);
}
const explain=(issue)=>{
  const names=issue.ids.join(', ');
  const descriptions={text_too_small:'Small text; review at actual size',collision:'Elements overlap',text_overflow_width:'Text extends past its box',text_overflow_height:'Text extends below its box',text_ink_off_canvas:'Text extends past the canvas',label_off_shape:'Label extends outside its background',edge_hugging:'Close to the canvas edge',hierarchy_inverted:'Text hierarchy changed'};
  return `${names}: ${descriptions[issue.code]||issue.code.replaceAll('_',' ')}`;
};
function showResult(result){
  state.result=result;
  const {width,height}=result.output.canvas;
  $('sourceImage').src=result.source_png;$('outputImage').src=result.output_png;
  $('sourceCanvasLabel').textContent=`${state.source.canvas.width} × ${state.source.canvas.height}`;
  $('outputCanvasLabel').textContent=`${width} × ${height}`;
  $('canvasLabel').textContent=`${width} × ${height}`;
  $('outputFrame').hidden=false;$('emptyState').hidden=true;
  $('previewStatus').textContent=`${result.output.elements.length} elements preserved · content unchanged`;
  $('runtime').textContent=`${result.elapsed_ms} ms reflow`;
  $('qualityPanel').hidden=false;
  $('qualityTitle').textContent=result.quality.issues.length?'A fit worth reviewing.':'Made to fit.';
  $('qualityBadge').textContent=result.quality.issues.length?`${result.quality.issues.length} things to review`:'Fit checks passed';
  $('qualityBadge').classList.toggle('warn',Boolean(result.quality.issues.length));
  $('checks').replaceChildren();
  for(const [label,value] of [['Content kept',`${result.output.elements.length} / ${state.source.elements.length}`],['Unwanted overlaps',String(result.quality.collisions.length)],['Local fit score',`${result.quality.score} / 100`]]){
    const item=document.createElement('div');item.className='check';item.textContent=label;
    const strong=document.createElement('strong');strong.textContent=value;item.append(strong);$('checks').append(item);
  }
  $('issues').replaceChildren();
  if(result.quality.issues.length||result.warnings.length){
    const list=document.createElement('ul');
    const notes=[...result.quality.issues.map(explain),...result.warnings];
    for(const note of notes){const li=document.createElement('li');li.textContent=note;list.append(li);}
    $('issues').append(list);
  }else{
    const p=document.createElement('p');p.className='ok';p.textContent='No overflow detected. Images stay in proportion. Text and brand styles are preserved.';$('issues').append(p);
  }
  setView();
}
async function run(){
  if(state.busy||!state.source)return;
  let target;try{target=dimensions();}catch(error){message(error.message);return;}
  const version=++state.version;
  busy(true);message('');
  try{
    const result=await (await request('/api/resize',payload(target))).json();
    if(version===state.version)showResult(result);
  }catch(error){if(version===state.version)message(`Couldn’t reflow the design. ${error.message}`);}
  finally{busy(false);}
}
function download(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function stem(){const c=state.result.output.canvas;return `chameleon-${c.width}x${c.height}`;}
$('pngBtn').onclick=async()=>{if(state.result)download(await(await fetch(state.result.output_png)).blob(),stem()+'.png');};
$('jsonBtn').onclick=()=>{if(state.result)download(new Blob([JSON.stringify(state.result.output,null,2)],{type:'application/json'}),stem()+'.json');};
$('batchBtn').onclick=async()=>{
  if(state.busy||!state.source)return;
  busy(true,'Building campaign…');message('');
  try{
    const body={...payload({width:1080,height:1080}),mode:'auto',targets:[{width:1080,height:1080},{width:1080,height:1920},{width:1280,height:720},{width:1600,height:400}]};
    const response=await request('/api/batch',body);download(await response.blob(),'chameleon-campaign.zip');message('Your four-size campaign is ready. Each layout includes PNG, JSON and a shared quality report.',true);
  }catch(error){message(error.message);}finally{busy(false);}
};
$('expressBtn').onclick=async()=>{
  if(!state.host||!state.result||state.busy)return;
  busy(true,'Adding to Express…');
  try{
    const blob=await(await fetch(state.result.output_png)).blob();
    await state.host.insertPage(blob,state.result.output.canvas);
    message('Added a new Express page at the target size. Your original page is unchanged.',true);
  }catch(error){message(`Couldn’t add the page. ${error.message}`);}finally{busy(false);}
};
$('runBtn').onclick=run;
$('importBtn').onclick=()=>$('jsonFile').click();
$('assetsBtn').onclick=()=>$('assetFiles').click();
$('taskSelect').onchange=()=>{
  const task=$('taskSelect').value==='custom'?state.custom:state.tasks[Number($('taskSelect').value)];
  if(!task)return;
  state.assets={...(task.assets||{})};setSource(task.source);
  if(task.target_canvas)setTarget(task.target_canvas.width,task.target_canvas.height);
  run();
};
$('modeSelect').onchange=invalidate;
for(const id of ['targetW','targetH'])$(id).oninput=()=>{updatePresets();invalidate();};
$('resultTab').onclick=()=>{state.compare=false;setView();};
$('compareTab').onclick=()=>{state.compare=true;setView();};
$('zoomBtn').onclick=()=>{const actual=$('previewStage').classList.toggle('actual');$('zoomBtn').textContent=actual?'Actual size':'Fit';};
$('jsonFile').onchange=async(event)=>{
  const file=event.target.files[0];if(!file)return;
  try{
    if(file.size>2000000)throw Error('Design JSON must be smaller than 2 MB.');
    const task=JSON.parse(await file.text());setSource(task.source||task);
    state.custom={source:structuredClone(state.source),target_canvas:task.target_canvas,assets:{...state.assets}};
    let option=$('taskSelect').querySelector('[data-custom]');
    if(!option){option=document.createElement('option');option.dataset.custom='true';$('taskSelect').append(option);}
    option.value='custom';option.textContent=file.name;$('taskSelect').value='custom';
    if(task.target_canvas)setTarget(task.target_canvas.width,task.target_canvas.height);
    message('Design imported. Add any referenced images, then reflow.',true);
  }catch(error){message(error.message);}event.target.value='';
};
$('assetFiles').onchange=async(event)=>{
  try{
    let total=0;const uploads={};
    for(const file of event.target.files){
      total+=file.size;if(total>16000000)throw Error('Keep uploaded images under 16 MB total.');
      const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file);});
      uploads[file.name]=data.split(',')[1];
    }
    state.assets={...state.assets,...uploads};
    if($('taskSelect').value==='custom'&&state.custom)state.custom.assets={...state.assets};
    if(state.source)setSource(state.source);message('Images added. Reflow to preview the updated design.',true);
  }catch(error){message(error.message);}event.target.value='';
};
for(const [name,width,height]of PRESETS){
  const button=document.createElement('button');button.className='preset';button.dataset.width=width;button.dataset.height=height;button.textContent=name;
  const dimensions=document.createElement('span');dimensions.textContent=`${width} × ${height}`;button.append(dimensions);
  button.onclick=()=>{setTarget(width,height);run();};$('presets').append(button);
}
async function loadTasks(){
  try{
    const response=await fetch(service+'/api/tasks');if(!response.ok)throw Error(`Service returned ${response.status}`);
    state.tasks=await response.json();$('taskSelect').replaceChildren();
    for(const [i,task]of state.tasks.entries()){const option=document.createElement('option');option.value=i;option.textContent=`${['Warm-up','Core campaign','Advanced campaign'][i]||task.task_id} · ${task.source.elements.length} elements`;$('taskSelect').append(option);}
    $('taskSelect').disabled=false;$('taskSelect').value=String(Math.min(1,state.tasks.length-1));
    const task=state.tasks[Number($('taskSelect').value)];setSource(task.source);setTarget(task.target_canvas.width,task.target_canvas.height);await run();
  }catch(error){message(`Start the local studio and check the service URL. ${error.message}`);}
}
$('connectBtn').onclick=()=>{try{const url=new URL($('serviceUrl').value);if(!['http:','https:'].includes(url.protocol))throw Error('Use an HTTP or HTTPS service URL.');service=url.origin;loadTasks();}catch(error){message(error.message);}};
document.addEventListener('keydown',(event)=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();run();}});
connectExpress().then(host=>{if(host){state.host=host;$('expressBtn').hidden=false;$('hostNote').hidden=false;$('expressBtn').disabled=!state.result;}}).catch(error=>message(`Express connection unavailable: ${error.message}`));
loadTasks();
