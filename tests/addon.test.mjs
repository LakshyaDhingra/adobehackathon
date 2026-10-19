import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';

const source=await fs.readFile(new URL('../express-addon/src/code.js',import.meta.url),'utf8');
function sandbox({failAppend=false}={}){
  let api,page,removed=false;
  const children={append(image){if(failAppend)throw Error('insert failed');this.image=image;}};
  const editor={context:{insertionParent:{}},documentRoot:{pages:{addPage(size){page={...size,artboards:{first:{children}},removeFromParent(){removed=true;}};return page;}}},
    async loadBitmapImage(blob){assert.equal(blob.type,'image/png');return {loaded:true};},
    async keepContentActiveDuringAsync(target,load,apply){apply(await load());},
    createImageContainer(bitmap,options){assert.equal(bitmap.loaded,true);return {...options};}};
  const code=source.replace(/^import .*;\n/gm,'');
  vm.runInNewContext(code,{addOnSandboxSdk:{instance:{runtime:{exposeApi(value){api=value;}}}},editor,Blob,Uint8Array});
  return {api,get page(){return page;},get removed(){return removed;},children};
}
test('new page is created at exact dimensions without changing an existing page',async()=>{
  const host=sandbox();await host.api.insertPage({bytes:[137,80,78,71],width:728,height:90});
  assert.equal(host.page.width,728);assert.equal(host.page.height,90);
  assert.deepEqual({...host.children.image.translation},{x:0,y:0});
});
test('invalid size is rejected before a page is created',async()=>{
  const host=sandbox();await assert.rejects(host.api.insertPage({bytes:[1],width:0,height:90}),/Invalid page/);assert.equal(host.page,undefined);
});
test('a failed insert removes only its newly created page',async()=>{
  const host=sandbox({failAppend:true});await assert.rejects(host.api.insertPage({bytes:[1],width:728,height:90}),/insert failed/);assert.equal(host.removed,true);
});
test('standalone studio does not request the Adobe SDK',async()=>{
  const code=await fs.readFile(new URL('../express-addon/src/host.js',import.meta.url),'utf8');
  const localWindow={};localWindow.parent=localWindow;
  const context=vm.createContext({window:localWindow});
  vm.runInContext(code.replace('export async function','async function')+';this.connect=connectExpress;',context);
  assert.equal(await context.connect(),null);
});
