# 香港服务器后端部署与验收

## 目标

Android 经网络调用香港服务器上的 Oops 后端；服务器本地执行 `lark-cli`，以用户授权身份创建、回读飞书任务。日常后端运行不经过 Mac，不依赖 Mac 的 CLI 登录态。手机继续直接连接豆包 ASR；收音设备、缓存、回听和用户确认仍在现场。

手机麦克风可独立使用。当前 Hollyland 接收器接在 Mac，选择「Mac 收音」时仍依赖 `mac_audio_bridge.py` 和 USB `adb reverse`。迁移业务后端不会改变这条物理收音链路。

## 当前结果（2026-10-06；服务器 UTC 2026-10-07）

已部署并接通。Redmi 的后端地址为 `https://giftedprofessor.xyz/oops-meeting`，Mock 关闭，服务访问凭据已配置。撤除了全部 adb reverse；手机使用移动网络完成真实 Jev 标签、召回、声纹请求、行动草稿及飞书确认/回读。Mac 本地业务后端未运行。日常音源已恢复手机麦克风，界面显示「收音中／实时识别中」；Hollyland 接收器接在 Mac 时，仍需原收音桥和音频 adb reverse。

目标是新的 Oops 组织，四人映射为林春源、江古月、洪玉麟、黄丽萍（飞书显示名 Lydia）。服务器启用 `OOPS_STRICT_PEOPLE=1`，缺失映射会报错，不会默认派给本人。手机不持有飞书应用密钥或用户 token，只保存这个 HTTPS 服务的访问凭据；H5 只获知是否已配置。

物理手机原生集成测试使用 App 的 `HttpOopsApi`、`ActionWorkflow` 和持久化操作库，创建且仅创建一条明确标为测试的任务：`[Oops服务器验收] 手机连接香港后端（测试）`，负责人林春源，GUID `5633ee62-6b72-48a9-8f17-8644c48517a3`。同一 `op_id=03f69dec-b746-49a6-afa6-cb653ccfa432` 重复确认返回同一 GUID，服务端 `attempts=1`。重启 `oops-backend` 后，手机再次查询和回读成功；服务器 CLI 也重新读取了飞书侧任务。该测试没有冒充真人点击 UI 或真人语音验收。

`./scripts/check.sh lintDebug` 成功：68 项 Android 单测、后端自检、10 项评估/规则测试和 1 项 HTTP 鉴权测试；Lint 无问题。Jev、声纹及 live 飞书已启用；句内拆分和 self 自动负责人仍在原验收闸门内，当前 `segments_validated=false`。尚未做迁移后的全天运行、网络切换或真人 Hollyland 听感验收。

## 部署位置与维护

- 香港 `103.110.81.78`：`oops-backend.service` 已启用自启，监听 `127.0.0.1:8787`；源代码 `/opt/oops/app`，Python `/opt/oops/venv`，独立 Node/CLI `/opt/oops/runtime`、`/opt/oops/tools`。
- 私有状态 `/var/lib/oops/backend`（人员映射和确认操作）；飞书 CLI HOME `/var/lib/oops`。`backend.env`、`jev.env` 和 CLI 配置文件均 0600；凭据不进入仓库或部署说明。上线设置必需 `OOPS_API_TOKEN` 和 `OOPS_STRICT_PEOPLE=1`。
- 公网入口仍经过日本 `177.2.186.107`，再到香港 TLS 8444；入口独立路径 `/oops-meeting/`，香港转发时剥离此前缀。两台 Nginx 都限制请求大小/等待时间，香港另有限流/连接限制。原 `/oops/` 官网及 `/oops-desk/` 不变；没有更改 DNS、防火墙或 Reality 443。
- 香港 Nginx 配置：`/etc/oops-meeting/locations.conf`、`/etc/nginx/conf.d/oops-meeting-limits.conf`；日本 `/opt/oops-meeting/nginx-locations.conf`。模板见仓库 `deploy/`。
- 香港原配置备份 `/var/backups/oops-meeting/20261007T064636Z/huanqiu-backend.original.conf`；日本原配置备份 `/var/backups/oops-meeting/20261007T064710Z/nginx.original.conf`。回退时恢复对应原配置，先 `nginx -t` 再 reload，停止 Oops 后端即可；保留私有状态和授权以便恢复。
- 核验摘要 `/var/lib/oops/verification/feishu-check.json`；本机测试日志和截图在 gitignored `.local-backend/diagnostics/`。部署源码 SHA-256 与本机 `scripts/local_backend.py` 一致。

服务操作（香港）：

```sh
systemctl status oops-backend --no-pager
journalctl -u oops-backend -n 80 --no-pager
systemctl restart oops-backend
```

GET `/healthz` 与业务接口都要求 `Authorization: Bearer <服务访问凭据>`；缺少或错误凭据返回 401。不要把凭据写进命令历史。手机更换服务地址时需单独配置该地址的凭据，旧操作仍使用原地址及其凭据查询。

原官网 `/`、`/oops/`、`/v1/models` 和 `/oops-desk/` 路由复查均可访问。完成了后端进程重启验证，未对整台服务器执行重启。

以下为排查过程历史，描述当时状态，以本节当前结果为准。

## 2026-10-07 排查状态（历史）

- 手机 `W4W8Q8LVBQ8XZHLF` 的设置为 remote 音源，音频 `127.0.0.1:7790`、业务后端 `http://127.0.0.1:8787`，Mock 关闭。
- 两个 adb reverse 映射存在，但 Mac 收音桥和本地业务后端当时均未运行。
- 已启动 `python3 -u scripts/mac_audio_bridge.py --device 'Wireless microphone'`。手机自动重连，界面显示「Mac 收音：Wireless microphone」「实时识别中」，缓存从 0:01 增至 0:35；TCP 已建立。没有重置正在进行的会话。
- 这是音频链路恢复证据，尚无本次真人语音转写/回听验收。不要把低底噪或已连接状态当作麦克风讲话效果已通过。
- 本机 `lark-cli auth status --json --verify` 成功，用户林春源，token 有效；这不是服务器登录证据。
- 香港 `root@103.110.81.78:22` 能到达认证阶段，默认 SSH 身份和现有 `hncloud_newapi_ed25519` 均被拒绝，仅允许密码认证。服务器资源、CLI、授权和部署尚未核实。
- 服务器历史配置为 Ubuntu 22.04、4 核/4 GB、80 GB、25 Mbps；引用聊天及官网部署记录仅作定位，不能作为当前资源余量或性能验收。

## 登录后顺序

1. 只读检查系统、磁盘、内存、现有服务、监听端口和防火墙。先核对官网和代理健康基线。
2. 创建独立服务用户和持久化目录，部署本项目后端源代码、Python 环境、CAM++ 模型和注册声纹。不要上传录音语料、Mac `.venv-voice` 或全部本地操作记录。
3. 在服务器安装 Linux 版官方 `@larksuite/cli`。本机核对的版本为 1.0.69，包声明支持 Linux；在服务器实际检查版本与执行结果。
4. 在服务用户下配置飞书应用。应用密钥经 stdin 提供，不写入命令行、日志或仓库；服务器建立自己的用户授权，不导出 Mac token。授权范围至少覆盖后端实际用到的任务读写与人员查询，并核对权限错误。按 Lark 技能流程提供登录链接和二维码。
5. 在服务器执行 `lark-cli auth status --json --verify`，确认 user 身份、用户名、有效 token；通过只读飞书查询证明服务器能直接访问。执行 `local_backend.py --resolve-people`，核对映射缺失项。
6. 先部署 dry-run 后端，只监听回环地址；配置系统服务自启、重启和私有持久化目录。Jev 凭据使用独立受保护配置。
7. 为公网调用补 HTTPS、手机身份鉴权、服务端请求限制和限流。当前 Handler 和 Android HTTP 客户端没有客户端身份鉴权，现有本机接口不能直接公开。手机应保存服务访问凭据，不持有飞书应用密钥或用户 token。
8. 配置独立入口。香港 443 已用于 Reality；现有 `giftedprofessor.xyz` 入口经日本旧机分流，`/v1` 已有其他业务。应核对入口后使用独立主机名或明确隔离的路由，不能直接占用 443 或覆盖原 `/v1`。
9. 先验证远程召回、标签、声纹和草稿，再切换 live 模式。真实任务由用户在手机检查并确认后创建，回读同一 GUID；重试同一 op_id 必须仍只有一条任务。
10. 拔掉 USB，在手机麦克风模式下验证网络调用、网络切换、超时恢复与任务查询。复查原官网和代理；记录实际时延和资源占用。

## 完成标准

- 服务器端的飞书用户授权核验和只读 API 调用成功。
- 手机通过 HTTPS 调用远程后端；Mac 本地业务后端停止后仍能运行。
- 用户确认后真实创建飞书任务、回读并防重；飞书调用实际发生在服务器。
- 进程/服务器重启后操作记录和授权仍可恢复；凭据失效有明确反馈。
- 原官网/代理未受影响。实际 CPU、内存、队列和延迟满足单手机试用要求后再增加并发。
- 句内拆分和 self 自动填人继续使用原验收闸门；迁移不会绕过人工验收。

当时尚未切换远程服务；当前部署与手机闭环结果见文档顶部。

## 后续：已连接服务器，等待新组织配置

用户完成服务器后台登录后，通过后台现有登录信息成功连接香港机。实测 4 核、3925 MiB 内存，其中约 3221 MiB available，根分区约 74 GiB 可用；官网 Nginx、官网应用及香港代理均 active。

已创建系统用户 `oops`，私有 HOME `/var/lib/oops`（0700）；将现有 Linux Node 22.23.2 可执行文件复制到独立 `/opt/oops/runtime/bin/node`，通过官方 npm registry 在 `/opt/oops/tools` 安装 `@larksuite/cli@1.0.69`。服务器实际执行 `lark-cli --version` 成功。未修改官网/代理配置，未开放 Oops 公网接口。

用户明确指定新的 **Oops 飞书组织**，包含录音中的林春源、江古月、洪玉麟、黄丽萍。因此服务器发起自己的 `config init --new` 应用配置流程；尚未完成应用绑定、用户授权或组织身份核验。下一步完成配置后发起最小任务读写及人员查询授权，并核对四人在目标组织中的身份。不能沿用本机旧组织的应用、用户授权或 `people.json`。

授权链接与验证码有时效，不写入此交接；过期后重新生成。当前仍没有在服务器创建任何真实飞书任务，也没有迁移业务后端。

## 后续：服务器飞书用户授权已验证

服务器应用为 `oops`（`cli_aa4f5425caf85cc8`）。用户完成授权后，服务器 CLI 成功取得林春源的用户登录态，`auth status --json --verify` 显示 user ready、verified true、token valid，任务读写和人员查询权限已授予。凭据只保存在独立服务用户 HOME，配置文件 0600、HOME 0700。

服务器实际执行 `task +get-my-tasks --page-limit 1 --as user` 返回 `ok:true, identity:user`。通讯录查询准确匹配林春源、江古月、洪玉麟，均已激活且非跨组织用户；黄丽萍未命中，全名及「黄」「丽萍」「萍」均无结果。未命中不能证明她不在组织内，还需核对显示名、邀请加入状态和当前授权的可见范围。不能默认把她的任务派给林春源。

不含凭据的核验摘要保存在服务器 `/var/lib/oops/verification/feishu-check.json`（私有权限）。服务器 CLI 独立连接飞书已验证；本次只读核验，没有创建真实任务。业务后端尚未部署，手机后端地址仍未切换，因此不能把 CLI 授权成功当作手机全链路已完成。

## 四人映射已补齐

用户确认黄丽萍在目标组织的显示名为 `lydia`。服务器通讯录精确查到唯一的 `Lydia`，已激活、非跨组织用户，且无更多匹配。已保存「黄丽萍 → Lydia」的显示名对应关系和该应用下的 open_id；四人均已匹配。

服务器 `/var/lib/oops/verification/feishu-check.json` 的 unresolved 已清空。另存后端兼容格式的 `/var/lib/oops/people.json`（0600），包含 self、people 和 display_names；待部署时交给后端使用，不能重新按「黄丽萍」搜索后覆盖这份确认映射。此文件尚未接入运行中的业务后端。


## 自助声纹录入已上线（2026-10-07）

香港后端已部署 App 录入 API 及公开静态手机页 `https://giftedprofessor.xyz/oops-meeting/voiceprint/`。完整个人邀请链接在服务器私有文件 `/var/lib/oops/verification/voiceprint-invites.json`（0600），不进入仓库；本次四人链接有效至 `2026-10-21T07:29:04Z`。可分别转发给林春源、江古月、洪玉麟、黄丽萍（Lydia）。本人在手机 Safari/Chrome 打开完整链接，勾选本人同意、允许麦克风、按提示朗读 30 秒，通过基本质量检查后明确保存，再用不同文字读 10 秒核对结果。不要将单纯无 fragment 的入口当成完整邀请，也不要用他人的链接。

邀请由独立 `OOPS_ENROLL_SECRET` 签名，存于 `/var/lib/oops/backend.env`（0600）；它不是 App 全权限 API token。个人链接不能写别人的声纹、删除声纹或创建飞书任务。需要重新发邀请时，从该私有 env 读取 secret，使用 `scripts/voiceprint_invites.py` 的 `issue_invite(secret, name, expires_at)` 为 TEAM 成员生成新链接；到期时间为 Unix 秒。调整 secret 并重启服务会撤销全部旧链接；App 凭据和 Feishu 授权不变。

运行时声纹文件 `/var/lib/oops/backend/voiceprints.json` 保存特征与成员元数据，0600 原子写入。文件不存在时读取 seed；首次本人录入后生成，随后启动总是读取运行时文件。删除全部声纹也保留空注册表，防止旧声纹恢复。变更后拆分/self 自动负责人闸门关闭，仍须新的独立验收。更新前源文件备份在 `/var/backups/oops-meeting/voiceprint-web-20261007/`；回退页面/代码时保留运行时声纹文件和 env。部署仅更新 Oops service 源码，不更改 Nginx、官网或代理路由。

已核验 4 个个人链接、越权 403、无凭据 401、静音录入 400、重启目录版本不变、线上 7 个源文件 SHA-256 与本机一致。自动测试没有覆盖线上四人的历史声纹；上线时运行时文件仍不存在，seed 哈希仍为 `60a4967b13f4848460bb6608cb209835fd231b2fd698c71a4230b2983b236bf1`。当前待四人现场录入及不同句子的真人匹配核对。网页在 390 px 手机尺寸核对过；iOS/Android 不同浏览器的实际麦克风授权和录入仍需团队试用反馈。


## 实时说话人试验已启用（2026-10-07）

用户提供现有 SSH 登录信息后，恢复香港部署连接。已核对并仅部署 local_backend.py 的 preview 相关差异、新模块 speaker_preview.py，以及 pyannote 3.0 ONNX 模型和许可；安装服务器 ONNX Runtime 1.23.2。受保护 backend.env 增加 OOPS_SPEAKER_PREVIEW=1，重启 oops-backend 成功。代码/env/原依赖清单备份在 /var/backups/oops-meeting/speaker-preview-20261007/。回退时恢复备份代码和 env，重启单项服务；运行时声纹和用户录入资料保留。

手机 speaker_preview=true、speaker_preview_url 为空，直接走现有 HTTPS 香港后端；Mock 仍 false，Mac Wireless microphone 收音及本场转写继续运行。通过调试调用正常 settings.apply 接口打开开关，没有重启 App 或清空本场缓存。没有启动 Mac 试验分析服务，也无 tcp:8790 reverse；音频 tcp:7790 继续使用。

公网 healthz 返回 speaker_preview=true、Jev/声纹/live 飞书正常、segments_validated=false。10 秒工程音频 preview 公网往返约 1364 ms，服务端计算 256 ms。实际手机滚动请求连续 200，静音/少语音窗口服务端约 65–160 ms；手机已显示试验姓名林春源、Jev 辅助分段及疑似多人同时说话提示。这是功能连通和 UI 证据，不是对应真人身份/重叠准确率验收。

运行时 voiceprints.json 在部署前后 SHA-256 相同（20f2f29f62612ae1ecd46dba22223661c20ea1f6aebf6c512a12220575926385）；未更改录入声纹、正式分段或 self 自动负责人状态。Nginx/oops-backend 均 active；未更改入口或其他服务。


## 2026-10-07：①–⑥香港部署已完成

用户完成原服务商登录后，香港原实例已备份并升级两个后端模块。公网healthz的notices/meetings均true；新增接口401/403及同步、防重、搜索、判断、问答、删除检查通过，环境/人员/声纹哈希不变，官网与既有入口正常，无新飞书任务。另修复Android Instant末尾Z/纳秒在香港Python3.10的兼容问题，服务器5项测试与公网往返通过。手机自动同步元数据已实际落盘0600，原设置逐项一致；ASR仍受上游资源权限403阻断，未产生真人转写验收结果。

最新备份、证据、回退与验收限制见 [P0上线记录](p0-rollout.md#2026-10-07-登录后香港部署完成)。以上取代先前“等待SSH/香港未上线”的当前状态；历史条目保留。

## 2026-10-08 UTC：本机 SSH 公钥登录已配置

用户授权添加本机密钥并提供服务器现有密码后，成功登录 `root@103.110.81.78:22`。原配置 `PubkeyAuthentication no`，root 的 `authorized_keys` 为空。已备份至 `/var/backups/oops-ssh/key-20261008T064154Z`，追加本机 `~/.ssh/id_ed25519.pub`，通过 `/etc/ssh/sshd_config.d/00-oops-pubkey.conf` 启用公钥认证。授权文件权限0600、目录0700；`sshd -t`通过后仅reload SSH，保留原密码登录。

公钥指纹：`SHA256:Tc+qHSBE3nP/4Ifxk2ppJA/ZuZrQUEJCNO1GHMYstm0`。禁用密码和交互认证、指定此密钥的连接成功；默认 `ssh -o BatchMode=yes root@103.110.81.78` 也成功。临时密码文件已清理。

本次未部署声纹来源升级包。`oops-backend`仍active，线上 `local_backend.py` SHA256仍为 `18b241634e8e5063153c57b061fd53d8d6b702585b35da94f422958e572a7cf0`；无token的本机healthz返回401。后续部署继续沿用备份、基线校验、替换指定模块、健康及鉴权检查、失败回退流程。

## 2026-10-08 UTC：说话人与任务分级 v2 首发部署

已部署 9 个指定源码模块和关闭验收文件，备份为 `/var/backups/oops-meeting/attribution-20261008T113750Z`，发布工具为 `/opt/oops/releases/speaker-attribution-20261008/speaker-attribution-upgrade.py`。部署前校验发布包、线上基线与两种模型哈希；失败可按工具内备份恢复。服务重启后认证、分段完整持久化往返、未知/重叠/集体承诺不预填负责人检查通过，新增飞书写入 0。线上声纹、人员映射、环境与固定验收公钥均保持一致。

health 为 `ok`，`segments_validated/strict_a_enabled/self_owner_enabled/named_owner_enabled` 全部 false。公网未鉴权 health 返回 401；原 Jev、声纹、飞书、会议、提醒与预览能力均正常。首发只是工程部署，不是说话人准确率或真人验收通过。

已单独初始化固定验收签名公钥 `/etc/oops/acceptance_allowed_signers`；私钥只在 Mac `~/.config/oops/acceptance_ed25519`，未上传。后续真实验收包仅含签名报告、冻结参数和校准器，不携带音频、人工标注或注册向量；只有实际链路验收和当前有效指纹通过才可原子安装。详情见 [操作说明](speaker-attribution-runbook.md) 和 [执行方案](speaker-attribution-plan.md)。

随后修复 Mac Python 3.14 与香港 Python 3.10 的 AST 指纹编码差异。兼容版发布工具为 `/opt/oops/releases/speaker-attribution-20261008-compat/speaker-attribution-upgrade.py`，最新备份为 `/var/backups/oops-meeting/attribution-20261008T114855Z`。再次验证认证、同步、草稿与受保护配置通过，四门禁仍全 false；两端源码指纹与全局引用集合实测一致。
