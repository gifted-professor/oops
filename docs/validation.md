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
