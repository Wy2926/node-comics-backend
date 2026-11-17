# 计算协议

处理流程、状态归属与调度规则见[任务执行架构](ARCHITECTURE.md)。HTTP 字段见 [OpenAPI](../contracts/openapi.json)；WebSocket 消息以 `backend/app/compute_channel.py` 为准。

## 身份与连接

节点使用 HTTPS / WSS，均携带 `Authorization: Bearer <node_token>` 与 `X-Node-Id`。注册声明 `protocol_version:3`、`control_channel:"websocket"`；启用文件能力时声明 `document_protocol:2`。注册后的调度空位为 0，建立通道并上报后才能参与分配。

`wss://<control-origin>/internal/compute/v3/nodes/{id}/channel` 使用子协议 `compute.v3`。一个节点只保留最新连接；连接替换或凭据轮换使旧连接失效。节点发 UTF-8 JSON 文本帧，每条最多 4096 字节；二进制、超限和无效消息关闭连接。代理要求见[部署说明](DEPLOYMENT.md)。

## 双向控制消息

| 方向 / type | 字段与约束 |
| --- | --- |
| 节点 → `ready` | `request_id`：1–64 位字母、数字、`_` 或 `-`；`config_version`：正整数；`count`：单图空位 0–4；`document_count`：文件空位 0/1；至少一项非零 |
| 中心 → `assignment` | 原 `request_id`、`server_time`、`config`、非空 `leases`；每个租约附 `workflow_kind:"image"` 或 `"document"`，其余为对应 HTTP 租约字段 |
| 节点 → `ack` | 原 `request_id`；须在所有返回租约落盘接管后发送 |
| 中心 → `updates` | 变化的 `config` 或 `leases`；每条最多一个租约变化，含译文、停止或终态，不携带续租授权 |
| 中心 → `error` | 原 `request_id`、固定 `code`；`NODE_CONFIG_CONFLICT` 同步配置后换编号，`CLAIM_CONFLICT` 表示同编号内容不一致 |

节点先落盘容量请求再发送 `ready`；中心先提交租约和回执再发送 `assignment`。无任务时请求保持待分配，不生成空回执；ACK 前不接受另一个编号。断线重发原编号和原内容，找回同一组租约及其当前状态。

配置包含 `version`、`enabled`、执行位和各超时字段。`performance_weight` 仅供服务端调度；保留的 HTTP 配置字段 `poll_seconds` 不控制 WebSocket 节点。

重连从数据库重建快照；通道每 5 秒核对状态，空位有效期 15 秒。Ping/Pong、推送和 ACK 均不续租；独立 HTTP 心跳续期。容量请求可能长期等待，节点按最近注册 / 心跳校准的服务端时间计算推送租约的本地期限。

单连接寿命为 300 秒，到期以 `1001` 关闭并按相同规则重连，便于代理切流排空；未决请求与已接管租约继续有效。

## 单图 HTTP v3

前缀 `/internal/compute/v3`。

| 请求 | 用途 |
| --- | --- |
| `POST /nodes/register` | 协议、引擎指纹、语言、字体与输出能力；返回配置及未释放租约 |
| `POST /nodes/{id}/claim` | 幂等分配或重放 HTTP 回执，含空回执；当前节点仅用于恢复已保存的 HTTP 请求 |
| `POST /nodes/{id}/heartbeat` | 逐租约续期、停止或终态 |
| `GET /leases/{id}/input` | 原图，支持单 Range 与绑定 SHA-256 的 If-Range |
| `POST /leases/{id}/analysis` | 幂等分析检查点；无字直接终态 |
| `PUT /leases/{id}/result` | multipart metadata 与最多一个 output |
| `POST /leases/{id}/complete` | 固定错误码或停止确认 |

- 原图入口信任已认证自管节点；执行与交付仍核验租约、代次、截止及取消。
- 输入为静态 sRGB PNG/JPEG/WebP，无待应用 EXIF/ICC；下载核对长度、SHA-256、尺寸和格式。节点验证像素，中心验证有界结构。
- JSON 摘要：UTF-8、键排序、ASCII 转义、无空白、禁止 NaN 的 JSON 再做 SHA-256。
- analysis ≤5 MiB，≤200 区域，单段≤4000 字符。相同检查点重放不重开文本任务。
- 译文绑定 analysis_hash、language 和完整 segment ID 集合；revision 是前三项的规范摘要。
- 字体 ID 与任务冻结，节点声明已安装能力；不传字体路径或静默替换字体。
- 结果冻结后重传原字节。明确 400/413/415/422 拒绝转 `RESULT_REJECTED`；网络未知、429/5xx 保留原交付。
- 中心持久受理、耐久落盘并复核后一次结算。节点收到终态回执才删除本地冻结结果。

### 输出

`original` 无文件；`overlay-v1` 为最小 bbox 的单帧无损 RGBA WebP，alpha 只取 0/255，客户端 `source-atop` 合成。差分包括抹字与文字，不能仅取 OCR mask。

超出 WebP 单边 16383 的输出须协商 `overlay-tiles-v1`：8 字节 `NCOT0001`、4 字节小端清单长度、UTF-8 JSON、连续 WebP 块。清单含 `format,input_sha256,width,height,tiles`，每块含 `x,y,width,height,byte_size,sha256`。清单≤256 KiB，1–4096 块，无重叠、越界或尾随字节。完整图先渲染再切块，不能逐块调用模型。

metadata ≤64 KiB；结果文件预算独立于输入，默认 88 MiB。格式与检查点不兼容时升级协议；引擎构建指纹只用于追溯。

## 文件 HTTP v2

前缀 `/internal/documents/compute/v2`，复用节点身份。租约 `action` 为 `doc_prepare`、`doc_ocr`、`doc_render` 或 `doc_zip`；新任务通过上述共享通道分配。

| POST 请求 | 用途 |
| --- | --- |
| `/nodes/{id}/sync` | 按 `workspace_ids` 恢复租约和目录状态，每批最多 200 个目录 |
| `/nodes/{id}/claim` | 幂等分配或重放 HTTP 回执，含空回执；当前节点仅恢复已保存的 HTTP 请求 |
| `/nodes/{id}/heartbeat` | 按租约续期，每批最多 32 项 |
| `/leases/{id}/inputs` | 刷新源文件 GET 签名 |
| `/leases/{id}/uploads` | 冻结最终 ZIP 描述并取得 PUT 签名 |
| `/leases/{id}/complete` | 提交阶段元数据或固定错误码 |

所有写入核验租约 generation 与 workspace_generation。页描述只含摘要、大小、MIME 和尺寸，不含本地路径。签名只用于源文件与最终 ZIP，独立客户端不继承中心 Bearer。目录缺项或断网不是删除授权。

调用与结算规则见[任务与额度](TASKS.md)，跨仓库联调命令见 [API 开发入口](../backend/README.md)。
