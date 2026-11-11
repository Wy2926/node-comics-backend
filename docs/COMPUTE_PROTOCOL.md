# 计算协议

权威字段见 [OpenAPI](../contracts/openapi.json)、`backend/app/compute_v3.py` 和文件计算路由。节点仓库为 `node-comics-compute`；通信只用 HTTPS，不共享源码或运行目录。

## 单图 v3

前缀 `/internal/compute/v3`，携带 `Authorization: Bearer <node_token>` 与 `X-Node-Id`。

| 请求 | 用途 |
| --- | --- |
| `POST /nodes/register` | 协议、引擎指纹、语言、字体与输出能力；返回配置及未释放租约 |
| `POST /nodes/{id}/claim` | 持久 request_id 领取；重放同一内容返回原回执 |
| `POST /nodes/{id}/updates` | 最长 20 秒增量长轮询，不续租 |
| `POST /nodes/{id}/heartbeat` | 逐租约续期、停止或终态 |
| `GET /leases/{id}/input` | 原图，支持单 Range 与绑定 SHA-256 的 If-Range |
| `POST /leases/{id}/analysis` | 幂等分析检查点；无字直接终态 |
| `PUT /leases/{id}/result` | multipart metadata 与最多一个 output |
| `POST /leases/{id}/complete` | 固定错误码或停止确认 |

- 执行位包含下载、等文本与交付；本地 GPU 并发由节点控制。新领取每批最多 4 页。
- 原图入口信任已认证自管节点；执行与交付仍核验租约、代次、截止及取消。
- 输入为静态 sRGB PNG/JPEG/WebP，无待应用 EXIF/ICC；下载核对长度、SHA-256、尺寸和格式。节点验证像素，中心验证有界结构。
- JSON 摘要：UTF-8、键排序、ASCII 转义、无空白、禁止 NaN 的 JSON 再做 SHA-256。
- analysis ≤5 MiB，≤200 区域，单段≤4000 字符。相同检查点重放不重开文本任务。
- 译文绑定 analysis_hash、language 和完整 segment ID 集合；revision 是前三项的规范摘要。
- 字体 ID 与任务冻结，节点声明已安装能力；不传字体路径或静默替换字体。
- 结果冻结后重传原字节。明确 400/413/415/422 拒绝转 `RESULT_REJECTED`；网络未知、429/5xx 保留原交付。
- 中心持久受理、耐久落盘并复核后一次结算。节点收到终态回执才删除本地冻结结果。

## 输出

`original` 无文件；`overlay-v1` 为最小 bbox 的单帧无损 RGBA WebP，alpha 只取 0/255，客户端 `source-atop` 合成。差分包括抹字与文字，不能仅取 OCR mask。

超出 WebP 单边 16383 的输出须协商 `overlay-tiles-v1`：8 字节 `NCOT0001`、4 字节小端清单长度、UTF-8 JSON、连续 WebP 块。清单含 `format,input_sha256,width,height,tiles`，每块含 `x,y,width,height,byte_size,sha256`。清单≤256 KiB，1–4096 块，无重叠、越界或尾随字节。完整图先渲染再切块，不能逐块调用模型。

metadata ≤64 KiB；结果文件预算独立于输入，默认 88 MiB。格式与检查点不兼容时升级协议；引擎构建指纹只用于追溯。

## 文件 v2

前缀 `/internal/documents/compute/v2`，复用节点身份。节点声明 `document_protocol:2`；中央只向固定宿主发 `doc_prepare`、`doc_ocr`、`doc_render`、`doc_zip`。

`sync` 恢复目录和租约；`claim` 幂等领取；`heartbeat` 续期；`inputs` 刷新源 GET；`uploads` 冻结最终 ZIP PUT；`complete` 提交阶段元数据或固定错误码。

所有写入核验租约 generation 与 workspace_generation。页描述只含摘要、大小、MIME 和尺寸，不含本地路径。签名只用于源文件与最终 ZIP，独立客户端不继承中心 Bearer。目录缺项或断网不是删除授权。

恢复与唯一 LLM 调用遵循[任务规则](TASKS.md)。节点联调需显式 `COMICS_COMPUTE_ROOT`；普通 API 测试独立运行。
