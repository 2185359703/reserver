# BOSS直聘 `__zp_stoken__` 逆向案例

目标:还原 BOSS直聘(zhipin.com)的会话风控 token `__zp_stoken__`,交付**浏览器外可复现**的采集链路。

## 结论(先看)

- 该 token 由服务端下发的种子派生,生成逻辑位于一个**自研字节码 VM** 混淆的安全 JS 内,
  逐调用依赖**宿主环境表面**(canvas / WebGL / navigator / screen / window / history …)。
- 因此本案例的可行交付形态是 **环境仿真**:用嵌入式 JS 引擎(`iv8`)提供保真宿主,
  由 Python 负责协议、会话与采集;纯 Python 端口**尚未落地**。
- 生成真值标签应如实写成 `snapshot-driven`:种子是每次请求现取的,但宿主环境画像来自真实浏览器采集。

## 目录

| 目录 | 内容 | 状态 |
| --- | --- | --- |
| [`pure algorithm/`](pure_algorithm/) | 已还原的纯算法部分(token 外层容器、时间戳换算、种子消费契约) | 已还原,可独立复现 |
| [`environment emulation/`](environment_emulation/) | 可运行交付:Python 采集 + iv8 宿主签名(3 个脚本 + 安全 JS + 脱敏报文样例) | 活体验收通过 |

## 协议要点(两句话版)

- 受保护接口(GET,列表/详情)在响应 `set-cookie` 里下发种子三元组:`__zp_sseed__`(随机种子)、
  `__zp_sname__`(安全 JS 版本名)、`__zp_sts__`(服务端毫秒时间戳);客户端须立刻用它们重签 token 并回写会话,
  否则下一次请求会被判为陈旧。种子只发给当前 token 已被接受的客户端 —— 完全过期时必须回到真机引导。
- 列表: `/wapi/zpgeek/pc/recommend/job/list.json`;详情: `/wapi/zpgeek/job/detail.json`(参数 `securityId` + `lid`)。

## 免责

仅用于协议学习与安全研究;`cookies.json` 等会话态**不入库**。
