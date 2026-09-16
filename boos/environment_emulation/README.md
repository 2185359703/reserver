# environment emulation — 可运行的浏览器外链路

Python 负责 HTTP 与业务,嵌入式 JS 引擎(`iv8`)负责宿主环境签名,最终路径**不含浏览器**。

## 依赖

```bash
pip install curl_cffi iv8
```

- `curl_cffi`:HTTP(浏览器 TLS/HTTP2 指纹,`impersonate="firefox"`)
- `iv8`:嵌入式 JS 引擎,提供与被采集页面同构的宿主表面(navigator/screen/window/location/webgl)

## 使用

1. 从已登录的浏览器导出 cookie,存成本目录 `cookies.json`(**不要提交**,见仓库 `.gitignore`;
   结构参考 `cookies.example.json`)。token 过期后需要重新导出或走真机引导刷新。
2. 运行:

```bash
python zp_stoken.py                  # 取种子 -> 生成 token -> 回写 cookies.json,stdout 仅一行原始 token
python zp_list.py 1 101090500        # 列表(页数 城市):每页输出 薪资/岗位名/学历/公司 + 每条岗位详情原始 JSON
python zp_raw.py                     # 落盘两个接口的原始请求/响应报文 -> raw_list.txt / raw_detail.txt
```

两个受保护接口都是 **GET**(见 `samples/*.raw.txt` 的请求行)。

## 文件

| 文件 | 说明 |
| --- | --- |
| `zp_stoken.py` | 最小链路:请求一次列表拿种子 → `new ABC().z(seed, ts)` → 写回 token(自动静音 iv8 原生横幅) |
| `zp_list.py` | 分页采集 + 岗位详情;响应若下发种子立即重签并回写 |
| `zp_raw.py` | 只做一件事:把两个接口的原始请求/响应报文落盘 |
| `security-js.js` | 页面实际加载的安全 JS(版本名见响应 `set-cookie: __zp_sname__`;轮换后需替换) |
| `cookies.example.json` | 会话文件结构示例(16 个字段名,值全部清空) |
| `samples/list.raw.txt`、`samples/detail.raw.txt` | 脱敏后的原始报文样例(cookie 值已用 `<REDACTED>` 替换,协议结构完整保留) |

## 关键机制

- **种子消费**:响应头 `set-cookie` 下发 `__zp_sseed__` / `__zp_sname__` / `__zp_sts__`,
  脚本收到即重签并回写 `cookies.json`(跳过这一步 → 下一次请求被判陈旧,链路断掉)。
- **时间戳换算**:`ts = parseInt(__zp_sts__) + (480 + 本地时区偏移分钟) * 60000`(UTC+8 即偏移 0)。
- **宿主保真**:签名产物是**宿主环境签名**:同一份安全 JS 在薄 shim 与保真宿主下产出的 token 前缀不同、
  长度也不同(薄 shim 约短 8%,且被拒)。因此本目录用 `iv8` 而不是手写 DOM 存根;
  校验口径是"长度带 + 结构量子 + 前缀",不是固定字面长度(调用内含随机数)。
- **请求节流**:脚本内 `time.sleep` ≥1s,只做只读采集。

## 免责

仅供协议学习与安全研究。请自行控制请求频率,不要用于大规模抓取。
