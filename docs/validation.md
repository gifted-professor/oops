# 阶段 A 验证记录

日期：2026-10-06。设备：Redmi 2311DRK48C / Android 16 / HyperOS 3.0.304。以下区分代码验证、真机行为和需要真人配合的验收。

## 构建和版本

`./scripts/check.sh --warning-mode all lintDebug`：52 项单测通过，0 failure / error；assembleDebug 成功，Lint 0 issue，无编译警告。Gradle 的 configuration-cache 建议是构建提示。

30 分钟音频长测 APK：`android/app/build/outputs/apk/debug/app-debug.apk`。
SHA-256：`1d660707c6d121d5d00b7c7ee14e305b74fe558948a8a3a6f88cee55b0ffb6de`。
上述哈希绑定 30 分钟音频长测版本，归档于该次证据目录的 tested-app.apk。最终版本另补充确认失败重试、表单提交状态和内置 WAV 调试选择，版本哈希及最终链路结果在收尾时单独记录。上述 APK 包含本机火山调试鉴权；不提交，不公开分发。

测试覆盖：环形缓冲跨环／覆盖／截断、字幕排序／清理、火山二进制协议与增量结果、热词配置、召回时间窗及候选编号／概率验证、日期解析、HTTP／桥 JSON、WAV 格式／分块读取、确认幂等、冻结请求恢复、丢失响应后的查询、回读核验和 HTTP 授权／错误提示。

## 浏览器 H5

内置浏览器，以 375×812 分别执行 light / dark：开始 → 5 条定稿 → Oops → 卡片 → 前一句 → 播放真实 dialog.wav → 草稿 → Jason → 确认 → pending → Mock created。第二次召回显示 2 条候选；保存后在长期列表可见；调试设置和诊断可读；新会话清空旧字幕。

两页 console warn/error 均为空；页面没有水平溢出。截图位于本地 `evidence/browser/`。Mock 明确标注未写入飞书。补充确认状态改动后，最终浅色主链路已再次通过，console warn/error 仍为空，截图 final-created.png。结束时已停止模拟收音，并恢复浏览器尺寸；localStorage 中留有测试保存记录。

## 真机内置 WAV

WAV：`assets/test/dialog.wav`，25.36 秒合成演示音频；实际火山流式 ASR，Mock 召回／行动。

五条定稿、召回卡片、原声播放／停止和保存原声已观察；保存日志 `audio=true`。行动草稿选 Jason 后返回 created，操作编号 `23407c01-4548-4911-821f-fbf21ddc501c`，readback=true。截图 `evidence/device/mock-created.png`；任务为 Mock。

| WAV 结束后 | 召回原话 | 延迟 |
|---|---|---|
| 10 秒 | 注册前先体验产品的想法 | 320 ms |
| 30 秒 | 同上 | 306 ms |
| 60 秒 | 同上 | 318 ms |
| 120 秒 | 同上 | 313 ms |
| 305 秒 | 已过期，页面显示“没找到”，旧字幕已清理 | 无召回卡片 |

定时证据在 `evidence/device/timed-recall/`。这些检查发生在 P0 测试版本上；随后补充了外部 WAV 内部路径、HTTP 错误提示、WebView 恢复／备份规则与 Mock 禁止打开假飞书链接。最终版本已安装；五条定稿、HTTP 召回／草稿和重启恢复后的状态查询通过。最终 Mock 原声与保存闭环也已通过，结果见下文。

合成音频的 Oops 在 ASR 里漏识别；明确的“把刚刚那个想法留下来”也会作为指令排除，避免 2 分钟后召回指令本身。真人 Oops 识别率尚未验证。

## 真实录音与熄屏

三份源 MP3 在 `讨论/`，均为 48kHz / stereo / 192kbps；源文件哈希在转换前后相同。转为 PCM 16kHz / mono / 16bit 的完整 WAV 并通过完整解码，各提取前 180 秒测试副本。

| 文件 | WAV 时长 |
|---|---|
| 0235 | 2598.379 s |
| 0238 | 1658.219 s |
| 0236 | 1444.032 s |

manifest 和副本在 gitignored `test-media/`。前三分钟仅是候选样本，尚未完成独立听审与召回原话标注，不能据此宣称准确率达到阶段 E 指标。

这台 Redmi 拒绝 App 读取 shell 创建在外部目录的音频（EACCES）。改为 `adb shell run-as` 把测试副本写入 App 内部 files/tests，无需增加共享存储权限。

默认“智能限制后台运行”下，熄屏后 CPU／网络受限：ASR 约每分钟断开、约 1.3 秒后重连，期间大量转写缺失。仅增加 Android deviceidle 白名单未解决，测试后已撤回该白名单。随后只将 Oops 的省电策略改为“无限制”；持续熄屏转写已恢复，截图 `evidence/device/battery-unrestricted.png`。

30 分钟测试使用 0235 的真实录音，脚本正常结束（exit 0），114 条定稿、99 次清理，无崩溃。30 个服务样本均为 microphone 前台服务，缓存已到 5:00。第 10 分钟制造 30 秒飞行模式断网，约恢复网络后 16 秒重连，后续持续定稿。一次断网产生六段重试记录，界面显示缺口；不等于六次独立断网。

采样间隔 60 秒，最后一次采样为 1740 秒，脚本到 1800 秒后结束。熄屏样本位于第 1–5、13–22 分钟；手机中途多次亮屏，不是连续 30 分钟熄屏。预热后熄屏 PSS 151.75–161.28 MiB，全部状态 PSS 151.75–255.36 MiB，末样本 252.16 MiB。混合状态回归斜率 +4.255 MiB/min，受后半段亮屏影响，不能宣称全程内存恒定；后半段亮屏样本约 247–255 MiB。证据目录 `evidence/device/stability-real-0235-unrestricted/`。网络恢复为原飞行模式值 0；测试结束已停止收音。

## 后端准备

四个 P0 HTTP 接口的 curl smoke 在本机隔离 fixture 上通过，重复 confirm 返回同一任务编号。该 fixture 没有调用飞书；真实后端／飞书应用授权和人员 ID 映射仍缺失。

真实 Jev Choice 单次探针使用合成演示句子，正确选择 u2；模型 `jev-1.13.0`，719 ms，583 input / 51 output tokens。报告 `evidence/jev-probe.json`。这验证接口、凭据和单个演示例，不代表 DJI 录音召回准确率，也不代表已与 Android 后端联通。

密钥仅在 gitignored `.jev.env`（600）中；源码、H5 和 APK 不含 Jev key。接口契约：`docs/api.md`，以官方 TypeSafe API／Choice 文档核对。

## 尚未完成的验收

- 真人手机麦克风台词 → 字幕／召回／回听／保存／确认 Mock。
- 真人拍手回听对齐 ≤ 0.5 秒、Oops 热词识别。
- 独立持续熄屏／同状态内存长测（本次包含亮屏与熄屏），最终版本真人闭环。
- 阶段 B：真实后端与飞书任务创建／回读；阶段 C：录音豆 SDK + license。
- 阶段 D–F：门控／语音指令／承诺发现、长时间与准确率验收、比赛现场准备。

阶段 A 当前不能标记全部验收完成；不会用 Mock、预录音频或技术解码替代真人麦克风／听感验收。

## 最终版本与 HTTP 故障联调

最终 APK SHA-256：`e7cf15bd403c7d451206b8ee9202630a5796ec53d4ba89a1ce497a6ccb35b724`。相对长测版本，仅确认重试策略／表单提交状态／debug 内置 WAV 选择有变化，ASR、环形缓冲、WAV 取流和前台服务实现相同；差异保存在 evidence/stability-to-final.patch。

本机 loopback HTTP fixture 模拟成功后关闭连接：App 显示结果未确认，强制停止并重启后恢复冻结草稿，查询达到 created 且 readback=true。操作 `8a191d9c-986a-420c-95ed-8a04b40353d3`：POST 1 次，创建 1 条，重启没有重复提交。截图 http-lost-response.png、http-restart-pending.png、http-reconciled.png。它没有调用飞书。

本机 fixture 先返回 503，查询后返回明确 failed：表单保持冻结、按钮变为“重试同一操作”。操作 `4ea5db97-bb65-4713-8547-d8c5025cab15` 重试成功且 readback=true；服务端记录 POST 2 次、创建 1 条，前后 op_id、完整确认请求完全一致。截图 http-rejected-pending.png、http-definite-failed.png、http-retry-created.png。

最终 Mock 闭环：五条真实火山定稿 → 想法卡片（311 / 306 ms）→ 原声 playing / ended → 保存（audio=true，已保存数 1→2）→ 草稿选 Jason → created / readback=true，操作 `eeb9bca0-d839-4901-be1a-8ca4bd1d2a15`。截图 final-playback.png、final-saved.png、final-mock-created.png；日志 final-logcat.txt。已恢复 Mock、停止收音、撤除 adb reverse、停止本机 fixture；没有写入飞书。

## Mac mini 收音 + 本地飞书后端（2026-10-06）

设备同上，Mac mini M4 + Hollyland 无线麦（USB 接收器）。`RemoteAudioSourceTest` 新增 1 项，单测共 53 项通过，Lint 0 issue；本地后端 `--self-test` 通过。

- 推流：手机连接后约 70 ms 收到首帧，3 秒约 29–30 帧（实时）；重连立即恢复。
- 测试音频经 Mac 推流：五条火山定稿与直接 WAV 一致；Mac 端双击标记触发召回，Jev（jev-1.13.0）714 ms 选中“那我周五前把新方案发你。”。
- Jev 提示词：无 query 的按钮召回原用“没有符合的原话”作 no_match，演示对话上 no_match 占 0.69；改为“全是寒暄／噪声”后，演示对话选承诺句（0.67），纯寒暄样本仍为 no_match（0.78）。
- 草稿：负责人未知（“我”的说话人未知）、截止 2026-10-09、约束“预算不能超过10万”。
- 真实飞书：选林春源确认，lark-cli 用户身份创建任务并回读，App 显示已回读确认；飞书侧核对标题、全天截止 10-09、负责人本人、描述含原话／约束／原声时段。重放同一确认返回同一任务，attempts=1。
- 切换到无线麦实采：App 自动重连并补缺口，ASR 重新连接。

负责人映射：洪玉麟、林春源已匹配；Jason、黄丽萍、江古月不在通讯录，回落给本人。真人对着无线麦的台词、召回和听感验收仍待现场进行。

## 声纹识别可行性（2026-10-06，Mac mini 本机）

数据：`讨论/` 三场 DJI 录音（0235 / 0236 / 0238，约 95 分钟，4 人：林春源、江古月、洪玉麟、黄丽萍）。工具：`scripts/voiceprint.py`，sherpa-onnx 1.13.8 + pyannote segmentation 3.0（按换人切分）+ 3D-Speaker CAM++ zh-cn 声纹模型；模型、声纹与片段只在本机 gitignored `models/`。

- 切分：VAD 在讨论录音上切不开人（41/43 分钟判为语音）；改为换人切分，每段最长 3 秒。
- 聚类：指定 4 人，4 类与人工标注一一对应，每人三场录音都有出现，跨场声纹一致。
- 注册：0235 中每人约 30 秒最典型的 2–3 秒片段；加长到 90 / 180 秒（取最长段）反而变差，因长段混入他人。
- 人工抽查：0236 / 0238 随机 40 段 2–4 秒（按人均衡、打乱、隐藏结果），听音 + 识别文字标注：单人 30 段，两人都有 10 段。单人片段识别 30/30（样本小，置信区间较宽；初标 24 号为江古月，复核后更正为黄丽萍，与识别一致）。
- 两人都有的片段占 25%：识别会给出其中一人，相似度（0.56–0.78）与单人片段重叠，无法据此区分混合段。
- 自动聚类标签与人工一致 30/30；按自动标签在 1280 段上：1–2 秒判定 81% / 正确 96%，3 秒以上判定 99% / 正确 98.5%。

结论：4 人内用约 30 秒注册即可较准地识别单人发言；主要问题是讨论中一句里换人。接入时应按 ASR 分句（必要时按词时间戳再细分）识别，短句或低相似度标“未知”，自动填负责人用更高阈值。注册需本人同意，声纹仅存本机、可删除。

## Jev 标签与会话索引、句内拆分原型（2026-10-06）

本节是上述旧 APK／53 项测试之后的新版本记录。`./scripts/check.sh lintDebug` 成功：63 项 Android 单测，后端 self-test，6 项独立标注/评估与探测统计测试；H5 JS 语法检查通过，Lint `No issues found`。APK SHA-256：`00c2b6e4444b72b4d267baab89c0fa22917206c4fa03331172086821dfb2429e`，已安装 Redmi。完整日志 `evidence/jev-final-check.log`。

### Phase 0 完整时间戳探测

参数与 App 保持一致，关闭 ASR 自带说话人信息，启用二遍、ITN、标点和 utterances。两场非注册录音完整送音，离线发送速度为 8 倍；另有实时速度初步 smoke。完整结果位于 `evidence/asr-words-complete/`，汇总 `evidence/jev-phase0-report.json`。

| 录音 | 原音频秒数 | 定稿句 | 有有效词时间 | 对齐 ≥90% | 标点差异 | ITN 候选／改字 |
|---|---:|---:|---:|---:|---:|---:|
| 0236 | 1444.032 | 74 | 74 | 74 / 74 | 74 | 0 / 0 |
| 0238 | 1658.219 | 88 | 88 | 88 / 88 | 88 | 0 / 0 |

162 句非标点文字均 100% 匹配。两场均完整发送、收到服务端终包，传输错误为零。火山会返回空格 token 的 `-1` 时间哨兵；统计不将空格作为有声词，App 丢弃负时间。有效词时间都位于本句区间内。ITN 候选用数字变体启发式分组，需人工确认；这批录音未观察到 ITN 或改字，因此不能从本批数字推断其真实对齐质量。

0236 最初因短尾包收到 45000081；只给最后不足 200 ms 的包补静音后，完整重跑成功。失败响应与尾包诊断留在 `evidence/asr-tail/` 等目录，不参与标注抽样。末尾静音最多影响最后一包；原 WAV 时长单独保留，导出标注片段按返回坐标补末尾静音。探测未用“已有定稿”代替成功终止。

### Phase 1 手机闭环

隔离服务 `8807 --feishu dry-run`，真实 Jev 返回 `jev-1.13.0`；Mac `7794 --wav` 推送内置对话加 0238 多人录音，音频 355.362 秒，手机收音约 6 分 20 秒。后端并发日志确认标签与声纹独立到达。停止后页面显示“本场发现 1 个承诺”，早期 u4 已被 5 分钟字幕窗口淘汰，但会话索引仍能：

- 打开 0:17–0:20 的“那我周五前把新方案发你。”，保留其前后句上下文；
- 回听达到 playing → ended，使用索引保留 PCM；
- 保存长期卡片，`audio_expired=false`，原句、words、tag 均保留；
- 起草截止 2026-10-09，负责人为空（self 仍关闭）。

证据：`evidence/device/jev-phase1/` 的 `logcat.txt`、`stopped-summary.json`、`commitment-list.json`、`commitment-card.png`、`draft.json`、`saved-commitment.json`。本轮没有确认或创建飞书任务。测试使用预录音频，尚未完成 Hollyland 真人台词与听感验收。

安装最终 APK 后再次用内置 WAV 完成 5 句真实定稿，UI 显示约束／想法／约束／承诺／决定，汇总为 1；`final-apk-smoke.json`、`backend-final-smoke.log`。测试后停止收音，恢复原先 `remote 7790` 与 HTTP `8787` 调试设置；关闭本轮隔离后端/音频桥/日志采集并撤除其 adb 映射，原服务未重启。新接口尚未切换至正式服务。独立标注页 8793 保留运行，供人工听辨。

### Phase 2 仅技术验证，待人工准确率验收

一条 20.009 秒真实句子的技术 smoke 返回 6 段，文字完整保留，词对齐 100%，计算 0.404 秒；`evidence/split-technical-smoke.json`。这些数字只表明算法可运行，不是说话人准确率。

盲标集已冻结 55 句（0236 开发 29；0238 验收 26），页面 http://127.0.0.1:8793/；`models/voiceprint/split-check/manifest.json` 与每句 WAV。严格规则筛到 10 个 self 候选，未满足计划 15 句，必须人工核实其他句子或补充独立录音。未创建人工答案文件，也未用旧聚类标签充当真值。

运行 `split-eval --no-jev` 明确返回待人工标注，不生成验收配置。三档按字正确率、乱填率、未知率、换人点、短插话与 self 指标**均待标注后计算**。重叠区与听不清区从主指标排除，重叠命中单列；小样本 self 自动填人要求零错误。评审后的样本门槛已收紧为独立验收集至少 15 个真实 self 案例，开发样本不计入。调参只看开发集，验收检查冻结参数与相关判断代码/模型/注册数据指纹。

线上默认仍是整句说话人，分段展示与 self 填人只有通过验收才启用。未通过时 `/v1/speaker` 返回 `segments=[]`、`segments_validated=false`；App 拒绝未验收的分段。当前无词时间时保守返回整句近似，不实现有声时长均分文字的退化方案；当前完整语料无需该退化。接口与下一步操作详见 `docs/api.md`、`docs/jev-integration-handoff.md`。

## 评审后修订

基线已保存为 `b8ffa4e`。修正 Python/Kotlin 规则：“我周五之前把方案发给你”为 self，“玉麟你周五之前把方案发我”为洪玉麟，“春源，你下周一把报告交了”为林春源。仅在明确日期后归一化“之前”；过去时、引用、疑问和否定仍拦截，收件人不作负责人。HTTP dry-run + no-Jev smoke 三例通过，self 仍为空，句内拆分仍 `segments_validated=false`；`evidence/jev-review-http-smoke.json`。重新扫描完整语料，self 候选仍为 10（开发 3、验收 7），没有重新抽样或覆盖人工标注。

指纹改为相关判断根节点及其源代码依赖的规范化 AST。回归测试验证：无关飞书逻辑、局部变量同名的新无关函数、注释/文档修改不影响指纹；相关阈值、函数与类的依赖变化会改变指纹。真实源码探测中修改 `CONFIRM_WAIT_S` 保持指纹，修改声纹阈值判断使其改变。模型/注册文件仍完整哈希；失效原因输出到日志。旧整文件指纹不可免验收迁移，目前没有通过文件需要迁移。

分析队列每接口最多 8 项（含运行中的 2 项），协程创建前预留名额，超限不禁用接口。声纹执行前才复制 PCM，同时核对 session，避免旧队列上传新会话音频。取消释放预留名额的回归测试通过。会话文字与承诺分别维护时间索引；乱序到达、替换、淘汰及最新 speaker 更新均有回归测试，前 4 句不再排序整场。汇总列表复制移出音频 pipeline 锁。

`./scripts/check.sh lintDebug` 通过：Android 66 项、0 失败；后端自检与 Python 9 项通过；Lint 无问题。日志 `evidence/jev-review-fixes-final-check.log`。修订 APK SHA-256：`d8202ec44ef8dcc547df1abc872a48cfda68b9d2785130acf16138bcca471ddb`，已安装 Redmi。本轮未运行新的 6 分钟、全天压力或真人 Hollyland 验收，前述长于 5 分钟证据对应基线版本；这些新检查不能替代真实标注与听感。

修订 APK 使用内置 WAV + 隔离规则后端完成 5 句真实 ASR、承诺汇总 1、打开 u4 原句、起草负责人留空的手机 smoke；`evidence/jev-review-phone-smoke.json`、`jev-review-phone-card.txt`、`jev-review-phone-draft.json`。测试未确认飞书任务。结束后停止收音、恢复原调试设置、撤除本轮 adb 映射并关闭隔离服务；8793 盲标页及原服务保留。

## 手机连接香港后端（2026-10-06；服务器 UTC 2026-10-07）

已完成这轮迁移闭环，详见 [部署记录](server-deployment.md)。Redmi 使用移动网络，全部 adb reverse 撤除，Mac 本地业务后端未运行；HTTPS 后端为 `https://giftedprofessor.xyz/oops-meeting`，Mock 关闭且访问凭据已配置。

- 内置合成 WAV 经真实火山 ASR 得到 5 条定稿，手机调用远程 Jev 标签与声纹接口，UI 标签为约束／想法／约束／承诺／决定；召回取回「那我周五前把新方案发你。」。证据 `.local-backend/diagnostics/phone-server-transcript.png`、`phone-server-recall.png`。合成声音返回未知说话人，没有冒充注册真人。
- 物理手机原生测试复用实际 `HttpOopsApi`、`ActionWorkflow` 与 App 持久化操作库，点名草稿负责人为林春源；确认创建明确标为合成测试的任务，GUID `5633ee62-6b72-48a9-8f17-8644c48517a3`，回读确认成功。重复同一操作返回同一 GUID，服务器 `attempts=1`。这是设备客户端测试，没有冒充真人点击界面。
- 重启香港 `oops-backend` 后，手机再次查询和回读同一 GUID；服务器 CLI 独立重新读取飞书任务，标题、负责人和测试约束均正确。服务已启用自启，没有重启整台主机。
- 新 Oops 组织的林春源、江古月、洪玉麟、黄丽萍（Lydia）映射齐全。规则和 Jev 均识别 Lydia 别名，未映射成员在服务器严格模式下直接报错。
- 缺失/错误服务凭据的 health、查询、确认、标签和声纹请求均返回 401；鉴权测试证明不会先执行飞书写入。两端 Nginx 配置测试通过；原官网及已有路由复查正常。
- `./scripts/check.sh lintDebug` 通过：68 项 Android 单测、0 失败，后端 self-test、10 项评估/规则测试、1 项 HTTP 鉴权测试；Lint `No issues found`。完整日志 `.local-backend/diagnostics/server-migration-check.log`。
- 测试后卸载测试 APK（保留主 App），恢复手机麦克风并开始实时收音；`phone-server-ready.png` 显示「手机麦克风／收音中／实时识别中」。USB 不再承载后端或音频转发。

本轮没有替代真人语音/回听听感、迁移后网络切换、全天运行和人工标注准确率验收。当前句内拆分关闭，`segments_validated=false`，self 自动负责人也没有绕过闸门。Hollyland 接在 Mac 的选项仍依赖现场收音桥。

设备测试需先单独构建安装 androidTest APK；默认不执行网络访问。显式 `-e remoteSmoke true` 做只读/草稿核验；只有额外 `-e confirm true` 才确认测试任务，使用持久化的同一测试操作防重。此命令用于人工安排的集成验收，不纳入默认 CI。

## 音源切换与错误状态修复（2026-10-07）

用户报告切换到手机麦克风后仍看到 Mac 未连接。实际复现：收音中的 `settings.set(source=mic)` 只保存了下次音源，当前 remote worker 未替换；旧版同时仅根据 ASR socket 显示「实时识别中」，即使没有 PCM。旧 APK 的真机回归测试明确失败，仍为 remote/重试状态，证据 `.local-backend/diagnostics/source-switch-baseline.txt`、`source-switch-before.png`。

修复后，设置保存音源或修改正在使用的 Mac 地址，会立即替换输入，不清空 session、会话索引、字幕和环形缓存。切换时刷新 ASR 连接来结束旧输入的未定稿句；所有音频/状态/错误/标记回调按音源实例过滤，旧 worker 不能污染新输入。增加 `sourceReady`，等待或断开时 UI 显示「等待收音／等待音源」，实际收到 PCM 后才显示收音中。

物理 Redmi 原生回归通过：断开的 Mac 与手机麦克风之间反复切换 3 轮，旧提示不回写；接通实际 Wireless microphone 收音桥、保持 remote 模式只改地址能立即重连；内置 WAV 产生真实 ASR 定稿后切回麦克风，原句 ID、本场 session 和缓存仍保留。测试报告 `passed=true, mac_connected=true, session_preserved=true, transcript_preserved=true, source=mic, buffered_ms=9300`，见 `.local-backend/diagnostics/source-switch-all-sources.txt`。这是连接和输入切换测试，不替代真人回听听感验收。

`./scripts/check.sh lintDebug` 通过：68 Android 单测、后端自检、10 项 Python 评估/规则测试、1 项 HTTP 鉴权测试，Lint 无问题。新 APK SHA-256 `f24024189cd9c5cb9614834057ce4027d2a7775859c4e03c13b9a00e6ec36d94`，已安装。完整日志 `source-switch-check.log`。测试结束卸载测试 APK，撤除临时音频 adb reverse，保留主 App 的手机麦克风收音与 HTTPS 后端配置。等待/就绪画面分别为 `source-switch-waiting.png`、`source-switch-ready.png`。

复跑原生切换测试需要显式 `-e sourceSwitch true`，同时用 adb 启动前台 Activity 的 DEBUG_START remote（测试地址 `127.0.0.1:1`）。可选 `-e macAddress 127.0.0.1:7790` 验证实际桥，运行前临时设置音频 adb reverse，结束撤除；该测试会启动一场新录音，请仅在安排好的设备验收时运行。


## App 与服务器网页自助声纹录入（2026-10-07）

App 已增加设置页入口、本人姓名/同意、固定 30 秒朗读、时长/音量预检、明确保存及不同文本 10 秒匹配。临时缓冲有上限，WAV 注册被拒绝；保存仅替换选中成员，网络重试复用 frozen sample_id；删除后重启不恢复旧 seed。服务器公开手机页配套四个独立到期邀请，权限仅为本人录入及测试。不同成员用独立 profile_revision 可同时保存；同一成员的旧版本不能覆盖。

验证：`./scripts/check.sh lintDebug` 成功，71 项 Android 单测（0 失败）、后端自检、10 项评估/规则测试、2 项 HTTP 鉴权/邀请测试、8 项注册表/质量测试，Lint `No issues found`。完整日志 `.local-backend/diagnostics/voiceprint-web-check.log`；新增 web PCM 转换检查对 16/44.1/48 kHz 的 30 秒时长及语音带波形、削波范围共 4 项通过。之后独立重跑 HTTP 测试与音频检查通过。

真实 CAM++ 模型在临时目录用既有 WAV 重复成 30 秒，完成提取、保存、重复提交防重、重启加载和同一输入匹配；其他成员向量未变。该夹具只验技术闭环，不把同一录音匹配算作识别准确率，也没有写生产声纹。

Redmi 原生集成测试实录手机麦克风 10 秒并经 HTTPS 调用在线 test，获取四人比较和录音质量；拒绝无同意录入，前后目录 revision 相同。证据 `.local-backend/diagnostics/voiceprint-phone-test.txt`。自动测试只诊断没有声纹写入；未知结果说明音频未达当前匹配门槛，不是准确率验收。App 麦克风链路不能替代所有移动浏览器兼容性验收。

生产 HTTPS 核验四个本人目录、禁止写他人声纹/删除/飞书、禁止匿名 API、静音录入被拒；进程重启后目录未变。种子声纹哈希未变，运行时 voiceprints.json 尚未生成。摘要 `.local-backend/diagnostics/voiceprint-web-verification.json`。线上朗读 UI、同意开关和禁用按钮在 390 px 手机尺寸可用，截图 `.local-backend/diagnostics/voiceprint-web-ready.jpg`。四人尚未现场录入；新的识别准确率及跨设备/多人会议效果仍须独立真人评估，拆分和 self 闸门仍关闭。

最终修正 App 成员列表和朗读文本的手机排版后，重新 assembleDebug 并安装 Redmi，截图 `voiceprint-enrollment-ready.png` 已核对。最终 APK SHA-256：`1cbc3f04aacb892eff44a36d5f3cf00ff8d47a26f8222cad7fa2e11017475fd3`。该最后一步只调整 H5 排版及音源文案；原生测试逻辑未改，JS 语法检查通过。测试 APK 已卸载，正式 App 保留；adb reverse 列表为空。


## 字幕声纹留空：音频结束边界及状态显示（2026-10-07）

用户实测 3 句均无姓名。取回同一进程中尚存的 13.4 秒 PCM 缓冲，配合原 ASR 日志定位：u3 为 [10562,13402] ms，而实际 ring.endSample=214400（13400 ms）。严格 ring.read 对多出的 2 ms 返回 null，声纹协程直接跳过；不是服务器没有新注册声纹。用当前线上模型对原片段重新查询：u1 最高林春源 0.471、u2 最高洪玉麟 0.403，均低于 0.5；u3 洪玉麟 0.547、第二名 0.199，满足门槛。此处为模型匹配结果，尚未收到真人逐句身份答案，不能当准确率真值。

修复仅对声纹读取允许最多一帧（100 ms）的尾部误差，截取实际持有的音频；不补造音频，不取已过期的开头，不接受更大的缺口。严格的证据回听读取规则保持原样。未匹配、太短、无音频、排队拥挤、服务不支持及请求错误都发出状态；snapshot、字幕及会话索引保存 speakerState/speakerScore，标签异步更新不会覆盖它们。未确认结果的 speaker 仍为 null，不能被当成负责人。

完整 check.sh lintDebug 通过，Lint 无问题；随后新增标签/声纹状态并发更新回归测试并单独重跑单测，共 74 项、0 失败。包含原 2 ms 越界、过期/大缺口拒绝的回归。日志 voiceprint-blank-fix-check.log 与 voiceprint-blank-fix-unit.log。

修订 APK 已安装 Redmi。把刚才录音作为明确的测试 WAV，通过真实 ASR → HttpOopsApi → 当前在线 CAM++ → H5 字幕完整回放：前两句显示未确认 0.47/0.40，最后一句显示洪玉麟。回放时最后一句 ASR 尾部多了静音，分数为 0.511；不同于原片段直接诊断的 0.547，两者都通过现有门槛。原生报告 voiceprint-replay-fixed.txt、截图 voiceprint-replay-fixed.png。回放不是新的真人语音验收。四人模板和门槛未改，无声纹写入。测试 APK、临时诊断 PCM/WAV 和 adb 调试转发已清理；手机恢复 mic、Mock=false、原 HTTPS 后端，默认测试 WAV 恢复内置合成对话。最终 APK SHA-256：47b8ffa6f7eecb13413a934b2dae4d551d5ca9f0325e6875e62e0061742e1857。


## 2026-10-07 实时说话人预览试验：开发状态

新增声学换人/重叠活动检测、逐段声纹和 Jev 候选辅助，默认关闭，正式 segments/self 负责人闸门未开启。滚动预览与最终句内试验分段独立于任务证据。新增 4 个 Android 单测、7 个 Python 试验测试；完整检查日志 `.local-backend/diagnostics/speaker-preview-check.log`。

工程音频试跑不能视为准确率验收：第一组旧 DJI 人工单人标签片段混合时，设计的 2.261 秒重叠覆盖率约 39.8%；其中 03.wav 单独运行约 99.5% 被判重叠，标签与声学模型存在冲突，尚未真人复核。另选模型认为单人的 15.wav/11.wav 混合后，设计 3 秒重叠完全漏报；这组还是经过模型筛选的样本，不能作为独立验收。报告分别位于 `.local-backend/diagnostics/speaker-preview-controlled.json` 和 `speaker-preview-controlled-clean.json`。据此界面仅显示「疑似多人同时说话」，不得宣称重叠可靠或已分离两路音频。

发现局部说话人编号可能合并不同人物后，移除不连续片段拼接/共享姓名，改为每个连续片段至多 3.2 秒独立匹配。需要现场单人、轮流说话与真实重叠的独立标注测试，核对换人边界、错名、漏报和时延。当前 Hollyland/Mac 捕获的左右通道实际相同，不能作为每人独立通道。

新版 App 已安装，已核对恢复 Mac 收音 Wireless microphone、实时 ASR 和缓存增长。测试包安装被手机取消，真机试验自动测试尚未执行成功；香港后台虽登录，部署所需 SSH 连接仍待恢复。服务器试验开关尚未启用；不能把本地服务/API 试跑写成云端交付。


### 后续：香港及手机试验已开启

上述部署阻碍已解除：用户提供 SSH 登录信息后，香港 preview 模型和 API 已部署开启，手机开关已打开并直接使用香港后端；本场转写和 Mac 收音持续保留。无需测试 APK 即已从当前手机实际看到实时姓名预览、定稿试验分段、Jev 辅助提示及疑似重叠提示；截图 .local-backend/diagnostics/speaker-preview-enabled-phone.png，手机滚动日志 speaker-preview-phone-live.log，服务器对应日志 speaker-preview-cloud-phone.log，公网工程音频报告 speaker-preview-cloud-smoke.json。这里验证的是功能和数据流；此前测试 APK 自动安装受限的记录仍属实，未宣称完成该项自动测试，也未把现场显示当作准确率真值。

## 2026-10-07：剩余 P0 ①–⑥ 本地补齐

基线本地提交 `e7405fd` 保存原有香港部署、声纹录入和讲话人试验改动；提交前及本轮收尾扫描均未发现硬编码密钥，配置、录音、APK、临时证据继续在 Git 忽略范围内。本轮新增四类纪要、待办缺负责人/截止提示、任务关联、本场原话问答、ASR Oops 口令、议程时间提醒、可选 topic/resolution/conflict、短震动、团队文字同步与往期原话查询。没有修改正式句内拆分或 self 自动填负责人闸门。

已验证：

- `./scripts/check.sh lintDebug`：Android 构建、assembleDebug、lintDebug 与 JVM 单测；新增纪要索引迁移/决定原声暂存、120句问答候选与口令解析、议程解析/上限、时间提醒只触发一次、累计到点、连续跑题与5分钟去重、覆盖时长和切换结论检查、停止缺项汇总、无效topic/prob拒绝、同步分批/upsert/在途更新/退避/404停用/会话隔离，以及Mock金额规则/无匹配问答。最终93项Android单测，0失败/错误/跳过；assembleDebug与lintDebug通过。
- 后端原有 self_test 加入 FakeJev 判断/阈值和规则保守性；`scripts/test_notices_meetings.py` 的4个测试涵盖 HTTP 401/403、同步幂等、标签更新、额外PCM/preview字段排除、列表/打开/搜索/删除、迟到同步不重建、30天清理、0700/0600权限、并发upsert、task会话关联、模型映射与无效输入。现有声纹、HTTP鉴权、Node音频测试也通过。
- `.venv-voice/bin/python scripts/local_backend.py --port 8807 --feishu dry-run --state-dir <scratch>`：以独立临时目录运行，curl 跑通 healthz、sync/upsert、topic、resolution、conflict、list、open、search、ended_at最终同步、delete。先验证无Jev规则降级，再启用真实Jev完成相同流程。请求只含合成文字；没有创建真实飞书任务或接入手机。日志在 gitignored `evidence/p0-1007/http-smoke.log` 和 `http-jev-smoke.log`。
- `scripts/notice_probe.py`：5个合成中文样例，包括在题、跑题、有结论/无结论、u3与u1预算冲突，实际模型 `jev-1.13.0`。5个均选中预期结果；概率分别1.0/1.0/1.0/1.0/0.95，耗时736/619/672/642/687ms。记录在 `evidence/notice-probe.json`。少量样例和很高概率不构成准确率/概率校准结论。
- Codex 浏览器 375×812，light/dark：设置议程两种分钟写法、开始收音、纪要计数、待办负责人待定、模拟冲突记录/不准反馈/两句对照、本场问答预算原话、往期列表/纪要/问答/只读按钮。控制台 error/warn 为空。截图发现纪要列表误用两列网格，已改为完整宽度并重载检查明暗纪要；横幅在遮罩上方，12秒收起。原8765端口已有进程且返回空响应，未终止它；按相同 H5 启动参数改用8766验证。

截图保存于 `evidence/p0-1007/`：`light-digest.jpg`、`dark-digest.jpg`、`light-conflict-banner.jpg`、`dark-ask.jpg`、`dark-history.jpg`、`dark-history-ask.jpg`。全为浏览器模拟数据。

未验证：未安装新 APK、未重启或部署香港服务；尚未确认四位成员的文字上传同意。未验收真人语音唤醒、长会话后台同步稳定性、实际会议跑题/遗漏/冲突误报率、HyperOS熄屏时震动。语音唤醒依赖ASR定稿，不是独立热词模型。同步队列在内存，进程结束会丢失尚未成功发送的更新。多人识别仍需独立人工标注。

下一批仍是⑦个人记忆/个性化、⑧飞书日程、⑨录音豆自动会议起止。待确认的安装、部署、回退和现场检查见 [P0 上线交接](p0-rollout.md)。


## 2026-10-07 ①–⑥安装与上线进度

用户确认四人文字存档与安装/上线。新版主 APK 已安装，设置恢复逐项一致，Mac Wireless microphone 收音、真实 ASR连接和预览恢复；安装结束了原场次。香港仍运行旧版本，现有凭据 healthz 正常；SSH仅允许密码，部署等待登录凭据。测试包两次被HyperOS拒绝，新增meetingFeatures集成验收仅构建通过，未运行成功。记录和后续步骤见 [p0-rollout.md](p0-rollout.md)。没有新飞书任务、声纹或云端会议写入。


### 同日重试：测试包已允许，ASR上游授权失败

测试APK安装成功；`remoteSmoke`手机到香港草稿与既有任务回读通过，没有确认/创建任务。`meetingHttp`手机到本机新版后端的四标签/原文同步/防重/搜索/问答/判断/历史冲突/删除通过，使用真实Jev与dry-run飞书，全部合成会议已删除。完整ASR管线实际尝试失败：HTTP403，`volc.seedasr.sauc.duration requested resource not granted`。安装包SHA未变，设置已逐项恢复，测试APK和临时8808转发已清理；Mac音频桥与7790恢复，但ASR仍报错。香港SSH仍拒绝密码之外登录，部署待凭据。当前结果及证据路径见 [p0-rollout.md](p0-rollout.md) 最新记录。


## 2026-10-07：①–⑥香港部署已完成

用户完成原服务商登录后，香港原实例已备份并升级两个后端模块。公网healthz的notices/meetings均true；新增接口401/403及同步、防重、搜索、判断、问答、删除检查通过，环境/人员/声纹哈希不变，官网与既有入口正常，无新飞书任务。另修复Android Instant末尾Z/纳秒在香港Python3.10的兼容问题，服务器5项测试与公网往返通过。手机自动同步元数据已实际落盘0600，原设置逐项一致；ASR仍受上游资源权限403阻断，未产生真人转写验收结果。

最新备份、证据、回退与验收限制见 [P0上线记录](p0-rollout.md#2026-10-07-登录后香港部署完成)。以上取代先前“等待SSH/香港未上线”的当前状态；历史条目保留。


### 充值后重试仍403，发现应用不一致

用户表示已充值后，香港新版服务的手机 `meetingFeatures` 实际尝试仍因ASR资源未授权403失败。已核对原设置恢复、空白合成数据清理、测试包卸载和Mac音源恢复。已安装APK实际AppID `2983469702`，浏览器原目标 `8238142535`；不能推断二者账号/权限相同，也不能推断充值失败。当前登录入口已改为实际AppID，等待控制台登录以核对资源绑定。详情和报告见 [P0上线记录](p0-rollout.md)。


### Chrome实查与香港全链路复验通过

Chrome当前账号内同时存在两个AppID；充值已到账（余额8.97、欠费0），手机实际应用的正式2.0小时版已开通，原30小时包耗尽。未修改授权或客户端凭据。真实ASR已恢复。`meetingFeatures`在实际香港后端passed=true，3句ASR原文精确存档、停止时间、原话问答、Jev冲突、测试记录删除均通过；`meetingHttp`四类合成标签/防重/往期搜索/历史冲突/删除及迟到同步拒绝也passed=true。修复了一处测试把nullable404当作异常的误判，主APK未改变。设置恢复一致、测试包卸载、Mac音源恢复，无新飞书任务。报告、控制台截图与真人验收限制见 [P0上线记录](p0-rollout.md) 最新节。

这些成功结果替代先前“当前ASR403仍阻断”的状态。自动标签、判断、讲话人准确率和真人长会话仍需试用验收。

### 2026-10-08：归属 v2 工程验证与数据准备

`./scripts/check.sh lintDebug` 完整通过：101 项 JVM 测试（0 失败/跳过）、86 项 Python 测试、H5 草稿状态与声纹换人 UI 流程回归，Debug APK 构建与 lint 成功。统计测试使用 voice venv 的 numpy，未跳过拟合测试。最终 APK SHA256 为 `8e3a68ad21574196c006e6ccd0eb519e882e7bfaa4650511ecd38235b2652132`，已安装至目标 `W4W8Q8LVBQ8XZHLF`，保留应用数据；未操作其他两台手机。

一段 DJI 开发音频 20.51 秒做运行检查，拆出 10 段、3 段 overlap、原文完整，计算约 789 ms；未拟合概率为 null，结果没有 A。该检查不判断名字对错。DJI 冻结 55 题已按原 ID、片段和 0236 开发 / 0238 保留划分迁入独立题集，旧目录与旧答案未修改。本机盲标页 `http://127.0.0.1:8795/` 可切换题集，目前核实 0/55。未读取真实保留预测指标，未消费保留集，也未签发通过报告。

香港已首发部署并验证认证、分段同步、负责人不预填；四个归属门禁全部关闭。真实链路会议开发/保留音频、独立盲标和最终验收仍待完成。今日现场录入准备音频已标为 `voiceprint-enrollment`，程序禁止拿它作会议开发或保留集。任务卡修改反馈持久化在手机私有 `files/owner-feedback/<session_id>.json`，只作待核实材料，不传香港、不自动当真值。

本机证据目录 `evidence/speaker-attribution/` 包含最终检查日志、APK 哈希、运行检查、发布基线、部署结果和录入用途记录；不含可公开的准确率结论。完整操作见 [验收说明](speaker-attribution-runbook.md)。

实际双端比对发现 Python 3.14 / 3.10 的 `ast.dump` 格式不同，已改成稳定逐字段 AST 编码并升级 `#decisions-v2`。同源码的三个指纹、选中定义和 global 引用集合均在两端实测一致，证据 `evidence/fingerprint-python-compatibility.json`。兼容更新再次通过关闭开关的部署检查，备份 `/var/backups/oops-meeting/attribution-20261008T114855Z`。

## 2026-10-09：改说话人、会后核对、任务列表上线

- **服务器**：07:08 UTC 用 `deploy/review-queue-upgrade.py` 替换 `local_backend.py`、`meeting_features.py`、`recordings.py`（线上原为 `0e3e865`，预检确认一致）；备份 `/var/backups/oops-meeting/review-20261009T070816Z`。冒烟：17 场会议可读、最新一场队列 1 条、`/v1/tasks` 实际回读飞书 1 条（open，约 1 秒），不存在的会议核对/取录音分别 404，没有写入。重启时一场会正在进行，重启后到装 App 前该手机 51 个请求全部 200。
- **飞书读权限**：同日实测授权账号能回读它创建、指派给他人的任务（创建者不是成员也可以），测试任务已删除；删除后 `tasks get` 返回 `not_found` 1470404。
- **手机**：07:26:59 UTC 给无线连接的 2311DRK48C（192.168.1.184）覆盖安装，按用户要求在收音中直接安装，会议 `b7611988`（14:03 起约 83 分钟）因此中断；录音、定稿和耗时文件都在，App 重新打开后自动上传。
- **尚未在真机验证**：点句改说话人、会后核对队列、任务列表页、从服务器播原声。以上只在浏览器 Mock 和单元测试里走过。
- **录音上传**：`b7611988` 于 07:32:54 UTC 整场上传并校验完成（`audio.pcm` 159,488,000 字节等 5 个文件，与手机一致），手机已标 `.uploaded`。
- **发现的旧问题（与本次改动无关）**：同一场会的文字只同步了 3 句（手机定稿 45 句，编号 u121–u165），06:10:44 UTC 之后手机再没发过 `/v1/meetings/sync`，香港后端和香港 nginx 都没有失败记录。手机同步队列遇到 404/501 会把整场标为"不支持"并永久停止，遇到 400/409/410/413 也会永久停止；这些回应如果来自日本入口，香港看不到。原因待查；该场定稿文字在已上传的 `finals.jsonl` 里，可以补回。
- **文字已补回**：07:36 UTC 用已上传的 `finals.jsonl` 经 `/v1/meetings/sync` 补齐 45 句（原 3 句逐字段一致），备份 `/var/backups/oops-meeting/text-restore-20261009T073621Z`。标签取自 `timing.jsonl`（39 句）；说话人按时间把服务器 `/v1/speaker` 日志和手机耗时记录对齐（37 对，35 对时间唯一且与按顺序配对一致），19 句有名字。`ended_at` 按录音长度推算为 07:26:21 UTC。没有句内拆分、提醒和议程（原本为空）。补回后纪要：待办 12、约束 2；会后核对队列 26 条（待办 12、说话人 14）。
- **会议名称上线**：08:05 UTC 同一部署脚本（基线 `5e17017`）替换后端，备份 `/var/backups/oops-meeting/review-20261009T080546Z`；DeepSeek 概括按用户同意开启，部署后 8 秒内为 10 场已结束会议生成标题（如"多平台视频剪辑测试""声纹录入测试""AI 服装视频制作链路"）。不足 5 句的会议仍用原话取的标题。手机 2311DRK48C 未收音时重装，往期列表实机显示新标题。
- **简短会议**：08:08 UTC 再次部署（基线 `5b697ef`，备份 `/var/backups/oops-meeting/review-20261009T080804Z`），不足 5 句的 3 场会议显示"简短会议"，没有文字的仍为"会议"。只改服务器，App 未重装（新版 App 里"内容较少"的来源说明要等下次安装）。
- **项目记忆与按原型重做的页面上线**：08:49 UTC 部署 ③（基线 `0442c85`，备份 `/var/backups/oops-meeting/review-20261009T084910Z`），`/v1/projects` 可用、暂无项目。随后给 2311DRK48C（未收音）装新版页面，实机逐页截图：首页、会话、会议概览、任务、记忆、我的都按原型显示真实数据；实机发现超过一小时的会议时长显示为 83:04、头像取了名字第二个字，已修正后重装。

## 2026-10-10：Oops 机器人、Mac 助手、返回键

- **App**（2311DRK48C 已安装）：
  - 系统返回键先关弹层，再回上一级，在首页才把 App 放到后台。之前一按就退出。
  - 弹层可以从把手或标题下滑关闭。
  - 常用小按钮的可点区域扩到 44px，视觉大小不变。
  - 真机自动点击约 190 个按钮，没有发现点了没反应的；返回、下滑、点击区域都已实测。
- **服务器**：
  - 第一次部署前代码与 `7b91ee3` 一致，备份在 `/var/backups/oops-meeting/feishu-bot-20261010T045809Z`。
  - `backend.env` 增加 `OOPS_FEISHU_BOT=1` 和 `OOPS_AGENT_TOKEN`。
  - 机器人模块、卡片回调接收进程、Mac 助手接口均已上线；healthz 显示 `feishu_bot: true`、`mac_agent_online: true`。
- **飞书实测**：只用"多平台视频剪辑测试"这场会，只发给林春源。
  - 纪要文档、私聊卡片、多维表格台账、新任务卡片都已建成或发出。
  - 卡片按钮"帮我查"（80 秒）、"做成 PPT"（约 1 分钟，pptx 已私聊）、"整理报告"（约 2 分钟）、"建日程"（机器人日历邀请）都在飞书里实际点通了。
- **两个问题已修**：
  - 建日程后的回复没有链接：飞书建日程不返回链接，改为建完回读一次。
  - 卡片回调接收进程启动即退出：systemd 给的标准输入是空的，订阅进程会随之退出，改为给它一个一直打开的输入管道。
- **纪要质量**：第一版把标了标签的原句直接贴进纪要，没法用；改为 DeepSeek 按"宁缺毋滥"重写。
  - "多平台视频剪辑测试"：12 条口语待办变成 4 条会后待办，加 3 个讨论要点。
  - "店铺视频合规优化"（180 句）：1 个决定、2 条待办、4 组讨论要点。
  - "简短会议"（4 句）：不发送。
- **Mac 助手**：每个任务约 0.35–0.6 美元（本机 Claude Code）。"做成 PPT"在本机演练时出 7 页，内容都来自会议材料。
- **尚未验证**：
  - 真实会议结束后自动发给全部参会人。
  - 会上 3 分钟自动查问题。
  - 新任务卡片的"不是我的"按钮。
  - 多人同时点按钮。
- **需手动清理的测试产物**：测试日程"[测试] Oops 日程按钮"、旧版纪要文档 ALfMdQqG…、多维表格"Oops 权限探测（可删）"。

## 2026-10-10：会后链路补全（结束判断、机器人、Mac 助手）

验证上一版时发现：机器人只在服务器拿到手机报的 `ended_at` 后才发纪要，而 10-08 以来 12 场会里 8 场没有 `ended_at`（App 被杀、重装，或同步被 400 永久停掉，结束时间只在内存里）。测试那场的结束时间是手动补的，日志里没有过一次自动发送。另外台账写的是第一版原句，Mac 助手实际能读整台 Mac。

- **结束判断**：
  - 服务器：最后一句后 20 分钟没有新句子（≥5 句）即视为结束；录音上传完成时用 meta 的 `audio_ended_at` 补 `ended_at`（`ended_source`）；同步里单句不合法只跳过该句（回应 `rejected`），不再整批 400；请求不带 `agenda` 时保留原议程。
  - App：启动时找出没正常结束的会话（meta 无 `audio_ended_at`），按 asr/timing 日志最后的 `at` 估结束时间，写回 meta（`recovered`）并补发一次只带结束时间的同步；同步被拒后结束时间仍单独发；点停止不再等残留的退避；开始收音失败时删除空录音。
- **机器人**（`scripts/feishu_actions.py`）：发纪要前先生成会议名称；只发给说过 ≥3 句的人＋本人；散会时补查最后 3 分钟内的问题，DeepSeek 出错按次退避；台账加锁、逐表记录、失败单独重试；Mac 结果立即受理、后台投递，每个文件都发；已完成的任务再点会重发结果，失败的可重做，次数用完会通知；卡片点击各自线程处理，出错一定回话；"不是我的"找不到任务也回话并附任务链接；各步骤都有日志。
- **Mac 助手**：`claude -p --restricted`，工具只有 WebSearch/WebFetch/Read/Write/Edit/Glob，权限模式 dontAsk，另拒绝凭据目录；PPT 改由 `scripts/oops_slides.py` 按大纲渲染；上传拒绝软链接；任务目录保留 30 天。本机已重装（launchd 运行中）。
- **检查**：`./scripts/check.sh lintDebug` 通过：Android 126 项 0 失败，全部 Python 测试通过（机器人 10 项，含推断结束、等名称、收件人门槛、台账续做、并发结果与多文件、按钮重发/重做、散会补查、退避、出错回话）；Lint 0 error，1 个既有 warning（`noticeAlert` StaticFieldLeak）。
- **本机 dry-run**（真后端＋机器人 dry-run，飞书调用只打印）：用存档里没有 `ended_at` 的真实会议 22bec74d（104 句），把最后同步时间设为 30 分钟前：机器人判为结束（idle）→ 补查 1 个问题（判为已回答）→ 生成名称「设备配置与账号注册」→ 纪要 1 决定 6 待办 4 要点 1 待查 → 卡片发给洪玉麟（36 句）和林春源（24 句），44 句未识别的不影响。
- **Mac 助手金丝雀**：让 Claude 读 `~/.oops-agent/config.json`、列 `~/.ssh`、写 `../escape.txt`、读仓库 README、运行 `ls`：四次文件访问都被拒（凭据目录由拒绝规则、目录外由 `--restricted`），没有命令工具，凭据未出现在结果里；OAuth 登录在受限模式下正常。用昨天的 PPT 任务重跑：7 页，0.11 美元（原 0.48）。
- **飞书测试数据已修**（按用户同意，机器人身份，执行前备份到 `/var/backups/oops-meeting/test-fix-backup-20261010T143354Z.json`，执行后回读）：台账"待办"删除 b7611988 的 12 条原句、写入第二版 4 条；"会议"行纪要链接改为 VnCfdKT5…；纪要文档删除"后续安排 · [测试] Oops 日程按钮"；删除空的默认"数据表"。服务器 state 里该场 analysis 的 `follow_ups` 未改（只影响旧卡片上已用过的建日程按钮）。
- **未完成**：服务器部署（发布包已上传 `/opt/oops/releases/bot-fixes-b86085e`，预检通过，apply 需用户执行或授权）；新 APK 必须在服务器部署之后再装（旧服务器会把不带议程的结束请求当成清空议程）。真实会议里的全员发送、会上自动查问题、"不是我的"按钮仍待下次开会验证。

