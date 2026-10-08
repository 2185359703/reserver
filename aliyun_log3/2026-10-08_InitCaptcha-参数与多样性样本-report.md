# InitCaptcha 参数还原与多样性样本

分析日期：2026-10-08。批次：`20261008-02318f4c`。范围见 [scope.md](analysis/initcaptcha/scope.md)。报告类型为普通 JS 参数分析，flavor=null。

已实现用户提供的 InitCaptcha 刷新请求形态：本地 Node.js 生成设备令牌、时间与签名，Python curl_cffi 完成初始化和刷新。15 组独立 Camoufox 会话全部通过初始化请求、刷新请求和 DeviceToken 的完整字节比对；每组的本地初始化/刷新流程均返回 HTTP 200、Code="Success"、Success=true。

关键动态状态来自本次服务端响应：先初始化获取新的 CertifyId 和 DeviceConfig，解包新的设备 AES 参数、会话标识和关联字段，再计算刷新请求。设备环境画像仍来自真实采样，不会在本地假装重新采集浏览器指纹。

## 使用

本机已有 15 组配套私有配置，可直接运行：

```powershell
Set-Location '.\aliyun_log3'

# 随机选组，仅查看本地初始化摘要
python .\aliyun_initcaptcha.py --dry-run

# 随机选组，执行初始化并构建、发送刷新请求
python .\aliyun_initcaptcha.py

# 指定某组环境画像；仍取得新的服务端状态并重算令牌与签名
python .\aliyun_initcaptcha.py --sample sample-03

# 初始单组配置
python .\aliyun_initcaptcha.py --sample baseline

# Node.js 独立生成初始化请求摘要
node .\aliyun_initcaptcha_node.cjs --phase bootstrap --sample random --summary

# 离线验证随机请求，不发送网络请求
node .\analysis\initcaptcha\verify_pool.cjs
```

依赖为 Python 3.10+、Node.js 20+、curl_cffi。Node.js 只使用内置库，无需 npm install。Python 支持 --proxy、--timeout、--node、--impersonate，以及成对指定 --profile/--secrets。

Python 模块入口 `initialize_and_refresh()` 返回本次初始响应、刷新请求和最终响应。CLI 默认显示状态和摘要；完整原始报文、CertifyId、DeviceToken 与 DeviceConfig 已按用户要求随案例资料公开提交。请求不自动重试。

## 请求来源与阶段

来源页面为 `https://art.ccmgip.com/login?reason=invalidtoken`，真实页面标题为“中国文化传媒新文创藏品平台”。公开应用源码以 SceneId="12u7sbfh"、prefix="10jtz0"、mode="popup" 初始化 AliyunCaptcha。

页面的首个请求与用户贴出的请求属于不同阶段：

| 阶段 | 特有字段 | 状态来源 |
|---|---|---|
| 首次 InitCaptcha | Mode、DeviceData | SDK 场景与设备初始化参数 |
| 验证码实例 refresh | CertifyId、DeviceToken | CertifyId 来自前次初始化响应，令牌由设备 SDK 生成 |

用户请求的字段顺序已复现：AaduaneId、SignatureMethod、SignatureVersion、Format、Timestamp、Version、Action、SceneId、CertifyId、Language、DeviceToken、SignatureNonce、Signature。

本地统一使用用户指定主域名 `https://10jtz0.captcha-open.aliyuncs.com/`。浏览器样本中 14 组刷新走主域名，1 组由 SDK 自动切到 `captcha-open-b`；该组在清单中保留实际端点记录。本地使用同组参数发送主域名，也验证成功。

## 签名与参数

### Signature

签名机制与此前 Log3 的编码方式相同：排除 Signature，参数名排序；使用 SDK 的 encodeURIComponent 规则拼 canonicalQuery；stringToSign 为 `POST&encode('/')&encode(canonicalQuery)`；HMAC-SHA1 的密钥为 SDK signingSecret 后接 `&`，结果编码为 Base64。

Captcha 的签名材料与设备 Log3 的材料不同，不能直接混用此前的 Log3 密钥。当前 AaduaneId 为 `111jdk439dJJIjd023823201`，Version 为 `2023-03-05`。Timestamp 为 UTC、精确到秒，格式为 `YYYY-MM-DDTHH:MM:SSZ`。

### DeviceData

首个请求的 DeviceData 使用两层已观察到的 AES-CBC/PKCS#7 封装：场景标记先加密，再与 appKey、deviceType、appVersion、environment、extra 以 `#` 连接并再次加密，输出 Base64。固定输入下与浏览器逐字节相同。

### DeviceToken

设备令牌外层为以下 UTF-8 字符串的 Base64：

```text
WEB#sessionId#AES_ciphertext_base64#cost#MD5_checksum
```

其中 AES-CBC/PKCS#7 的明文是以 `#` 分隔的 142 个设备字段。MD5 输入为前四个外层组件，追加 SDK 校验 salt，同样以 `#` 连接。取样通过 CryptoJS AES/MD5 调用记录确认了全部原始入参、密文和摘要。

连续 getToken 观察确定了两个会变化的位置：设备字段 74 是本次调用时间；字段 43 的日志条目 93/94 是相对设备初始化时刻的记录。运行时更新这些时间相关字段；其余浏览器画像沿用所选配套样本。

### 新的 DeviceConfig

初始化响应的 DeviceConfig 先经 AES 解包，当前布局为 10 个 `#` 分隔组件。观察到的映射如下：

| 响应组件位置 | 用途 |
|---|---|
| 0 | Base64 编码的设备令牌 AES 参数 |
| 2 | 新的设备会话标识，写入令牌外层 sessionId |
| 7 | 更新设备字段 87 |
| 8 | 更新设备字段 42 |

Node.js 在每次新的初始化后使用这份响应重新绑定设备参数，Python 固定首次随机选中的 sampleId，确保后续刷新仍使用同组环境画像。CertifyId 始终来自服务端，不随机编造。

## 多样性与验证

| 项目 | 结果 |
|---|---|
| 独立浏览器样本组 | 15 |
| 语言 | en-US、zh-CN、zh-TW，各 5 组 |
| 屏幕尺寸 | 7 种：1600×900、1680×1050、1920×1080、2560×1440、3072×1728、3440×1440、5120×1440 |
| 不同设备环境 / 捕获会话 / 捕获令牌 | 分别 15 / 15 / 15 |
| 初始化与刷新完整字节比对 | 15/15 |
| 浏览器初始化 / 刷新成功 | 15/15；15/15 |
| 本地初始化 → 刷新成功 | 15/15 |
| 新 DeviceConfig / CertifyId 状态配对 | 15/15 |
| 45 条离线随机计算 | 令牌、nonce、请求体均无重复；不发送网络请求 |

采样使用真实 Camoufox 会话及不同的语言、实际设备环境、输入/点击节奏和页面停留时间。交互仅发生在非必填邀请码输入框，使用明确的 sample_ 测试字符串；没有提交手机号、短信、登录或验证码答案。

站点有一次页面加载超时，需要重新取样；最后一组设备 SDK 就绪较慢，确认原页面与 SDK 实际就绪后继续完成，未用错误页或旧页面替代样本。

原始用户 cURL 的 HMAC 与 DeviceToken MD5 校验均匹配。其历史令牌的 AES 设备参数没有包含在 cURL 中，当前参数不能解密该旧密文，因此没有宣称完整还原那份历史令牌明文。新取样的 15 组已经记录对应参数，并全部完成精确比对。

## 文件与证据

- [Python 请求入口](aliyun_initcaptcha.py)
- [Node.js 参数计算](aliyun_initcaptcha_node.cjs)
- [样本清单](analysis/initcaptcha/samples/20261008-02318f4c/manifest.json)
- [逐组比较表](analysis/initcaptcha/samples/20261008-02318f4c/samples.csv)
- [随机计算校验](analysis/initcaptcha/samples/20261008-02318f4c/random_validation.json)
- [历史原包与初始样本校验](analysis/initcaptcha/baseline_validation.json)
- [证据哈希](analysis/initcaptcha/samples/20261008-02318f4c/evidence_hashes.json)

完整原始材料位于 `.private/initcaptcha`，包括原始报文、DeviceToken、DeviceConfig、逐组环境、密钥和取证 SDK。默认样本索引为 `.private/initcaptcha/index.json`。此目录虽然名称保留 .private，但本轮按用户明确要求完整提交到公开仓库；它不再代表未公开数据。

## Evidence → Finding → Path

| Evidence | source_type / source_ref | observed_at | content_hash | 复现与摘录 |
|---|---|---|---|---|
| E-I01 | file；私有 AliyunCaptcha.js 原始源码 | 2026-10-08 | c39b3b4093e4226b05eb0242c93f4219d1416caa34cf01dfd13b78913bac3a9f | 原始 SDK Cr/Qr、Er、refresh 生成路径；源码在 .private/initcaptcha/sdk |
| E-I02 | runtime / file；各组 capture.json | 2026-10-08 | n/a，私有材料 | AES、MD5、XHR 入参/返回、DeviceConfig 解包与页面环境；离线使用 finalize_samples.cjs 检查 |
| E-I03 | file；样本 manifest.json 与 baseline_validation.json | 2026-10-08 | 见 evidence_hashes.json | 15/15 完整比对、本地流 Success、新的状态正确配对 |
| E-I04 | file；random_validation.json | 2026-10-08 | 见 evidence_hashes.json | `node .\analysis\initcaptcha\verify_pool.cjs`；45 条令牌/nonce/报文唯一 |

linked_workitem=n/a、supersedes=none。

| Finding | severity / category | evidence_ids | status / confidence | location / impact |
|---|---|---|---|---|
| F-I01 | n/a_re / reverse_algo | E-I01、E-I02、E-I03 | validated / high | InitCaptcha HMAC、DeviceData、DeviceToken 的本地算法实现 |
| F-I02 | n/a_re / reverse_algo | E-I02、E-I03 | validated / high | useServerConfig；刷新使用新的服务端设备状态与 CertifyId |
| F-I03 | n/a_re / other | E-I03、E-I04 | validated / high | 随机样本池，15 组本地初始化/刷新成功，45 条离线计算唯一 |

repro_steps：运行 --dry-run 查看本地选组；运行 aliyun_initcaptcha.py 取得服务端新状态并刷新；运行 verify_pool.cjs 查看离线唯一性。remediation=n/a，纯参数分析；optional_attack=n/a。

P-I01：path_type=callflow，start=用户 InitCaptcha cURL，goal=随机化本地初始化/刷新请求。steps 为公开页面观察（E-I01）→ AES/MD5/XHR 取样（E-I02）→ 固定输入与状态映射比对（E-I03，F-I01/F-I02）→ 15 组真实本地流及随机计算验证（E-I03/E-I04，F-I03）。

过程记录：2026-10-08 页面首次加载较慢后确认登录入口；区分首次与刷新请求；还原签名、令牌 AES/MD5 与 DeviceConfig 映射；完成 15 组多样性样本并启用随机池；默认随机入口实发成功；撤销临时 SDK 响应插桩与网络捕获，保留原浏览器页面。状态见 [task.json](analysis/initcaptcha/task.json)。

residual_risks：当前 SDK 与场景、Windows 平台、同一网络出口；浏览器环境画像仍为采样数据。成功范围是 InitCaptcha 初始化/刷新，不包含验证码答案、业务验证或登录结果。SDK 字段布局变化时需重新取样与比对。
