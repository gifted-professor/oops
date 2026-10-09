# Oops Android

把最近的原话找回来，回听／保存；整理行动草稿，明确确认后再创建飞书任务。

Android 原生音频、5 分钟环形缓冲、豆包流式 ASR 和前台服务负责持续运行；H5 通过受来源限制的桥展示字幕和卡片。当前 Mock 可独立验证软件链路；真实 Jev／规则／飞书后端由 HTTP 契约接入。

本地新版本已补齐本场纪要（决定/待办/约束/想法）、本场问答、ASR 定稿后的 Oops 语音口令、议程时间提醒、跑题/结论遗漏/可能冲突提醒，以及往期会议文字查询。收音中和停止后都能打开本场纪要，待办标注负责人/截止待定及飞书任务状态。Jev 只判断或选原话，不生成回答；缺 Jev 时提醒保守降级。

5 分钟音频窗口外有全场内存索引（最多 20,000 句），决定/约束/待办原声共用 50 段/16 MiB 暂存上限。新场开始或进程退出清空本场索引。明确保存的卡片在手机落盘；**会议文字同步默认开启**，会把转写文字、标签、说话人、议程和提醒自动存到团队服务器，不含音频与试验 preview。可在设置关闭自动发送，在“已保存 → 往期会议”删除文字。服务器默认保留 30 天，所有持有 App 服务凭据的成员可查看。尚未同步的内存队列可能在进程结束时丢失。

这批服务接口目前完成本地验证，尚未安装手机或更新香港服务。按本轮计划，上线前需确认四位成员同意会议文字上传；安装会结束当前试用场次，须先获得用户确认。
句内说话人拆分已实现离线原型与接口，但默认关闭；“我来做”的负责人也保持空白。后端仅在独立人工标注、开发集冻结参数、验收集通过数值闸门后启用。执行与交接见 [Jev 接入交接](docs/jev-integration-handoff.md)，当前证据见 [验证记录](docs/validation.md)。

## 构建

环境：JDK 21、Android SDK（platform-tools、platform 37.2，许可已接受），Gradle Wrapper 9.8 / AGP 9.4.1。App compileSdk 37.2、targetSdk 36、minSdk 26。

在 `android/local.properties` 设置本机参数，该文件被 Git 忽略：
```properties
sdk.dir=/Users/你的用户名/Library/Android/sdk
VOLC_APP_ID=从火山旧版控制台获取
VOLC_ACCESS_TOKEN=从火山旧版控制台获取
VOLC_RESOURCE_ID=volc.seedasr.sauc.duration
OOPS_BACKEND_URL=
```
新版鉴权可用 `VOLC_API_KEY`；也支持同名环境变量。未填后端地址时默认 Mock。Jev 的 `TYPESAFE_API_KEY` 仅配置在后端环境，不进入 H5 或 APK。

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
./scripts/check.sh
cd android
./gradlew installDebug
```

本机 SDK 安装路径若不同，请使用实际路径；macOS 默认 `/usr/bin/java` 不一定能找到 Homebrew JDK。

## 手机运行与 adb

本轮 Redmi 需将 Oops 的“省电策略”设为“无限制”；默认“智能限制后台运行”会在熄屏后限制 CPU／网络，前台服务本身不能绕过该策略。

手机开启开发者选项、USB 调试和通过 USB 安装；连接后允许电脑调试。小米／Redmi 系统如返回 INSTALL_FAILED_USER_RESTRICTED，解锁手机并允许安装提示。

```sh
export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"
adb devices -l
adb shell pm grant com.oops.app android.permission.RECORD_AUDIO
adb shell pm grant com.oops.app android.permission.POST_NOTIFICATIONS
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.DEBUG_START --es source wav --ez builtin true
adb logcat -v threadtime -s Oops OopsBridge OopsWeb
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.RECALL
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.DEBUG_STOP
```

`--es source mic` 测手机麦克风。回听时 ASR 接收等长静音，避免外放重复转写。会话时间由已收到的 PCM 采样数推进。WAV 播完后继续送静音，所以仍可测 10／30／60／120 秒后的召回和 5 分钟过期。

定时召回并保存日志／截图：
```sh
python3 scripts/device_wav_recall.py --include-expiry
```
测试启动时会停止上一段收音，并新建 WAV 会话。真机真人声音、拍手对齐、Oops 热词和听感需要现场执行，截图或解码结果不能替代这些验收。

## 浏览器开发

无需前端构建：
```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory android/app/src/main/assets
```
打开 `http://127.0.0.1:8765/web/index.html`，`.claude/launch.json` 有同一启动配置。桥模拟器只在没有 Android 原生桥时生效；字幕时码采用内置 WAV 的真机 ASR 观察。浏览器会播放真实 WAV、模拟召回和 Mock 行动；保存列表留在当前浏览器 localStorage，未调用云端。

页面按设计原型（`proto.css` 原样引用，图标、Logo、字体一并放在 `web/` 下）只有浅色主题。`?frame=ios` 按原型的 iPhone 尺寸留出 54px 状态栏和 34px 底部安全区，便于和原型截图逐像素对照。

## 真实录音测试素材

用户提供的三份 DJI MP3 当前位于项目的 `讨论/` 目录，目录和媒体均被 Git 忽略。生成测试副本：
```sh
python3 scripts/prepare_test_audio.py 讨论/*.mp3
```
输出到 gitignored `test-media/`，附原文件及 WAV SHA-256、时长、格式、完整解码结果；脚本不覆盖已有文件。默认前三分钟只是候选样本，需听后挑选明确想法／承诺并标注参考原话与时间点。存储卡未挂载时 manifest 明确列出 missing。

App 调试设置可“选择测试 WAV”，支持系统文件选择器。长 WAV 逐帧读取，不整段载入内存。adb 路径方式：
```sh
adb shell mkdir -p /sdcard/Android/data/com.oops.app/files/tests
adb push test-media/你的文件.wav /sdcard/Android/data/com.oops.app/files/tests/dialog.wav
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.DEBUG_START --es source wav --es wav_path /sdcard/Android/data/com.oops.app/files/tests/dialog.wav
```
需先停止当前收音。此 debug 入口只允许 App 自己的内部／外部 files/tests 目录。这台 Redmi 实测拒绝读取 shell 放进外部目录的文件，改用内部目录：
```sh
adb shell run-as com.oops.app mkdir -p files/tests
adb shell "run-as com.oops.app sh -c 'cat > files/tests/dialog.wav'" < test-media/你的文件.wav
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.DEBUG_START --es source wav --es wav_path /data/user/0/com.oops.app/files/tests/dialog.wav
```

30 分钟长音频、熄屏和断网 30 秒测试（测试会停止当前收音）：
```sh
python3 scripts/device_stability.py --wav-path /data/user/0/com.oops.app/files/tests/dialog.wav --interrupt-network
```
脚本只在原飞行模式关闭时制造断网，结束／异常退出会恢复网络并停止测试收音。日志、每分钟 meminfo／前台服务／屏幕状态和最终截图保存在 evidence/device/stability。

## 后端接入

见 `docs/api.md`：召回、草稿、确认和查询四接口，snake_case、会话毫秒、10 秒调用时限。草稿提供绝对参考时间和时区；后端负责显示名到飞书用户 ID 的映射。

确认之前持久化 op_id 和冻结的内容；网络响应丢失或 pending 超时后继续查询原操作。未完成操作可在重开 App 时恢复。服务器真实写入、同操作重复确认及后端重启后的查询已通过手机联调；网络切换与响应丢失场景的远程实测仍待补充。

`飞书后端` 是处理这四个接口的 Oops 中间服务，不是 Jev 的 System One 接口。Jev 做候选选择；后端另外负责草稿、显示名到飞书用户 ID 的映射、飞书授权和任务读写。当前服务器入口为 `https://giftedprofessor.xyz/oops-meeting`，服务器已授权新 Oops 飞书组织；应用凭据仅保存在服务器。

仅测试 Jev 的真实 Choice 接口：
```sh
python3 scripts/jev_probe.py
```
脚本读取后端环境变量 `TYPESAFE_API_KEY` 或本机 gitignored `.jev.env`，只发送合成演示句子；保存实际模型、概率、延迟和 token 使用，密钥不打印、不打包。

```sh
scripts/api_smoke.sh http://后端地址:端口
# 已确定为飞书测试环境时，执行确认与重复确认：
OOPS_SMOKE_CONFIRM=1 scripts/api_smoke.sh http://后端地址:端口
```

## ASR 与 LAS 观察

计划中的先前实验及本轮真机内置 WAV 均使用 `bigmodel_async`、二遍定稿、`result_type=single`。首句 start_time 可能省略，按 0 解析；说话人字段为 additions.speaker_id。说话人区分默认关闭，先前五句测试开启后丢两句且未分开声音。合成音频的 Oops 漏识别，因此也排除明确召回指令句；真人热词识别率单独测。

LAS 的三个算子为异步录音文件识别，只接收公网 URL／TOS 路径；广州区域的 LAS key 不用于本 App 流式主链路，可由后端做事后精转写。

火山调试凭据会打包进 APK，赛后更换；不要提交 local.properties、密钥或含密钥的日志。

## 目录

- `android/app/src/main/java/com/oops/app/`：audio、buffer、asr、engine、backend、recall、playback、store、service、bridge。
- `android/app/src/main/assets/web/`：无需构建的 H5 及桌面 bridge mock。
- `android/app/src/main/assets/test/dialog.wav`：25.36 秒合成测试对话。
- `android/app/src/test/`：缓冲、ASR 协议、召回、日期、JSON、WAV、确认恢复单测。
- `docs/`：HTTP 契约、桥协议及验收记录。
- `scripts/`：构建检查、接口 smoke、素材转换、真机定时召回。
- `evidence/`、`test-media/`：本地生成证据／测试副本，默认不提交。

## 阶段边界

A：软件收尾＋USB 真机麦克风／WAV＋30 分钟运行。B：真实后端和飞书行动闭环。C：录音豆 SDK/license。D：门控、语音 Oops、承诺发现和当事人确认。E/F：长时间、准确率验收和现场准备。各项实际状态见 `docs/validation.md`，不把 Mock 成功当作真实飞书写入。

本机 HTTP 故障联调（不调用飞书）：`python3 scripts/local_api_fixture.py --scenario drop-response-once`，或 `--scenario reject-once`。手机使用 `adb reverse tcp:8766 tcp:8766`，在 App 设置填 `http://127.0.0.1:8766` 并关闭 Mock。前者模拟已创建但响应丢失；后者模拟未接收确认后失败。`/_test/operations` 可核对同一 op_id 的请求、次数与创建数。结束后恢复 Mock 并执行 `adb reverse --remove tcp:8766`。

## 香港后端 + 手机独立收音

Redmi 已安装当前 APK，后端地址 `https://giftedprofessor.xyz/oops-meeting`、Mock 关闭、服务访问凭据已配置。收音中在设置保存新音源会立即切换，并保留本场字幕与音频缓存。默认手机麦克风，可通过移动网络运行，不需要 Mac 后端或 USB 转发。App 设置可修改服务地址和访问凭据；凭据按地址保存，留空保留，勾选清除才删除。正式任务仍需检查草稿后明确确认。

设置 →「声纹录入与匹配测试」可选择本人姓名、同意后按固定文本朗读 30 秒，检查时长/音量，再明确保存。支持手机麦克风和现场外接音源，WAV 测试音源不能注册。保存后用另一段文本录 10 秒核对匹配结果。

团队也可使用服务器上的个人邀请链接，在手机 Safari/Chrome 直接录入，无需安装 App 或连接 Mac。录入页路径 `/oops-meeting/voiceprint/`；需使用发给本人的完整链接，每个邀请只能更新对应成员的声纹，不能操作飞书或删除声纹。请保持页面在前台。四人仍使用原 DJI 声纹，待本人录入后更新；识别准确率仍需真人验收。部署和邀请维护见 [服务器说明](docs/server-deployment.md)。

服务器运行 Jev、声纹和独立授权的飞书 CLI。四人负责人是林春源、江古月、洪玉麟、黄丽萍；Lydia 对应黄丽萍。真实测试任务创建、回读、防重及后端重启后的查询已通过。句内拆分与 self 自动填负责人继续等待人工验收。部署路径、维护、回退与完整证据见 [服务器部署](docs/server-deployment.md)。

## Mac mini 收音 + 本地飞书后端

手机插着 USB 调试时没法再接麦克风，所以把收音放到 Mac mini：Hollyland 无线麦接收器插在 Mac 上，`scripts/mac_audio_bridge.py` 用 ffmpeg 持续采集，转成 16 kHz 单声道 PCM，经 `adb reverse` 走 USB 推给 App 的「Mac 收音」音源（`RemoteAudioSource`）。它和以后的录音豆走同一条外接设备链路：断线自动重连并补缺口，回车 = 双击召回。

`scripts/local_backend.py` 按 `docs/api.md` 实现四个接口：召回用 Jev（`.jev.env`），草稿的事项／截止／约束用规则、负责人由 Jev 在团队名单里选；确认后以 `lark-cli` 当前授权用户的身份创建真实飞书任务，`client_token` = op_id，先落盘再写飞书，建完回读核验。只监听 127.0.0.1。

```sh
python3 scripts/local_backend.py --resolve-people   # 一次：团队姓名 → 飞书 open_id，结果在 .local-backend/people.json
python3 scripts/local_backend.py                     # 终端 1；--feishu dry-run 不写飞书
python3 scripts/mac_audio_bridge.py --adb-reverse    # 终端 2；--list 看设备，--wav 文件 用测试音频代替真人
adb reverse tcp:8787 tcp:8787
adb shell am start -n com.oops.app/.MainActivity -a com.oops.app.action.DEBUG_START \
  --es source remote --es remote 127.0.0.1:7790 --es backend http://127.0.0.1:8787 --ez mock false
```
也可以在 App 调试设置里手动选「Mac 收音」、填后端地址并关闭 Mock。本地默认模式下缺失映射会回落给授权用户，并把原定负责人写入描述；服务器严格模式会直接拒绝未映射负责人。首次采集时 macOS 会请求麦克风权限。


字幕说话人姓名在一句转写完成、服务器匹配通过后补上；未通过会显示“声纹未确认”及最高相似度。语音太短、请求失败或音频不可用也有独立提示。当前每句只给一个姓名，句内换人仍待独立拆分验收。声纹读取容忍最多 100 ms 的转写尾部时间误差，防止停录后末句因几毫秒越界被整段跳过；匹配阈值未降低。
