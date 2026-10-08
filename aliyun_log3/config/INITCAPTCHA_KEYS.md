# InitCaptcha SDK 共享参数

`initcaptcha_sdk_keys.json` 只包含当前 SDK 的共享 HMAC、MD5 校验 salt，以及 DeviceData/DeviceConfig 的通用 AES 参数。15 组样本中的这些参数均一致，来源与验证过程见 InitCaptcha 报告。

设备令牌的 AES 密钥会随服务端初始化状态变化，未作为固定密钥提交。文件中 token 角色只保存 IV；实际运行从新的 DeviceConfig 响应取得 AES 密钥和会话标识。它不包含 CertifyId、DeviceToken、设备画像、Cookie、账号凭证或原始响应。

完整历史取样的配套设备参数已按用户本轮明确要求公开保存在 `.private/initcaptcha/`；这里的共享配置仍只保留通用运行参数，不将历史设备密钥当作固定运行密钥。

该配置用于运行时新参数计算。对历史捕获令牌做固定向量比对时，仍需本地取样中的配套设备 AES 参数。SDK 布局或场景变化后，应重新取样验证。
