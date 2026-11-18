# API

Python 3.11+。安装 `requirements.txt`；测试另装 `requirements-test.txt`。配置来自根 `.env.example`，本地设 `APP_ENV=development`。

在本目录运行迁移，再分别启动三个进程：

```sh
python -m app.migrate
python -m uvicorn app.main:app --host 127.0.0.1 --port 18088 --no-access-log
python -m app.workers
python -m app.dispatcher
```

```sh
python -m pytest -q tests
```

数据库回归只用 PostgreSQL。推荐从仓库根运行 `docker compose -f deploy/compose.tests.yaml up --build --abort-on-container-exit --exit-code-from tests`。直接运行 pytest 需提供 `TEST_PG_PASSWORD` 和专用库 `nodecomics_concurrency_test`（默认 `127.0.0.1:5432`、用户 `nodecomics`；可设置 `TEST_PG_HOST`、`TEST_PG_PORT`、`TEST_PG_USER`）。每例独占临时 schema。

`RUN_POSTGRES_CONCURRENCY=1` 启用额外竞争测试，`RUN_QUOTA_SCALE=1` 启用额度规模测试。进程间共享限流需指定专用 `TEST_REDIS_URL`，单进程可用默认的 fakeredis。

安装节点传输依赖后，设置 `COMICS_COMPUTE_ROOT` 指向节点仓库，再运行 `tests/test_compute_node_v3.py`。双节点 TLS、弱网与断线接管见 [Docker 网络验收](../scripts/README.md)。这些用例使用合成图像与模型响应。

[任务执行架构](../docs/ARCHITECTURE.md) · [API 契约](../contracts/README.md) · [部署](../docs/DEPLOYMENT.md) · [任务规则](../docs/TASKS.md)
