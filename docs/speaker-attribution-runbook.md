# 句内说话人与负责人验收操作

执行方案见 [speaker-attribution-plan.md](speaker-attribution-plan.md)。首发源码部署保持拆分显示、严格 A、本人负责人、点名负责人四个门禁关闭。模型余弦分数不等于概率；没有拟合的概率为 `null`。本人和点名负责人必须分别验收，不能用段概率代替。

## 现场录入与录音

连接目标手机后，用设备序列号避免控制其他手机：

```sh
python3 scripts/mac_audio_bridge.py --device 'Wireless microphone' \
  --port 7790 --adb-reverse --serial W4W8Q8LVBQ8XZHLF \
  --adb /Users/a1234/Library/Android/sdk/platform-tools/adb \
  --record evidence/remote-recordings --purpose voiceprint-enrollment
```

手机选择“Mac 收音”，确认已收到声音，再在声纹页逐人勾选同意、开始录入、保存。桥接程序会检查指定手机的 USB 转发，拔插后自动补回丢失的映射；Android 会自动重连。若仍显示等待收音，确认手机已解锁并允许 USB 调试，再手动执行 `adb -s W4W8Q8LVBQ8XZHLF reverse tcp:7790 tcp:7790`。恢复连接不需要重新打开声纹页，避免清除尚未保存的录音。手机麦克风与 Mac 收音分别保存，不能把手机来源的录入当成实际无线麦链路重录。

录入后用不同短句做独立 10 秒测试。这只检查链路和录入状态，不作为识别准确率验收。注册音频禁止混入会议开发集或保留集。录音文件仅留本机；手机原有火山 ASR 推流仍会发送音频到既有识别服务。

每次连接单独生成 WAV 与 JSON，只有成功发送的 PCM 被记录，断连前后不合并。`receiver_consumption_verified=false` 表示记录的是发送成功而不是手机消费确认。提前退出也不能将中断音频当作完整会议。

真实开发会议使用 `--purpose meeting-development`；另一场独立会议使用 `--purpose meeting-holdout`。两场必须独立，保留场不能参与参数选择。至少有 15 句本人承诺只是最初的数据要求，达到严格概率门禁还需要更多独立证据。

## 出题和盲标

DJI 原 55 题及 0236 开发 / 0238 保留划分保持不变：

```sh
.venv-voice/bin/python scripts/voiceprint.py split-prepare --dataset dji --count 55
.venv-voice/bin/python scripts/voiceprint.py spotcheck --split --dataset dji --port 8795
```

实际链路先生成完整逐字 ASR，再指定完整的场次清单。下列文件名为待实际录制完成后替换的示例，不是已经存在的会议：

```sh
.venv-voice/bin/python scripts/asr_words_probe.py \
  evidence/remote-recordings/DEV.wav evidence/remote-recordings/HOLDOUT.wav \
  --output evidence/asr-words-actual-chain
.venv-voice/bin/python scripts/voiceprint.py split-prepare \
  --dataset actual-chain-v1 --source 'Hollyland-Mac-phone' --count 40 \
  --recordings-root evidence/remote-recordings \
  --dev-recording DEV.wav --holdout-recording HOLDOUT.wav
.venv-voice/bin/python scripts/voiceprint.py spotcheck --split \
  --dataset actual-chain-v1 --port 8795
```

ASR probe 沿用火山链路，录音不会传香港。盲标页可切换题集，不显示模型名字或候选。逐段标人、多人重叠、听不出、未注册人员；逐个承诺标字区间、本人/点名类型、负责人和行动绑定。多人不能复用一个承诺标签。

## 开发冻结与最终查看

先只拉取线上声纹向量快照，不上传音频：

```sh
.venv-voice/bin/python scripts/speaker_acceptance.py --snapshot-only
```

完成开发集盲标后，固定版本、完整保留场次清单和最终有效样本目标。下例按现有完整清单冻结；若选定更高的 `--holdout-case-count`，未达到目标不会运行保留预测或消费保留集。

```sh
.venv-voice/bin/python scripts/speaker_acceptance.py \
  --dataset actual-chain-v1 --tune --version actual-chain-v1
.venv-voice/bin/python scripts/speaker_acceptance.py \
  --dataset actual-chain-v1 --version actual-chain-v1
```

同一场音频即使改名也不能跨开发和保留集。最终查看之前先写入消费账本，程序中断也不会允许该场次给新版本签发验收。同一冻结版本可复现；看过结果后改参数、特征、代码或声纹，需要新独立场次。DJI 与实际链路分别出报告，线上仅使用实际链路报告。

A 错名精确单侧 95% 上界需 ≤2%，同时要求段级与含 A 段的句级通过。本人及点名行动绑定准确率下界分别需 ≥95%。零错时，2% 上界需 149 个样本，5% 上界需 59 个样本；一句或一场中的相关性不能凭样本数消除，报告保留相关性限制。样本不足返回 `insufficient_evidence`，不会启用 A。

## 部署和回退

首发使用 `deploy/speaker-attribution-upgrade.py`：先校验源码、模型和线上基线哈希，备份应用与受保护配置，再安装关闭验收文件。检查认证、分段同步、集体承诺与负责人不预填；失败自动回退源码与开关。不会创建飞书任务或覆盖声纹/人员映射。

后续验收安装只上传校准器、冻结参数和签名验收元数据。完整标注与音频保留本机；服务器只接受预置可信签名，并再次检查当前实际声纹、模型和判定代码指纹。重录或代码更新后旧验收失效，必须使用新的独立验收场次。

本地签名密钥初始化只做一次，私钥不上传：

```sh
.venv-voice/bin/python scripts/speaker_acceptance.py --init-signing-key
```

管理员首发在香港单独固定这把公钥，`--trust-signer` 默认只预检，`--apply` 才创建固定可信文件；已有不同可信 key 时拒绝覆盖。验收包不能携带或替换可信 key。

完整实际链路验收结束且 display 门禁通过后，可明确导出仅启用显示的包：

```sh
.venv-voice/bin/python scripts/speaker_acceptance.py \
  --export-acceptance models/voiceprint/split-datasets/actual-chain-v1/validation.json \
  --package evidence/acceptance-actual-chain-v1 --enable display
```

默认不传 `--enable` 时四个开关仍全部关闭。严格 A、本人、点名分别只在报告相应门禁通过时，显式追加 `strict_a self_owner named_owner`；DJI 报告不能开启线上归属。源码与模型指纹必须和验收版本一致，不要导出后继续修改判定代码。

只传导出的 `report.json/report.json.sig/frozen.json/calibrators.json` 四文件，在香港执行：

```sh
python3 /opt/oops/releases/speaker-attribution-20261008-compat/speaker-attribution-upgrade.py \
  --install-acceptance /var/tmp/acceptance-actual-chain-v1
python3 /opt/oops/releases/speaker-attribution-20261008-compat/speaker-attribution-upgrade.py \
  --install-acceptance /var/tmp/acceptance-actual-chain-v1 --apply
```

安装前后均用服务器正在生效的声纹校验。四文件同代原子切换，健康状态必须与验签出的独立开关一致；失败回到旧验收指针。备份位于 `/var/backups/oops-meeting/`。

## 自动检查与真人验收的边界

```sh
./scripts/check.sh lintDebug
```

统计校准测试使用 `.venv-voice/bin/python`（可用 `OOPS_VOICE_PYTHON` 指定），不能跳过缺失的 numpy 测试。自动检查覆盖后端、统计边界、Android/H5 数据链、逐段降级、晚到结果保护和香港同步。

真人标注、保留集报告和实际会议真机抽查分别记录。未完成的步骤不会被自动测试替代；负责人修改反馈只是待核实材料，不能自动成为验收真值。
