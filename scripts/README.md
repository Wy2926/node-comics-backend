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

浏览器工具使用 `PLAYWRIGHT_MODULE` 和脚本中的浏览器路径变量。真实 GPU 联调显式设置 `COMICS_COMPUTE_ROOT`；真实 LLM、R2 和支付另行配置，可能产生费用。
