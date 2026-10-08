# 本次分析范围

- auth：当前用户提供 cURL，要求使用 Camoufox 分析生成链路，允许本地 Node.js 计算并构建稳定请求。
- in_scope：`https://console.beeize.com/` 及其公开加载的阿里云认证 JavaScript；用户所给 `cloudauth-device-dualstack.cn-shanghai.aliyuncs.com` 的 `Log3` 请求。
- network_profile：Camoufox 正常页面初始化取样；随后使用 `curl_cffi` 顺序发送三次本地计算的 `Log3`，不做并发或自动重试。
- outcome：还原参数编码、AES 封装和 HMAC 签名，完成 Python/Node.js 调用及请求接受验证。
- validation_scope：同一已采样设备会话的日志上报；登录、注册、短信发送和验证码业务结果不在本次验证范围。

## 后续多样性取样

- auth：用户要求多取 10–20 组具有多样性的样本，并让每次请求随机化。
- 本轮选择 15 组独立 Camoufox 会话，改变语言、实际浏览器环境、输入/鼠标事件与停留时间；取样交互不提交表单。
- 每组保存原始浏览器请求及一条本地重新计算的请求，共 30 次 Log3 接受验证；随后验证一次默认随机请求。
- 041 的 3 组中，1 组为自然加载，2 组为明确记录的公开 SDK 源码受控替换。API 响应没有模拟。
