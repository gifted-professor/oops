# Oops ①–⑥ 上线记录与验收

本轮范围：本场四类纪要、原话问答与 Oops 口令、议程时间提醒、跑题/结论遗漏、可能冲突、团队会议文字存档与往期问答。⑦个人记忆/个性化、⑧飞书日程授权、⑨录音豆自动起止仍留下一批。正式说话人拆分和 self 负责人闸门保持原有设置。

## 确认后才进行

这两步来自用户提供的《Oops 剩余 P0：排序与本轮补齐计划》，其中明确写了“计划批准不包含这些”：

1. 手机安装新 APK 会结束当前试用场次；先获得用户确认，并只读记录现有业务后端、音源、远端音频地址和试验开关，联调后恢复。
2. 香港部署须先确认四位成员均同意会议转写文字、标签、说话人、议程和提醒存到团队服务器；不含音频。服务凭据持有人共享访问，默认保留 30 天。

2026-10-07 用户回复「同意」，授权安装和香港部署，并确认前述四人文字上传。主 App 已安装；用户完成服务商网页登录后，香港部署已完成。当前 healthz 已包含 notices/meetings；最新结果见文末。此前等待凭据的条目保留为历史记录。

## 具体部署包与检查

香港仍使用 `/opt/oops/app` 的后端和 `/var/lib/oops/backend` 状态目录。新增运行依赖是 `scripts/meeting_features.py`（与 local_backend.py 同目录）；部署时两者必须同时更新，其他既有语音模块保留。不需要新增 Python 库。

批准后先备份整个 `/opt/oops/app` 和受保护 env，记录当前部署版本和 healthz。以 oops 服务用户创建 `/var/lib/oops/backend/meetings`（0700），复制源码，核对文件哈希，执行语法检查，再重启 `oops-backend`。只从受保护 env 加载凭据，不在日志或命令历史显示。

部署后 healthz 应包含 notices/meetings；使用 App 全权限凭据和独立合成会话完成 sync/upsert/list/open/search/delete。声纹邀请凭据对所有新接口须返回403，没有凭据须返回401。复查召回、标签、草稿及原官网/代理健康。真实飞书创建仍需要用户确认草稿。

回退：恢复备份源码及 env 并重启；保留已同步会议状态目录，以便恢复。旧 App 忽略新增字段；新 App 访问旧后端的可选接口得到404/501后停用判断/同步，时间提醒继续运行。

## 现场验收

安装后：设置议程，真实麦克风定稿，查看四类纪要，停止后提问，再到往期会议确认全场文字与任务关联；开新场测试往期约束冲突；在当前音源恢复后继续试用。单独核验语音口令5秒去重、短震动60秒间隔、HyperOS熄屏震动与网络中断重试。所有判断均引用原话，真实误报/漏报要人工记录，不以合成探针或 mock 替代准确率验收。

内存同步队列不抗进程被结束；失败期间尚未传到服务器的文字可能丢失。开始新场不会将旧队列改投另一后端。当前后台同步与提示的真实长会议稳定性尚未验收。

## 2026-10-07 已批准后的执行记录

- 新主 APK 已通过 `adb install -r` 安装，SHA-256 `50cd0671916a5aeb92c074beb456553586b897f8746ac5283acc6763b4098f89`。
- 安装前私有设置已备份到忽略目录 `evidence/p0-1007/device-settings-before.xml`（0600）；测试后逐项解析对比完全一致。恢复香港 HTTPS 后端、Mock=false、remote 音源 `127.0.0.1:7790`、speaker_preview=true、speaker_info=false；已有音频 adb reverse 保留。原场次已结束，恢复后为新场次。
- 已恢复启动收音；截图 `evidence/p0-1007/phone-restored.png` 显示 Mac Wireless microphone、实时识别、缓存增长，预览日志继续产生。仅短暂回放内置合成 WAV，看到真实 ASR 定稿；未完成全场特性自动验收。
- 测试 APK 两次被手机安装器返回 `INSTALL_FAILED_USER_RESTRICTED: Install canceled by user`。新增 `RemoteBackendSmoke -e meetingFeatures true` 已构建，待手机允许测试包安装后运行；它只上传独立合成会议、核对原文和停止时间、查询和删除合成记录，不创建飞书任务，最后恢复全部原设置。
- 公网凭据读取 healthz 成功，旧服务 Jev/voiceprint/live Feishu/preview 均正常；目前不含 notices/meetings。新 App 显示「当前后端不支持会议文字同步」，没有声称云端已上线。
- `deploy/p0-upgrade.py` 为已授权部署准备了源码/环境备份、基线 SHA 校验、替换两模块、服务重启、401/403及合成业务检查、失败回退。打包路径 `.local-backend/diagnostics/p0-release-1007.tar.gz`，不含凭据、声纹或音频。部署业务检查已在本机 dry-run 服务配合真实 Jev 跑通，包含六接口401/403、同步/upsert/列表/原文/搜索/三类判断/跨场预算冲突/召回/标签/草稿/删除/删除后迟到同步拒绝；日志 `evidence/p0-1007/deploy-route-check.log`。实际香港服务备份、重启和回退尚未执行。部署包 SHA-256 `a5938f68339174b2fea8f1cab53c35463725763aee69b66588d4bdab6647734e`。

待提供 SSH 登录凭据或受保护凭据路径后继续部署；无需再次确认已批准的范围。部署后重新开始手机场次，解除旧场次已缓存的 unsupported 状态，再联调自动文字同步。真人误报、麦克风唤醒、回听对齐、熄屏震动和长会议稳定性仍需现场验收。


## 2026-10-07 用户要求重试后的结果

此次测试包已成功安装。以下结果替代上面的「测试包尚不能安装」阻碍，但香港上线仍未执行。

- 原 Redmi `W4W8Q8LVBQ8XZHLF` 的 `remoteSmoke` 实际运行通过：手机直连香港，行动草稿负责人林春源，回读原有测试任务 GUID 成功；未使用 confirm 参数，未创建任务。报告 `evidence/p0-1007/retry-phone-cloud-baseline.log`。
- 新增 `meetingHttp` 真机检查实际运行通过。手机使用正式 `HttpOopsApi` / `MeetingSync` / `NoticeJson`，连接本机新版后端 dry-run 配合真实 Jev，完成四类合成纪要、精确原文、重复同步防重、往期列表/搜索、本场原话问答、主题判断、跨场历史冲突、停止时间、删除及删除后迟到同步拒绝。合成会议已删除，服务器列表 count=0。报告 `retry-phone-meeting-http.log`，后端日志 `retry-local-backend.log`；这是本地新版后端的手机 HTTP/JSON 验收，不能写成香港已上线或真人语音验收。
- `meetingFeatures` 全 ASR 流程实际失败，报告 `retry-phone-full-pipeline.log`：豆包 WebSocket 返回 HTTP403，`resource_id=volc.seedasr.sauc.duration`，`requested resource not granted`。当前安装 APK 与原构建 SHA 一致 `50cd0671916a5aeb92c074beb456553586b897f8746ac5283acc6763b4098f89`；没有更换密钥、资源编号或主 App。需核对上游资源授权，不能推断为缓存/网络问题或宣称识别正常。
- 试验入口改为直接启动 Engine 的 WAV 管线，避免 HyperOS 拒绝后台 startActivitySync 后一直等待；ASR明确失败时快速退出，全部私有设置在 finally 恢复。测试 APK重新构建通过。
- 测试后逐项解析设置与本轮开始快照一致；测试包卸载，本机业务后端停止，临时8808反向映射撤除。重试开始时 Mac 音频桥和7790映射均未运行，已恢复 Wireless microphone 音频桥、7790反向映射和手机 remote 收音。现能接收PCM、缓存增长，但 ASR 仍显示403，不能称为实时识别恢复。截图 `retry-phone-restored.png`。
- 香港 SSH 重新尝试仍返回 `Permission denied (password)`；公网 healthz仍只有旧功能，尚未出现 notices/meetings。部署仍等待有效 root SSH 密码或受保护凭据路径，备份/重启/回退尚未执行。

现阶段剩余外部阻碍：香港登录凭据、豆包流式语音资源授权。用户已批准的部署范围仍有效；这两项解除后继续上线和全流程验收，不重复征求范围确认。


## 2026-10-07 登录后：香港部署完成

用户在梦云互联完成登录后，SSH 成功连接原香港实例 `103.110.81.78`；继续执行既有安装与上线授权。已部署 `local_backend.py` 与 `meeting_features.py`，服务运行用户仍为 oops，公网入口仍为 `https://giftedprofessor.xyz/oops-meeting`。healthz 实测 notices/meetings=true，Jev、声纹、live Feishu、preview 保持原值。

- 完整源码/受保护环境备份：`/var/backups/oops-meeting/p0-20261008T032526Z`。本地日志 `evidence/p0-1007/hk-deployment.log`。环境、四人映射、声纹数据哈希未变，四人声纹目录回读成功；原官网、desk、模型入口均返回200。未创建飞书任务，未更改代理或鉴权。
- 香港内部与公网分别通过六个新增接口401/403隔离，以及真实 Jev 合成 sync/upsert/list/open/search/topic/resolution/history-conflict/recall/tag/draft/ended_at/delete/tombstone 检查。独立合成会议已删除。公网日志 `hk-public-verification.log`。
- 手机自动同步发现真实兼容缺陷：Android `Instant` 输出末尾Z，香港 Python3.10原生fromisoformat拒绝；本机Python新版与使用+00:00的部署样例未覆盖。新增统一日期解析器，兼容Z及纳秒精度，保留存档原始日期字符串；同步与30天清理共用。香港Python3.10运行5项后端测试全部通过，公网纳秒Z时间往返和完整业务检查再通过。修复备份 `/var/backups/oops-meeting/timestamp-20261008T033120Z`，日志 `hk-timestamp-deployment.log`。最终 meeting_features.py SHA256 `09851b23c6921e446c435a465f77b6f4d0cd572fe139a8b088e269496751aefa`。
- 重新开始手机remote场次后，自动队列已在香港存下当前会议元数据：Java格式开始日期 `2026-10-08T03:31:47.256060Z`，文件0600、oops所有，无音频/preview字段；手机待同步0。证据 `hk-phone-auto-sync.json`、`hk-phone-final.png`。停止手机场次后，结束日期也由手机自动同步成功（`hk-phone-ended-sync.json`）；该空白验收场次随后删除并恢复remote收音。这证明手机自动同步元数据和结束路径，因ASR403而没有真实转写文字，不能替代真人语音全流程验收。
- 主APK未更换；本轮测试包安装再次被手机取消，因此没有把公网服务合成检查写成测试APK已在香港运行。手机私有设置与上线前快照逐项完全一致，Mac Wireless microphone桥与7790映射保留。

截至此次部署完成时，外部阻碍为豆包ASR资源授权：HTTP403 `volc.seedasr.sauc.duration requested resource not granted`。这个资源名为官方2.0小时版（[官方模型列表](https://docs.volcengine.com/docs/DoubaoVoice/model-list?lang=en)）；需在对应火山账号核对已授权模型和当前应用/API凭据绑定，不能从服务商服务器登录推断语音授权已恢复。真人口令、误报率、熄屏震动和长会议稳定性仍未验收。同步队列仍不抗进程退出。


## 2026-10-07：用户充值后重新验收

测试APK这次安装成功，手机直接对香港新版后端运行 `meetingFeatures`。ASR连接仍返回HTTP403 `volc.seedasr.sauc.duration requested resource not granted`，未收到语音定稿，后续全流程未通过；不能把用户已充值视为资源权限已恢复。报告 `evidence/p0-1007/recharged-full-pipeline.log`。独立合成场次仅产生空白元数据，已按合成议程/开始日期/无文字无提醒核对后删除；原设置完全一致，测试APK卸载并恢复Mac remote收音。

进一步核对发现：已安装主APK与本机APK的SHA256均为 `50cd0671916a5aeb92c074beb456553586b897f8746ac5283acc6763b4098f89`，其构建配置及DEX中AppID为 `2983469702`，使用AppID+AccessToken鉴权，资源为 `volc.seedasr.sauc.duration`；此前浏览器指向另一AppID `8238142535`。证据 `recharged-client-config.json`。两个应用是否属于同一账号、充值和资源授权是否覆盖手机凭据，尚无控制台证据。登录入口现指向手机实际应用 `2983469702`；内置浏览器仍是登录页，需用户完成登录后继续核对。未更换凭据、资源ID或主APK。


## 2026-10-07：改用Chrome后，ASR与香港真机闭环通过

用户要求控制普通浏览器后，接入已登录Chrome。实时控制台确认：`2983469702`（shandianshuo）和 `8238142535`（shibie）均在同一火山账号下；手机应用的2.0小时版为正式服务，页面操作为“暂停”，不是“开通”，原30小时包已使用30/30。费用中心可用余额8.97元、欠费0元、1.03元账单已还款；用户充值已到账。因此不能继续将AppID不同描述成充值覆盖错误。截图 `chrome-recharged-balance.png` / `chrome-asr-service.png`，无明文凭据。只读检查，没有购买、改授权、暂停服务或更换凭据。

- `meetingFeatures` 首次充值到账后的真机运行已完成实际ASR定稿、Jev纪要、自动同步、精确原文回读、预算原话问答和冲突判断。最后测试误报删除超时：`HttpOopsApi.historyOpen`将404映射为null，测试却等HttpFailure404。独立公网GET确认该会议已经404。该问题只在测试断言，主App无需更新。首次报告 `chrome-paid-full-pipeline.log`。
- 修正删除验收：要求historyOpen=null且列表无该session；同时提前记录测试session，确保ASR失败也能清理。`assembleDebugAndroidTest`成功。
- 再次真机到香港的 `meetingFeatures` **passed=true**：3句实际ASR定稿，3句服务器存档，原文完全一致，停止时间及合成议程存在，原话问答和实际Jev冲突判断通过，测试会议已删除。报告 `chrome-full-pipeline-final.log`，测试专属手机日志 `chrome-full-pipeline-final-phone.log`。
- 独立 `meetingHttp` **passed=true**：正式手机HttpOopsApi/MeetingSync对实际香港服务完成四类合成标签、原文/防重、往期列表/搜索/问答、主题判断、历史冲突、停止时间、删除和删除后迟到同步400。报告 `chrome-hk-native-http.log`。四标签是构造的合成输入验证，不是四类别自动分类准确率测量。
- 独立读回确认三个测试session均404，healthz各项正常，所有私有设置与此次开始快照逐项一致。报告 `chrome-final-verification.json`。测试APK卸载，恢复Mac Wireless microphone远端收音和7790映射；新remote场次日志实际ASR connected，截图 `chrome-phone-restored.png` 显示实时识别中、缓存增长、待同步0，音频桥TCP连接established。主APK SHA256仍 `50cd0671916a5aeb92c074beb456553586b897f8746ac5283acc6763b4098f89`；未创建飞书任务。

当前ASR403阻碍已解除；无法仅凭时间关联断定火山后台恢复机制，未改客户端凭据/模型或服务器授权。①–⑥已安装上线并通过合成音频真机业务闭环，现可继续真人试用。真实口令、跑题/遗漏/冲突误报率、自动标签准确率、HyperOS熄屏震动和长会议稳定性仍尚未验收；讲话人预览仍不进入正式拆分/self闸门。
