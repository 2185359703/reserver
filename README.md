# reserver

Web 客户端协议逆向案例集。仓库关注浏览器中的动态参数、挑战 Cookie、混淆 VM 和环境指纹如何还原为可验证的本地协议链路。

项目不把“能生成一个长得像 token 的字符串”视为完成。每个案例都尽量说明真实请求、动态状态来源、浏览器外运行形态、验证证据，以及仍未纯算法化的边界。

## 案例总览

| 目录 | 研究对象 | 主要难点 | 交付形态 | 当前证据 |
|---|---|---|---|---|
| [`cqvip/`](cqvip/) | 维普期刊瑞数 412 挑战与公开元数据采集 | `S/T` Cookie 配对、148 字节环境块、双层加密、空 200 诱饵 | Python HTTP + 单文件 Node Cookie helper；运行时不需要浏览器或 npm 依赖 | 实时回放通过；500 个刊期、5,314 篇详情完整校验 |
| [`tds/`](tds/) | 腾讯行为式验证码 `tdc.js` | Chaos VM、XTEA 变体、动态密钥/偏移、图像缺口定位 | 参数链主要为纯 Python；新脚本版本首次提参需要 Node | 仓库内包含固定向量、样本评估和历史成功截图 |
| [`boos/`](boos/) | BOSS直聘 `__zp_stoken__` | 自研 VM、种子轮换、Canvas/WebGL 等宿主环境表面 | Python 协议层 + `iv8` 环境仿真 | 已记录活体验收；明确标注为 `snapshot-driven`，尚非纯 Python |

## 仓库原则

- **先证据，后结论**：先确认真实请求、响应和状态变化，再还原算法。
- **浏览器只用于取证**：最终采集链路应在浏览器外运行；确实依赖宿主环境时如实标注。
- **区分算法正确与服务端接受**：固定输入逐字节一致、实时回放成功、业务正文完整是不同验证层级。
- **不隐藏残余边界**：环境快照、索引日期、版本轮换和风控污染都要写清楚。
- **敏感状态不入库**：Cookie、账号会话、原始 token 和未脱敏报文不提交。

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
- 不提交登录 Cookie、账号令牌、未脱敏请求或可识别个人信息。
- 默认使用低并发和请求间隔；出现拒绝或风控升级时停止重复探测。
- 不用于未授权访问、付费内容获取、账号接管或生产系统破坏。

## License

仓库根目录代码使用 [MIT License](LICENSE)。`cqvip/` 中包含来自 `rs-reverse` 的 BSD-3-Clause 通用 VM 解码代码，具体署名和依赖许可证见：

- [`cqvip/LICENSE`](cqvip/LICENSE)
- [`cqvip/THIRD_PARTY_NOTICES.md`](cqvip/THIRD_PARTY_NOTICES.md)
- [`cqvip/THIRD_PARTY_LICENSES.txt`](cqvip/THIRD_PARTY_LICENSES.txt)
