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
| snapshot | {state,transcript,team,insets,pendingActions:[{draft,result}]} |
| state | {capturing,sessionId,source,sessionMs,bufferedMs,retentionMs,asr,asrDetail,level,playing,savedCount,backend,backendUrl,speakerInfo,testWavName,asrGaps} |
| insets | {top,bottom} CSS 像素 |
| asr.partial | {text,startMs,endMs} |
| asr.final | {id,text,startMs,endMs,speaker} |
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

`sessionId` 变化时清空字幕。snapshot 先清空再回填，防止重复字幕。pending 且 polling=true 表示正在查询；polling=false 表示结果仍未知，可继续查询。mock=true 的成功页明确标识没有真实创建飞书任务；readBackConfirmed 只表示后端查询出了同一 guid。

## 浏览器模拟

bridge-mock.js 只有在原生桥不存在时安装模拟桥，使用 dialog.wav 的句子时间轴，提供 partial/final、两种召回卡片、真实 WAV 回听、localStorage 保存、草稿和复用 op_id 的 pending→created。页面显示“浏览器模拟识别”，所有调用均留在本地。
启动 `python3 -m http.server 8765 --bind 127.0.0.1 --directory android/app/src/main/assets`，进入 `/web/index.html`；测试音频通过 `/test/dialog.wav` 提供。无需前端构建。
