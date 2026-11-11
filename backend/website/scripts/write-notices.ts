import {readFile,writeFile,readdir} from 'node:fs/promises';
const lock=JSON.parse(await readFile('package-lock.json','utf8'));
const manifest=JSON.parse(await readFile('package.json','utf8'));
const names=[...new Set([...Object.keys(manifest.dependencies),...Object.keys(manifest.devDependencies),'jwt-decode','scheduler','sharp'])].sort();
const rows=names.map(name=>{const item=lock.packages[`node_modules/${name}`];return `| ${name} | ${item.version} | ${item.license} | [npm source](${item.resolved}) | \`${item.integrity}\` |`;});
await writeFile('DEPENDENCIES.md','# 官网依赖与许可\n\n由 `npm run notices` 从已安装依赖及 package-lock.json 生成。校验和为 npm tarball 的 SRI SHA-512；全部间接依赖版本、下载源和校验和以 package-lock.json 为准。没有在官网分发模型权重或字体文件；使用系统字体。Sharp/libvips 仅在构建期处理图片，不进入浏览器。\n\n| 组件 | 版本 | npm 声明许可 | 来源 | 校验和 |\n| --- | --- | --- | --- | --- |\n'+rows.join('\n')+'\n\n浏览器运行依赖的完整许可证随静态产物发布在 `/third-party-licenses.txt`。\n');
const runtime=['astro','@astrojs/react','react','react-dom','scheduler','oidc-client-ts','jwt-decode','@zip.js/zip.js','@noble/hashes'];
const notices=[];
for(const name of runtime){
  const dir=`node_modules/${name}`;
  const files=await readdir(dir);
  const license=files.find(file=>/^licen[sc]e(?:\.md|\.txt)?$/i.test(file));
  if(!license)throw Error(`License not found: ${name}`);
  notices.push(`${name} ${lock.packages[dir].version}\n${(await readFile(`${dir}/${license}`,'utf8')).replace(/[ \t]+$/gm,'')}`);
}
await writeFile('public/third-party-licenses.txt',notices.join('\n\n----------------------------------------\n\n'));
