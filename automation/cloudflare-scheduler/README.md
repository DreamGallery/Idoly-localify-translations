# Cloudflare 发布触发器

独立 Worker `idoly-localization-scheduler`，每天北京时间 23:00（UTC 15:00）调用本仓库 `release.yml` 的手动入口。抓取、翻译、构建及发布仍在 GitHub 自托管运行器执行；Worker 不保存游戏账号、模型密钥或 APK 签名材料。

## 部署

使用 Node.js 22+ 和 Wrangler 4，在此目录执行：

```sh
npx wrangler login
npx wrangler secret put GITHUB_DISPATCH_TOKEN
npx wrangler deploy
```

Secret 使用仅授权 `DreamGallery/Idoly-localify-translations`、权限 `Actions: Read and write` 的 fine-grained PAT。不要使用范围更大的日常登录令牌。过期前创建替代令牌，再次执行 `secret put`；密钥不进入 Git。首次部署可先将 `triggers.crons` 设为空数组，设置 Secret 并验证后再启用定时。

定时调用明确传入 `dry_run=false`、`no_translate=false`，并受 GitHub 仓库 `RELEASE_ENABLED` 开关控制。任务名称包含北京时间日期；触发前检查近期已有任务，相同日期已存在则跳过。请求结果不明确时不自动重发，以免重复发布；失败后查看 Cloudflare Worker 日志及 GitHub Actions，按需手动重跑。此检查不能保证并发请求严格只执行一次。

不开放 HTTP 触发地址，人工运行继续使用 GitHub Actions → **Verified localization releases**。手动入口默认 `dry_run=true`；需要正式发布时自行关闭。`schedule_key` 留空即可。

## 验证与维护

```sh
node --test worker.test.mjs
npx wrangler deploy --dry-run
```

本地联通测试可在未提交的 `.dev.vars` 中设置 `GITHUB_DISPATCH_TOKEN` 与 `DRY_RUN=true`，运行 `npx wrangler dev --test-scheduled`，请求其本地 `/__scheduled` 入口。该模式只抓取和验证，不调用翻译模型、不提交数据、不发布资源；有新增未译文本时，验证任务可能停止。

切换完成后删除 GitHub 工作流中的 `schedule`，只保留 `workflow_dispatch`，避免两个定时器重复启动。Cloudflare 定时配置修改可能需要最多约 15 分钟传播；外部触发不会消除运行器排队或并发锁等待。
