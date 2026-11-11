# 运维

- `/health/ready` 检查 API 依赖；`/health/cluster` 用于诊断后台、节点和积压，不作为所有发布的门禁。
- 容器用 `python -m app.healthcheck <role>` 检查本实例的持久心跳和 Redis，直接读取 Compose 注入的环境。文件清理每 60 秒执行一批；租约恢复仍按 `DISPATCH_INTERVAL_SECONDS` 运行。
- 数据库与译图文件建立一致恢复点。`scripts/database_backup.py` 备份，`verify_database_restore.py` 在隔离库验证；参数见 `--help`。
- 结果已发送但未知时查原 UUID、租约与供应商记录，不新建任务重试。未确认交付不能按年龄删除。
- 图片卷、节点工作目录设容量监控；清理只按持久终态和当前保留设置执行。
- `scripts/update_geoip.py` 更新本地 MMDB，保留来源、摘要和许可；API 只读挂载，缺库不阻塞任务管理。
- NginxPulse 配置在 `deploy/nginxpulse/`，日志脱敏使用 `scripts/nginx_error_log.py`；不记录凭据或签名 URL。
- 凭据轮换、备份恢复和不兼容迁移按实际在途任务安排，禁止清空生产 Redis 或测试连接生产库。

部署与排空见[部署说明](DEPLOYMENT.md)。
