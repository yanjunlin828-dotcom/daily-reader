# 文章校改与回收

## 修改已有段落

先 get_article 读取当前 content_version 和目标完整块，然后在当前用户授权下调用 update_article_blocks：

```json
{
  "article_id": "article-example",
  "expected_version": 3,
  "updates": [{
    "block_id": "p001",
    "before": {"id":"p001", "type":"paragraph", "original":"Exact saved text.", "translation":"原有译文。"},
    "after": {"translation":"校正后的译文。"}
  }],
  "confirmed": true
}
```

before 必须等于当前完整旧块；after 只含要改的 original、translation、type。修改英文必须同时提供更新后的 translation 或明确为 null，避免把旧译文配给新英文。批次全部校验通过才追加一个文章版本；编号、顺序、标题、来源、文件夹和最初 import_source 不变。相同内容不新增版本，旧版本仍可按 content_version 回读。stale version / before 不匹配时整批拒绝，不自动重放。

英文文本变化后旧中文导读继续保留在历史中，但暂停展示，get_reading_guide 返回 guide=null、stale=true；网页显示“原文已更新，导读待更新”。有新的导读保存授权时，按当前文章版本重新准备/保存，追加新 guide_version；当前有效导读依然不可覆盖。只改译文不使导读过期。

## 删除和恢复

删除前读取文章，核对准确 article_id、完整标题、当前内容版本和 lifecycle.state_version。delete_article 请求示例：

```json
{
  "article_id":"article-example",
  "expected_version":3,
  "expected_state_version":0,
  "expected_title":"Exact saved title",
  "confirmed":true
}
```

删除只追加 `data/trash/{article_id}/v{八位状态版本}-{UUID}/state.json`，完成写入、fsync、回读后最后建立 COMMITTED。没有删除或移动任何文章/导读文件，没有覆盖目录配置。默认列表及网页隐藏文章；刷新正在阅读的已删文章时切换到其他文章或显示移出提示，不继续显示过期正文。

list_articles(include_deleted=true) 可查询所有活动及已删除文章，按 deleted 区分；get_article(include_deleted=true) 可读取回收区正文及历史版本。restore_article 使用相同字段，expected_state_version 取回读得到的最新状态版本。恢复不增加 content_version；原文件夹仍存在则归属保留，目录已删除则在根目录。已删除文章禁止补译、校改或新建导读；同原文重导入也会拒绝并给出已有 article_id，应先按用户授权恢复。

删除/恢复使用独立状态版本，防止旧删除请求在恢复后再度生效。丢失响应时先回读状态；完全相同的最近操作可返回 unchanged=true。恢复与修改都保留旧版本，ZIP 备份包含回收区、状态历史、原文和所有导读版本。没有永久销毁接口。

## 使用和验证

随仓库提供的 english-news-ingest Skill 已加入本流程。原生新工具尚未加载时，library_cli.py 支持同名工具；请求 JSON 不含 confirmed，获得本次授权后命令使用 --confirmed。新增能力本身不是操作真实文章的授权。

`python -B -X utf8 tests/test_article_maintenance.py` 在 output 新虚构数据目录验证内容保留、原子校改、授权、并发、状态重试、旧版文章、导读过期/再生成、备份独立恢复、真实 MCP 和 HTTP；不写正式库。
