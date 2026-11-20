import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';
export default defineConfig({
  cacheDir:'node_modules/.vite-account-fixture',
  resolve:{alias:[{find:/^(?:\.\.\/lib|\.)\/auth$/,replacement:fileURLToPath(new URL('./account-fixture-auth.ts',import.meta.url))}]},
  server:{host:'127.0.0.1',port:5193,strictPort:true},
  plugins:[{name:'local-checkout-preview',configureServer(server){
    server.middlewares.use((request,_response,next)=>{
      if(request.url==='/'||request.url?.startsWith('/?'))request.url='/tests/checkout-preview.html'+request.url.slice(1);
      next();
    });
  }}],
});
