# 原生 ↔ H5 消息协议

桥只对打包页面的来源 `https://appassets.androidplatform.net` 主框架开放。
H5 用 `window.OopsNative.postMessage(JSON.stringify({type,payload}))` 发送；通过 `OopsNative.onmessage` 接收同形 JSON。桥在 hello 后绑定，页面重载会重新绑定。

## H5 → 原生

| type | payload | 行为 |
|---|---|---|
| hello | {} | 请求完整 snapshot |
| capture.start | {source?:"mic"或"wav"或"remote"} | 请求权限并启动前台收音服务 |
| capture.stop | {} | 停止服务及收音 |
| recall.trigger | {query?:string} | 按钮召回 |
| clip.play | {key,startMs,endMs,sessionId?} | 回听缓存原声；key 标识按钮。带 sessionId 时，本机没有（或不是本场）就从服务器存档取 |
| clip.stop | {} | 停止回听 |
| clip.remote | {key,sessionId,startMs,endMs} | 从服务器存档取一句原声播放（往期会议，或本场录音上传后） |
| speaker.correct | {sessionId,utteranceId,speaker} | 人工改说话人：团队成员、`other`/`multi`/`unclear`，`null` 撤销。本场立即改显示，再写服务器（会议还没同步到服务器时每 25 秒重试，最多 3 次）；往期会议直接写服务器 |
| projects.list | {} | 项目列表 |
| project.open | {name} | 一个项目的会议、已确认决定、任务 |
| meeting.project | {sessionId,project} | 设所属项目，空字符串移出；本场会议还没同步到服务器时每 25 秒重试，最多 3 次 |
| history.rename | {sessionId,title} | 往期会议改名，空字符串恢复自动名称 |
| review.set | {sessionId,utteranceId,fields:{verdict?,owner?}} | 会后核对：是否待办/决定（confirmed/rejected）、负责人；null 清除 |
| tasks.list | {mine,status:"open"/"done"/"all"} | 读飞书任务列表；`mine` 需要设置里选了本人 |
| recall.save | {cardId,utteranceIds,startMs,endMs} | 明确保存原句及可用原声 |
| recall.dismiss | {cardId} | 丢弃卡片 |
| commitment.open | {utteranceId} | 从本场索引打开承诺，进入现有卡片流程 |
| saved.list | {} | 返回长期保存列表 |
| action.draft | {cardId,utteranceIds} | 请求行动草稿，不写入飞书 |
| action.confirm | {draftId,who,what,whenIso,whenRaw,constraint} | 冻结草稿、持久化 op_id、提交确认 |
| action.status | {draftId} | 查询原操作，不重新提交 |
| settings.set | {source,remoteAddress,mockBackend,backendBaseUrl,backendToken?,speakerInfo,selfName?} | 保存；收音中切换音源/远端音频地址立即生效，保留本场缓存 |
| testAudio.pick | {} | 系统文件选择器导入 WAV，持久保留读取权限 |
| testAudio.reset | {} | 恢复内置合成对话 |
| open.url | {url} | 系统打开 HTTP(S) 链接 |
| log | {message} | OopsWeb 调试日志 |

## 原生 → H5

| type | payload |
|---|---|
| snapshot | {state,transcript,summary,team,insets,pendingActions:[{draft,result}]} |
| state | {capturing,sessionId,source,sourceReady,sourceDetail,sessionMs,bufferedMs,retentionMs,asr,asrDetail,level,playing,savedCount,backend,backendUrl,speakerInfo,testWavName,asrGaps,commitmentCount,analysisPending,analysisFailed,tagsSupported,sessionTruncated} |
| insets | {top,bottom} CSS 像素 |
| asr.partial | {text,startMs,endMs} |
| asr.final | {id,text,startMs,endMs,speaker} |
| asr.tag | 定稿句完整记录，增加 tag/tagProb/tagEngine |
| asr.speaker | {id,speaker,score,segments} |
| session.summary | {sessionId,commitments:[定稿句完整记录 + audioExpired]} |
| transcript.pruned | {ids} |
| recall.pending | {trigger} |
| recall.card | {id,trigger,query,nowMs,single,engine,windowMs,latencyMs,items,pool} |
| recall.notFound | {reason} |
| clip.state | {key,state:"playing"/"stopped"/"ended"/"expired"/"loading"/"unavailable"} |
| speaker.corrected | {sessionId,utteranceId,speaker,status:"local"/"saved"}；local 是本机已改，saved 是服务器已保存 |
| tasks.list | `GET /v1/tasks` 的结果，另加 {mine,status} |
| projects.list | {items} |
| project.open | `GET /v1/projects/{名称}` 的结果 |
| meeting.project | {session_id,project} |
| history.renamed | {session_id,title,title_source} |
| review.saved | {sessionId,utteranceId,review} 服务器已保存的核对 |
| recall.saved | {cardId,savedCount} |
| action.draft | {draftId,cardId,who,what,whenIso,whenRaw,constraint,assigneeCandidates,evidence} |
| action.result | {draftId,opId,status,taskGuid,taskUrl,message,readBackConfirmed,polling,mock} |
| saved.items | {items} 元数据采用存储的 snake_case |
| latency | {recentMs,medianMs} |
| toast | {message} |
| error | {scope,message} |

items 含候选 id/text/startMs/endMs/speaker/prob；pool 含时间排序的候选及邻句。
evidence 为 {utteranceIds,quote,startMs,endMs}。所有时间是会话毫秒。

定稿句完整记录还包括 `words:[{text,startMs,endMs}]` 与 `segments:[{startMs,endMs,text,speaker,score,source,approx}]`；桥使用 camelCase，HTTP 使用 snake_case。segments 只接收经后端独立验收的结果，文字拼接必须等于原句，区间必须连续且覆盖原句。近似段在字幕中显示浅色提示，不能用于 self 自动填人。`tagsSupported=null` 表示尚未探明支持情况；false 表示本场已停用标签请求。停止收音后在途标签仍会更新汇总，开始新会话后旧结果丢弃。

`sessionId` 变化时清空字幕。snapshot 先清空再回填，防止重复字幕。pending 且 polling=true 表示正在查询；polling=false 表示结果仍未知，可继续查询。mock=true 的成功页明确标识没有真实创建飞书任务；readBackConfirmed 只表示后端查询出了同一 guid。

## 浏览器模拟

bridge-mock.js 只有在原生桥不存在时安装模拟桥，使用 dialog.wav 的句子时间轴，提供 partial/final、两种召回卡片、真实 WAV 回听、localStorage 保存、草稿和复用 op_id 的 pending→created。页面显示“浏览器模拟识别”，所有调用均留在本地。
启动 `python3 -m http.server 8765 --bind 127.0.0.1 --directory android/app/src/main/assets`，进入 `/web/index.html`；测试音频通过 `/test/dialog.wav` 提供。无需前端构建。

`sourceReady` 表示音源已经交付 PCM，且尚未断开。Mac 重连或切换音源时为 false，收到音频后为 true；UI 在 false 时显示「等待收音／等待音源」，不会只因 ASR socket 连接就宣称实时识别。旧音源的状态、音频、错误和标记回调均按具体音源实例过滤，不能影响已切换的新音源。


## App 自助声纹录入

- `voiceprint.list {}`：鉴权获取成员和目录版本。
- `voiceprint.record {name,consent:true,mode:"enroll"|"test"}`：本人同意后，冻结服务客户端与当前音源，录入最多 30 秒／测试最多 10 秒。若未收音，原生请求权限并启用手机麦克风。WAV 音源不能注册。
- `voiceprint.stop {}`：结束录音；录入达到 15 秒后先只读检查音量，测试录音自动匹配。
- `voiceprint.save {}`：质量通过后明确提交保存；保留 sample_id 实现响应丢失后的幂等重试。
- `voiceprint.cancel {}`：清除尚未提交的临时录音，日常收音继续。
- `voiceprint.delete {name,confirm:true}`：再次确认后按目录版本删除。

`state.voiceprint` 含 `phase/name/mode/elapsedMs/source/message/catalog/diagnosis/quality/qualityPassed`；阶段为 idle/loading/recording/checking/review/saving/testing。H5 不获得任何服务访问凭据或 PCM。浏览器 mock 明确不支持录入，不生成虚假声纹。关闭页、停止收音、切换音源或服务器均放弃临时录音；已经提交的保存可能在服务器完成，取消界面不能撤销在途写入。

原生临时声纹缓冲最多 30 秒；没有额外写录音文件。App 正常会话音频仍按既有环形缓存和明确保存规则处理。


### 每句声纹判断状态

定稿句、snapshot 和 asr.tag 增加 speakerState/speakerScore。asr.speaker 增加 speakerState；score 可为 null（没有完成模型计算）。状态有 pending/matched/unconfirmed/too_short/busy/audio_unavailable/unsupported/error。matched 显示已匹配姓名；unconfirmed 显示“声纹未确认”及最高相似度；其他状态显示对应原因。unknown 状态不填候选姓名，也不绕过负责人验收。标签异步更新和页面重载保留已完成的声纹状态。


## 说话人拆分试验（2026-10-07）

settings.set 增加 `speakerPreview`，默认 false；state 增加 `speakerPreviewEnabled/liveSpeakerStatus/liveSpeakers/liveOverlap`。定稿句及 asr.speaker 增加单独 `speakerPreview:{experimental,startMs,endMs,activities,segments,jevUsed}`，内部字段使用 camelCase。H5 开关显示「实时换人拆分与重叠提示（试验）」；滚动提示身份候选/待确认/疑似多人同时说话，定稿按原文词时间显示试验分段。Jev 辅助姓名标为待核对。刷新及开关切换保留试验数据并重新渲染，不写入正式 segments、Candidate 或行动草稿。

## 2026-10-07：纪要、问答、议程、提醒、往期会议

新增 H5 → 原生：

| type | payload | 行为 |
|---|---|---|
| digest.open | {utteranceId} | 打开本场纪要原话；commitment.open 保留为别名 |
| digest.openMany | {ids:[1–3个原话id]} | 冲突两句并列查看；含历史句时只读 |
| ask.session | {query:1–500字} | 全场原话问答，与召回共用在途锁 |
| agenda.set | {goal,items:[{title,minutes?:整数}],minutes?:整数} | 保存下一场议程，收音中更新本场；时长1–480分钟 |
| agenda.clear | {} | 清除本场及下次议程 |
| notice.dismiss | {id} | 收起提醒并同步该反馈 |
| notice.feedback | {id,useful:boolean} | 记录“有用/不准” |
| history.list | {} | 团队往期会议列表 |
| history.open | {sessionId} | 读取历史文字与纪要 |
| history.search | {query} | 搜索往期原话 |
| history.delete | {sessionId,confirm:true} | H5 二次确认后删除；本场收音中须先停止 |

`settings.set` 新增 voiceTrigger/noticeVibrate/memorySync，三个开关默认开。`state` 新增 digestCounts:{decision,commitment,constraint,idea}、agenda:{goal,items:[{id,title,plannedMs,coveredMs}],plannedMs,currentItemId,revision,tracking}、notices、voiceTrigger、noticeVibrate、memorySync、syncStatus（idle/off/pending/retrying/unsupported/failed/synced）、syncPending。tracking 为 waiting/supported/unsupported。

`session.summary` 保留 commitments，增加 decisions/constraints/ideas/notices；待办每句附 ownerHint（明确点名）、whenRaw、taskStatus、taskUrl。audioExpired 表示未暂存此原话。召回卡片 items 增加 audioAvailable；历史卡片增加 readOnly:true/startedAt，历史上下文按钮、回听、保存、起草均禁用。

原生 → H5 的 `notice` 为 {id,kind,title,detail,atMs,evidenceIds,itemId,history?,dismissed,useful}，横幅位于遮罩上方，最多两条，12 秒后收起到“提醒 N”。history 为历史原话的 snake_case 数据。`history.list/open/delete/search` 事件直接携带对应 API 响应，search 增加 query。snapshot/state/session.summary 均可恢复提醒记录；重新载入页面不会重新震动。

说 Oops 或欧普斯/哦普斯/奥普斯可触发，问句走本场问答，其他走召回；整句“把刚刚那个想法留下来”等指令也触发。开关关闭不触发，5秒内去重；语音口令仅在 ASR 定稿后响应，不是独立低功耗热词模型。
