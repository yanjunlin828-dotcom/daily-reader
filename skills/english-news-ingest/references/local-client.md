# 原生 MCP 工具未加载时

`library_cli.py` 使用已有官方 SDK 启动同一个 STDIO MCP，所有操作仍经过相同校验。不得直接修改 data JSON 代替工具，也不把此方式描述为原生工具已加载。

在当前用户指定的 daily-reader 仓库根目录执行，使用已经安装 requirements-mcp.txt 的 Python 解释器。使用 `-B -X utf8`；不自动安装依赖或修改用户配置。

工具参数先写到项目 output 内一个新的 UTF-8 JSON 请求文件，不覆盖已有文件。参数与原生工具相同，但 JSON 不含 confirmed：

```powershell
python -B -X utf8 library_cli.py list_articles --request output/list-request-唯一编号.json
python -B -X utf8 library_cli.py prepare_article --request output/prepare-request-唯一编号.json --output output/prepared-唯一编号.json
python -B -X utf8 library_cli.py save_article --request output/save-request-唯一编号.json --confirmed
python -B -X utf8 library_cli.py get_article --request output/get-request-唯一编号.json
python -B -X utf8 library_cli.py add_translations --request output/translations-request-唯一编号.json --confirmed
python -B -X utf8 library_cli.py prepare_reading_guide --request output/guide-prepare-唯一编号.json --output output/guide-preview-唯一编号.json
python -B -X utf8 library_cli.py save_reading_guide --request output/guide-save-唯一编号.json --confirmed
python -B -X utf8 library_cli.py get_reading_guide --request output/guide-get-唯一编号.json
python -B -X utf8 library_cli.py update_article_blocks --request output/blocks-update-唯一编号.json --confirmed
python -B -X utf8 library_cli.py delete_article --request output/article-delete-唯一编号.json --confirmed
python -B -X utf8 library_cli.py restore_article --request output/article-restore-唯一编号.json --confirmed
```

`--confirmed` 只用于已有本次授权的写操作。`--output` 只创建新结果文件；错误或中断可能留下空结果文件，保留以供调查。命令不成功时先回读库确认是否保存，不能据空文件猜测结果。

prepare 返回 `{article, warnings}`；save 请求需要 `{article}` 完整对象。读取时用 `{article_id, content_version, block_offset, block_limit}`，current version 可首次省略。补译请求为 `{article_id, expected_version, translations}`，每项严格只含 block_id、original、translation。输出是 UTF-8 JSON。

只能访问项目内路径；没有工具时不要自行改变沙箱或权限。遇到权限拒绝走现有审批流程，并说明具体动作。准备文件在 output 是操作记录，不是已保存的正式文章。


使用 --output 时，完整 MCP 返回对象只保存在新结果文件；标准输出是路径、编号、版本和块数摘要。读取该文件中的 article 后再翻译或构造保存请求，不把 stdout 摘要当作文章。
