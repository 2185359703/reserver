# InitCaptcha 分析范围

- 用户提供 `https://10jtz0.captcha-open.aliyuncs.com/` 的 InitCaptcha 刷新请求，要求 Camoufox 分析、本地 Node.js 计算、10–20 组多样性样本及逐次随机化。
- 本轮选择 15 组独立 Windows Camoufox 会话，3 种语言各 5 组；页面为 `https://art.ccmgip.com/login?reason=invalidtoken`。
- 操作仅为初始化、验证码实例刷新以及邀请码输入框的非提交取样交互；未提交手机号、短信、登录或验证码答案。
- 浏览器取样观察到 1 组 SDK 备用域名刷新；本地运行统一发送到用户指定的主域名。
- 验证目标为 InitCaptcha 初始化/刷新响应 Success、完整请求字节一致，以及新的 DeviceConfig / CertifyId 状态配对。
