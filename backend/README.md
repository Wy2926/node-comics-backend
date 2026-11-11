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

默认测试使用 SQLite 与 fakeredis。`TEST_REDIS_URL` 启用专用真实 Redis；PostgreSQL 并发测试需显式设置 `RUN_POSTGRES_CONCURRENCY=1` 和 `TEST_PG_*`，只使用测试库。也可从根目录运行 `docker compose -f deploy/compose.tests.yaml up --build --abort-on-container-exit --exit-code-from tests`。

节点跨仓库联调显式设置 `COMICS_COMPUTE_ROOT` 后运行 `tests/test_compute_node_v3.py`；默认跳过该项。模拟测试不调用真实身份、支付或模型。

[API 契约](../contracts/README.md) · [部署](../docs/DEPLOYMENT.md) · [任务规则](../docs/TASKS.md)
