# reserver

Web 客户端协议逆向案例集。仓库关注浏览器中的动态参数、挑战 Cookie、混淆 VM 和环境指纹如何还原为可验证的本地协议链路。

项目不把“能生成一个长得像 token 的字符串”视为完成。每个案例都尽量说明真实请求、动态状态来源、浏览器外运行形态、验证证据，以及仍未纯算法化的边界。

## 案例总览

| 目录 | 研究对象 | 主要难点 | 交付形态 | 当前证据 |
|---|---|---|---|---|
| [`cqvip/`](cqvip/) | 维普期刊瑞数 412 挑战与公开元数据采集 | `S/T` Cookie 配对、148 字节环境块、双层加密、空 200 诱饵 | Python HTTP + 单文件 Node Cookie helper；运行时不需要浏览器或 npm 依赖 | 实时回放通过；500 个刊期、5,314 篇详情完整校验 |
| [`tds/`](tds/) | 腾讯行为式验证码 `tdc.js` | Chaos VM、XTEA 变体、动态密钥/偏移、图像缺口定位 | 参数链主要为纯 Python；新脚本版本首次提参需要 Node | 仓库内包含固定向量、样本评估和历史成功截图 |
| [`boos/`](boos/) | BOSS直聘 `__zp_stoken__` | 自研 VM、种子轮换、Canvas/WebGL 等宿主环境表面 | Python 协议层 + `iv8` 环境仿真 | 已记录活体验收；明确标注为 `snapshot-driven`，尚非纯 Python |
| [`aliyun_log3/`](aliyun_log3/) | 阿里云 `Log3` 设备上报与 `InitCaptcha` 初始化/刷新 | AES/Base64、HMAC、令牌 MD5、新 DeviceConfig / CertifyId 配对、SDK 切换 | Python `curl_cffi` + 无 npm 依赖的 Node.js 计算；配套样本随机选择 | 两个接口各 15 组完整比对与真实流程成功；设备画像为 `snapshot-driven` |
| [`geetest/`](geetest/) | 极验 v4 `icon` 点选与 `/verify` | POW、AES-CBC、RSA、`w` 拼装、题目状态配对与评测计分 | 协议计算为 Python；图像定位依赖外部视觉模型 | `pt=1` 真实成功记录与对应截图；`pt=0` 尚未线上确认 |
| [`radissonhotels/`](radissonhotels/) | 丽笙酒店公开数据接口 | 参数构造、TLS/HTTP2、跨地区分批采集 | Python + 自动编译的 Go 后端 | 8 接口三轮通过；80 家酒店批量实采 |

## 仓库原则

- **先证据，后结论**：先确认真实请求、响应和状态变化，再还原算法。
- **浏览器只用于取证**：最终采集链路应在浏览器外运行；确实依赖宿主环境时如实标注。
- **区分算法正确与服务端接受**：固定输入逐字节一致、实时回放成功、业务正文完整是不同验证层级。
- **不隐藏残余边界**：环境快照、索引日期、版本轮换和风控污染都要写清楚。
- **敏感状态不入库**：Cookie、账号会话、原始 token 和未脱敏报文不提交。

InitCaptcha 当前案例按用户明确要求完整公开其授权取样资料，包含原始请求、设备令牌、响应和配套密钥，保存在 `aliyun_log3/.private/initcaptcha/`；这是本轮指定数据集的公开交付，其他案例仍保持原数据处理方式。

## 快速开始

### 维普瑞数 Cookie 与公开期刊数据

```powershell
cd cqvip
python -m pip install -r requirements.txt
node .\js\cqvip_cookie.bundle.js --verify --trace
python .\verify_all.py
python .\collect_all.py
```

验证日志位于 [`cqvip/evidence/`](cqvip/evidence/)，Cookie 值已脱敏。完整说明见 [`cqvip/README.md`](cqvip/README.md)。

### 腾讯 TDC 案例

```powershell
cd tds
python -m pip install -r requirements.txt
python .\verify_captcha.py --times 1 --gap 10
```

该命令会访问线上验证服务，运行前请阅读 [`tds/README.md`](tds/README.md) 中的频率限制和授权边界。

### BOSS token 环境仿真

```powershell
cd boos\environment_emulation
python -m pip install -r requirements.txt
```

此案例需要使用者自行提供合法会话，仓库只保留空值示例。详情见 [`boos/README.md`](boos/README.md)。

### 阿里云 Log3 与 InitCaptcha 随机样本

```powershell
cd aliyun_log3
python -m pip install -r requirements.txt
python .\aliyun_log3.py --help
```

Log3 配置自己的合法取样后，可使用 `python .\aliyun_log3.py --dry-run` 查看随机选组摘要。InitCaptcha 本轮 15 组原始样本、配置和取证资料已按用户要求完整上传，可直接使用随附样本池。详见 [`aliyun_log3/README.md`](aliyun_log3/README.md)。

新增的 `python .\aliyun_initcaptcha.py` 会随机选择设备画像，重新初始化取得服务端 CertifyId / DeviceConfig，再计算并发送刷新请求；它不处理验证码答案或登录业务。

### 极验 v4 协议与真实验证

```powershell
cd geetest
python -m pip install -r requirements.txt
python .\py\geetest_verify.py --selftest
```

按 [`geetest/README.md`](geetest/README.md) 配置 `GEETEST_CAPTCHA_ID` 与视觉模型环境变量后，可运行 `python .\py\geetest_verify.py --full --attempts 1`。使用 `--benchmark 10` 统计全量尝试通过率，未提交和错误组也计入分母。案例附有真实 `/verify` 成功记录和本地运行记录查看器截图，截图不冒充官方页面。

### 丽笙酒店

需要 Python 3.10+ 和 Go 1.26+，首次运行自动编译 HTTP 后端。

```powershell
cd radissonhotels
python .\main.py
python .\collect_batch.py
```

接口和批量参数分别在各目录 config.json 与 batch_config.json 中配置，结果写入本地 JSONL/CSV，详见[案例说明](radissonhotels/README.md)。

## 目录结构

```text
reserver/
├── cqvip/                       # 瑞数 412 Cookie 与期刊公开元数据
│   ├── data/                    # 已验证索引和 CSV 结果
│   ├── evidence/                # 脱敏运行证据
│   ├── js/                      # 可读算法源码和独立运行 bundle
│   ├── collect_all.py
│   └── README.md
├── tds/                         # 腾讯 TDC/Chaos VM 案例
├── boos/                        # BOSS token 环境仿真案例
├── aliyun_log3/                 # 阿里云 Log3 / InitCaptcha 参数与随机样本
├── geetest/                     # 极验 v4 POW/AES/RSA 与真实 /verify 证据
├── radissonhotels/              # 丽笙酒店接口与批量采集
├── LICENSE
└── README.md
```

## 验证级别

| 标签 | 含义 |
|---|---|
| `fixed-vector` | 固定输入下，本地结果与已捕获真值逐字节一致 |
| `live-verified` | 使用新会话重新生成动态状态，服务端实时接受 |
| `snapshot-driven` | 动态种子可刷新，但部分环境画像来自已采集快照 |
| `offline-only` | 只完成本地还原，尚未证明当前线上接受 |

阅读案例时应以其 README 和 evidence 为准，不要把一种验证级别替换成另一种。

## 安全与数据处理

- 仅用于协议学习、公开数据采集、授权测试和防护研究。
- 常规案例不提交登录 Cookie、账号令牌、未脱敏请求或可识别个人信息。InitCaptcha 本轮指定原始数据集的公开交付按用户明确要求记录在其 README 中。
- 默认使用低并发和请求间隔；出现拒绝或风控升级时停止重复探测。
- 不用于未授权访问、付费内容获取、账号接管或生产系统破坏。

## License

仓库根目录代码使用 [MIT License](LICENSE)。`cqvip/` 中包含来自 `rs-reverse` 的 BSD-3-Clause 通用 VM 解码代码，具体署名和依赖许可证见：

- [`cqvip/LICENSE`](cqvip/LICENSE)
- [`cqvip/THIRD_PARTY_NOTICES.md`](cqvip/THIRD_PARTY_NOTICES.md)
- [`cqvip/THIRD_PARTY_LICENSES.txt`](cqvip/THIRD_PARTY_LICENSES.txt)
