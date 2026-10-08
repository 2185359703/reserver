> 仓库整合说明：运行代码与脱敏证据公开保存。原始设备/会话报文及 SDK 源码在本机 .private 中；公开 SDK 的共享密钥已单独保存于 config/sdk_keys.json。

# 阿里云 Log3 参数分析与本地请求实现

分析日期：2026-10-08。分析环境：Camoufox MCP 1.2.0、Node.js 24.14.0、curl_cffi 0.15.0。报告类型：普通 JS 签名逆向，flavor=null。

后续已采集并验证 15 组多样性配置，默认请求入口改为随机选择完整配套样本，详见 [多样性样本报告](2026-10-08_多样性样本-aliyun-log3-report.md)。使用 `--sample baseline` 可选择本报告的初始单组配置；显式 `--profile` 与 `--secrets` 需要成对指定。

已还原 `Data` 的分层编码与 AES 加密、`Signature` 的 HMAC-SHA1 计算和表单序列化。固定浏览器输入时，Node.js 生成的密文、签名和请求体均与浏览器逐字节相同，用户提供的原始 cURL 也能正确解密并匹配签名。随后顺序发送了三条独立计算的请求，均返回 HTTP 200、Code="200"、ResultObject=true。

默认请求每次刷新上报时间，重算 Data，并生成新的 nonce 与签名。设备环境、会话标识和交互事件来自受保护的 Camoufox 采样配置；每次请求无需启动浏览器。该实现不会自动建立新的浏览器设备会话，也不会重新采集指纹或生成交互事件。

## 直接运行

本机已具备依赖。在 PowerShell 中执行：

```powershell
Set-Location '.\aliyun_log3'

# 本地计算摘要，不发网络请求
python .\aliyun_log3.py --dry-run

# 本地计算后发送一次，输出 HTTP 状态与响应正文
python .\aliyun_log3.py

# 单独查看 Node.js 计算摘要
node .\aliyun_log3_node.cjs --summary

# 仅在明确需要原始样本重放时使用
python .\aliyun_log3.py --replay
```

迁移环境需要 Node.js，以及 `python -m pip install -r requirements.txt`。运行文件与 `.private` 配置共同构成当前可用交付；不要把 `.private` 上传到 Git 或公共位置。

Python 可作为模块使用，返回原始 `curl_cffi.Response`：

```python
from aliyun_log3 import post_log3

response = post_log3(timeout=30)
result = response.json()
assert response.status_code == 200
assert str(result.get("Code")) == "200"
assert result.get("ResultObject") is True
```

`--proxy` 指定请求代理，`--timeout` 指定超时。`--profile`、`--secrets` 指定本地配置；`--node` 指定 Node.js 路径。`--events` 接收真实采集的完整事件 JSON 对象，替换配置中的事件快照；字段结构见下文。默认使用 curl_cffi 的 `firefox` 指纹，`--impersonate none` 可关闭模拟。库内 TLS 指纹与 Camoufox 的真实 TLS 实现不保证完全一致；本次三条服务端请求均接受了这一传输配置。

CLI 仅在 HTTP 成功且业务响应 Code="200"、ResultObject=true 时返回退出码 0。不对 POST 自动重试。

## 目标与范围

用户明确要求对所提供请求进行分析、用 Camoufox 取证，并以本地 Node.js 计算参数构建稳定请求。具体范围记录在 [scope.md](analysis/scope.md)。

| 项目 | 值 |
|---|---|
| URL | `https://cloudauth-device-dualstack.cn-shanghai.aliyuncs.com/` |
| 方法 | POST |
| Content-Type | `application/x-www-form-urlencoded; charset=UTF-8` |
| Action / Version | `Log3` / `2020-10-15` |
| 来源页面 | `https://console.beeize.com/`；实际取样页面为 `/regist` |
| 主要字段 | AaduaneId、Version、SignatureMethod、SignatureVersion、Format、Action、Data、SignatureNonce、Signature |
| 原始请求体 | 23,307 字节；Data 为 21,760 个 Base64 字符 |

HTTP/业务响应的成功仅证明 Log3 上报被接受。本次未提交登录、注册、短信或验证码业务操作。

## 定位与取样

公开页面加载 `AliyunCaptcha.js`，随后加载 FeiLin 1.5.1 与动态 SG 脚本。目标参数与请求封装位于 FeiLin，核心函数如下。

| 函数或对象 | 作用 | 证据 |
|---|---|---|
| `um.getToken` / `sT` | 设备令牌入口；本次首次调用触发 Log3 上报 | E-002 |
| `r0`、`r2` | 参数排序、编码和 HMAC-SHA1 签名 | E-001、E-003 |
| `r9` | 按对象插入顺序序列化表单 | E-001、E-003 |
| `r7`、`r3` | XHR POST 与请求调度 | E-001、E-002 |
| `rz` / `rw`、CryptoJS AES | 参数的 AES 加密 | E-002、E-003 |
| `rj` | 解包 SDK 配置中的签名材料 | E-001、E-002 |

Camoufox 自动化 evaluate 的默认世界与页面 JS 世界不同，直接读取 `window.um` 曾返回 undefined。使用 Firefox 的 `window.wrappedJSObject` 读取页面实际对象后确认 SDK 已加载。默认 XHR Hook 同样未取得目标调用栈，因此对当前 FeiLin 响应做了临时、局部的取样插桩：暴露原签名函数，记录 AES 输入/输出及 XHR 调用栈。请求和服务端响应没有伪造。

取样结束后已解除脚本响应替换、移除持久 Hook、停止网络捕获，并在无已填写输入的注册页上重新加载原页面。已确认临时分析对象消失，正常 `um` 对象仍存在，Camoufox 保留运行。

## 参数算法

### Signature

1. 从表单参数中排除 Signature。
2. 将其余参数名按 JavaScript 默认字符串顺序排序；SignatureNonce 参与签名。
3. 对键和值执行 SDK 编码：encodeURIComponent，然后处理 `+`、`*` 和 `%7E`。
4. 拼成 `encodedKey=encodedValue`，以 `&` 连接，形成 canonicalQuery。
5. stringToSign 为 `POST&encode('/')&encode(canonicalQuery)`。
6. 使用 `signingSecret + '&'` 作为 UTF-8 HMAC 密钥，计算 SHA1，输出 Base64。

实现位于 [aliyun_codec.cjs](aliyun_codec.cjs)。签名材料由运行时 SDK 配置解包获得，实际值仅写入受保护的 `.private/secrets.json`。

### Data

观测到 CryptoJS 接收原始 16 字节 AES 密钥，显式传入 IV 与 Pkcs7 填充，未指定 mode，因此使用默认 CBC。Node.js 实现为 AES-128-CBC，PKCS#7 填充，密文转 Base64。各加密角色的实际密钥分别保存在私有配置中。

Log3 明文具有如下层级；`#` 为连接符，顺序不可改变：

| 层级 | 按顺序排列的内容 |
|---|---|
| telemetry | sessionId、AES(事件 JSON)、AES(appName)、AES(clientVersion)、extra、AES(上报时间) |
| inner | recordType、deviceData、Base64(telemetry) |
| outer | appKey、deviceType、AES(flag)、appVersion、environment、gatherCost、Base64(inner) |
| Data | Base64(AES-CBC-PKCS7(outer)) |

当前采样的事件对象包含 mousemove、mouseclick、keyup、scrollTop、scrollLeft、pointerEvent、clientType、startTime、timestamp。默认保持这些真实快照字段；更新的是独立的上报时间，不会编造鼠标轨迹，也不会把事件的历史时间改成当前时间。用户可通过 `--events` 输入新的真实事件对象。

`deviceData`、sessionId 和环境字段与已采样设备配置绑定。若要切换设备、场景或 SDK 构建，需要重新取样相应配置；跨版本和新会话的自动初始化不在本次实现中。

### 请求序列化

HMAC 使用排序参数，线上请求体保留 SDK 的对象插入顺序。Node.js 使用 encodeURIComponent 序列化表单，Python 直接发送该 ASCII 请求体，不再次 urlencode，不使用 JSON POST。

正常模式采用浏览器取样请求头，过滤 Host、Content-Length、Connection、Accept-Encoding，由传输库处理连接与长度。原始 cURL 的内容保存在本地 `.private/replay_request.json` 中，仅在 `--replay` 下使用。

## 验证结果

离线证据见 [offline_validation.json](analysis/offline_validation.json)。

| 校验 | 结果 |
|---|---|
| 浏览器 Log2 / Log3 的 HMAC、AES、表单序列化 | 全部匹配 |
| Log3 的完整分层本地构建 | Data、Signature、body 逐字节匹配 |
| 用户原始 cURL 的签名 | 匹配 |
| 用户原始 Data 解密并重新加密 | 16,311 字符明文，重新加密逐字节匹配 |
| 更改上报时间、生成新 nonce | Data、nonce、Signature 均改变 |

真实验证记录见 [live_validation.json](analysis/live_validation.json)。三条请求均由本地 Node.js 计算，Python 使用 curl_cffi 顺序发送。全部返回 HTTP 200、Code="200"、ResultObject=true；三组 nonce、Data 哈希与请求体哈希均不同。

复现离线构建验证：

```powershell
Set-Location '.\aliyun_log3'
node .\analysis\verify_builder.cjs
```

本次验证覆盖当前采样设备会话的三次独立上报，不代表所有设备、长期会话、所有 SDK 版本或验证码业务结果均已验证。

## Evidence → Finding → Path

### Evidence

| ID | source_ref / artifact_path | source_type | observed_at | content_hash | repro_command / raw_excerpt |
|---|---|---|---|---|---|
| E-001 | .private/sdk/feilin.js；FeiLin 1.5.1 公共脚本 | file | 2026-10-08 | 34057464b5d3da9732a4ad120074561471ef038715874eb39fc2402176003d06 | `node --check .\.private\sdk\feilin.js`；r0、r2、r9 与 CryptoJS 引用 |
| E-002 | .private/browser_capture.json；Camoufox 网络请求 25 | network / file | 2026-10-08 | n/a，含私有运行时材料 | 私有离线取样；AES 入参、返回密文、XHR 栈、Log3 的 Code="200" |
| E-003 | analysis/offline_validation.json | file | 2026-10-08 | 8b6ab0a69c58e23617a6714e67c444f3a0a7e62d43aa68ede266d97dfc323525 | `node .\analysis\verify_builder.cjs`；全部检查 true |
| E-004 | analysis/live_validation.json | file | 2026-10-08 | 6e1caee7d89ae89986a4f60f255ca126eb17d8bafb53ded3ce12595e5fc3225a | `python .\aliyun_log3.py` 可再发一条；原三条为 HTTP200 / Code200 / true |

各 Evidence 的 linked_workitem=n/a、supersedes=none。公共原始脚本与验证文件的 SHA-256 清单见 [evidence_hashes.json](analysis/evidence_hashes.json)。私有运行时材料不公开密钥或完整数据。

### Findings

| ID | title | severity / category | status / confidence | evidence_ids | location | impact / remediation |
|---|---|---|---|---|---|---|
| F-001 | HMAC 与表单序列化已还原 | n/a_re / reverse_algo | validated / high | E-001、E-003 | FeiLin r0/r2/r9；aliyun_codec.cjs | 可独立计算；纯算法分析，无漏洞修复项 |
| F-002 | 分层 Data 封装已还原 | n/a_re / reverse_algo | validated / high | E-002、E-003 | FeiLin CryptoJS 调用；aliyun_log3_node.cjs | 可在固定取样配置下构建新 Data；新设备需重新取样 |
| F-003 | 本地生成请求可被接口接受 | n/a_re / other | validated / high | E-003、E-004 | aliyun_log3.py send_calculated | 三条独立上报接受；业务验证范围见 scope.md |

repro_steps：从私有配置执行 verify_builder.cjs，确认固定输入匹配；随后执行 aliyun_log3.py，确认 HTTP 与业务响应。optional_attack=n/a，本任务没有攻击链或漏洞认定。

### P-001

- title：Camoufox 取样 → Node.js 计算 → curl_cffi 发送。
- path_type：callflow。
- start：用户提供的 Log3 cURL 与公开来源页面。
- goal：生成新的 Data、SignatureNonce、Signature 并确认接口接受。
- steps：观察公开 SDK（E-001，F-001）；记录 AES/XHR 运行时材料（E-002，F-002）；完成固定输入逐字节比对（E-003，F-001/F-002）；发送三条独立计算请求（E-004，F-003）。
- residual_risks：同一采样会话、固定 SDK 构建；未验证跨设备、长期会话与验证码业务结果。

### 过程记录

2026-10-08：来源页面观察并保存公开脚本；在实际页面世界定位 SDK；记录 Log2 和首次 getToken 触发的 Log3；实现纯 Node.js 加密与签名；完成原始样本和浏览器样本比对；发送三条独立计算请求；撤销临时插桩并保留原浏览器页面。状态记录见 [task.json](analysis/task.json)。

## 文件说明

| 文件 | 用途 |
|---|---|
| aliyun_log3.py | 默认调用本地 Node.js 后使用 curl_cffi 发送；支持原始样本重放 |
| aliyun_log3_node.cjs | 构建各层 Data、刷新上报时间、生成 nonce 与签名 |
| aliyun_codec.cjs | AES、HMAC、SDK 参数编码与表单序列化 |
| .private/runtime_profile.json | 真实取样的设备与事件配置 |
| .private/secrets.json | 签名与各 AES 角色的运行时密钥，受 ACL 保护 |
| analysis/extract_profile.cjs | 从私有浏览器捕获提取配置，不访问网络 |
| analysis/verify_crypto.cjs | 密码与序列化比对，可附带原始 cURL 文本路径 |
| analysis/verify_builder.cjs | 完整分层构建比对，不访问网络 |

可重建本地配置：`node .\analysis\extract_profile.cjs`，其输入是已保存的私有浏览器取样，不会刷新浏览器设备环境。SDK 版本或环境变化时应重新采集输入，再提取配置并重新验证。
