# 数据约定 v1 / v2

文章使用 UTF-8 JSON，每个不可变版本一个文件，最大 2 MiB。保留 v1 演示数据；新录入使用 v2，增加原始粘贴输入记录。

| 字段 | 类型与约定 |
| --- | --- |
| schema_version | 整数，支持 1 和 2 |
| id | 1–80 位小写字母、数字、连字符；等于文件名（不含扩展名） |
| title / source | 非空文本 |
| source_url | 无凭据的 HTTP(S) 链接或 null；未知链接不编造 |
| published_date | ISO 日期 YYYY-MM-DD 或 null；未知日期不编造 |
| created_at | 带时区的 ISO 时间，如 2026-10-14T02:00:00Z |
| folder_id | 必须存在于 folders.json |
| content_version | 正整数；修改原文、顺序或翻译时增加，旧阅读状态不应用到新内容 |
| translation_status | complete / partial / untranslated，必须与所有内容块实际译文数量一致 |
| is_demo | 布尔值；演示文章在阅读区明确标记 |
| blocks | 有序内容块数组，1–1000 个；顺序即阅读顺序 |
| import_source | v2 必需；只含 format 和 text，记录最初粘贴内容；format 为 plain/marked/json/blocks |

内容块只含 `id`、`type`、`original`、`translation`：

- `id` 在同一篇文章内唯一，以小写字母开头，只含小写字母、数字、连字符。
- `type` 为 paragraph、heading 或 list。
- `original` 是非空英文文本；不执行 HTML。list 用换行分隔列表项。
- `translation` 是非空中文文本或 null；null 表示尚未翻译。禁止把空字符串当作完成译文。
- 中文与英文共享块编号，不按数组位置单独猜测匹配。
- 版本 1、2 分别按自己的字段集校验；拒绝其他未知字段。

网页准备草稿时编号由程序生成，双语标记保留指定编号。未保存的纯英文预览合并/拆分后重新编号；保存以后编号和原文固定。网页编辑不改 import_source.text，最初输入始终保留。

创建需要版本 1，按编号及归一化英文全文查重（忽略空白和分段方式）。补译同时提交 expected_version、block_id、精确 original 和 translation；整批校验后才新增版本。既有不同译文、原文不符、旧版本和重复编号均拒绝。相同译文重试不产生额外版本。

新快照保存到 records/{id}/v{八位版本}-{UUID}/article.json，先完整写入、flush/fsync、回读校验，再建立 COMMITTED 标记。失败时记录保留但不发布，不回退或覆盖其他文件。OS 文件锁协调网页和 MCP 进程；当前及历史快照都可读取。未验证真实断电、损坏磁盘或网络文件系统的行为。

`folders.json` 为 schema_version=1 和 folders 数组；每个文件夹只含唯一 id 和非空 name。

接口：

- GET /api/health：服务健康与只读状态。
- GET /api/library：文件夹、文章摘要及无法读取文件的提示。
- GET /api/articles/{id}：校验后的完整文章。
- GET /api/session：本机网页获得当前进程的 CSRF 令牌，不持久保存令牌。
- POST /api/prepare：只返回草稿与提示，不发布文章。
- POST /api/articles：新增完整文章，要求 confirmed=true、同源 Origin 和有效 X-Reader-CSRF；重复返回 409。
- 其他 POST、PUT、PATCH、DELETE：返回 405，不覆盖或删除文章。

POST 请求上限 4 MiB，保存后文章（含原始粘贴输入）上限 2 MiB。拒绝跨源、非 JSON、缺少令牌、错误字段和错误格式的请求。

静态资源使用明确白名单；不提供 data 目录下载或任意文件读取。单篇文章损坏时列表提示异常，其余有效文章仍可读取。文件夹配置损坏时返回错误，避免以空配置掩盖问题。

阅读记录使用浏览器 localStorage 键 `english-news-reader.v1`，包含结构版本、字号、上次文章、文件夹和按文章编号保存的阅读状态。文章状态绑定 content_version，包含展开块编号、滚动位置及段落锚点。与文章文件独立。

未保存的录入使用 sessionStorage 键 english-news-import.v1；与已提交文章分开。真实文章文件以本地数据目录为准，草稿缓存不等于已保存。
