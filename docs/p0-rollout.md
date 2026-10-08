# Oops ①–⑥ 本地实现与待上线步骤

本轮范围：本场四类纪要、原话问答与 Oops 口令、议程时间提醒、跑题/结论遗漏、可能冲突、团队会议文字存档与往期问答。⑦个人记忆/个性化、⑧飞书日程授权、⑨录音豆自动起止仍留下一批。正式说话人拆分和 self 负责人闸门保持原有设置。

## 确认后才进行

这两步来自用户提供的《Oops 剩余 P0：排序与本轮补齐计划》，其中明确写了“计划批准不包含这些”：

1. 手机安装新 APK 会结束当前试用场次；先获得用户确认，并只读记录现有业务后端、音源、远端音频地址和试验开关，联调后恢复。
2. 香港部署须先确认四位成员均同意会议转写文字、标签、说话人、议程和提醒存到团队服务器；不含音频。服务凭据持有人共享访问，默认保留 30 天。

2026-10-07 用户回复「同意」，授权安装和香港部署，并确认前述四人文字上传。主 App 已安装；香港 SSH 密钥登录被拒绝，仅允许密码登录，当前等待凭据，尚未替换云端源码。

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
