# Oops App ↔ 后端接口契约（P0）

后端负责人：洪玉麟。Android 已实现 HTTP 客户端；未填写地址时使用 App 内 Mock。
所有接口使用 JSON，字段名 snake_case；整次 HTTP 请求超时 10 秒。时间窗口、原声起止、`now_ms` 都是**当前会话音频时间轴的毫秒**，不是 Unix 时间戳。

## 1. POST /v1/recall

后端只选择请求中的候选编号，App 的文字和回听时间始终取自原始候选。

请求：
```json
{
  "session_id": "session-example",
  "trigger": "button",
  "query": "把刚才那个方案留下来",
  "now_ms": 25000,
  "candidates": [
    {"id":"u1","start_ms":5000,"end_ms":10400,"speaker":null,"text":"我觉得可以先做一个找回想法的功能，预算控制在十万以内。"},
    {"id":"u2","start_ms":11700,"end_ms":16100,"speaker":null,"text":"我周五前把方案发给你。"}
  ]
}
```
响应：
```json
{"picks":[{"id":"u1","prob":0.82},{"id":"u2","prob":0.12}],"engine":"jev-1.13.0"}
```
无匹配：
```json
{"picks":[],"engine":"jev-1.13.0"}
```

`trigger` 当前为 button / notification / double_tap，后续增加 voice。`query` 可以为 null。
App 先请求最近 2 分钟；有效候选最高概率不足 0.3 时扩到 5 分钟。低于 0.1 不展示，前两项差距至少 0.2 显示单卡，否则最多显示 3 条。不存在的编号、重复编号、非法概率和原声已覆盖的候选会先过滤，再判断是否扩窗。

Jev 适配建议（已核对 [官方 Choice 文档](https://docs.typesafe.ai/primitives/choice) 与 [HTTP API](https://docs.typesafe.ai/api)）：把候选编号作为 Choice 选项，**另外提供 `no_match` 选项**，避免所有内容都无关时仍强制挑一句。`no_match` 占优则返回空 picks；其余选项直接使用 Jev `probabilities` 对应值，不要把剔除 no_match 后的概率重新归一化，也不要把整体 confidence 当作某句的概率。Choice 最多 255 选项，因此单次候选最多 254 条；更多时先分组缩小。中文对话和阈值要用真人样本验证。固定模型版本并记录真实返回版本。

## 2. POST /v1/actions/draft

只生成草稿，不创建任何任务。`reference_time` 为 ISO 8601 绝对时间，`time_zone` 为 IANA 时区，用于解析“明天／周五前”。测试预录音频时应以录制时刻为参考，正式录音用当前时刻；App 当前默认发送草稿创建时刻与手机时区。

请求：
```json
{
  "session_id":"session-example",
  "reference_time":"2026-10-06T10:00:00Z",
  "time_zone":"Asia/Shanghai",
  "utterances":[{"id":"u2","start_ms":11700,"end_ms":16100,"speaker":null,"text":"我周五前把方案发给你。"}],
  "context":[{"id":"u1","start_ms":5000,"end_ms":10400,"speaker":null,"text":"预算控制在十万以内。"}]
}
```
响应：
```json
{
  "who":null,
  "what":"把方案发给对方",
  "when":{"iso":"2026-10-09","raw":"周五前"},
  "constraint":"预算控制在十万以内",
  "assignee_candidates":["Jason","黄丽萍","江古月","洪玉麟","林春源"]
}
```
无法解析时间时 `when` 可以为 null，或 `{ "iso":null, "raw":"原始时间措辞" }`。负责人不确定时返回 null，让用户选择。Jev 负责选择／判断；自由文本草稿提取需要独立 LLM 或规则实现。

## 3. POST /v1/actions/confirm

只有用户明确点确认后调用。后端需预配置上述显示名到飞书 `open_id` / `user_id` 的映射；不根据显示名盲目创建负责人。截止日期按请求时区转换，不能默认服务器时区。

请求：
```json
{
  "op_id":"op-example-001",
  "session_id":"session-example",
  "draft":{"who":"Jason","what":"把方案发给对方","when":{"iso":"2026-10-09","raw":"周五前"},"constraint":"预算控制在十万以内"},
  "evidence":{"utterance_ids":["u2"],"quote":"我周五前把方案发给你。","clip_start_ms":11700,"clip_end_ms":16100}
}
```
接受并处理中：
```json
{"op_id":"op-example-001","status":"pending","feishu_task":null,"message":null}
```
直接完成：
```json
{"op_id":"op-example-001","status":"created","feishu_task":{"guid":"feishu-task-guid","url":"https://applink.feishu.cn/client/todo/detail?guid=feishu-task-guid"},"message":null}
```

**幂等规则**：先持久化 op_id 与确认内容，再执行飞书写入；同一 op_id 的重试、连点、网络断开或进程重启都至多建一条任务。同 op_id 不同 payload 返回 409 `OP_PAYLOAD_CONFLICT`，不得静默使用新内容。飞书调用成功但客户端响应丢失时保留可查询结果。服务端实现应覆盖调用超时但飞书已写入的恢复路径，不能仅依靠内存字典或防连点。

Android 在发请求前保存原操作及冻结的草稿。响应丢失／10 次轮询后仍 pending 时显示“查询创建结果”，继续 GET 原 op_id。不会因 HTTP 超时更换 op_id，也不会将结果未知宣称为创建失败。

## 4. GET /v1/actions/{op_id}

无请求体。返回同一个操作的当前状态；created 必须有任务 guid。
```json
{"op_id":"op-example-001","status":"created","feishu_task":{"guid":"feishu-task-guid","url":"https://applink.feishu.cn/client/todo/detail?guid=feishu-task-guid"},"message":null}
```
明确业务失败：
```json
{"op_id":"op-example-001","status":"failed","feishu_task":null,"message":"飞书授权已失效，请重新授权"}
```
未知操作：HTTP 404，使用统一错误格式，不能伪造已创建状态。

状态：pending / created / failed。App 会对 created 再 GET 一次；只有回读出同一任务 guid 才标记“已回读确认”。回读失败仍保留后端的 created 声明，同时显示“尚未回读核验”。

## 错误格式与时限

```json
{"error":{"code":"FEISHU_AUTH_EXPIRED","message":"飞书授权已失效，请重新授权"}}
```

推荐 HTTP 状态：400 参数错误；401/403 授权失效；404 操作不存在；409 同 op_id 内容冲突；429 限流；500/502 服务错误。错误信息应可显示，且不带密钥或底层凭据。长任务尽早返回 pending，App 的整次调用时限为 10 秒。流式 ASR 使用独立 WebSocket，持续静音时不设读取超时。

## P1 预留（尚未接入）

- `POST /v1/utterances`：session_id、conversation_id、utterance_id、sequence、原句与起止毫秒；以 utterance_id 去重，异步重试不阻塞录音。
- `POST /v1/conversations/{id}/end`：携带最后 sequence，后端确认之前定稿已收齐再返回承诺列表。
- 当事人确认需版本化扩展状态，例如 awaiting_confirmation / accepted / rejected；当前 P0 App 不接受这些状态。
- 长对话中承诺原声应在初步发现时暂存；不能等对话结束后才从已覆盖的 5 分钟缓冲中取回。

## 联调验收

先用 `scripts/api_smoke.sh BACKEND_URL` 核对召回、草稿；确认环境是飞书测试环境后设置 `OOPS_SMOKE_CONFIRM=1` 才执行确认与重复确认。随后在 App 调试设置关闭 Mock，填写同一地址。

需覆盖：未确认不写入；无匹配返回空 picks；非法编号不会阻止扩窗；草稿字段与证据一致；真实创建并回读；重复确认只建一条；延迟 pending、HTTP 超时和授权失效可恢复。
