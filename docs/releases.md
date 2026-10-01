# 自动更新与协作校对

所有 GitHub Actions 都在本仓库；插件源码与配套工具仓库不运行 Actions。工作流每天北京时间 23:00（UTC 15:00）运行，也可在 Actions 手动启动。首次手动运行默认 `dry_run=true`：会抓取、校验并生成完整包，但不提交译文、不上传或发布。模型调用仍可能发生；同时选择 `no_translate` 可关闭，存在待译内容时会阻止发布。

## 一次性配置

运行器上线后，可手动运行 `Runner environment check` 检查环境，再运行 `Prepare self-hosted release runner` 安装依赖和配置。支持 Ubuntu/Linux x86_64、Python 3.12；依赖保存在运行器用户的 `~/.local/share/idoly-release/`，不需要 sudo。首次下载的工具版本和 Python 依赖摘要会锁定，重跑复用已有安装。

引导任务还需要环境 Secret `BOOTSTRAP_PRIVATE_B64`：经维护者明确授权的私密配置包，包含游戏接口参数、专用采集账号及 APK 签名材料。它通过 `scripts/configure_runner.py` 写入权限为 `0600` 的本地文件；重跑保留已刷新的账号令牌，拒绝替换不同签名密钥。字体从固定版本公开 APK 提取，并验证 APK 和字体摘要。不要把配置包、签名文件或日志提交到仓库。

引导成功后，将任务摘要显示的路径填入 `IDOLY_RUNNER_CONFIG`，先手动运行发布工作流，保持 `dry_run=true` 并选择 `no_translate=true` 做验证；有新待译内容时该验证会停止。验证通过再将 `RELEASE_ENABLED` 设为 `true`。

使用专用、自托管 Linux X64 运行器，不允许外部 PR 使用它。设置 `localization-release` environment，只允许本仓库受保护主分支；启用 CODEOWNERS 审批，限制 `.github/` 修改和默认分支写入权限。没有 `pull_request`、`pull_request_target`、Issue 触发器；外部投稿经审阅合入后只作为 CSV/JSON 数据读取，绝不执行其中的脚本、工作流或命令。Viewer 只能写入其许可的数据路径。

`localization-release` environment secrets：

- `SOURCE_DEPLOY_KEY`：仅用于 `Idoly-localify` 的独立只读 SSH 部署私钥。
- `TOOLKIT_DEPLOY_KEY`：仅用于 `HoshimiToolkit` 的独立只读 SSH 部署私钥。
- `TRANSLATOR_DEPLOY_KEY`：仅用于 `Hoshimi_Teleprompter` 的独立只读 SSH 部署私钥。
- `OPENAI_API_KEY`：增量机器翻译 API 密钥。
- `OPENAI_API_BASE`、`OPENAI_MODEL`：模型 API 地址与模型名称。引导和发布工作流均从此环境的 Secrets 读取；不要放入仓库 Variables 或手动运行参数。

仓库 variables：

- `IDOLY_RUNNER_CONFIG`：运行器本地 JSON 配置的绝对路径。
- `RELEASE_ENABLED`：完成预检后设为 `true` 才允许定时或手动正式发布。未开启时，仍可在 `main` 手动运行 `dry_run=true`，不会提交数据或发布。
- 可选 `IDOLY_SOURCE_REF`、`TOOLKIT_REF`、`TRANSLATOR_REF`：默认各代码仓库的 `main`；需要冻结工具版本时设置完整 commit SHA。这些分支仅由可信维护者更新。

三个部署公钥分别登记在对应代码仓库，关闭写入权限，不复用密钥。所有 checkout 均设置 `persist-credentials: false`。发布步骤使用本公开仓库内建 `github.token`，job 仅授予 `contents: write`，无需个人访问令牌。主分支规则需允许此工作流提交已验证的数据；不允许时会安全失败，不强推。workflow 的分支条件与 environment 部署分支规则都限制 `main`。

工作流只有定时和手动触发器；此外，内建令牌产生的普通 push 不会触发后续工作流，避免递归发布，GitHub 文档说明见[工作流触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。本流程不调用 `workflow_dispatch` 或 `repository_dispatch`。

API 地址必须是无用户名、密码、查询参数和 fragment 的 HTTPS URL；模型名称必须为单行文本。地址、模型与密钥统一在 `localization-release` 的 Secrets 中设置；手动运行仅保留 `dry_run` 和 `no_translate`，避免把服务配置写入公开的运行参数记录。环境配置优先于运行器本地 JSON 的兼容配置；工作流不在日志、摘要或发布报告中输出 API 地址和模型名。

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
  "initialize_collector_day": false
}
```

`signing.json` 权限必须为 `0600`，包含 `IDOLY_KEYSTORE_FILE`、`IDOLY_KEYSTORE_PASSWORD`、`IDOLY_KEY_ALIAS`、`IDOLY_KEY_PASSWORD` 四个字符串。它作为 JSON 解析，不使用 shell `source`。保留同一个 keystore 才能让用户覆盖更新 APK。字体 bundle 按私有主项目字体构建说明生成；SolisClient 及其 protobuf/SQLCipher 依赖预先放在配置路径。公告使用已创建的专用 collector，不使用玩家账号、不自动创建账号；需要日初始化时默认停止并报告；已获授权的专用 collector 可在本地配置设置 `initialize_collector_day: true`，仅允许该账号的 Home.Login 日初始化。Octo 与 Firebase 配置留在本地配置目录，工作流不会把它们放入 release。

运行器先安装 Git、GitHub CLI、JDK 17、Android SDK 36、build-tools、NDK 26.3.11579264、CMake 3.22.1。Python 依赖可在三个代码仓库首次 checkout 后安装：

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
