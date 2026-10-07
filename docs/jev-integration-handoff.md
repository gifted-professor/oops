# Jev 标签与句内拆分交接

## 当前状态

Phase 0 完成：0236、0238 两场完整 16 kHz 录音，共 162 句；有效词时间戳与文字对齐覆盖率均为 100%，收到服务端终包且无传输错误。此次只观察到标点差异，ITN 与改字由合成测试覆盖，仍需真实样本验证。

Phase 1 已实现：独立异步标签、成功结果缓存、限并发与重试、召回加权、字幕标签、内存会话索引及承诺入口。手机超过 6 分钟测试中，首分钟承诺在环形音频淘汰后仍能打开、回听、保存与起草；self 负责人为空。测试后端为 dry-run，本轮未创建飞书任务。

Phase 2 已实现离线原型、盲标页面、开发/验收分离评估及 App 的受控接口和展示。**尚未通过人工准确率验收，不启用句内拆分或 self 自动填人。** 当前 55 句开发集 29 句（0236）、验收集 26 句（0238）；严格规则筛到 10 个自我承诺候选，少于计划的 15 个，需在盲标时核实其他句子或新增独立录音补样。未完成真人 Hollyland 演示，也未与正式服务负责人约定切换时间。

## 听辨与评估

依赖使用已有 `.venv-voice`（numpy、sherpa-onnx）；ASR 探测另需 `websocket-client`，可运行 `.venv-voice/bin/pip install websocket-client`。凭据只从 gitignored `android/local.properties` 和 `.jev.env` 读取。

```sh
# 两场完整 ASR 探测，默认实时送音；--speed 8 用于离线加速探测
.venv-voice/bin/python scripts/asr_words_probe.py --output evidence/asr-words-complete
# 首次抽样后冻结，不覆盖人工答案
.venv-voice/bin/python scripts/voiceprint.py split-prepare
.venv-voice/bin/python scripts/voiceprint.py spotcheck --split --port 8793
```

打开 http://127.0.0.1:8793/，逐句听音，在可映射词边界切段，选择实际说话人（重叠可多选），核实是否为自我承诺及承诺人，勾选已核实后保存。页面不显示声纹预测；旧聚类标签仅用于分层抽样，不作真值。真值保存在 `models/voiceprint/split-truth.json`。页面支持分次保存；仅保存页面不会完成验收。

```sh
# 只使用开发集扫描并冻结短段阈值，不能根据验收结果回调参数
.venv-voice/bin/python scripts/voiceprint.py split-eval --tune
# 验收集比较整句基线、纯声纹、声纹 + Jev，完整走负责人规则
.venv-voice/bin/python scripts/voiceprint.py split-eval
```

没有人工真值时评估返回待标注，不生成配置。冻结参数后，修改开发集答案、代码、声纹模型或注册数据会要求新评估版本。验收失败时不得反复调参复用同一验收集；应补充新的独立样本。self 自动填人另需全标注集至少 15 句经人工确认的自我承诺。

闸门：验收集按字正确率比整句基线高至少 10 个百分点；乱填率不超过 5% 且不高于基线；换人点召回至少 70%；短插话召回至少 50%、错标率不超过 10%。self 覆盖率至少 60%、准确率至少 95%；自动填人样本不超过 20 条时要求零错误。self 未过可单独关闭。Jev 还需将未知率降低至少 5 个百分点、乱填率上升不超过 1 个百分点，否则仅保留纯声纹。

通过后 `models/voiceprint/split-validation.json` 保存三档指标、配置、真值和清单哈希及代码/模型指纹。后端启动时核验，文件缺失、失效或数值未过则关闭；App 还要求 `segments_validated=true` 才接受分段。人工标注是对真实音频的独立判断，不能由模型预测补填。

## 本地与正式后端

```sh
.venv-voice/bin/python -u scripts/local_backend.py --port 8807 --feishu dry-run
adb reverse tcp:8807 tcp:8807
```

手机调试设置选择 HTTP 后端 `http://127.0.0.1:8807`，关闭 Mock。正式实现遵循 `docs/api.md`：所有 HTTP 时间均为会话毫秒；`/v1/utterances` 是可选接口，404/501 本场停用；规则兜底 `prob=null`；`/v1/speaker` 保留整句 speaker，并明确传入定稿 text、clip_start_ms 与 words。切换到正式服务时，需交接验收文件/注册声纹及接口支持状态，并重新验证超过 5 分钟的承诺原声链路和真人麦克风表现。

当没有可用词时间戳时，本实现保守返回整句近似结果，不均分文字强行切段；该情况不满足启用要求，应报告并另评估退化方案。近似切字与 Jev 辅助声纹均不用于自动归属 self。

技术检查：`./scripts/check.sh`。新测试覆盖字时间平移、索引与音频上限、会话隔离、重试/停用、标签和分段契约，以及独立真值和评价指标；这些检查不能替代人工准确率与真人演示。
