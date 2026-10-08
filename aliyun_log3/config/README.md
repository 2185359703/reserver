# SDK 共享算法参数

`sdk_keys.json` 保存本次分析取得的共享 HMAC-SHA1 与 AES-CBC 参数，按用户的明确要求提交。

来源为公开 FeiLin 1.5.1 的 040 与 041 构建。HMAC 材料由 SDK 的 `ACCESS_SEC` / `ACCESS_KEY.SECRET` 配置解包取得；各层 AES 参数由同一 SDK 的实际加密调用记录确认。15 个独立会话中的 `signingSecret` 和全部 AES 角色参数逐组相同。

该文件只包含 `signingSecret` 以及 outer、flag、events、appName、clientVersion、timestamp 六个角色的 keyHex / ivHex，不包含 Cookie、设备令牌、AccessKeyId/AccessKeySecret、账号密码、用户会话或原始事件数据。

| 构建 | 原始公开脚本 SHA-256 |
|---|---|
| FeiLin 040 | 34057464b5d3da9732a4ad120074561471ef038715874eb39fc2402176003d06 |
| FeiLin 041 | 2a3946443830dedf38954c509ea97a8de5b4bb27464aff39d5ccaf0c982b9449 |

当前文件经 15 组完整请求体比对与成对服务端请求接受验证。SDK 构建变化时应重新取样和比对，不能把它当作所有版本的永久参数。
