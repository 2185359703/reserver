# Verification evidence

这些日志由真实命令生成，Cookie 值已脱敏，可直接用于项目说明或截图。

| 日志 | 证明内容 |
|---|---|
| `cookie_verification.log` | 412 挑战、S/T 长度、148 字节分支、非空 200 回放 |
| `data_verification.log` | 5,314 行、唯一 ID、索引覆盖、年份一致性、SHA-256 |
| `resume_check.log` | 已有全量 CSV 时正确识别待采集 0 篇 |

复现命令：

```powershell
node .\js\cqvip_cookie.bundle.js --verify --trace
python .\verify_all.py
python .\collect_all.py --limit 1
```

请勿把 `--show-cookie` 的输出写入日志或提交到仓库。
