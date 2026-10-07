# 原生 ↔ H5 消息协议

桥只对打包页面的来源 `https://appassets.androidplatform.net` 主框架开放。
H5 用 `window.OopsNative.postMessage(JSON.stringify({type,payload}))` 发送；通过 `OopsNative.onmessage` 接收同形 JSON。桥在 hello 后绑定，页面重载会重新绑定。

## H5 → 原生

| type | payload | 行为 |
|---|---|---|
| hello | {} | 请求完整 snapshot |
| capture.start | {source?:"mic"或"wav"} | 请求权限并启动前台收音服务 |
| capture.stop | {} | 停止服务及收音 |
| recall.trigger | {query?:string} | 按钮召回 |
| clip.play | {key,startMs,endMs} | 回听缓存原声；key 标识按钮 |
| clip.stop | {} | 停止回听 |
| recall.save | {cardId,utteranceIds,startMs,endMs} | 明确保存原句及可用原声 |
| recall.dismiss | {cardId} | 丢弃卡片 |
| commitment.open | {utteranceId} | 从本场索引打开承诺，进入现有卡片流程 |
| saved.list | {} | 返回长期保存列表 |
| action.draft | {cardId,utteranceIds} | 请求行动草稿，不写入飞书 |
| action.confirm | {draftId,who,what,whenIso,whenRaw,constraint} | 冻结草稿、持久化 op_id、提交确认 |
| action.status | {draftId} | 查询原操作，不重新提交 |
| settings.set | {source,mockBackend,backendBaseUrl,speakerInfo} | 修改调试设置 |
| testAudio.pick | {} | 系统文件选择器导入 WAV，持久保留读取权限 |
| testAudio.reset | {} | 恢复内置合成对话 |
| open.url | {url} | 系统打开 HTTP(S) 链接 |
| log | {message} | OopsWeb 调试日志 |

## 原生 → H5

| type | payload |
|---|---|
| snapshot | {state,transcript,summary,team,insets,pendingActions:[{draft,result}]} |
| state | {capturing,sessionId,source,sessionMs,bufferedMs,retentionMs,asr,asrDetail,level,playing,savedCount,backend,backendUrl,speakerInfo,testWavName,asrGaps,commitmentCount,analysisPending,analysisFailed,tagsSupported,sessionTruncated} |
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
| clip.state | {key,state:"playing"/"stopped"/"ended"/"expired"} |
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
