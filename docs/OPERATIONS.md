# 运维

- `/health/ready` 检查 API 依赖；`/health/cluster` 用于诊断后台、节点和积压，不作为所有发布的门禁。
- 容器用 `python -m app.healthcheck <role>` 检查本实例的持久心跳和 Redis，直接读取 Compose 注入的环境。文件清理每 60 秒执行一批；租约恢复仍按 `DISPATCH_INTERVAL_SECONDS` 运行。
- 历史单图检查点在 maintenance 启动时及之后每 6 小时清理一轮。每轮只处理开始前已结束且可清理的任务；分批提交，批间继续恢复租约。保留期内的可读译文照常保留。
- 数据库与译图文件建立一致恢复点。`scripts/database_backup.py` 备份，`verify_database_restore.py` 在隔离库验证；参数见 `--help`。
- 结果已发送但未知时查原 UUID、租约与供应商记录，不新建任务重试。未确认交付不能按年龄删除。
- 单页和整本的失败答复保存在 `text_calls.failed_output`（JSON 字符串），按 `job_id`、`sequence` 与 `error_code`、`usage` 一起只读查询。可包含截断片段或拒答；未收到正文或已清理时为 SQL NULL。终态且核算完成后，单页按译图保留期清理（游客 24 小时），整本按文件到期时间清理；计量记录保留。
- 图片卷、节点工作目录设容量监控；清理只按持久终态和当前保留设置执行。
- `scripts/update_geoip.py` 更新本地 MMDB，保留来源、摘要和许可；API 只读挂载，缺库不阻塞任务管理。
- 后台「提交端」按首次创建任务的请求头识别：官网匹配 `GUEST_ORIGIN`，PC 插件识别 Chrome／Edge／Firefox 扩展 `Origin`，App 识别 `nodelane-comics/` User-Agent。任务重放或复用不覆盖；官网游客可从体验额度识别，其余无依据或有冲突的显示「未知」。此标记仅供查看。
- 概览的两张任务图共用一个滚动窗口（近 24 小时或近 7 天），按任务创建时间而非完成时间计数。按 UTC 整点或零点分段，首尾仅含窗口内的部分；「未知」计入平台占比的分母。
- NginxPulse 配置在 `deploy/nginxpulse/`，日志脱敏使用 `scripts/nginx_error_log.py`；不记录凭据或签名 URL。
- 凭据轮换、备份恢复和不兼容迁移按实际在途任务安排，禁止清空生产 Redis 或测试连接生产库。
- 从全局调度锁切到行锁（含回滚）时，先停止接单并排空旧 API、control-worker、document-worker 和 maintenance 的写事务；不要让两套加锁规则同时写同一队列。节点协议不变，保留未决回执和冻结结果。
- `backend/app/catalog_defaults.json` 仅初始化一次性会员卡及额度包；调价新增编号。`python -m app.migrate` 补齐缺失草稿，复用完全匹配的权益版本及报价（编号可不同），保留停售状态。月卡、季卡、年卡绑定一次性 Stripe／Creem 商品，按 UTC 权益期锚点计算日历月有效期。
- 后台和 API 只开放一次性报价的新购。使用后台停售历史自动续订价格，勿移除渠道绑定或取消供应商订阅；已有订阅仍按原约定续费或取消。官网以实时目录为准，空目录不回填静态价格；原待付款订单按原账户、报价和渠道恢复。清理旧目录须先备份并检查订单、结账、订阅及权益引用。

部署与排空见[部署说明](DEPLOYMENT.md)。
