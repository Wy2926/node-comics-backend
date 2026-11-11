# 契约

`openapi.json` 是后端 API 快照。从仓库根、已安装后端依赖的环境运行：

```sh
python scripts/export_openapi.py
```

离线导出不加载私有配置或启动服务。接口修改同步更新快照，并通知客户端更新类型及兼容测试。

`extension/` 由插件的 `scripts/export-website-catalog.mjs` 导出，包含版本、站点、图标、权限和分析事件声明。只在插件发布变更时替换完整目录；后端构建不依赖插件 checkout。

官网的 `backend/shared/` 是本仓库维护的图像、字体与模型代码。协议或字体 ID 改动需核对插件和计算节点；实现细节可独立演进。
