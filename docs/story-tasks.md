# 剧情协作任务

任务 Issue 在 GitHub 中创建和管理。申请者获得本仓库编辑权限后，可在协作网站领取、保存草稿和提交翻译／校对结果；网站不负责发布任务。

## 创建任务

- 自动：`Hoshimi-Adv` 的 `Resource/`、`CSV/` 或 `revision` 更新后，触发本仓库 **Sync original stories and create tasks**。先校验、迁移并提交文本，再为新增或变更的剧情创建或更新 Issue。
- 手动：本仓库 Actions → **Create story task by filename** → Run workflow，以下两项二选一：
  - `filename`：单个 `adv_…txt` 或 `adv_…csv` 文件名。
  - `story_id`：章节共同的剧情 ID，例如 `adv_card_ktn_15` 创建这张卡的全部小章节，`adv_event_2107` 创建该活动的全部章节，`adv_main_01` 创建主线第一大章的全部小章节。按下划线边界精确匹配，自动排除 `_short` 结尾的脚本，每个脚本一个 Issue。
- 同一剧情复用同一个 Issue，包括已关闭的任务；原文变化时会重新打开。首次同步不会为已有、未变化的全部剧情批量开单。
- 新创建的任务自动添加 `待翻译` 标签；重复运行不重置已有任务的标签或领取状态。

Issue 标题使用脚本名，正文中的路径、原文校验和及翻译／校对状态标记供协作网站读取，请勿删除。可正常添加说明、评论和负责人。

## 原文更新

校验原始 TXT 的 SHA-256、CSV 字段、发言人和脚本类别。能唯一对应且原文未变的译文保留；变化或无法确定对应关系的译文留空，不按行号强行套用。

受影响的人工稿、校对稿及记录先备份到 `archive/`，再迁移到 `story/drafts/translation/` 和 `story/drafts/proofread/`。完成状态撤回，版本递增，旧编辑会话需重新加载。重新提交校对结果后，才会再次作为校对稿参与发布和标题覆盖。

原文仓库中消失的脚本只报告，保留已有译稿，不自动删除。同步发现原文版本落后、校验失败或有人同时提交时会停止；修正后重新运行即可。

`automation/story-sync-state.json` 记录已同步的原文和待处理任务。数据提交后才调用 Issue 接口；中途失败时重试会补齐任务，不重复建单。

## 原文仓库的一次配置

在 `Hoshimi-Adv` 的 Actions Secrets 中设置 `TRANSLATIONS_DISPATCH_TOKEN`：使用仅能访问 `DreamGallery/Idoly-localify-translations`、仓库权限仅需 **Actions: Read and write** 的 fine-grained PAT。其作用只是触发本仓库主分支上的固定工作流。到期后创建替代令牌并更新此 Secret。

本仓库同步任务使用自身 `GITHUB_TOKEN` 的 Contents／Issues 写权限，不需要模型密钥或游戏账号。同步在现有 `self-hosted, Linux, X64` 运行器执行，与发布任务串行；原文仓库的轻量通知使用 GitHub 托管运行器。

原文上传器如果使用 NAS 的 PAT／SSH 密钥推送，会触发通知；如果改为另一个 GitHub Action 使用内置 `GITHUB_TOKEN` 推送，需要在该上传 Action 中显式调用通知工作流，GitHub 不会为这类推送递归触发后续 Action。
