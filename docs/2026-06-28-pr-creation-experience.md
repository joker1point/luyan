# 2026-06-28 PR 创建经验总结

## 任务背景

为 GitHub 仓库 `https://github.com/Shirotori0/CharacterSeed` 创建 Pull Request，包含本地所有未提交的代码改动。

## 完成的工作

### 1. Git 状态检查
- 确认 `CharacterSeed/` 是独立的 Git 仓库（非外层 luyan 目录）
- 本地分支 `main` 落后远程 2 个提交（`7e836cf 更新README`, `3ede3d3 代号1.6版本迭代已完成`）
- 本地有大量未跟踪文件和已修改文件

### 2. 同步远程最新代码
- 创建特性分支 `feature/comprehensive-enhancements`
- Stash 本地改动 → 拉取远程最新 → Pop stash 恢复

### 3. 解决合并冲突
- `.pyc` 文件冲突：直接删除（已在 `.gitignore` 中排除）
- `frontend/api_client.py` 和 `frontend/app.py` 冲突：选择删除（前端已迁移到 React/Vite）
- 源代码冲突（README.md, main.py, models.py 等）：使用 `--theirs` 保留 stash 中的本地版本

### 4. 提交内容
- 143 个文件变更，30333 行新增，7233 行删除
- 包含：Jiwen 情绪引擎、世界引擎、记忆系统、API 路由重构、前端 React 迁移、19 个测试文件、文档和路演材料

### 5. 推送和 PR 创建
- 推送失败：当前 GitHub 凭据 (`joker1point`) 无权限推送到 `Shirotori0/CharacterSeed`
- Fork 方案失败：MCP GitHub 工具认证失败（Bad credentials）

## 踩坑与解决方案

### 坑1：Stash Pop 合并冲突
**问题**：`git stash pop` 后出现 13 个 `UU`（both modified）和 2 个 `UD`（deleted by them）冲突  
**原因**：远程 2 个提交修改了与本地相同的文件  
**解决**：
- `.pyc` 文件：`git rm --cached` 批量删除
- 源代码：`git checkout --theirs` 保留 stash 版本（本地开发版本）
- `frontend/*.py`：`git rm -f` 删除（前端已迁移到 React）

### 坑2：嵌入 Git 仓库问题
**问题**：`web/react-vite` 有自己的 `.git` 目录，被当作 submodule 添加  
**原因**：`git add web/react-vite/` 检测到嵌套仓库  
**解决**：`git rm -f --cached web/react-vite` 移除缓存，然后逐个添加文件  
**建议**：删除 `web/react-vite/.git` 目录，或将其转为真正的 submodule

### 坑3：GitHub 权限不足
**问题**：`remote: Permission to Shirotori0/CharacterSeed.git denied to joker1point`  
**原因**：当前 Git 凭据是 `joker1point`，不是仓库所有者  
**解决方案**：
1. Fork 仓库到 `joker1point/CharacterSeed`，推送到 fork，再向原仓库发起 PR
2. 或配置 `Shirotori0` 账号的凭据
3. 或让仓库所有者添加 `joker1point` 为 collaborator

### 坑4：MCP GitHub 工具认证失败
**问题**：`fork_repository` 调用返回 `Authentication Failed: Bad credentials`  
**原因**：MCP GitHub 工具配置的 token 无效或过期  
**解决**：需要更新 MCP GitHub 的认证 token，或使用 `gh` CLI 手动操作

## 技术要点

### Git Stash 工作流
```bash
# 1. 暂存本地改动
git stash

# 2. 拉取远程最新
git pull --ff-only origin main

# 3. 恢复本地改动
git stash pop

# 4. 解决冲突后提交
git add .
git commit -m "feat: ..."
```

### 处理嵌套 Git 仓库
```bash
# 移除嵌套仓库缓存
git rm -f --cached path/to/nested/repo

# 逐个添加文件（排除 .git）
Get-ChildItem -Path path/to/repo -Recurse -File |
  Where-Object { $_.FullName -notmatch '\\.git\\' } |
  ForEach-Object { $_.FullName.Replace($basePath, '') } |
  git add --pathspec-from-file=-
```

### Fork 工作流（推荐）
```bash
# 1. Fork 原仓库（通过 GitHub UI 或 gh CLI）
gh repo fork Shirotori0/CharacterSeed

# 2. 添加 fork 为远程
git remote add fork https://github.com/YOUR_USERNAME/CharacterSeed.git

# 3. 推送到 fork
git push -u fork feature/comprehensive-enhancements

# 4. 创建 PR（从 fork 向原仓库）
gh pr create --repo Shirotori0/CharacterSeed --head YOUR_USERNAME:feature/comprehensive-enhancements --base main
```

## 未完成项

1. **推送代码到远程**：需要解决 GitHub 权限问题
   - 方案 A：Fork 到 `joker1point/CharacterSeed` 后推送
   - 方案 B：配置 `Shirotori0` 账号凭据
   - 方案 C：使用 `gh auth login` 重新认证

2. **创建 Pull Request**：推送成功后创建 PR
   - 标题：`feat: 全面增强 CharacterSeed 系统功能`
   - 描述：包含 Jiwen 情绪引擎、世界引擎、记忆系统、前端 React 迁移等

3. **更新 MCP GitHub 认证**：修复 Bad credentials 问题

## 验证清单

- [x] 本地 main 分支已同步到最新（`7e836cf`）
- [x] 特性分支 `feature/comprehensive-enhancements` 已创建
- [x] 所有本地改动已提交（143 文件，30333 行新增）
- [x] 合并冲突已解决（保留本地开发版本）
- [ ] 推送到远程仓库（权限问题待解决）
- [ ] 创建 Pull Request（待推送成功后执行）

## 经验总结

1. **定期同步远程**：本地开发前应先 `git pull` 避免大量冲突
2. **使用 Fork 工作流**：对于非自己的仓库，先 fork 再开发，避免权限问题
3. **清理 `.pyc` 文件**：已在 `.gitignore` 中排除，但历史文件需手动删除
4. **嵌套仓库处理**：删除子目录的 `.git` 或使用 submodule 管理
5. **GitHub 凭据管理**：使用 `gh auth login` 或 Git Credential Manager 统一管理
6. **MCP 工具认证**：定期检查 token 有效性，避免过期导致操作失败
