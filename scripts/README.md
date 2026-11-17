# 脚本

从仓库根运行，Python 使用已安装后端依赖的环境；参数见各脚本 `--help`。

| 入口 | 用途 |
| --- | --- |
| `bootstrap.ps1` | 本地初始化与启动 |
| `export_openapi.py` | 离线导出契约 |
| `prepare_static_release.py`、`switch_release.py`、`retire_release.py` | 静态发布、切流与排空 |
| `database_backup.py`、`verify_database_restore.py` | 备份与隔离恢复 |
| `update_geoip.py`、`configure_nginxpulse.py` | 统计与地理库 |
| `verify_extension_release.py` | 验证发布包、摘要与下载地址 |
| `verify_website_*.mjs`、`verify_admin_completion.mjs` | 页面浏览器验收 |
| `tests/` | 运维与联调工具回归 |

计算通道网络验收：在后端测试依赖环境运行 `python scripts/tests/rehearse_compute_network.py --compute-root ../node-comics-compute`。使用独立 Docker 网络、临时 PostgreSQL/Redis、OpenResty TLS 和 `tc netem`；需要 Docker 的 `NET_ADMIN` 能力。报告写入 `artifacts/compute-network/`，容器和网络自动清理。

覆盖延迟 / 抖动 / 丢包 / 限速、下载中断续传、分配 / 分析 / 交付提交后的黑洞断网、服务不可用退避、租约过期和文件控制回执恢复。双节点通过两条 TLS 路径验收：只切断或延迟高权重节点，核对健康节点接单耗时、租约归属、模型调用和结算次数。图像算法、文本供应商和文件 R2 传输为本地合成夹具，ZIP 解包 / 检查点 / 打包使用真实节点代码。

浏览器工具使用 `PLAYWRIGHT_MODULE` 和脚本中的浏览器路径变量。真实 LLM、R2 和支付另行配置，可能产生费用。
