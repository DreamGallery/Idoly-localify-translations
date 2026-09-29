# Idoly-localify-translations

《IDOLY PRIDE／偶像荣耀》简体中文本地化文本仓库，用于保存译文、校对资料和通用词典。当前剧情内容以 AI 初译及少量定向修订为基础，仍需人工校对。

## 目录

| 路径 | 内容 |
| --- | --- |
| `story/ai/` | 现有 AI 剧情译文 CSV，按剧情类别、角色和章节分类 |
| `story/human/` | 人工翻译目录，后续根据翻译工作流创建子目录 |
| `master/orig/` | MasterDB 文本原文，按表、记录和字段保存 |
| `master/zh-Hans/` | 对应的简体中文译文 |
| `master/overrides.json` | 已有的定向译文修订规则 |
| `master/retained-titles.json` | 保留原文的标题及参考来源 |
| `ui/` | 界面原文、中文译文和原文变体 |
| `notice/` | 官方公告原文、译文及入口标题的冲突处理记录 |
| `legal/` | 游戏内条款等正文的原文和译文 |
| `story-metadata/` | 剧情分类目录快照和定向修订规则 |
| `glossary/names.json` | 通用人名词典 |
| `glossary/terms.json`、`glossary/term-aliases.json` | 通用术语及表记别名 |
| `glossary/extracted/` | 外部模型提取的术语候选、上下文证据和待审决定；不会自动并入正式词典 |
| `upstream.json` | 本次整理所用的原文 revision、MasterTag 和来源摘要 |

剧情 CSV 使用 `id,name,text,trans` 四列；ID 中的 `title`、`text`、`choice`、`narration` 分别表示标题、普通对白、选项和旁白。`name` 仅提供原文说话人上下文，人名译文统一维护在词典中。没有正文的 CSV 不收录。

`glossary/extracted/candidates.json` 是术语候选，`evidence/` 保存对应证据，`coverage-gaps.json` 记录尚未确认的原文，`decisions.json` 用于后续审阅，`manifest.json` 记录来源和摘要。

原始剧情 TXT 由 [Hoshimi-Adv](https://github.com/DreamGallery/Hoshimi-Adv) 保存。本仓库当前保存翻译源文件；正式资源发布流程将在翻译工作流确定后添加。`upstream.json` 是来源记录，插件更新地址使用的 `manifest.json` 将由发布流程另外生成。
