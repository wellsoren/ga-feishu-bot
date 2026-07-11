# 安全策略

## 🔒 密钥保护

本项目涉及飞书开放平台的 **App ID** 和 **App Secret**，请严格遵守：

1. **绝不提交密钥到 Git 仓库** - `.gitignore` 已配置排除 `mykey.json` 和 `channels/lark.json`
2. **配置文件模板** - 仓库只提供 `channels/lark.json.template`（占位符），用户使用时复制并填入真实值
3. **环境变量优先** - 推荐通过环境变量 `FS_APP_ID` / `FS_APP_SECRET` 传入，避免凭证落盘

## 🐛 报告安全漏洞

如发现安全漏洞，请**不要公开提交 Issue**，而是通过以下方式联系维护者：

- 在 GitHub 上提交 [Security Advisory](https://github.com/你的用户名/ga-feishu-bot/security/advisories)
- 或发送邮件至维护者邮箱

我们会尽快响应并修复。
