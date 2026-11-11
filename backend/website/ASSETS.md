# 官网素材来源

文件来源和 SHA-256 保存在 [provenance.json](assets/provenance.json)（路径相对本目录）；翻译样张的语言、字体、提示词、引擎及模型摘要见 [home-translations.json](assets/home-translations.json)。原图属于本仓库，构建生成响应式 WebP，点击才加载原尺寸。

## 产品素材

- `src/assets/screenshots/`、`home/inline-*`、`home/search.png`：真实插件截图；网页翻译为 MangaPill 英文原图与繁中译图，搜索为 Atsumaru／MangaPill 结果。
- `home/mono-original.png`、`home/color-original.jpg`：用户提供的原始黑白／彩色页。32 份译图使用 Classic Engine 检测、OCR、LaMa 修复与排版，DeepSeek V4.1 Flash + `comic-json-v14` 翻译，原尺寸 WebP quality 95。保留自动处理结果，存在漏检、误识、截断、修复痕迹和小字号，不代表人工校译。
- `journey-original.webp`、`reading-corner.webp`：gpt-image-2 high 生成的原创插画；提示词见 [hero.txt](assets/prompts/hero.txt)、[reading.txt](assets/prompts/reading.txt)。`journey-translated/en/ko.webp` 为用户提供并确认的翻译实测图；`public/social-cover.webp` 从阅读角插画裁切。
- Logo／favicon：插件品牌资源的本地副本，沿用 GPL-3.0-only；`ComicSymbol.astro` 为项目自绘 SVG。

漫画、封面、站点 UI 与商标归各自权利人；用户提供或截图复用不授予额外许可。生成插画不重新授予第三方开源许可。

## 教程截图

- `src/assets/guides/{zh-CN,en,ja,ko}/`：NodeLane Comics 0.11.2 的八个真实桌面场景，1440×960、WebP quality 92。远程书库日／韩／英版来自用户 PNG，其余为浏览器截图；展示 wol.moe／Wenku8 OPDS 目录。阅读示例为插件原创 `samples/starlight-bookshop.png` 英文原图；界面语言切换不表示翻译效果，截图不证明下载、同步、授权或结算全流程。
- `builtin-*.webp`：1.2.0 设置组件的本机浏览器预览，中／英／日／韩四语、1280×720，空白资料、未登录及未下载资源状态；不作为安装版推理与权限验收。
- `guides/firefox/`：Android 14 模拟器、Firefox 157.0.1 的六张真实 PNG，1080×2400。阅读器使用 Mozilla 签名 0.10.3 XPI，经 web-ext 临时加载；永久安装未确认。英文原创单页 CBZ 经文件选择器导入，只切换界面语言，保留浏览器及 AMO 提示。
- `guides/app/`：用户确认复用的 [node-comics-app](https://github.com/Wy2926/node-comics-app/tree/52d8723f30ff5b036c97db6aa2b5a9b8c07780cd/docs/images) iOS 模拟器中文 PNG；Android 教程仅复用作界面示意。上游基于 Mangayomi，保留 [Apache-2.0](https://github.com/Wy2926/node-comics-app/blob/52d8723f30ff5b036c97db6aa2b5a9b8c07780cd/LICENSE)、[来源声明](https://github.com/Wy2926/node-comics-app/blob/52d8723f30ff5b036c97db6aa2b5a9b8c07780cd/UPSTREAM.md)及 [GPL-3.0-only 品牌来源](https://github.com/Wy2926/node-comics-app/blob/52d8723f30ff5b036c97db6aa2b5a9b8c07780cd/docs/design/nodelane-brand-assets.md)。

截图保留实际文字和界面，不重画或拼接，不冒充视频截帧、其他平台实机或其他语言实测。

## 第三方图标

- GitHub、勾选、箭头：[Octicons](https://github.com/primer/octicons/tree/90af1f14984832de34e94b2d530043fbcf85eb7f/icons)，保留原始路径；[MIT](public/licenses/octicons.txt)。
- 国旗：[flag-icons 7.5.0](https://github.com/lipis/flag-icons/tree/v7.5.0/flags/4x3)，原始 SVG 字节；[MIT](public/licenses/flag-icons.txt)。
- Android／Apple：[Simple Icons 16.0.0](https://github.com/simple-icons/simple-icons/tree/16.0.0)，仅调整填色；[CC0 1.0](public/licenses/simple-icons.txt)不授予商标权。
- Chrome／Edge／Firefox：官方原始标识，具体来源及摘要见 provenance；仅标识浏览器和商店入口，不表示合作或背书。

许可证随静态产物分发；依赖声明由 `npm run notices` 生成到 [DEPENDENCIES.md](DEPENDENCIES.md)，素材来源独立维护。
