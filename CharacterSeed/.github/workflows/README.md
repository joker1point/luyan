# GitHub Actions 自动化 PR 指南

## 工作流概览

本项目配置了两个 GitHub Actions 工作流：

### 1. `auto-pr.yml` - 自动 PR 创建
- **触发条件**: 推送到 `feature/*` 分支
- **功能**: 自动运行测试并创建 PR 到 main 分支

### 2. `lint-test.yml` - 代码检查与测试
- **触发条件**: push 到 main 或 PR
- **功能**: 运行 lint 检查、格式化检查、多 Python 版本测试

---

## 使用方法

### 方式一：通过 Git Push 触发

```bash
# 1. 在 feature 分支上开发
git checkout -b feature/your-feature

# 2. 提交代码
git add .
git commit -m "feat: 新功能"

# 3. 推送到远程
git push origin feature/your-feature

# 4. GitHub Actions 自动创建 PR
```

### 方式二：手动触发

在 GitHub 仓库页面：
1. 点击 **Actions** 选项卡
2. 选择 `Auto PR - Multi-LLM Support`
3. 点击 **Run workflow**

### 方式三：使用本地脚本

```bash
# 给脚本执行权限
chmod +x scripts/auto-pr.sh

# 运行脚本
bash scripts/auto-pr.sh "提交信息" "PR 标题"
```

---

## 权限配置

### 必需的 GitHub Secrets

在仓库 Settings → Secrets 中添加：

| Secret 名称 | 说明 |
|------------|------|
| `GITHUB_TOKEN` | 自动提供（无需配置） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（用于真实测试） |

### 启用 Actions 权限

仓库 Settings → Actions → General：
- ✅ Allow all actions and reusable workflows
- ✅ Read and write permissions

---

## 工作流状态

查看工作流运行状态：
- 访问仓库的 **Actions** 选项卡
- 点击具体的工作流查看详细日志

---

## 故障排除

### PR 创建失败

**问题**: `403 Permission denied`

**解决**:
1. 确认已在 Settings → Actions 中启用读写权限
2. 确认 GITHUB_TOKEN 有足够权限
3. 重新生成 Personal Access Token

### 测试失败

**问题**: 测试运行出错

**解决**:
1. 查看 Actions 日志获取详细错误
2. 在本地运行 `pytest` 调试
3. 检查依赖版本是否正确

---

## 最佳实践

1. **分支命名**: 使用 `feature/`、`fix/`、`docs/` 前缀
2. **提交信息**: 使用 conventional commits 规范
3. **PR 描述**: 详细说明变更内容和测试情况
4. **代码审查**: 等待 CI 通过后再请求 review

---

*配置完成后，所有推送到 feature 分支的代码都会自动运行测试并创建 PR。*
