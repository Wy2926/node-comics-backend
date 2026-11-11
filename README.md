# Node Comics Backend

FastAPI 控制服务，包含账户、额度、支付、持久翻译任务、Astro 官网和管理后台。依赖 PostgreSQL、Redis 8；图像计算通过 HTTPS 交给独立 `node-comics-compute` 节点。

## 本地运行

需要 Docker Compose、Node.js 22.12+ 和 PowerShell，在仓库根目录运行：

```powershell
./scripts/bootstrap.ps1
# 填写 .env 和 deploy/.env.local
./scripts/bootstrap.ps1 -Start
```

API：`http://127.0.0.1:18088`。生产部署见[部署说明](docs/DEPLOYMENT.md)。

## 开发入口

- [API 与测试](backend/README.md) · [官网](backend/website/README.md) · [脚本](scripts/README.md)
- [API 契约](contracts/README.md) · [计算协议](docs/COMPUTE_PROTOCOL.md)
- [任务与额度](docs/TASKS.md) · [运维](docs/OPERATIONS.md) · [协作约定](AGENTS.md)

可独立构建；官网站点清单来自 `contracts/extension/` 发布快照，普通构建不读取插件或计算节点源码。

源码起点：[node-comics@e0fb68b](https://github.com/Wy2926/node-comics/commit/e0fb68b1177ad0c5844fbe490bc3171753da40e2)。[GPL-3.0-only](LICENSE)，第三方许可随模块保留。
