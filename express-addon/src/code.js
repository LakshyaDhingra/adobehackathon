import addOnSandboxSdk from 'add-on-sdk-document-sandbox';
import { editor } from 'express-document-sdk';

const { runtime } = addOnSandboxSdk.instance;
runtime.exposeApi({
  async insertPage({bytes,width,height}){
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>8192||height>8192)throw Error('Invalid page dimensions');
    if(!Array.isArray(bytes)||bytes.length>16000000||bytes.some(b=>!Number.isInteger(b)||b<0||b>255))throw Error('Invalid PNG image');
    const blob=new Blob([new Uint8Array(bytes)],{type:'image/png'});
    await editor.keepContentActiveDuringAsync(
      editor.context.insertionParent,
      ()=>editor.loadBitmapImage(blob),
      (bitmap)=>{
        const page=editor.documentRoot.pages.addPage({width,height});
        try{
          page.name=`Chameleon · ${width} × ${height}`;
          const image=editor.createImageContainer(bitmap,{initialSize:{width,height}});
          image.translation={x:0,y:0};
          page.artboards.first.children.append(image);
        }catch(error){page.removeFromParent();throw error;}
      }
    );
    return {width,height};
  }
});
