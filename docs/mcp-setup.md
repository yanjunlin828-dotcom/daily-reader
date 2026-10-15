# AI 辅助录入

Daily Reader 展示预先保存的原文、译文和中文导读。网页本身不调用翻译模型；AI 对话通过本地 STDIO MCP 完成准备、保存及回读。

## 准备环境

在仓库根目录中，使用你准备运行 MCP 的 Python 解释器安装可选依赖：

```sh
python -m pip install -r requirements-mcp.txt
```

普通网页阅读无需安装这项依赖。建议为 MCP 使用独立虚拟环境，并在配置中指定该环境的解释器路径。

## 配置 MCP

参考 [mcp-config.example.toml](mcp-config.example.toml)，将示例中的仓库路径和 Python 解释器改为自己的实际位置，再按所用 AI 客户端的配置方式添加。此示例采用 Codex 的 TOML 格式；其他支持 STDIO MCP 的客户端需要转换为其配置格式。

仓库不包含注册或修改全局配置的自动脚本。不要把示例路径直接当作可运行配置。

服务提供十一个工具：

| 用途 | 工具 |
| --- | --- |
| 预览原文分段 | `prepare_article` |
| 新增文章 | `save_article` |
| 查询文章 | `list_articles`、`get_article` |
| 补充空缺译文 | `add_translations` |
| 校改已有段落 | `update_article_blocks` |
| 可恢复删除及恢复 | `delete_article`、`restore_article` |
| 准备、保存和读取中文导读 | `prepare_reading_guide`、`save_reading_guide`、`get_reading_guide` |

保存需要当前用户授权、`confirmed=true` 和通过结构校验。补译核对文章版本、块编号及原文，只填空缺；导读独立保存，不改文章版本，也不覆盖当前有效的不同导读。英文校改使旧导读过期时，可以追加当前原文的导读版本。校改及回收的授权、版本核对与回读要求见 [文章维护](article-maintenance.md)。

## 使用 Skill

将 [skills/english-news-ingest](../skills/english-news-ingest) 按客户端的 Skill 安装方式放入可发现的位置，或在对话中提供其 `SKILL.md`。首次使用时说明当前仓库的实际路径。

可发送：

> 使用 $english-news-ingest，把我提供的这篇新闻录入 Daily Reader，保持原文分段，逐段翻译，检查对应关系后保存。再生成一份按自然段组织的中文导读，并回读核对。

只要草稿时明确说“预览，不保存”。录入真实新闻时使用实际可读的原文与来源，不编造缺失段落或来源，不绕过付费墙。

若对话暂时未加载原生工具，可按 Skill 的 [本地客户端说明](../skills/english-news-ingest/references/local-client.md) 调用同一 MCP。网页点击刷新后即可读取已保存的内容。
