"""
记忆管理 API 路由

提供三层记忆的 CRUD 和检索接口：
- 短期记忆状态查询
- 长期记忆添加/检索
- 知识库文档管理
- 记忆统计信息
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.crud import character as character_crud
from backend.modules.enhanced_interaction import EnhancedInteractionPipeline
from backend.memory import ContextManager

router = APIRouter(prefix="/api/memory", tags=["memory"])

# 全局增强版管线实例（生产环境应使用依赖注入）
enhanced_pipeline = EnhancedInteractionPipeline(enable_memory=True)


# ==================== Pydantic Schemas ====================

class MemoryAddRequest(BaseModel):
    """添加记忆请求"""
    character_id: int = Field(..., description="角色 ID")
    content: str = Field(..., description="记忆内容")
    user_id: Optional[str] = Field(None, description="用户 ID")
    metadata: Optional[dict] = Field(None, description="元数据")


class MemorySearchRequest(BaseModel):
    """记忆搜索请求"""
    character_id: int = Field(..., description="角色 ID")
    query: str = Field(..., description="搜索查询")
    user_id: Optional[str] = Field(None, description="用户 ID")
    limit: int = Field(5, description="返回数量", ge=1, le=50)


class KnowledgeAddRequest(BaseModel):
    """知识库添加请求"""
    character_id: int = Field(..., description="角色 ID")
    text: str = Field(..., description="知识文本")
    source: Optional[str] = Field(None, description="来源标识")


class KnowledgeSearchRequest(BaseModel):
    """知识库搜索请求"""
    character_id: int = Field(..., description="角色 ID")
    query: str = Field(..., description="搜索查询")
    limit: int = Field(3, description="返回数量", ge=1, le=20)


class ContextBuildRequest(BaseModel):
    """上下文构建请求"""
    character_id: int = Field(..., description="角色 ID")
    query: str = Field(..., description="当前查询")
    user_id: Optional[str] = Field(None, description="用户 ID")
    include_short_term: bool = Field(True, description="包含短期记忆")
    include_long_term: bool = Field(True, description="包含长期记忆")
    include_knowledge: bool = Field(True, description="包含知识库")
    template: str = Field("default", description="格式化模板: minimal/default/detailed")


# ==================== Memory Endpoints ====================

@router.get("/stats/{character_id}")
def get_memory_stats(
    character_id: int,
    user_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    获取角色记忆系统统计信息
    
    返回:
    - 短期记忆数量
    - 长期记忆数量
    - Token 预算
    - 检索限制等
    """
    # 验证角色存在
    character = character_crud.get_character(db, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    stats = enhanced_pipeline.get_memory_stats(character_id, user_id)
    if not stats:
        raise HTTPException(status_code=503, detail="记忆系统未启用")
    
    return {
        "character_id": character_id,
        "character_name": character.name,
        "memory_stats": stats
    }


@router.post("/add")
def add_memory(request: MemoryAddRequest, db: Session = Depends(get_db)):
    """
    添加一条长期记忆
    
    用于手动添加重要信息到长期记忆（如角色设定、用户偏好等）
    """
    # 验证角色存在
    character = character_crud.get_character(db, request.character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 获取或创建上下文管理器
    cm = enhanced_pipeline._get_context_manager(request.character_id, request.user_id)
    if not cm:
        raise HTTPException(status_code=503, detail="记忆系统未启用")
    
    # 添加记忆
    memory_id = cm.long_term.add(
        request.content,
        metadata=request.metadata or {}
    )
    
    return {
        "success": True,
        "memory_id": memory_id,
        "content": request.content,
        "character_id": request.character_id
    }


@router.post("/search")
def search_memories(request: MemorySearchRequest, db: Session = Depends(get_db)):
    """
    语义检索长期记忆
    
    基于相关性而非时间检索，返回最相关的 N 条记忆
    """
    # 验证角色存在
    character = character_crud.get_character(db, request.character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 执行检索
    results = enhanced_pipeline.search_memories(
        character_id=request.character_id,
        query=request.query,
        user_id=request.user_id,
        limit=request.limit
    )
    
    return {
        "query": request.query,
        "character_id": request.character_id,
        "count": len(results),
        "memories": results
    }


# ==================== Knowledge Base Endpoints ====================

@router.post("/knowledge/add")
async def add_knowledge(request: KnowledgeAddRequest, db: Session = Depends(get_db)):
    """
    添加知识到知识库
    
    文本会自动分块、向量化、构建知识图谱
    """
    # 验证角色存在
    character = character_crud.get_character(db, request.character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 获取知识库实例
    cm = enhanced_pipeline._get_context_manager(request.character_id)
    if not cm:
        raise HTTPException(status_code=503, detail="记忆系统未启用")
    
    success = await cm.knowledge.add_text(
        request.text,
        source=request.source
    )
    
    return {
        "success": success,
        "character_id": request.character_id,
        "source": request.source,
        "length": len(request.text)
    }


@router.post("/knowledge/upload")
async def upload_knowledge_document(
    character_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    上传文档到知识库
    
    支持 .txt, .md, .pdf 等格式（取决于 cognee 支持范围）
    """
    # 验证角色存在
    character = character_crud.get_character(db, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 保存上传文件
    import os
    import tempfile
    
    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=os.path.splitext(file.filename)[1]
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # 添加到知识库
        cm = enhanced_pipeline._get_context_manager(character_id)
        if not cm:
            raise HTTPException(status_code=503, detail="记忆系统未启用")
        
        success = await cm.knowledge.add_document(tmp_path)
        
        return {
            "success": success,
            "filename": file.filename,
            "character_id": character_id,
            "size": len(content)
        }
    finally:
        # 清理临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/knowledge/search")
async def search_knowledge(
    request: KnowledgeSearchRequest,
    db: Session = Depends(get_db)
):
    """
    语义检索知识库
    
    返回最相关的 N 条知识条目
    """
    # 验证角色存在
    character = character_crud.get_character(db, request.character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 执行检索
    cm = enhanced_pipeline._get_context_manager(request.character_id)
    if not cm:
        raise HTTPException(status_code=503, detail="记忆系统未启用")
    
    results = await cm.knowledge.search(request.query, limit=request.limit)
    
    return {
        "query": request.query,
        "character_id": request.character_id,
        "count": len(results),
        "results": results
    }


# ==================== Context Endpoints ====================

@router.post("/context/build")
def build_context(request: ContextBuildRequest, db: Session = Depends(get_db)):
    """
    构建完整的 LLM 上下文
    
    按优先级组装：短期记忆 → 长期记忆 → 知识库
    支持多种格式化模板
    """
    # 验证角色存在
    character = character_crud.get_character(db, request.character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 获取上下文管理器
    cm = enhanced_pipeline._get_context_manager(request.character_id, request.user_id)
    if not cm:
        raise HTTPException(status_code=503, detail="记忆系统未启用")
    
    # 构建上下文
    context = cm.build_context(
        current_query=request.query,
        include_short_term=request.include_short_term,
        include_long_term=request.include_long_term,
        include_knowledge=request.include_knowledge
    )
    
    # 格式化
    formatted = cm.format_for_prompt(context, template=request.template)
    
    return {
        "character_id": request.character_id,
        "query": request.query,
        "context": context,
        "formatted_prompt": formatted,
        "template": request.template
    }


# ==================== Cache Management ====================

@router.post("/cache/clear")
def clear_context_cache(character_id: Optional[int] = None):
    """
    清空上下文缓存
    
    Args:
        character_id: 指定角色清空（None 则清空所有）
    """
    enhanced_pipeline.clear_context_cache(character_id)
    return {
        "success": True,
        "cleared": character_id or "all"
    }


# ==================== Health Check ====================

@router.get("/health")
def memory_health_check():
    """
    记忆系统健康检查
    
    返回各模块可用性
    """
    from backend.memory.short_term import ShortTermMemory
    from backend.memory.long_term import LongTermMemory, MEM0_AVAILABLE
    from backend.memory.knowledge_base import KnowledgeBase, COGNEE_AVAILABLE
    
    return {
        "status": "healthy",
        "modules": {
            "short_term": {
                "available": True,
                "engine": "LangChain"
            },
            "long_term": {
                "available": MEM0_AVAILABLE,
                "engine": "Mem0" if MEM0_AVAILABLE else "Local JSON (fallback)"
            },
            "knowledge_base": {
                "available": COGNEE_AVAILABLE,
                "engine": "Cognee" if COGNEE_AVAILABLE else "Local File (fallback)"
            }
        }
    }
