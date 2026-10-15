# 离线单词查词

在英文正文中按住 **Alt** 再用鼠标左键单击单词，自动显示 ECDICT 释义。程序直接识别点击位置的完整单词，不选中文字，从而避开浏览器的选词迷你菜单。支持词内连字符和撇号；原文中的小标题和列表同样支持。点击空白、标点或中文译文不查词。

普通单击正文仍展开或收起段落翻译；普通拖选、双击只选择文字，不触发查词。Alt 查词不会展开段落译文；按住 Alt 拖动鼠标不触发查询。

浮窗显示词库音标和中文释义，保留原词性标记、领域标签和释义顺序；仅将 CSV 中的换行编码还原为换行。长释义可以展开。没有中文释义时明确提示并展示词库的英文原文，没有对应条目时显示未收录。词库提供的词形索引用于原词未命中时查找原形，不根据上下文推断词义。

点击空白处、关闭按钮或按 Esc 关闭。查词后可按 Alt+向下方向键进入浮窗，再用 Tab 操作关闭或展开按钮。正文滚动、换文章、进入导读或录入面板时关闭浮窗。浏览器标签页内最多缓存 200 个查询结果；刷新页面即可清除，不写入阅读记录。

## 可选词库安装

公开仓库提供查询代码、构建工具、许可证和固定来源记录，不包含约 66 MB 的 CSV 或约 124 MB 的 SQLite 数据库。未安装时，阅读和翻译照常使用，查词会提示词库未就绪。安装后，后台从 `resources/ecdict/dictionary.sqlite` 以只读方式查询，无查词 API 密钥，无查词外网请求。

首次安装：从 `resources/ecdict/source.json` 的固定来源下载 CSV 到 `resources/ecdict/ecdict.csv`，保留随仓库提供的 LICENSE 和 source.json。在确认目标数据库及 manifest.json 均不存在时执行：

```sh
python -B -X utf8 tools/build_dictionary.py --source resources/ecdict/ecdict.csv --provenance resources/ecdict/source.json --output resources/ecdict/dictionary.sqlite
```

工具先核对 CSV 的 SHA-256，再构建并验证数据库；拒绝覆盖已有数据库或清单。下列大小和数量来自固定版本的本机构建验证，首次安装需要自行执行构建：

- 来源：https://github.com/skywind3000/ECDICT
- 固定提交：`bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b`
- 原始 CSV：65,933,428 字节；770,611 条词条。
- 查询数据库：123,555,840 字节；79,141 条词形关联。
- 1,872 条记录没有中文释义，界面按上述规则处理。
- 导入已通过数据库完整性检查，以及所有原始字段的全量回读一致性核对。

本项目只展示词库原文，不保证词库自身每条释义均准确。来源许可文件随数据保留。

文章备份工具只备份 `data/`，不包含本词库。备份整个 `resources/ecdict/` 可保留离线查词能力；源 CSV 和 `source.json` 也可重新生成查询库。重建时必须输出到一个已创建的独立新目录：

```powershell
python -B -X utf8 tools/build_dictionary.py --source resources/ecdict/ecdict.csv --provenance resources/ecdict/source.json --output output/你新建的目录/dictionary.sqlite
```

工具拒绝覆盖已有数据库或清单，失败时保留现场。数据库完全生成并验证后才能安装到正式位置，不能直接覆盖现有文件。

## 更新后打开

桌面快捷方式可能复用仍在运行的旧后台。若浮窗提示后台没有查词接口，刷新网页不足以解决，需要使用新版后台。不要按进程名结束现有服务；可在一个空闲本机端口另开新版：

```powershell
python -B -X utf8 server.py --port 8766
```

然后打开 http://127.0.0.1:8766 。端口被占用时改用其他空闲端口。不同端口的浏览器阅读记录独立，保存的文章仍来自同一文章库。

## 简短检查

`python -B -X utf8 tests/test_dictionary.py` 只做真实词条、基础输入校验和本地 HTTP 接口检查，在 `output/` 保留独立空文章库测试目录。查词前后核对数据库未改变。页面视觉、鼠标手感、手机系统选词菜单和完整阅读流程由实际阅读验证。
