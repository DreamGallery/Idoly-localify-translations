# 自动更新与协作校对

所有 GitHub Actions 都在本仓库；三个私有代码仓库不运行 Actions。工作流每天北京时间 23:00（UTC 15:00）运行，也可在 Actions 手动启动。首次手动运行默认 `dry_run=true`：会抓取、校验并生成完整包，但不提交译文、不上传或发布。模型调用仍可能发生；同时选择 `no_translate` 可关闭，存在待译内容时会阻止发布。

## 一次性配置

使用专用、自托管 Linux X64 运行器，不允许外部 PR 使用它。设置 `localization-release` environment，只允许本仓库受保护主分支；启用 CODEOWNERS 审批，限制 `.github/` 修改和默认分支写入权限。没有 `pull_request`、`pull_request_target`、Issue 触发器；外部投稿经审阅合入后只作为 CSV/JSON 数据读取，绝不执行其中的脚本、工作流或命令。Viewer 只能写入其许可的数据路径。

仓库 secrets：

- `CODE_READ_TOKEN`：仅允许读取三个私有代码仓库。
- `RELEASE_TOKEN`：仅允许对本公开仓库提交数据及发布 Releases，不能读私有代码。
- `OPENAI_API_KEY`：增量机器翻译 API 密钥。

仓库 variables：

- `IDOLY_RUNNER_CONFIG`：运行器本地 JSON 配置的绝对路径。
- `RELEASE_ENABLED`：完成预检后设为 `true`；未设置时不运行，不建空 release 或 Issue。
- 可选 `IDOLY_SOURCE_REF`、`TOOLKIT_REF`、`TRANSLATOR_REF`：默认各私有仓库的 `main`；需要冻结工具版本时设置完整 commit SHA。仅可信维护者能够更新这些私有分支。

本地配置示例（路径是示例，按运行器实际位置填写；此文件不提交）：

```json
{
  "python": "/srv/idoly/venv/bin/python",
  "api_python": "/srv/idoly/api-venv/bin/python",
  "work_dir": "/srv/idoly/cache/release",
  "solis_dir": "/srv/idoly/dependencies/SolisClient",
  "toolkit_config": "/srv/idoly/config/toolkit.ini",
  "octo_settings": "/srv/idoly/config/octo-settings.json",
  "firebase_settings": "/srv/idoly/config/firebase-settings.json",
  "notice_account": "/srv/idoly/config/collector-account.json",
  "font_bundle": "/srv/idoly/config/resource-han-rounded.bundle",
  "signing_env_file": "/srv/idoly/config/signing.json",
  "android_home": "/srv/idoly/android-sdk",
  "java_home": "/usr/lib/jvm/java-17-openjdk-amd64",
  "app_version": "6.0.2",
  "initialize_collector_day": false,
  "openai_api_base": "https://your-approved-provider.example/v1",
  "openai_model": "your-approved-model"
}
```

`signing.json` 权限必须为 `0600`，包含 `IDOLY_KEYSTORE_FILE`、`IDOLY_KEYSTORE_PASSWORD`、`IDOLY_KEY_ALIAS`、`IDOLY_KEY_PASSWORD` 四个字符串。它作为 JSON 解析，不使用 shell `source`。保留同一个 keystore 才能让用户覆盖更新 APK。字体 bundle 按私有主项目字体构建说明生成；SolisClient 及其 protobuf/SQLCipher 依赖预先放在配置路径。公告使用已创建的专用 collector，不使用玩家账号、不自动创建账号；需要日初始化时默认停止并报告；已获授权的专用 collector 可在本地配置设置 `initialize_collector_day: true`，仅允许该账号的 Home.Login 日初始化。Octo 与 Firebase 配置留在本地配置目录，工作流不会把它们放入 release。

运行器先安装 Git、GitHub CLI、JDK 17、Android SDK 36、build-tools、NDK 26.3.11579264、CMake 3.22.1。Python 依赖可在三个私有仓库首次 checkout 后安装：

```sh
python3.12 -m venv /srv/idoly/venv
/srv/idoly/venv/bin/python -m pip install \
  -r HoshimiToolkit/requirements.txt \
  -r Idoly-localify/tools/requirements-font.txt \
  -r Idoly-localify/tools/requirements-web.txt
sdkmanager --sdk_root=/srv/idoly/android-sdk \
  "platforms;android-36" "build-tools;36.0.0" \
  "ndk;26.3.11579264" "cmake;3.22.1" "platform-tools"
# API 环境依照 SolisClient 自身 requirements 安装，需能导入 grpc、protobuf、SQLCipher。
chmod 600 /srv/idoly/config/signing.json /srv/idoly/config/collector-account.json
python3 Idoly-localify/tools/release_translations.py \
  --config /srv/idoly/config/runner.json --preflight-only \
  --repository-dir "$PWD/Idoly-localify-translations" \
  --adv-dir "$PWD/Hoshimi-Adv" --toolkit-dir "$PWD/HoshimiToolkit" \
  --translator-dir "$PWD/Hoshimi_Teleprompter"
```

预检不访问游戏接口或发布。正式执行时增加 `--dry-run` 可完整生成并验证产物；第一次需要网络和配置好的模型服务，依赖、账号或译文不完整均停止。不能把“生成了 workflow”当成已配置运行器或已成功运行。

## 发布规则

每轮先检查 Octo、MasterDB 和当前公告，建立最新官方原文；再校验并导入公共 AI CSV。机器翻译与翻译记忆仅处理 AI 层，最后才叠加人工稿并重新检查每行覆盖，避免撤销的人工稿流回机器记忆。只翻译新增或变化后缺失的字段，旧译文与精确原文匹配的缓存优先复用。任何抓取、原文匹配、翻译覆盖或构建失败均不发布。机器稿会按字段和原文安全迁移，变化字段清空后重新翻译；原文发生变化但旧人工稿尚未更新时停止，不自动把旧校对套在新剧情上。

Viewer 正式稿优先级为 `story/reviewed`、`story/human`、`story/ai`。人工稿必须有 `records/<脚本ID>.json` 的完成轨道和匹配 artifact path；CSV 的 `info.text` 必须等于当前 TXT SHA-256，`info.name`、逐行 `id/name/text` 必须与当前原始 CSV 一致。Issue 的开关状态和正文标记仅用于协作展示，不是发布授权；草稿、备份和 `proofread_txt` 不直接进入游戏。PR 必须先合入受保护分支，流水线不会 checkout 投稿分支。

已完成校对的 `story/reviewed` 标题通过 Story 记录对应的剧情资源 ID 关联，只在同条记录原文 `name` 与标题或话数后标题一致时覆盖发布工作区的 MasterDB 译文。同一条记录有多个不同校对标题会阻止发布。`story/human` 正文优先于 AI，但未校对标题不覆盖 MasterDB。完成记录仍有空译文时会阻止发布。人工稿不会回写机器剧情目录；MasterDB 的人工标题覆盖也只用于编译产物。

完整验证后，仅同步机器剧情 CSV、MasterDB 原文和机器译文、公告等明确数据白名单；保留 `story/human`、`story/reviewed`、`records`。推送前检查默认分支还是开始时的 commit，出现并发更新则停止，绝不 force push。数据提交成功才发布，未变则不提交。输出协议为 schema 2：`manifest.json` 引用固定版本 `text-update.zip`，ZIP 内路径与 `files` 键精确一致，每个文件及整包都有 SHA-256。ZIP 排序、时间戳与权限固定，同内容不会生成新的版本。

Release 先建 draft，上传完整附件后才公开；失败的 draft 可安全重试，已上传同名附件必须摘要相同，差异会停止。APK 仅在 Gradle `versionName` 对应的 `v版本号` release 尚未发布时构建并发布，采用持久 release 签名，标记 `latest=false`。日常文本 release 标记 latest。

游戏更新地址：

`https://github.com/DreamGallery/Idoly-localify-translations/releases/latest/download/manifest.json`

需要支持 schema 2 ZIP、HTTPS 重定向及 MasterDB 更新的插件版本。文本包只含字典、公告、剧情 TXT 和已验证的 `master-blobs.bin`；源码、构建缓存、账号、日志和签名文件不上传。相同文本版本核验通过后会恢复其 latest 指向；已发布插件版本缺少对应 APK 时明确停止，不静默跳过。

本地人工运行数据出口：

```sh
python3 Idoly-localify/tools/export_translation_sources.py \
  --project Idoly-localify --csv-dir /path/to/current/machine-csv \
  --source-dir /path/to/current/original-txt \
  --repository Idoly-localify-translations
```

默认只列出差异。确认输入是机器译文暂存区后加 `--apply` 写入；可提供 `--collaboration-receipt` 排除人工稿对应的 AI 文件。该命令自身不 commit 或 push。
