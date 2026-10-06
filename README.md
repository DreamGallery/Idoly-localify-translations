# Idoly-localify-translations

《IDOLY PRIDE／偶像荣耀》简体中文本地化文本仓库，用于保存译文、校对资料和通用词典。当前剧情内容以 AI 初译及少量定向修订为基础，仍需人工校对。

> ### [下载最新文本和插件并查看更新说明 →](https://github.com/DreamGallery/Idoly-localify-translations/releases/tag/latest)

## 安装插件

插件仍在开发中，总之你懂的，源代码仓库暂时私有，如有需要可以提Issue申请添加访问权限。
虽然可能官方应该不会管，但还请自己承担使用风险。
插件编译Target为Android ARM64。请前往[最新发布](https://github.com/DreamGallery/Idoly-localify-translations/releases/tag/latest)，下载 `idoly-localify-版本号.apk`。

### Root：LSPosed／Vector

1. 在已安装兼容 LSPosed／Vector 框架的设备上，安装插件 APK。
2. 在框架管理器中启用插件，作用域勾选《IDOLY PRIDE》（`game.qualiarts.idolypride`）。
3. 完全关闭游戏后重新启动；此方式使用原版游戏 APK。

### 无 Root：LSPatch

可使用 [Idoly-Patcher](https://github.com/DreamGallery/Idoly-Patcher) 自动下载插件并修补自己提供的原版游戏 APK。准备文件、签名密钥和安装步骤见该仓库 README；也可按以下步骤手动修补。

1. 准备原版游戏 APK 和插件 APK，在兼容的 LSPatch 中选择游戏，使用嵌入模块的修补方式加入插件。
2. 游戏为拆分安装包时，需要同时处理 base 和设备所需的 split APK，并作为一组安装。
3. 安装修补后的游戏并启动。首次从原版切换前请先确认账号已绑定；修补包签名不同，不能直接覆盖原版。

仓库已提供自动触发并修补后的[最新游戏安装包](https://github.com/DreamGallery/Idoly-Patcher/releases)，原始XAPK文件来自网络，通过工作流验证Play Store分发包的开发者签名，请酌情下载使用。

### 更新文本与插件

首次启动后，打开屏幕边缘的插件悬浮按钮，进入「详细设置 → 版本与更新」，点击「下载并更新文本」，完成后重启游戏。这里也可查看当前版本和检查更新。

默认文本更新地址已内置；如需重新填写，使用：

```text
https://github.com/DreamGallery/Idoly-localify-translations/releases/download/latest/manifest.json
```

插件升级时，LSPosed／Vector 用户安装新版插件并重启游戏；LSPatch 内嵌用户需要用新版插件重新修补，并沿用相同的修补签名密钥以覆盖安装。文本热更新不会替换插件 APK。

遇到漏翻或显示问题，可在插件中采集并导出文本，检查个人信息后，附在本仓库的 [Issues](https://github.com/DreamGallery/Idoly-localify-translations/issues) 中反馈。

## 目录

| 路径 | 内容 |
| --- | --- |
| `story/ai/` | 现有 AI 剧情译文 CSV，按剧情类别、角色和章节分类 |
| `story/human/`、`story/reviewed/` | Viewer 完成的人工翻译、校对 CSV |
| `records/` | Viewer 完成轨道、版本与产物路径记录 |
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

原始剧情 TXT 保存在 [Hoshimi-Adv](https://github.com/DreamGallery/Hoshimi-Adv)。本仓库提供每天北京时间 23:00 执行及手动触发的[更新发布工作流](docs/releases.md)，人工正式稿校验原文版本后优先合入。`upstream.json` 用于记录原文来源；插件使用的 `manifest.json` 由发布流程生成并附在 Release 中。
