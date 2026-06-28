# 安全政策 / Security Policy

## 支持的版本 / Supported Versions

| 版本 / Version | 支持状态 / Support Status |
|---------------|--------------------------|
| 2.x (latest) | ✅ 积极维护 / Actively maintained |
| < 2.0 | ❌ 不再支持 / No longer supported |

---

## 安全态势 / Security Posture

SonettoHere 是一个**单用户本地部署**的 AI Agent 应用。当前安全措施：

- **网络层**：后端绑定 `127.0.0.1`，仅本机可访问
- **API 认证**：全局 `AuthMiddleware`，需 `X-Sonetto-Token` 请求头或 `?token=` 查询参数
- **CORS**：仅限本地来源（`localhost:5173` / `:8000`）
- **拒止锚**：`api/data/` 目录自动放置 `SonettoBlocker` 标记，防止 Agent 工具访问配置数据
- **凭据存储**：API Key 存储在 `providers.yaml`（已 gitignored）

> **注意**：本项目为个人/本地使用设计，**不包含多用户认证、权限控制或审计日志**。不建议暴露到公网。

SonettoHere is a **single-user local deployment** AI Agent application. Current security measures:

- **Network layer**: Backend bound to `127.0.0.1`, localhost only
- **API authentication**: Global `AuthMiddleware` requiring `X-Sonetto-Token` header or `?token=` query parameter
- **CORS**: Restricted to local origins (`localhost:5173` / `:8000`)
- **Denial anchors**: `SonettoBlocker` marker auto-placed in `api/data/` to prevent Agent tools from accessing config data
- **Credential storage**: API keys stored in `providers.yaml` (gitignored)

> **Note**: This project is designed for personal/local use. It does **not** include multi-user auth, access control, or audit logging. Exposure to public networks is **not recommended**.

---

## 报告漏洞 / Reporting a Vulnerability

### 我们鼓励的 / We encourage

如果您发现安全相关问题，请通过以下渠道之一报告：

If you discover a security-related issue, please report it through one of the following channels:

1. **GitHub 私有报告 / GitHub Private Report**
   - 访问本项目仓库的 [Security Advisories](https://github.com/Miso2233/SonettoHere/security/advisories) 页面
   - 点击 "New draft advisory" 创建私有报告
   - Visit the [Security Advisories](https://github.com/Miso2233/SonettoHere/security/advisories) page and click "New draft advisory"

2. **Issues（非敏感问题 / Non-sensitive issues）**
   - 对于不涉及敏感信息的问题，可直接创建 [Issue](https://github.com/Miso2233/SonettoHere/issues/new)
   - For non-sensitive issues, feel free to open an [Issue](https://github.com/Miso2233/SonettoHere/issues/new)

### 预期响应时间 / Expected Response Time

| 时间窗口 / Timeframe | 响应 / Response |
|---------------------|----------------|
| 48 小时内 / Within 48 hours | 确认收到 / Acknowledgment |
| 7 天内 / Within 7 days | 修复或缓解计划 / Fix or mitigation plan |
| 30 天内 / Within 30 days | 发布修复版本 / Release fix (if applicable) |

### 漏洞评估标准 / Vulnerability Assessment

| 级别 / Severity | 描述 / Description |
|----------------|-------------------|
| 🔴 严重 / Critical | 远程代码执行、未经授权的数据泄露 / RCE, unauthorized data breach |
| 🟡 高 / High | 认证绕过、敏感文件读取 / Auth bypass, sensitive file read |
| 🟢 中 / Moderate | CSRF、路径遍历（有限制）/ CSRF, path traversal (limited) |
| ⚪ 低 / Low | 信息泄露、日志中包含敏感数据 / Info disclosure, sensitive data in logs |

### 漏洞处理流程 / Disclosure Process

1. 报告者创建私有安全通告 / Reporter creates a private security advisory
2. 维护者确认问题并评估影响 / Maintainer confirms and assesses impact
3. 修复开发并在私有分支中测试 / Fix developed and tested in private branch
4. 发布修复版本并公开通告（可选）/ Release fix and public disclosure (optional)

我们不设赏金计划。感谢您的贡献！

We do not operate a bug bounty program. Thank you for your contribution!

---

## 安全依从 / Security Checklist

- [x] `127.0.0.1` binding — 防止远程直接访问
- [x] API Token authentication — 防止未授权 API 调用
- [x] CORS restriction — 防止跨域劫持
- [x] Secret files gitignored — 防止凭据意外提交
- [x] SonettoBlocker on config directories — 防止 Agent 自篡改配置
- [x] CodeQL scanning — 代码静态分析
- [x] Gitleaks scanning — 密钥泄露检测（CI 自动运行）
- [ ] Encryption at rest — API Key 文件加密（设计决策：单用户本地部署场景未启用）
- [ ] Audit logging — 所有 API 请求审计日志（规划中）
