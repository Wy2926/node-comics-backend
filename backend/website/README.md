# 官网

Astro 静态页面与 React 翻译、账户工作台。Node.js 22.12+，在本目录运行：

```sh
npm ci
npm run dev
npm test
npm run build
npm run preview
```

开发地址 `http://127.0.0.1:4321`。验证付款流程：构建并启动 `npm run preview`，再从仓库根运行 `node scripts/verify_website_checkout.mjs`。使用本地模拟订单，不会扣款。

付款设计体验：`npm run dev:checkout` 后打开 `http://127.0.0.1:5193/`。账户验收共用此服务，从仓库根运行 `node scripts/verify_website_account.mjs`。

- 语言与文案：`src/i18n/`；公开页面同步 17 语。
- 域名与商店：`src/data/site.ts`；安装包：`../extension-release.json`。
- 支持站点：`../../contracts/extension/` 的版本化快照；更新时替换完整导出目录并运行测试。
- 图像协议：`../shared/translation-images/`；文件任务沿用服务端持久状态。
- 公开页面静态可读；私有页面 no-store/noindex，写请求不自动重放，历史按账户隔离。

[部署](../../docs/DEPLOYMENT.md) · [SEO](SEO.md) · [图片来源](ASSETS.md) · [依赖许可](DEPENDENCIES.md)
