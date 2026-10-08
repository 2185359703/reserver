> 仓库整合说明：运行代码与脱敏证据公开保存。原始设备/会话报文及 SDK 源码在本机 .private 中；公开 SDK 的共享密钥已单独保存于 config/sdk_keys.json。

# Log3 多样性取样与随机请求

日期：2026-10-08。批次：`20261008-3676378a`。范围与用户指令见 [scope.md](analysis/scope.md)。报告类型为普通 JS 请求分析，flavor=null。

本轮已取得 15 组独立 Camoufox 会话样本，包含 15 种不同设备环境、3 种语言、7 种屏幕尺寸和 2 种 FeiLin 构建。每组均保存浏览器原始参数、AES 输入、对应配置和本地重建请求。15 组的 Data、Signature 与完整请求体均逐字节匹配；浏览器 15 次、本地 15 次 Log3 均返回 HTTP 200、Code="200"、ResultObject=true。

默认请求已改为从这 15 组中随机选择一组完整配置，然后使用当前上报时间、新 UUID nonce 重新计算 Data 与签名。设备、会话、事件和密钥随整组选取，保留内部关联。原始单组配置可通过 `--sample baseline` 选择。

## 运行

```powershell
Set-Location '.\aliyun_log3'

# 默认随机选择整组配置，本地计算并发送一次
python .\aliyun_log3.py

# 查看本次随机选组及新参数摘要，不发送请求
python .\aliyun_log3.py --dry-run

# 指定某一组，仍生成新的上报时间、nonce、Data 和签名
python .\aliyun_log3.py --sample sample-12

# 使用初始单组配置
python .\aliyun_log3.py --sample baseline

# Node.js 可独立选择和计算
node .\aliyun_log3_node.cjs --sample random --summary

# 完整样本池与随机请求离线校验，不发送网络请求
node .\analysis\verify_pool.cjs
```

随机选择采用 `crypto.randomInt`，允许重复选择同一组；请求时间、nonce 和签名每次重算。显式使用 `--profile` 和 `--secrets` 时需要同时提供配套文件。`--replay` 保持原始 cURL 重放行为。

15 组私有材料位于 `.private/cohort/20261008-3676378a/sample-01` 至 `sample-15`。每组含 capture.json、profile.json、secrets.json、node_request.json、node_response.json；该目录继承 `.private` 的 Windows ACL，并被 Git 忽略。默认选择索引为 `.private/cohort_index.json`。

## 多样性统计

| 项目 | 结果 |
|---|---|
| 有效样本组 | 15 |
| 独立设备环境 / 会话 / deviceData / 事件载荷 | 分别 15 / 15 / 15 / 15 |
| 语言 | zh-CN、zh-TW、en-US，各 5 组 |
| 屏幕尺寸 | 1600×900、1680×1050、1920×1080、2560×1440、3072×1728、3440×1440、5120×1440 |
| FeiLin 构建 | 040：12 组；041：3 组 |
| 动态 SG 脚本 | 13 种 |
| 鼠标移动事件 | 每组 0–91 条 |
| 键盘 keyup 事件 | 每组 11–32 条 |
| 点击 | 每组 0–2 次 |
| Data 长度 | 2,200–11,180 字符 |
| 浏览器请求接受 / 本地请求接受 | 15/15；15/15 |
| 完整本地参数比对 | 15/15 |

设备环境差异按 UA、platform、language/languages、hardwareConcurrency、屏幕、窗口尺寸、devicePixelRatio 等实际观测值进行统计，环境对象不含采集时间，因此“15 种环境”并非仅由不同时间造成。独立会话、deviceData 与事件对象也分别做了哈希去重。

取样交互是在浏览器内执行的随机自动化输入与输入框点击，属于非提交操作。没有把这些事件描述为人工用户行为，没有提交注册、登录、短信或验证码操作。

### SDK 选择方式

sample-13 自然加载了 041。sample-12 和 sample-15 使用已经公开观测到的 041 原始源代码进行受控脚本响应替换，并仅插入取样探针；这两组在清单中明确标记 controlled=true。其余 12 组自然使用 040。两组受控 SDK 样本的接口响应均来自真实服务端。

首次遇到自然加载 041 时，040 专用探针未命中，该次尝试未计入有效组。一次 302 重定向试采加载了 041，但重定向后的脚本没有经过 Playwright 源码拦截，仍未计入有效组。两次取样调整记录在 [sampling_notes.json](analysis/samples/20261008-3676378a/sampling_notes.json)。

## 验证与数据文件

完整清单：[manifest.json](analysis/samples/20261008-3676378a/manifest.json)。逐组比较：[samples.csv](analysis/samples/20261008-3676378a/samples.csv)。离线随机选择校验：[pool_validation.json](analysis/samples/20261008-3676378a/pool_validation.json)。证据哈希：[evidence_hashes.json](analysis/samples/20261008-3676378a/evidence_hashes.json)。

样本池启用前，已再次核对全部 15 组原始请求的完整字节比对。随后进行了 45 次离线随机选组计算，nonce、Data 和请求体均各有 45 个不同值，未发送网络请求。启用默认随机模式后又执行了一次真实请求，返回 HTTP 200、Code="200"、ResultObject=true。

本轮采样结束后，已解除脚本响应替换、停止网络捕获，并重新加载原页面清除临时 `__ali_sample` 探针。Camoufox 保留运行。

## Evidence → Finding → Path

### Evidence

| ID | source_type / source_ref / artifact_path | observed_at | content_hash | repro_command / raw_excerpt |
|---|---|---|---|---|
| E-C01 | file；analysis/samples/20261008-3676378a/manifest.json | 2026-10-08 | 见同目录 evidence_hashes.json | `Get-Content .\analysis\samples\20261008-3676378a\manifest.json`；15 组，原始与本地响应成功，逐字节匹配 |
| E-C02 | file / runtime；.private/cohort/20261008-3676378a 各组 capture.json | 2026-10-08 | n/a，私有运行时材料 | `node .\analysis\verify_pool.cjs`；原始 AES/XHR 与环境对象，密钥不公开 |
| E-C03 | file；analysis/samples/20261008-3676378a/pool_validation.json | 2026-10-08 | 见同目录 evidence_hashes.json | `node .\analysis\verify_pool.cjs`；15 组完整比对，45 条离线随机请求唯一 |
| E-C04 | file；.private/sdk/feilin.js 与 .private/sdk/feilin041.js | 2026-10-08 | 见 evidence_hashes.json | `node --check .\.private\sdk\feilin041.js`；原始公开构建，取样时仅插入探针 |

各 Evidence 的 linked_workitem=n/a、supersedes=none。私有材料仅留在受保护目录。离线随机校验再次运行会生成新的分布记录，如需保持当前证据哈希，应先保留原文件快照。

### Findings

| ID | title | severity / category | evidence_ids | status / confidence | location / impact |
|---|---|---|---|---|---|
| F-C01 | 15 组具有实际设备与事件差异 | n/a_re / other | E-C01、E-C02 | validated / high | manifest.summary；支持多组完整配置随机选择 |
| F-C02 | 已观测 040/041 均可完整重建 | n/a_re / reverse_algo | E-C01、E-C02、E-C04 | validated / high | aliyun_log3_node.cjs、aliyun_codec.cjs；受控与自然来源分别标注 |
| F-C03 | 随机请求生成保持样本配对且参数更新 | n/a_re / reverse_algo | E-C01、E-C03 | validated / high | loadPairedProfile、buildRequest；45 条离线计算与真实配对请求均通过 |

repro_steps：执行 verify_pool.cjs 查看完整比对；执行 aliyun_log3.py --dry-run 查看随机组；执行 aliyun_log3.py 验证服务端接受。remediation=n/a，纯参数分析；optional_attack=n/a。

### P-C01

- title：独立会话取样 → 整组配置提取 → 字节比对 → 真实发送 → 随机池启用。
- path_type：callflow。
- start：用户要求 10–20 组多样性样本与逐次随机请求。
- goal：15 组真实运行时材料及可用的随机请求入口。
- steps：启动新的 Camoufox 会话并改变语言/交互条件（E-C02，F-C01）；记录 AES 与 XHR 并提取配套配置（E-C02，F-C02）；固定原始时间与 nonce 做完整比对（E-C01/E-C03，F-C02）；发送本地新参数请求（E-C01，F-C03）；启用完整组随机选择并验证唯一性（E-C03，F-C03）。
- residual_risks：Windows 平台、同一站点场景、同一网络出口；两组为明确标记的受控 SDK 选择。验证目标是 Log3 接受，不涉及验证码业务成功或长期会话有效性。

### 过程记录

2026-10-08：规划 15 组；取样 3 组键盘输入与 12 组鼠标/键盘输入；发现 041 并记录两次探针调整；完成 15 组有效样本、30 次成对请求与字节比对；启用随机池；验证 45 条离线随机请求与一次默认真实请求；移除临时探针并保留浏览器。状态汇总见 [task.json](analysis/task.json)。
