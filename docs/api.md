# Oops App ↔ 后端接口契约

后端负责人：洪玉麟。Android 已实现 HTTP 客户端；未填写地址时使用 App 内 Mock。
所有业务接口使用 JSON，字段名 snake_case；Android 整次 HTTP 请求超时 10 秒。时间窗口、原声起止、`now_ms` 都是**当前会话音频时间轴的毫秒**，不是 Unix 时间戳。

音频坐标统一约定：ASR 返回连接内时间，App 先加该连接的 `baseMs`；App 内部及所有 HTTP 音频时间字段均为会话坐标。`clip_start_ms` 是上传 PCM 第一个样本的会话时间。后端只在模型计算中使用 `t - clip_start_ms`，响应再加回。草稿的 `reference_time` 仍是 ISO 8601 绝对时间。

候选、草稿原话及上下文可增加 `tag`、`words:[{text,start_ms,end_ms}]`、`segments:[{start_ms,end_ms,text,speaker,score,source,approx}]`；旧后端可忽略新增字段。原文必须完整保留，分段文本拼接须等于 ASR 定稿全文，起草和保存按原句 ID 从会话索引取最新信息。

## 服务地址与鉴权

当前部署 base URL：`https://giftedprofessor.xyz/oops-meeting`，例如确认地址为 `/oops-meeting/v1/actions/confirm`。部署后端监听回环，由 HTTPS 入口转发；`OOPS_API_TOKEN` 设置后所有接口（含 GET `/healthz`）要求 `Authorization: Bearer <服务访问凭据>`，鉴权失败返回 401 `UNAUTHORIZED`，不会触发飞书调用。

Android 设置按规范化 base URL 保存访问凭据；恢复旧操作时使用该操作原地址的凭据。客户端仅通过 HTTPS 或本机回环发送凭据；H5 状态只暴露 `hasBackendToken`，不返回凭据。`GET /healthz` 返回 `status/jev/voiceprint/feishu/segments_validated`，不返回飞书授权或密钥。

服务器 `--state-dir /var/lib/oops/backend` 持久化映射与确认操作。新 Oops 组织的四人名单为黄丽萍、江古月、洪玉麟、林春源，Lydia 是黄丽萍的别名。`OOPS_STRICT_PEOPLE=1` 时未配置负责人会明确报错。

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

`trigger` 为 button / notification / double_tap / voice / ask。`query` 可以为 null；ask 要求非空问题。
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
  "assignee_candidates":["黄丽萍","江古月","洪玉麟","林春源"]
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
  "draft":{"who":"林春源","what":"把方案发给对方","when":{"iso":"2026-10-09","raw":"周五前"},"constraint":"预算控制在十万以内"},
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

- `POST /v1/conversations/{id}/end`：携带最后 sequence，后端确认之前定稿已收齐再返回承诺列表。
- 当事人确认需版本化扩展状态，例如 awaiting_confirmation / accepted / rejected；当前 P0 App 不接受这些状态。
- 长对话中承诺原声应在初步发现时暂存；不能等对话结束后才从已覆盖的 5 分钟缓冲中取回。

## 5. POST /v1/utterances（已接入）

```json
{
  "session_id":"session-example", "utterance_id":"u2",
  "start_ms":11700, "end_ms":16100, "text":"我周五前把方案发给你。", "speaker":null,
  "context":[{"text":"我们还需要一个新方案。","speaker":null}]
}
```
```json
{"utterance_id":"u2","tag":"commitment","prob":0.91,"engine":"jev-1.13.0"}
```

标签枚举：`commitment / decision / idea / constraint / chitchat / uncertain`。Jev 所选项概率低于 0.5 时返回 `uncertain`，不算寒暄、不计承诺，召回加权视为无标签。规则兜底只输出明确的 `commitment`、`constraint` 或 `uncertain`，`prob:null, engine:rules`；它没有可校准的模型概率。仅出现日期、人名、收件人、否定、疑问、引用、假设或已完成事项不判为新的承诺。

标签在定稿时独立发出，不等待声纹。`context` 最多前 4 句，固定发送时的快照。成功结果按 `(session_id, utterance_id)` 缓存，重复请求直接返回首次结果，context 的变化不触发重算。成功的规则兜底结果也缓存；真正失败、没有结果的请求不缓存。当前原型最多缓存 20,000 个结果。

App 可选接口重试策略：IO、429、5xx 最多重试 2 次，间隔 1、3 秒；400、401、403 等不重试。404/501 表示不支持，本场停用对应接口。标签和声纹各限 2 个并行请求、最多 8 项运行或排队；超限标签计入分析失败、超限声纹跳过，不标为不支持。排队声纹不复制 PCM，得到执行名额后核对 session 再复制音频。单次网络调用 10 秒超时。停止收音保留进行中请求，后续结果仍更新本场；下一场开始后丢弃旧场结果，旧场的停用状态不影响新场。

## 6. POST /v1/speaker（整句已接入，句内拆分待人工验收）

```json
{
  "session_id":"session-example", "utterance_id":"u2", "sample_rate":16000,
  "pcm_base64":"<16 kHz mono s16le>", "clip_start_ms":11700,
  "text":"我周五前把方案发给你。",
  "words":[{"text":"我","start_ms":11700,"end_ms":11900}], "context":[]
}
```
```json
{
  "utterance_id":"u2", "speaker":"洪玉麟", "score":0.81, "runner_up":0.42,
  "engine":"campplus-zh", "segments_validated":true,
  "segments":[{"start_ms":11700,"end_ms":16100,"text":"我周五前把方案发给你。",
    "speaker":"洪玉麟","score":0.81,"source":"voiceprint","approx":false}]
}
```

示例只展示格式，不是实际验收结果。PCM 长度允许 0.5–30 秒；JSON 请求体上限 2 MB，容纳 base64 编码后的 30 秒 PCM。`text` 是二遍识别后的定稿全文，作为 words 对齐目标。缺字段的旧请求继续整句识别，旧响应可以没有 segments。

`source` 为 `voiceprint`（达到普通/短段对应阈值，先锁定，Jev 不可改）、`voiceprint+context`（未锁定段，经 Jev 概率 ≥0.7 且声纹前两名守卫通过）、或 null（未知）。`approx` 表示词/原文时间映射是否近似，不能用近似切字自动解析“我”的负责人。Jev 批量裁决只包含未锁定段，故障保持未知。

本地后端默认关闭拆分和 self 自动填人。只有 `voiceprint.py split-eval` 在冻结参数后的独立验收集通过，生成 `models/voiceprint/split-validation.json`，且相关判断代码、模型、注册声纹、标注集和人工真值指纹仍一致，后端重启后才允许启用。代码指纹只覆盖拆分/负责人/评估判断及其源代码依赖的规范化 AST，不含飞书与召回等无关代码；失效原因输出到后端日志。未通过时返回 `segments:[], segments_validated:false`。负责人自动填写另需独立验收集至少 15 个真实 self 案例，覆盖率 ≥60%、准确率 ≥95%，自动填人不超过 20 条时必须零错误；失败时拆分可启用而 self 仍留空。

负责人先区分明确点名/self/未知；点名须有指派关系，“发给某人”不算。self 必须只有一个明确的第一人称承诺，承诺短语完整位于一个 `source=voiceprint, approx=false` 的分段中，并已通过自动填人验收。跨段、多个承诺、近似、Jev 辅助声纹和未知均留空，交由用户选择。

## 本场承诺索引

文字、字时间戳、标签和说话人信息在内存中保留至下次开始收音，上限 20,000 句；达到上限移出最早条目并提示。标签为承诺时及时复制原声，最多 50 段且总字节不超过 16 MiB；超过上限淘汰最早原声，文字保留并显示“原声未保留”。回听/保存仅在完整选中范围被 ring 或暂存原声覆盖时取音频，不把缺失的上下文或音频空隙伪装成完整录音。

停止后显示承诺数、待分析句数和分析失败句数，结果到达实时更新。承诺列表可打开现有召回卡片、回听、保存和起草；未支持分析的后端明确显示不支持。本轮不落盘，进程退出会丢失本场索引；只有用户点“保存”才写入长期记忆。

## 联调验收

先用 `scripts/api_smoke.sh BACKEND_URL` 核对召回、草稿；确认环境是飞书测试环境后设置 `OOPS_SMOKE_CONFIRM=1` 才执行确认与重复确认。随后在 App 调试设置关闭 Mock，填写同一地址。

需覆盖：未确认不写入；无匹配返回空 picks；非法编号不会阻止扩窗；草稿字段与证据一致；真实创建并回读；重复确认只建一条；延迟 pending、HTTP 超时和授权失效可恢复。


## 自助声纹录入（2026-10-07）

`GET /v1/voiceprints` 返回 `people:[{name,registered,seconds,source,updated_at,sample_id,profile_revision}]`、`revision`、`accept`、`required_margin`。不返回声纹向量。App 服务凭据可查看四人；个人邀请凭据只返回本人及 `person` 字段。

`POST /v1/voiceprints/enroll` 输入：

```json
{"name":"林春源","source":"mic","consent":true,"single_speaker":true,
 "sample_rate":16000,"pcm_base64":"<16 kHz 单声道 s16le>",
 "sample_id":"<冻结录音的 UUID>","expected_revision":"<目录版本>",
 "expected_profile_revision":"<可选：本人声纹版本>"}
```

只允许团队成员及 `mic/remote` 现场音源。录音须 15–30 秒，有效音量片段至少 8 秒，削波比例不超过 1%；3 秒片段声纹向量的中位一致性须 ≥0.6，最弱片段 ≥0.35。以上为试用质量检查，不能验证本人身份。模型提取归一化特征后，原子替换所选成员；其他人的声纹保留。运行时文件 `<state-dir>/voiceprints.json` 为 0600，不保存 PCM 文件。

重复提交同一成员的 `sample_id` 返回原保存结果，不重复更新；版本不符拒绝。网页使用本人 `profile_revision`，不同成员可从同一次目录读取结果分别保存；同一人的过期版本仍拒绝覆盖。App 继续支持全局 `revision`。保存/删除使已有拆分及 self 自动填人验收失效；不能用历史 DJI 验收打开新注册声纹的闸门。

`POST /v1/voiceprints/test` 输入 `sample_rate:16000/pcm_base64`（0.5–30 秒），只返回 `speaker/closest/score/margin/reason/scores/accept/required_margin/quality`，不更改声纹。`quality` 含 `seconds/active_seconds/clipped_ratio`。匹配门槛仍为原配置，未为了录入降低阈值。

`POST /v1/voiceprints/delete` 输入 `name/confirm:true/expected_revision`，App 服务凭据才允许删除。删除后重启不会恢复 seed 声纹；全部删除后仍可再次录入。

### 网页与个人邀请

公开静态页为 `GET /voiceprint/`，JS/CSS 同源，无第三方脚本；配置 CSP、no-store、no-referrer。业务接口仍鉴权。部署前缀下的分享链接形如 `https://giftedprofessor.xyz/oops-meeting/voiceprint/#invite=<个人邀请>`。邀请放在 fragment，浏览器通过 `Authorization: Bearer <邀请>` 调用 API，不把 App 全权限凭据传给网页。专用 `OOPS_ENROLL_SECRET` 生成 HMAC 签名，包含成员和到期时间；更换该 secret 可撤销全部旧邀请。

个人邀请只允许 GET 本人目录、POST 本人 enroll、POST test；其他成员 enroll、delete、飞书和其他业务接口均 403，过期/篡改邀请 401。页面不存 localStorage；暂存录音在内存中，离开前台时中止正在录音，保存/放弃后清除临时音频。浏览器须支持 HTTPS getUserMedia/Web Audio，页面自己把 44.1/48 kHz 录音转为 16 kHz PCM。Web 请求客户端预算 40 秒，现有代理预算 15 秒；超时保存保留同一 sample_id 供重试。


## 试验：POST /v1/speaker-preview（2026-10-07）

仅完整 App 服务凭据可调用；个人录入邀请无此权限。服务器须安装 ONNX Runtime，并显式设置 `OOPS_SPEAKER_PREVIEW=1`。未启用返回 501。请求沿用 `/v1/speaker` 的 16 kHz mono s16le PCM、`clip_start_ms`、`text/words/context`；滚动窗口限 3–10 秒，带定稿原文时限 0.5–30 秒，可给 `clip_end_ms` 处理 ASR 末端最多 100 ms 的取整差。

返回 `experimental:true/start_ms/end_ms/activities/segments/coverage/jev_used/compute_ms/engine`。activities 的 kind 为 speech/silence/overlap；overlap 不指定姓名。segments 只按 ASR 已提供的字词时间切分，拼接必须逐字等于原文；无可靠词时间只返回整句 unknown。Jev 仅辅助声纹支持的候选、可保留 unknown，不能改写原文或补出重叠中未识别的另一人的话。

pyannote 3.0 局部编号不作为身份，不跨不连续发言共用声纹判断；连续单人活动按至多 3.2 秒独立比对，短于 1.6 秒保持未知。10 秒窗口每 5 秒移动，边缘裁剪后查询注册声纹。试验有漏报和误报；重叠活动只能视为疑似提示。

`/v1/speaker` 请求带 `preview:true` 时也可附带相同结构的 `preview` 字段。原 `speaker/segments/segments_validated` 及负责人验收闸门继续独立工作。App 将 preview 保存在单独展示字段；Candidate、召回证据、负责人解析与飞书任务不接收此字段。App 默认关闭开关，每次至多一个滚动请求，约两秒请求一次，过期、换音源或换会话结果丢弃。

## 2026-10-07：本场问答、提醒和会议文字（本地待部署）

`recall.trigger` 新增 `ask/voice`。问答从全场索引预选最多 120 句，排除口令与过短句，优先标签、字面重合与时间；Jev 只选择最能回答问题的原话，提供 `no_match`。问答校验原话编号与概率，不因原声已过期而丢弃文字。原声状态由 App 提供。规则问答无字面匹配时返回空 picks。

### POST /v1/notices

可选接口：404/501 后 App 停用此场的判断请求，仅保留规则时间提醒。全部文字明确作为数据处理；App 丢弃跨会话或旧议程 revision 的 topic/resolution 结果。

| kind | 请求字段（另含 session_id） | 响应 |
|---|---|---|
| topic | agenda: {goal,items:[{id,title,planned_ms?}],planned_ms?}, utterances: 定稿句，最多 40 句/4000 字 | {topic: 议程 id 或 on_topic/off_topic/unclear,prob,probabilities?,engine} |
| resolution | item:{id,title}, utterances: 最多 60 句 | {resolved:true/false/null,prob,engine} |
| conflict | utterance: 当前句, previous: 本场先前决定/约束，最多 30 句 | {conflict,with: 原话或 null,prob,engine} |

只有目标没有议程时 topic 可返回 `on_topic`；有议程时必须返回所发送的 id 或 `off_topic/unclear`。prob 必须为 0–1 有限数或规则的 null。跑题 ≥0.6 连续两次才提醒，5 分钟去重。缺结论 ≥0.6 才提醒，规则 resolution 返回 null，规则 topic 只做标题字面重合，从不判跑题。

冲突 Jev 选项为 p0..pn/none，≥0.6 且非 none 才提示“可能冲突”。规则仅比较明确金额上限与超限金额；提问、假设和新上限不触发。服务器补入最近 10 场的决定/约束，按字面重合取前 10 条。历史 with 增加 `id:"h:<session>:<uid>"/session_id/started_at`；本场文字仍从本地发送的原话取，历史原话来自受鉴权服务器。

### 会议文字接口

全部要求 App 全权限凭据，声纹邀请凭据返回 403；没有有效凭据返回 401。公网部署仍需设置 `OOPS_API_TOKEN`。文件存于 `<state-dir>/meetings/<session_id>.json`，目录 0700、文件 0600，带锁、原子写入。默认保留 30 天，`OOPS_MEETING_RETENTION_DAYS` 可调整；读取/同步时清理过期文字。删除的编号保留无文字标记，阻止延迟请求重建该场。

| 接口 | 请求/响应 |
|---|---|
| POST /v1/meetings/sync | {session_id,started_at,time_zone,ended_at?,agenda?,utterances:[最多500句],notices:[最多100条]} → {session_id,utterance_count,notice_count} |
| GET /v1/meetings?limit=30 | limit 1–100 → {count,items:[session_id,started_at,ended_at,goal,utterance_count,digest_counts]} |
| GET /v1/meetings/{id} | 元数据、按时间排列的 utterances、notices、digest 四类原话、按 operations.request.session_id 关联的 tasks |
| POST /v1/meetings/search | {query:1–500字} → {items:[id,text,start_ms,end_ms,speaker,tag,session_id,started_at,prob],engine} |
| POST /v1/meetings/delete | {session_id,confirm:true} → {deleted:true}；重复删除幂等，迟到同步返回 400 |

句子/提醒按 id upsert；会议上限 20,000 句及 2,000 条提醒。同步字段采用白名单，仅文字、正式标签、说话人、提醒和议程；不保存音频、词时间戳、试验 preview 或额外上传字段。每句最多 2,000 字，编号 `[A-Za-z0-9_-]{1,100}`，开始日期必须含时区且创建后不可更改。历史待办另有 owner_hint/when_raw/task_status/task_url；self 仍不自动填负责人。搜索预选最多 120 句交给 Jev，失败时仅按字面重合找原话。历史打开与搜索在 App 中只读，不回听、不创建任务。

App 每场队列绑定开始时的后端；20 秒或累积 30 句发送，同一队列单请求，停止时标记 ended_at 并最终发送。失败指数退避，404/501 停用，不合法数据等永久错误停止队列。队列在内存中，进程被系统结束时尚未成功同步的文字可能丢失；已同步内容仍在服务器。`memorySync` 默认开，可关闭自动发送。部署/安装前须按本轮计划确认四位成员同意文字上传。

`healthz` 新增 `notices:true/meetings:true`，只表示接口存在，不表示 Jev 中文判断已验收。
