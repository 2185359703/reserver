# 《中国农村经济》维普公开元数据采集器

本项目采集《中国农村经济》（维普刊号 `94178X`）公开展示的文章元数据与摘要，不下载付费全文或 PDF。

当前索引覆盖 1985–2026 共 500 个刊期。1985–1988 的维普列表接口只有刊期标题，没有文章链接；实际可采集文章覆盖 1989–2026，共 5,314 篇，文章 ID 全部唯一。

`article_index_all.json` 是 2026-09-10 的已验证快照，当时 2026 年发布到第 6 期。`collect_all.py` 会更新快照内文章的详情，但不会自动发现之后新增的刊期；更新索引需要重新完成列表接口取证。

## 项目结构

| 文件 | 用途 |
|---|---|
| `collect_all.py` | 全量采集入口；并发、重试、断点续采、增量写 CSV |
| `cqvip_client.py` | Python HTTP 会话、Cookie 装载和响应校验 |
| `article_parser.py` | 文章详情字段解析与 CSV 写入 |
| `verify_all.py` | 校验全量 CSV 的表头、行数、唯一 ID 和年份/期号一致性 |
| `data/article_index_all.json` | 500 个刊期和 5,314 篇文章的已验证索引 |
| `data/中国农村经济_全年份_文章详情.csv` | 已采集的全量结果，UTF-8 with BOM |
| `js/cqvip_cookie.source.js` | 可阅读的维普 148 字节 Cookie 算法主链 |
| `js/cqvip_cookie.bundle.js` | 可直接运行的单文件算法，不依赖本机其他仓库或 npm 包 |
| `evidence/` | 已脱敏的实际验证日志，适合截图或项目展示 |

## 环境

- Python 3.11+
- Node.js 18+

安装 Python 依赖：

```powershell
git clone https://github.com/2185359703/reserver.git
Set-Location '.\reserver\cqvip'
python -m pip install -r .\requirements.txt
```

运行时不需要 `npm install`。如果 Node 不在 PATH，可设置：

```powershell
$env:CQVIP_NODE = 'C:\path\to\node.exe'
```

## 验证 Cookie 算法

```powershell
node .\js\cqvip_cookie.bundle.js --verify --trace
```

输出会显示以下证据，但不会打印完整 Cookie：

```text
[1/6 challenge] status=412 ...
[2/6 server-cookie] ... value=<redacted>
[3/6 challenge-js] status=200 ...
[4/6 local-rebuild] ... basearr=148 ...
[5/6 client-cookie] ... value=<redacted>
[6/6 replay] status=200 ... passed=true
```

只有本地调试时才使用 `--show-cookie`。不要把完整 `S/T` 写入日志或提交到仓库。

## 采集和续传

```powershell
python .\collect_all.py
```

常用参数：

```text
--workers 4       独立 Cookie 会话并发数，允许 1–8
--delay 0.15      每个线程请求后的等待秒数
--retries 3       单篇最大尝试次数
--output FILE     指定输出 CSV
--limit N         仅采集前 N 篇；0 表示全部
--no-resume       忽略已有 CSV，从头采集
```

已有 CSV 默认按 `article_id` 断点续采。采集过程中每成功一篇就立即写入；全部完成后按索引顺序重新排序。

## 验证全量数据

```powershell
python .\verify_all.py
```

验证项包括：

- CSV 表头与当前代码一致；
- 5,314 行、5,314 个唯一文章 ID；
- 索引无缺失、无额外记录；
- 页面年份与索引年份一致；
- 期刊名全部为《中国农村经济》；
- 输出 SHA-256。

## 算法依赖说明

维普专属的环境块、时间块、代码特征、随机数消费顺序、148 字节 `basearr`、两层加密和 Cookie 回放逻辑都在 `js/cqvip_cookie.source.js`。

通用瑞数 VM/任务解码器来自 `rs-reverse` 1.16.3。为了避免该包庞大的依赖树，发布运行时已经打进 `cqvip_cookie.bundle.js`，因此使用者不需要安装 `rs-reverse`。许可证和署名见 `THIRD_PARTY_NOTICES.md`。

## 成功判定

本站存在 `HTTP 200 + 空正文` 诱饵。项目不会只检查状态码：期刊页必须大于 100 KB 且包含“中国农村经济”，详情页必须存在 `.article-title h1`，写入前还会复核刊名和年份。

## 使用边界

仅用于公开数据、授权研究和教学。请控制并发和请求频率，遵守目标站点条款与适用法律，不用于账号、付费内容或访问控制绕过。
