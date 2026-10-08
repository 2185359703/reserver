# 阿里云 Log3 与 InitCaptcha 参数案例

使用 Camoufox 观察阿里云 SDK，定位 `Data`、`SignatureNonce` 与 `Signature` 的生成链路，再通过本地 Node.js 计算参数，使用 Python `curl_cffi` 发送请求。运行时不需要浏览器或 npm 依赖。

验证级别为 **fixed-vector + snapshot-driven**：密码与表单算法已完整还原，设备、会话和事件仍来自配套采样配置。2026-10-08 的 15 组样本均通过完整请求体逐字节比对，浏览器与本地共 30 次 Log3 请求均返回 `HTTP 200 / Code=200 / ResultObject=true`。这证明设备上报被接受，不代表登录、注册或验证码业务结果已验证。

新增的 InitCaptcha 案例覆盖首次初始化与带 CertifyId / DeviceToken 的刷新请求。另有 15 组独立样本通过完整字节比对与本地两阶段流程验证；每次从新的初始化响应解包设备参数和会话标识，再生成令牌与签名。验证范围为初始化/刷新接受，不包含验证码答案或登录业务。

本轮用户明确要求 InitCaptcha 不脱敏、完整上传。因此 InitCaptcha 的原始取样、请求与响应、设备参数、密钥及取证 SDK 已保留原字段并提交在 `.private/initcaptcha/`。目录名称兼容现有代码，但这部分资料已公开。其他案例的私有目录继续按原规则忽略。

## 文件与运行环境

| 文件 | 用途 |
|---|---|
| `aliyun_log3.py` | 中文注释的 Python 请求入口，支持新参数计算、随机选组和原始请求回放 |
| `aliyun_log3_node.cjs` | 配套样本选择、Data 分层封装、上报时间与签名计算 |
| `aliyun_codec.cjs` | HMAC-SHA1、AES-CBC、SDK 参数编码和表单序列化 |
| `config/sdk_keys.json` | 用户要求公开保存的前端 SDK 共享 HMAC/AES 参数，适用于本次 040/041 构建 |
| `aliyun_initcaptcha.py` | InitCaptcha 随机选组、首次初始化与刷新入口 |
| `aliyun_initcaptcha_node.cjs` | DeviceData、DeviceToken、DeviceConfig 解包与 HMAC 计算 |
| `config/initcaptcha_sdk_keys.json` | InitCaptcha 的 SDK 共享参数；设备令牌 AES 密钥由本次服务端响应取得 |
| `analysis/initcaptcha/` | InitCaptcha 取样探针、提取/校验工具及脱敏验证资料 |
| `analysis/capture_probe.js` | 在 SDK 原始模块作用域插入的 Camoufox AES/XHR 取样探针 |
| `analysis/extract_profile.cjs` | 从私有取样提取一组设备、事件与密钥配置 |
| `analysis/verify_pool.cjs` | 对全部样本做完整字节比对，再验证随机请求唯一性 |
| `analysis/samples/20261008-3676378a/` | 脱敏样本清单、比较表、校验记录与证据哈希 |

需要 Python 3.10+、Node.js 20+。实际验证版本为 Node.js 24.14.0、curl_cffi 0.15.0。

```powershell
Set-Location '.\aliyun_log3'
python -m pip install -r .\requirements.txt
python .\aliyun_log3.py --help
```

## 准备自己的本地配置

公开仓库包含当前前端 SDK 的共享算法密钥，15 组样本中的各层密钥均一致，来源是公开 SDK 配置的本地解包。它们不包含账号 AccessKey、Cookie、设备令牌或账号会话凭证。原始请求体、设备事件和会话配置仍不公开；计算真实请求前，需要在 `.private` 中放入使用者自己的合法取样材料。

已有本机交付包含完整私有配置，默认请求会从 15 组配套样本中随机选择。

| 私有路径 | 内容 |
|---|---|
| `.private/browser_capture.json` | 浏览器取样，包含 secret、aes 和 requests；由取样探针获得 |
| `.private/browser_headers.json` | 取样请求的请求头 |
| `.private/runtime_profile.json` | 初始设备与事件配置，由 extract_profile.cjs 提取 |
| `.private/secrets.json` | 可选本地密钥覆盖；未提供时使用 `config/sdk_keys.json` 的共享 SDK 参数 |
| `.private/cohort_index.json` | 完整样本对索引，引用各组 profile.json 和 secrets.json |
| `.private/cohort/批次/样本编号/` | 各组 capture.json、profile.json、secrets.json 与私有请求/响应 |
| `.private/replay_request.json` | 可选原始回放文件：url、headers、body |
| `.private/sdk/` | 本机取证保存的原始 SDK 源码，未公开分发 |

单组配置提取：

```powershell
node .\analysis\extract_profile.cjs
node .\analysis\verify_builder.cjs
```

多组配置由 `analysis/process_sample.cjs` 提取、比对并验证；`analysis/finalize_cohort.cjs` 完成清单检查后生成随机样本索引。取样输出仍写入被 Git 忽略的 `.private` 目录。`config/sdk_keys.json` 是经过逐组比对后单独公开的 SDK 共享参数，SDK 构建变化后应重新确认其适用性。

## 生成和发送请求

```powershell
# 默认随机选择完整样本组，本地生成新参数并发送一次
python .\aliyun_log3.py

# 查看随机选中的样本编号与参数摘要，不发送请求
python .\aliyun_log3.py --dry-run

# 指定样本组，仍重新生成上报时间、nonce、Data 和签名
python .\aliyun_log3.py --sample sample-12

# 使用初始单组配置
python .\aliyun_log3.py --sample baseline

# 使用自定义的配套配置
python .\aliyun_log3.py --profile .\.private\runtime_profile.json --secrets .\config\sdk_keys.json

# 原始回放仅在本地文件可用时执行
python .\aliyun_log3.py --replay
```

`--profile` 与 `--secrets` 必须成对指定，避免混用不同设备状态与密钥。`--sample random` 使用 `crypto.randomInt` 随机选组，允许重复选择同一组；每次上报时间、nonce、Data 和签名重新计算。交互事件是采样快照，不会自动生成新的用户行为。

`--timeout` 设置请求超时，`--proxy` 设置代理，`--node` 指定 Node.js 可执行文件。默认使用 curl_cffi 的 Firefox 指纹；其 TLS 实现不保证与 Camoufox 完全一致。请求执行一次，不自动重试 POST。退出码 0 同时要求 HTTP 成功、`Code=200` 和 `ResultObject=true`。

## 多样性与证据

### InitCaptcha 初始化与刷新

配置自己的合法设备采样后执行：

```powershell
# 默认随机选择完整设备画像，初始化取得新状态，再发送刷新请求
python .\aliyun_initcaptcha.py

# 只查看本地初始化请求摘要
python .\aliyun_initcaptcha.py --dry-run

# 固定设备样本，仍取得新的服务端状态、令牌、时间和签名
python .\aliyun_initcaptcha.py --sample sample-03

# 使用初始画像与公开 SDK 共享参数；设备 AES 密钥不需要预先写死
python .\aliyun_initcaptcha.py --profile .\.private\initcaptcha\profile.json --secrets .\config\initcaptcha_sdk_keys.json
```

设备画像、原始 DeviceToken / DeviceConfig 和响应保存在 `.private/initcaptcha/`，随机池索引为 `.private/initcaptcha/index.json`。本轮完整资料已按用户明确要求公开提交，新克隆可直接使用随附样本池。每次执行仍取得新的服务端状态；随附环境画像为本次取样快照。

新增样本覆盖 3 种语言各 5 组、7 种屏幕尺寸和 15 种设备环境。15/15 的初始化、刷新请求及令牌逐字节匹配；15/15 本地两阶段流程成功；45 条离线随机计算的令牌、nonce 和请求体无重复。1 组浏览器刷新由 SDK 自动切到备用域名，本地验证使用用户指定主域名，也成功。

- [InitCaptcha 完整报告](2026-10-08_InitCaptcha-参数与多样性样本-report.md)
- [InitCaptcha 样本比较表](analysis/initcaptcha/samples/20261008-02318f4c/samples.csv)
- [InitCaptcha 样本清单](analysis/initcaptcha/samples/20261008-02318f4c/manifest.json)
- [InitCaptcha 离线随机校验](analysis/initcaptcha/samples/20261008-02318f4c/random_validation.json)

### Log3 设备上报

- 15 个不同设备环境、15 个独立会话、15 份不同 deviceData 和事件载荷。
- zh-CN、zh-TW、en-US 各 5 组；7 种屏幕尺寸；13 种动态 SG 脚本。
- FeiLin 040 为 12 组，041 为 3 组；其中 sample-12、sample-15 明确标记为公开 041 源码受控替换，sample-13 自然加载 041。
- 键盘、点击和鼠标移动来自浏览器执行的随机非提交交互；未操作验证码完成动作或提交账号表单。
- 15/15 完整请求体匹配，30/30 成对请求成功；45 条离线随机计算的 Data、nonce 与请求体均无重复。

完整报告与脱敏证据：

- [参数算法报告](2026-10-08_签名逆向-aliyun-log3-report.md)
- [多样性采样报告](2026-10-08_多样性样本-aliyun-log3-report.md)
- [逐组比较表](analysis/samples/20261008-3676378a/samples.csv)
- [样本清单](analysis/samples/20261008-3676378a/manifest.json)
- [随机池校验](analysis/samples/20261008-3676378a/pool_validation.json)

Log3 的公开证据中的 nonce 和服务端请求 ID 保持此前的哈希处理。InitCaptcha 原始资料在 `.private/initcaptcha/` 完整公开，未对原始取样字段做替换。

## 当前边界

Node.js 已实现参数封装与签名的本地计算，尚未自动建立新设备环境或重新采集指纹。SDK 构建、设备或站点场景变化后，需要重新取样并做完整字节比对。验证范围仅为本案例的 Log3 上报接受，遵循仓库的授权研究与敏感数据处理原则。
