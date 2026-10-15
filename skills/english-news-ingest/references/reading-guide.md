# 中文导读

用于“给这篇新闻生成并导入中文导读”“把这份导读加到文章”。用户只要讲解、生成草稿或预览时不保存；明确要求导入/添加导读时，授权只适用于该文章的本次新增。网页中用“中文导读 / 返回原文”切换。导读只有中文，不要求英文对应、不放进逐段 translation，也不创建另一篇文章。

## 内容

先通过 list_articles 确定 article_id，用 get_article 按同一 content_version 读齐原文；分页读取完毕后再写导读。不要只根据标题、开头或零散片段宣称掌握全文。如果是节选，写清处理范围。已有译文仅帮助理解，核对事实以原文为准。

用户给了完整导读时保留其内容与顺序，不擅自重写。用户要求生成时，用自然中文解释文章要回答的问题、主要线索和必要术语/背景，帮助读者进入原文。篇幅与文章难度相称；不要逐段复述全文或堆砌固定模板。数字、因果、专名必须核对，观点与事实区分；原文没说的推断要标明，不补造背景。不额外联网收集资料或调用付费服务。必要英文专名、缩写和数字可保留。

正文按空行分段，需要时用独立的 `# 中文小标题`，列表用 `- 项目`。不要为每个段落强行加标题。工具会给每块稳定的 g001、g002 编号；这些编号与英文的 p001 无关。

## 导入

1. get_reading_guide({article_id}) 查是否已有导读。已有内容沿用并告知用户；不覆盖、不换 article_id 绕过保护。
2. prepare_reading_guide({article_id, raw_text, split_mode: "blank_lines"}) 得到未保存的 `{article_id, expected_content_version, blocks, saved:false, guide_exists}`。每个 blocks 项严格只有 `{id, type, text}`，类型 paragraph/heading/list。默认空行分段；确知每行独立成段时才用 every_line。核对分段、完整性与中文内容。
3. 核对预览的 expected_content_version 与刚才读齐原文的版本相同。若不同，先回读确认原文没变，再保存。save_reading_guide({article_id, expected_content_version, blocks, confirmed:true}) 原样提交预览块，不提交整个预览对象。没有当前保存授权时止于预览。
4. 用 get_reading_guide 回读并逐块比对 id/type/text、块数和文章编号。工具返回 saved/unchanged 不能替代回读。保存不会增加文章 content_version，不改变原文、译文或阅读记录。

version_conflict 回读当前文章后重新核对，最多重试一次；guide_exists 不覆盖。响应丢失时先 get_reading_guide 核对，完全相同的保存可安全重试；不同内容停止调查。读取错误不能当作“还没导读”继续写。

导读工具未出现在当前聊天目录中时，使用 [本地客户端](local-client.md) 调用同名工具；它会启动最新项目 MCP。不要自动重启用户的其他 MCP 进程或修改用户配置。

完成后报告标题、article_id、导读块数及网页入口，提示点击网页“刷新文章库”，再选“中文导读”。只在实际保存、回读一致后称为导入完成。
