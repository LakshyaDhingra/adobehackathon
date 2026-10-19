/** Adapter kept separate so the studio remains fully offline outside Express. */
export async function connectExpress(){
  if(window.parent===window)return null;
  const {default:sdk}=await import('https://express.adobe.com/static/add-on-sdk/sdk.js');
  await sdk.ready;
  const sandbox=await sdk.instance.runtime.apiProxy('documentSandbox');
  return {
    async insertPage(blob,canvas){
      const bytes=Array.from(new Uint8Array(await blob.arrayBuffer()));
      return sandbox.insertPage({bytes,width:canvas.width,height:canvas.height});
    }
  };
}
