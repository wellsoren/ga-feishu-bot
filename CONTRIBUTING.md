# 🤝 贡献指南

感谢您对 `ga-feishu-bot` 的兴趣！欢迎各种形式的贡献。

## 行为准则

本项目采用 [Contributor Covenant](CODE_OF_CONDUCT.md) 行为准则。请确保您的互动是友善、尊重和包容的。

## 如何贡献

### 🐛 报告 Bug

1. 搜索 [Issues](https://github.com/你的用户名/ga-feishu-bot/issues) 确认是否已存在
2. 创建新 Issue，使用 Bug Report 模板
3. 提供：
   - 手机型号和 Android 版本
   - GA App 版本
   - 完整的错误日志
   - 复现步骤

### 💡 提交功能建议

1. 先开 Issue 讨论，避免做无用功
2. 清晰描述使用场景和期望行为
3. 如果可能，提供实现思路

### 🔧 提交代码

1. **Fork** 本仓库
2. 创建特性分支: `git checkout -b feature/your-feature`
3. **保持小提交**，每个提交完成一个逻辑变更
4. 确保代码风格一致（遵循 PEP 8）
5. 提交 Pull Request 到 `main` 分支

### 开发注意事项

- **不要提交真实密钥！** `.gitignore` 已配置，提交前双重确认
- **纯 Python 兼容** — 运行环境是 Chaquopy，不支持 C 扩展
- **路径无关** — 不要引入硬编码路径，使用 `os.path` 动态检测
- **测试** — 提交前确保 `start()` / `stop()` / `status()` 可用

## 代码规范

- Python 3.10+
- 遵循 [PEP 8](https://pep8.org/)
- 函数/类加 docstring
- 类型注解（可选但推荐）

## 发布流程

维护者执行：

1. 更新 `CHANGELOG.md`
2. 创建 Git Tag: `git tag v1.x.x`
3. 构建部署包: `tar -czf ga-feishu-bot-1.x.x.tar.gz ga-feishu-bot/`
4. 创建 GitHub Release，附上 tar.gz

## 问题求助

如有问题，先看 [README](README.md#-故障排查) 的故障排查章节。
