# 部署

本地用根 `compose.yaml`；生产用 [compose.server.yaml](../deploy/compose.server.yaml)。配置模板为 [server.env.example](../deploy/server.env.example) 与 [.env.server.example](../deploy/.env.server.example)。

## 约束

- PostgreSQL、Redis、网络和图片卷为持久基础设施。应用发布不重建或清空它们。
- 所有 API、worker、maintenance 共用数据库、Redis namespace 和图片卷。Redis 要求 `noeviction`。
- 身份使用 OIDC，生产关闭开发认证；源站 Cookie 不上传。
- 迁移显式运行 `python -m app.migrate`。就绪探针不代表数据库已迁移。
- 不兼容升级先排空任务和未知供应商调用，再备份、迁移、发布；保留原授权、预占与账本。
- API 蓝绿切流；worker 独立排空；maintenance 单活交接。静态页面独立发布。

## 构建

从仓库根目录执行，版本使用不可变标签：

```sh
docker build --build-arg RELEASE_ID=<版本> -t <API镜像:版本> backend
docker build -f backend/Dockerfile.static --target website --output type=local,dest=artifacts/site-export .
docker build -f backend/Dockerfile.static --target admin --output type=local,dest=artifacts/admin-export .
```

API 镜像不含页面。官网和后台由 Nginx 提供，参考 [代理模板](../deploy/openresty.comics.conf)。Drive 连接页作为插件仓库的独立静态产物部署。

## 切换

1. 配置候选槽位并启动，核对 `/health/ready` 和 release。
2. `scripts/switch_release.py api` 原子更新代理 include，校验并 reload。
3. 旧代理连接退出后，用 `scripts/retire_release.py api` 排空旧实例。
4. 静态产物用 `scripts/prepare_static_release.py` 准备，再用 `switch_release.py static` 激活。

各命令参数见 `--help`。切换后探测失败不自动回退；确认实际代理状态再处理。保留仍被页面引用的哈希资源。

文件翻译还需私有 R2、上传 CORS、document-worker 和具备文件能力的持久节点；按[任务规则](TASKS.md)完成配置后启用。匿名体验需真实 Turnstile、稳定 HMAC 密钥和正确的可信代理/IP 配置。

部署演练：`python scripts/tests/rehearse_deployment.py`，使用隔离 Docker 与合成供应商。真实登录、付款、GPU 和生产部署分别验证。
