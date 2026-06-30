"""
CharacterSeed API 入口。

启动方式：
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

本文件职责（保持精简）：
  1) 加载 .env（先于其他 import）
  2) 创建 FastAPI app
  3) CORS 中间件（开发环境多端口友好）
  4) 启动/关闭事件（DB 迁移、LoggingService worker）
  5) 全局异常处理（LoggingService 兜底）
  6) 注册所有业务 router（从 backend.api/ 拆分）
  7) 静态文件挂载（react-vite/dist > web/dist）
  8) 根路径（智能返回 SPA 或 JSON）

业务实现已拆分到：
  backend/api/character_router.py       角色 CRUD + 描述润色
  backend/api/chat_router.py            对话（同步 + 流式 SSE）
  backend/api/session_router.py         ChatSession 会话管理
  backend/api/growth_router.py          角色成长（Growth LLM 管线）
  backend/api/event_router.py           事件推进 + 时间迭代
  backend/api/character_memory_router.py  角色记忆/对话/成长读路径
  backend/api/performance_router.py     缓存统计与失效
  backend/api/llm_router.py             LLM 设置 + API 测试
  backend/api/logs_router.py            日志系统
  backend/api/memory_router.py          增强记忆系统（ContextManager 等）
"""
from __future__ import annotations
import json
import logging
import traceback
from pathlib import Path

# 关键：在所有其他导入前加载 .env
# 这样 os.environ.get("AGNES_API_KEY") 等就能拿到 .env 中的值
# 作为 LLM settings store 的兜底
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[startup] 已加载 .env 文件", flush=True)
except ImportError:
    pass
except Exception as e:
    print(f"[startup] 加载 .env 失败: {e}", flush=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from backend.database import engine, Base
from backend.services import db_migration
from backend.services.logging_service import LoggingService

# 业务路由（拆分自原 1218 行单文件）
from backend.api import (
    character_router,
    chat_router,
    session_router,
    growth_router,
    event_router,
    character_memory_router,
    performance_router,
    llm_router,
    logs_router,
    jiwen_router,
    world_router,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ==================== FastAPI 应用 ====================
app = FastAPI(
    title="CharacterSeed API",
    description="AI NPC生命模拟系统",
    version="0.1.0",
)


# ==================== Gzip 压缩中间件 ====================
# 体积减 60-70%。最小压缩 500 字节（小于此值无收益且浪费 CPU）
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


# ==================== 静态资源强缓存中间件 ====================
# Vite 构建产物文件名带 hash（如 index-CXq80mmT.js），内容不可变
# 设 1 年强缓存，浏览器刷新会换文件名 → 零网络成本
class StaticCacheControlMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_age: int = 31536000) -> None:
        super().__init__(app)
        self.max_age = max_age

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        # /assets/* 下是带 hash 的 Vite chunk，永久缓存
        if path.startswith("/assets/") or path.startswith("/_next/") or path.startswith("/static/"):
            response.headers["Cache-Control"] = f"public, max-age={self.max_age}, immutable"
        # index.html 必须每次校验（spa fallback 也走这里）
        elif path == "/" or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

app.add_middleware(StaticCacheControlMiddleware, max_age=31536000)

# ==================== CORS ====================
# 开发环境允许多 Vite 端口（5173-5175）和 8000 直连
# 启用后前端可选择不走 Vite 代理，避免 Vite http-proxy 默认缓冲导致 SSE 失效
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ==================== 全局异常处理 ====================
def _log_uncaught_exception(request: Request, exc: Exception) -> None:
    """
    把未处理异常写入日志系统（不影响响应返回）。
    等级：5xx → CRITICAL，4xx → WARNING，其他 → ERROR。
    类型推断：数据库关键字 / openai+httpx+api 路径 → 对应 err_type。
    永不让日志系统影响主响应（多层 try-except 兜底）。
    """
    try:
        try:
            path = request.url.path
        except Exception:
            path = "<unknown>"
        try:
            method = request.method
        except Exception:
            method = "<unknown>"
        try:
            params = json.dumps(
                dict(request.query_params), ensure_ascii=False,
            )[:1000]
        except Exception:
            params = ""
        status = getattr(exc, "status_code", 500) or 500
        if status >= 500:
            level, err_type = "CRITICAL", "backend"
        elif status >= 400:
            level, err_type = "WARNING", "backend"
        else:
            level, err_type = "ERROR", "internal"
        msg = str(exc) or exc.__class__.__name__
        if "database" in msg.lower() or "sqlalchemy" in msg.lower() or "sqlite" in msg.lower():
            err_type = "database"
        elif "openai" in msg.lower() or "httpx" in msg.lower() or "api" in path.lower():
            err_type = "third_party"
        try:
            LoggingService.instance().record(
                level=level,
                error_type=err_type,
                source=f"{method} {path}",
                message=msg[:500],
                stack_trace=traceback.format_exc()[:5000],
                request_path=path,
                request_params=params,
                user_id="-",
                env_info=json.dumps({"status": status}, ensure_ascii=False),
            )
        except Exception:
            pass
    except Exception:
        pass


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """全局兜底：记 ERROR/CRITICAL 后返回 5xx + 简洁 detail。"""
    _log_uncaught_exception(request, exc)
    from fastapi.exceptions import HTTPException as FastHTTPException
    if isinstance(exc, FastHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": f"{exc.__class__.__name__}: {str(exc)[:200]}"},
    )

# ==================== 启动 / 关闭 ====================
@app.on_event("startup")
def startup_event():
    """应用启动时执行。"""
    # 0) 初始化日志系统单例（启动后台 worker 线程）
    try:
        LoggingService.instance()
    except Exception as e:
        print(f"[startup] LoggingService 启动失败: {e}", flush=True)
    # 1) 确保所有表存在
    Base.metadata.create_all(bind=engine)
    # 2) 执行 schema 迁移（幂等）
    try:
        history = db_migration.run_all_migrations(engine)
        for h in history:
            if h.get("backfilled", 0) > 0 or h.get("added_column"):
                logger.info("[migration] %s", h)
    except Exception as e:
        logger.exception("迁移失败: %s", e)
    # 2.5) 启动 jiwen 后台 tick 调度器（CRITICAL_MODULE — 默认 5 分钟一次）
    # 降级不会停止调度器，只会降低 tick 频率。详见 jiwen_scheduler.set_mode
    try:
        from backend.jiwen import start_scheduler, get_scheduler
        start_scheduler(
            interval_seconds=300,
            degraded_interval=900,   # 降级间隔 15 分钟
            recovery_interval=300,   # 恢复间隔 5 分钟
            mode='normal',
        )
        # 验证启动成功（CRITICAL_MODULE 必须可用）
        sched = get_scheduler()
        assert sched._is_running, "jiwen scheduler 启动失败"
        assert sched.is_critical, "jiwen scheduler 必须是关键模块"
        logger.info(
            "[CRITICAL_MODULE] jiwen scheduler 已启动，"
            "interval=%ds, degraded=%ds, recovery=%ds",
            sched.interval_seconds,
            sched.degraded_interval_seconds,
            sched.recovery_interval_seconds,
        )
    except Exception as e:
        # CRITICAL_MODULE 启动失败：记录后仍允许服务运行（避免完全宕机）
        # 但会被 /api/health/jiwen 暴露为 unhealthy
        logger.exception("jiwen scheduler 启动失败（CRITICAL_MODULE）: %s", e)
    # 2.6) v009: 初始化头像存储目录 + 预热 AvatarGenerationService 单例
    try:
        from backend.services.avatar_generation_service import (
            STORAGE_ROOT, AvatarGenerationService,
        )
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        logger.info("[startup] 头像存储目录已就绪: %s", STORAGE_ROOT)
        # 预热：避免首次生成时才创建 client 失败（Agnes key 缺失时立即暴露）
        try:
            AvatarGenerationService.instance()
            logger.info("[startup] AvatarGenerationService 单例已初始化")
        except Exception as e:
            logger.warning(
                "AvatarGenerationService 预热失败（AGNES_API_KEY 缺失？头像功能将不可用）: %s",
                e,
            )
    except Exception as e:
        logger.warning("初始化头像目录失败: %s", e)
    # 3) 记录一条 INFO 启动事件（自监控）
    try:
        LoggingService.instance().record(
            level="INFO",
            error_type="internal",
            source="app:startup",
            message="CharacterSeed API 启动成功",
            request_path="/",
            env_info=json.dumps({"version": app.version}, ensure_ascii=False),
        )
    except Exception:
        pass
    print("=" * 50)
    print("CharacterSeed API 启动成功！")
    print("访问 http://localhost:8000/docs 查看API文档")
    print("=" * 50)


@app.on_event("shutdown")
def shutdown_event():
    """应用关闭：优雅停止后台服务。"""
    try:
        LoggingService.instance().shutdown(timeout=2.0)
    except Exception:
        pass
    try:
        from backend.jiwen import stop_scheduler
        stop_scheduler()
    except Exception:
        pass

# ==================== 业务路由注册 ====================
# 顺序：先注册具体的（/api/*），再注册 SPA fallback
app.include_router(character_router.router)
app.include_router(chat_router.router)
app.include_router(session_router.router)
app.include_router(growth_router.router)
app.include_router(event_router.router)
app.include_router(character_memory_router.router)
app.include_router(performance_router.router)
app.include_router(llm_router.router)
# 日志路由（含全量日志管理 + 告警配置）
app.include_router(logs_router.router)
# jiwen 情绪引擎（状态/触发器/调度/记忆衰减）
app.include_router(jiwen_router.router)
# world 四要素（ADR-009 / Phase 2：世界/地点/物品/关系/天气/上下文）
app.include_router(world_router.router)

# ==================== 根路径（智能 SPA / JSON）====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REACT_DIST = _PROJECT_ROOT / "web" / "react-vite" / "dist"
_VUE_DIST = _PROJECT_ROOT / "web" / "dist"
_WEB_DIST = _REACT_DIST if _REACT_DIST.exists() else _VUE_DIST
if _WEB_DIST.exists():
    print(f"[startup] 静态前端目录: {_WEB_DIST} ({'react-vite' if _WEB_DIST == _REACT_DIST else 'vue'})", flush=True)


@app.get("/")
def root(request: Request):
    """
    智能根路径：
      - 浏览器请求（Accept: text/html）→ 返回 SPA index.html
      - API 请求（Accept: application/json 或 */*）→ 返回 JSON 元信息
    """
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept and _WEB_DIST.exists():
        return FileResponse(str(_WEB_DIST / "index.html"))
    return {
        "message": "CharacterSeed API is running!",
        "docs": "http://localhost:8000/docs",
        "version": "0.1.0",
    }


@app.get("/api/health/jiwen")
def jiwen_health():
    """
    jiwen 关键模块健康检查。
    降级不会让 jiwen 不健康，但停止或异常会标记为 unhealthy。
    """
    from backend.jiwen import get_scheduler
    try:
        sched = get_scheduler()
        s = sched.status()
        # 健康：调度器在跑 + is_critical=True
        healthy = s.get("is_running", False) and s.get("is_critical", False)
        return {
            "module": "jiwen",
            "is_critical": s.get("is_critical", False),
            "status": "healthy" if healthy else "unhealthy",
            "is_running": s.get("is_running", False),
            "mode": s.get("mode", "normal"),
            "interval_seconds": s.get("interval_seconds"),
            "degraded_interval_seconds": s.get("degraded_interval_seconds"),
            "recovery_interval_seconds": s.get("recovery_interval_seconds"),
            "last_run_at": s.get("last_run_at"),
        }
    except Exception as e:
        return {
            "module": "jiwen",
            "status": "unhealthy",
            "error": str(e),
        }


# ==================== 前端静态文件挂载 ====================
# 优先级低于 /api 路由（已注册的 API 路由优先匹配）
# v009: 角色头像存储目录（与 react-vite/web dist 同级；StaticFiles 直读）
_AVATAR_DIR = _PROJECT_ROOT / "usercontext" / "avatars"
_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/avatars",
    StaticFiles(directory=str(_AVATAR_DIR)),
    name="avatars",
)

if _WEB_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_WEB_DIST / "assets")),
        name="web-assets",
    )

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """SPA fallback：未匹配的路径都返回 index.html。"""
        candidate = _WEB_DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_WEB_DIST / "index.html"))
else:
    print(f"[startup] 未检测到 {_WEB_DIST}，跳过前端静态文件挂载（开发模式）", flush=True)
