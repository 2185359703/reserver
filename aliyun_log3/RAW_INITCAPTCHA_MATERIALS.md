# InitCaptcha 完整原始资料

当前用户明确要求“不要脱敏，全部上传”。本次提交因此包含当前 InitCaptcha 案例的全部原始取样和取证资料，未替换原始 token、CertifyId、DeviceConfig、请求或响应字段。

| 路径 | 内容 |
|---|---|
| `.private/initcaptcha/original_form.json` | 用户提供的原始 InitCaptcha 表单 |
| `.private/initcaptcha/baseline_network.json` | 首次页面观察的原始网络请求和响应 |
| `.private/initcaptcha/baseline_capture.json` | 初始 AES、MD5、XHR 与 SDK 运行时记录 |
| `.private/initcaptcha/profile.json` / `secrets.json` | 初始配套设备画像及密码参数 |
| `.private/initcaptcha/index.json` | 默认随机样本池索引 |
| `.private/initcaptcha/cohort/20261008-02318f4c/sample-01` 至 `sample-15` | 各组 capture.json、profile.json、secrets.json、local_flow.json |
| `.private/initcaptcha/sdk/` | 本轮取证保存的 AliyunCaptcha.js 与网站应用源码 |
| `analysis/initcaptcha/` | 未额外替换字段的样本清单、比较表、取样与验证工具 |

`.private` 名称为兼容现有入口保留；此 InitCaptcha 子目录已公开提交，不能将目录名称理解为保密标记。此次只包括当前 InitCaptcha 案例资料，不扩展到其他案例的本地私有状态。

首次运行：

```powershell
Set-Location '.\aliyun_log3'
python -m pip install -r .\requirements.txt
python .\aliyun_initcaptcha.py --dry-run
python .\aliyun_initcaptcha.py
```

设备环境仍是随附采样快照，运行时重新取得服务端初始化状态并计算新参数。验证范围为初始化与刷新成功，不包含验证码答案或登录业务结果。
