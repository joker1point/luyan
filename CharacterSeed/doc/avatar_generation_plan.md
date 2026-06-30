# 角色自画像自动生成系统 — 完整实现计划

> 严重依赖 Agnes AI 平台
> 生图模型：agnes-image-2.1-flash  |  生视频模型：agnes-video-v2.0
> API 端点：<https://apihub.agnes-ai.com/v1/images/generations> 和 /v1/videos

***

## 审计发现（前置条件）

|  #  | 发现                                         | 影响                         | 处理方式                     |
| :-: | ------------------------------------------ | -------------------------- | ------------------------ |
|  1  | Character 模型**没有** **`appearance`** **字段** | 生图 prompt 缺少外貌描述，头像与角色设定脱节 | 新增字段 + 迁移 v009           |
|  2  | creation.txt 提示词**不要求 LLM 生成外貌**           | 新建角色永远不会有 appearance 数据    | 增加第 15 个字段 `appearance`  |
|  3  | 项目**没有任何图像 API 集成代码**                      | 需从零构建 Agnes 客户端            | 新建 `agnes_client.py`     |
|  4  | main.py **没有** **`/avatars`** **静态挂载**     | 生成的图片无法通过 HTTP 访问          | 新增 StaticFiles 挂载        |
|  5  | 前端只用 CSS 纯色圆做头像                            | 无图片显示/上传组件                 | 新建 `CharacterAvatar.vue` |
|  6  | `.env` 已有 `AGNES_API_KEY`（L3）              | 无需额外配置，直接用                 | 复用现有 key                 |
|  7  | Agnes Image API 当前免费 ($0/张)                | 无成本压力                      | 可放心生成多张候选                |
|  8  | 视频 API 为异步模式（POST→GET video\_id）           | 需轮询或 webhook               | 见 Step 4                 |

***

## 完整实现步骤

### Step 0 — 前置条件：`appearance` 字段

> 这是头像生成系统的前置依赖。没有外貌描述，prompt 质量会严重下降。

#### 0.1 修改 Character 模型

**文件：** `backend/models.py` L38（`config` 字段之后）

```python
# v009: 外貌描述（JSON 格式，供画像/视频生成使用）
appearance = Column(Text, nullable=True)
```

字段示例值：

```json
{
  "height": "168cm",
  "build": "纤瘦",
  "hair_color": "银白色短发",
  "hair_style": "不对称刘海，右侧略长遮住半边眉",
  "eye_color": "琥珀色，猫瞳",
  "skin_tone": "冷白皮，微微透蓝血管",
  "clothing": "黑色高领战术毛衣 + 深灰工装裤 + 皮质手套",
  "accessories": "左耳一枚银环耳钉，右手腕戴数据手环",
  "distinctive_features": "左眼下有十字形小疤痕",
  "overall_impression": "冷峻、精干、带点疏离感但并非不可接近"
}
```

#### 0.2 修改 creation.txt 提示词

**文件：** `backend/prompts/creation.txt` — 在 L42（`short_term_goals`）之后插入第 15 个字段：

```
15. "appearance" - 外貌描述（JSON 对象，尽可能详细，包含以下字段）：
   - "height": 身高（字符串，如 "168cm", "约一米七"）
   - "build": 体型（字符串，如 "纤瘦", "魁梧", "中等"）
   - "hair_color": 发色（字符串，如 "银白色短发", "黑色长发及腰"）
   - "hair_style": 发型描述（字符串）
   - "eye_color": 瞳色（字符串，尽量有特色，如 "琥珀色猫瞳", "冰蓝色"）
   - "skin_tone": 肤色（字符串，如 "冷白皮", "小麦色", "古铜色"）
   - "clothing": 服装（字符串，如果世界设定有风格约束请遵循）
   - "accessories": 配饰（字符串，如 "无"、"银耳钉"、"机械义眼"）
   - "distinctive_features": 显著特征（字符串，如 "左眼下方十字疤痕"、"虎牙"）
   - "overall_impression": 整体印象（字符串，一句话概括气质）
```

#### 0.3 修改 CreationModule 解析逻辑

**文件：** `backend/modules/creation.py` — `parse_response()` 方法中新增 `appearance` 解析

在 `character_router.py` 的 `create_character` 端点（约 L69-73）新增：

```python
appearance_data = parsed.get("appearance")
char.appearance = json.dumps(appearance_data, ensure_ascii=False) if appearance_data else None
```

#### 0.4 数据库迁移 v009A

**文件：** `backend/services/db_migration.py` — 新增 `migrate_v009A_character_appearance()`

```python
def migrate_v009A_character_appearance(engine: Engine) -> dict:
    """迁移 v009A：给 characters 表新增 appearance 列（外貌描述 JSON）"""
    result = {"added_column": False}
    if not _sqlite_table_exists(engine, "characters"):
        return result
    cols = _sqlite_columns(engine, "characters")
    if "appearance" in cols:
        logger.debug("迁移 v009A: characters.appearance 已存在，跳过")
        return result
    logger.info("迁移 v009A: 添加 characters.appearance 列")
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE characters ADD COLUMN appearance TEXT"
        ))
    result["added_column"] = True
    return result
```

***

### Step 1 — Agnes 客户端封装

#### 1.1 新建文件

**文件：** `backend/services/agnes_client.py`（新建）

```python
"""
Agnes 多模态 API 客户端
=======================
封装 Agnes 图像生成 (agnes-image-2.1-flash) 和视频生成 (agnes-video-v2.0) API。

API 认证方式：Bearer Token（与文本 API 相同，复用 .env 中的 AGNES_API_KEY）
Base URL：https://apihub.agnes-ai.com/v1
"""

class AgnesImageClient:
    """Agnes 图像生成客户端"""
    
    BASE_URL = "https://apihub.agnes-ai.com/v1"
    IMAGE_ENDPOINT = "/images/generations"
    IMAGE_MODEL = "agnes-image-2.1-flash"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0),  # 图像生成可能较慢
        )
    
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",         # 1:1 正方形头像
        n: int = 4,                       # 生成 4 张候选
        response_format: str = "url",     # "url" 或 "b64_json"
        **extra_body,
    ) -> List[str]:
        """
        生成图像，返回 URL 列表。
        
        Agnes Image API 响应格式：
        {
          "created": 1710000000,
          "data": [
            {"url": "https://...", "b64_json": null},
            ...
          ]
        }
        
        注意：Agnes 可能不支持 n 参数直接生成多张。
        如果不支持，改为循环调用 4 次，每次用不同的 seed。
        """
        pass
    
    async def generate_single(
        self,
        prompt: str,
        size: str = "1024x1024",
        response_format: str = "url",
        seed: Optional[int] = None,
    ) -> str:
        """生成单张图像，返回 URL。内部使用，供 generate() 循环调用。"""
        pass


class AgnesVideoClient:
    """Agnes 视频生成客户端"""
    
    VIDEO_ENDPOINT = "/videos"
    VIDEO_MODEL = "agnes-video-v2.0"
    
    async def create_task(
        self,
        prompt: str,
        image_url: str,
        width: int = 768,
        height: int = 768,               # 1:1
        num_frames: int = 73,            # 8n+1, ~3s@24fps
        frame_rate: int = 24,
        seed: Optional[int] = None,
    ) -> str:
        """
        创建视频生成任务，返回 video_id。
        
        POST /v1/videos
        → {"video_id": "xxx", "status": "pending"}
        """
        pass
    
    async def get_result(self, video_id: str) -> Optional[str]:
        """
        查询任务结果，返回视频 URL（完成后）。
        
        GET /v1/videos/{video_id}
        → {"status": "completed", "url": "https://..."}
        → {"status": "pending"}
        → {"status": "failed", "error": "..."}
        """
        pass
    
    async def wait_for_completion(
        self,
        video_id: str,
        poll_interval: float = 5.0,
        max_wait: float = 300.0,
    ) -> Optional[str]:
        """轮询等待视频生成完成，返回 URL 或 None（超时/失败）。"""
        pass
```

#### 1.2 API 调用细节

| 参数       | 图像 API                        | 视频 API                                     |
| -------- | ----------------------------- | ------------------------------------------ |
| Endpoint | `POST /v1/images/generations` | `POST /v1/videos`                          |
| Model    | `agnes-image-2.1-flash`       | `agnes-video-v2.0`                         |
| 认证       | `Authorization: Bearer {key}` | 同                                          |
| 响应格式     | `{"data": [{"url": "..."}]}`  | `{"video_id": "...", "status": "pending"}` |
| 超时       | 120s                          | 创建 30s + 轮询最多 300s                         |
| 价格       | $0/张（当前免费）                    | 按 token 计费                                 |

***

### Step 2 — AvatarGenerationService

#### 2.1 新建文件

**文件：** `backend/services/avatar_generation_service.py`（新建）

```python
class AvatarGenerationService:
    """
    角色头像生成服务
    
    职责：
    1. 从 Character 对象构建生图 prompt
    2. 调用 AgnesImageClient 生成 4 张候选图
    3. 下载图片到本地存储
    4. 更新 Character 数据库记录
    5. (可选) 触发视频头像生成
    """
    
    STORAGE_PATH = "usercontext/avatars"
    STATIC_URL = "/avatars"
    
    def __init__(self):
        self.image_client = AgnesImageClient(api_key=settings.AGNES_API_KEY)
        self.video_client = AgnesVideoClient(api_key=settings.AGNES_API_KEY)
    
    # ──────────────────────────────
    # Prompt 构建
    # ──────────────────────────────
    def build_image_prompt(self, character: Character, style: str) -> str:
        """
        从角色数据构建生图 prompt。
        
        优先级：
        1. appearance JSON → 最精确的外貌数据
        2. description → 用户原始输入，可能包含外貌信息
        3. personality + world_setting → 气质 + 世界背景（兜底）
        """
        pass
    
    def _format_appearance(self, appearance_json: dict) -> str:
        """将 appearance JSON 转为自然语言片段"""
        pass
    
    # ──────────────────────────────
    # 图像生成
    # ──────────────────────────────
    async def generate_avatars(
        self,
        character_id: int,
        style: str = "anime",
        expression: str = "neutral",
        background: str = "simple",
    ) -> AvatarGenerationResult:
        """生成 4 张候选头像，下载到本地，更新 DB"""
        pass
    
    # ──────────────────────────────
    # 视频生成（可选）
    # ──────────────────────────────
    async def generate_avatar_video(
        self,
        character_id: int,
        motion: str = "breathing",
        duration: int = 3,
    ) -> Optional[str]:
        """基于已选择头像生成动态视频头像"""
        pass
    
    # ──────────────────────────────
    # 存储
    # ──────────────────────────────
    def _save_image(self, image_url: str, character_id: int, filename: str) -> str:
        """下载图片到本地，返回相对路径"""
        pass
    
    def _build_storage_path(self, character_id: int) -> Path:
        """usercontext/avatars/{id}/"""
        pass
```

#### 2.2 Prompt 构建策略

> 这是整个系统质量的核心。Prompt 的好坏直接决定画像是否"像"角色。

```python
def build_image_prompt(self, character: Character, style: str = "anime") -> str:
    parts = []
    
    # 1. 外貌描述（最优先）
    appearance = json.loads(character.appearance) if character.appearance else None
    if appearance:
        parts.append(self._format_appearance(appearance))
    elif character.description:
        # 兜底：从用户原始输入中提取外貌相关片段
        parts.append(f"根据以下描述创作：{character.description[:200]}")
    
    # 2. 角色名 + 身份（用于一致性）
    parts.append(f"角色名：{character.name}")
    
    # 3. 人格气质（影响表情和姿态）
    if character.personality:
        personality = json.loads(character.personality) if isinstance(character.personality, str) else character.personality
        traits = []
        if personality.get("sociability", 50) < 30:
            traits.append("内向、沉静")
        if personality.get("courage", 50) > 70:
            traits.append("大胆、直视镜头")
        if personality.get("empathy", 50) > 70:
            traits.append("目光温柔")
        if traits:
            parts.append("气质：" + "，".join(traits))
    
    # 4. 世界设定（影响背景和服装风格）
    if character.world_setting:
        parts.append(f"世界观：{character.world_setting[:100]}")
    
    # 5. 风格修饰
    style_modifiers = {
        "anime": "anime style, Studio Ghibli inspired, soft colors, portrait",
        "realistic": "photorealistic portrait, professional lighting, 85mm lens",
        "watercolor": "watercolor painting, soft edges, artistic, pastel colors",
        "pixel": "pixel art, retro game style, limited palette"
    }
    parts.append(style_modifiers.get(style, style_modifiers["anime"]))
    
    # 6. 质量要求
    parts.append("高质量，单人半身像，面部清晰，特征鲜明")
    
    # 7. 负面约束
    parts.append("不要NSFW，不要恐怖，不要血腥，不要政治敏感符号")
    
    return "，".join(parts)
```

***

### Step 3 — 后端 API 路由

#### 3.1 端点设计

**文件：** `backend/api/character_router.py` — 新增 4 个端点

| 方法   | 路径                                     | 说明                       |
| ---- | -------------------------------------- | ------------------------ |
| POST | `/api/characters/{id}/avatar/generate` | 异步生成头像（返回 task\_id，后台执行） |
| GET  | `/api/characters/{id}/avatar/status`   | 查询生成状态 + 候选图列表           |
| POST | `/api/characters/{id}/avatar/select`   | 选择第 N 张候选图为正式头像          |
| POST | `/api/characters/{id}/avatar/video`    | 异步生成视频头像                 |

#### 3.2 POST generate 端点

```python
class AvatarGenerateRequest(BaseModel):
    style: str = "anime"           # anime / realistic / watercolor / pixel
    expression: str = "neutral"    # neutral / smile / serious / shy
    background: str = "simple"     # simple / scene / transparent
    regenerate: bool = False       # True = 强制重新生成

@router.post("/characters/{character_id}/avatar/generate")
async def generate_avatar(
    character_id: int,
    request: AvatarGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """异步生成角色头像（4 张候选图）"""
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(404, "角色不存在")
    
    task_id = f"avatar-{character_id}-{int(time.time())}"
    
    # 提交后台任务
    background_tasks.add_task(
        _do_avatar_generation,
        character_id=character_id,
        style=request.style,
        expression=request.expression,
        background=request.background,
        regenerate=request.regenerate,
    )
    
    return {"status": "pending", "task_id": task_id, "estimated_seconds": 30}
```

#### 3.3 GET status 端点

```python
@router.get("/characters/{character_id}/avatar/status")
async def get_avatar_status(character_id: int, db: Session = Depends(get_db)):
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(404)
    
    candidates = json.loads(character.avatar_candidates) if character.avatar_candidates else []
    
    return {
        "status": "completed" if candidates else "pending",
        "candidates": [{"url": url, "index": i} for i, url in enumerate(candidates)],
        "selected_index": character.avatar_selected_index or 0,
        "current_avatar": character.avatar_url,
        "video_url": character.avatar_video_url,
        "video_status": character.avatar_video_status or "none",
        "generated_at": character.avatar_generated_at.isoformat() if character.avatar_generated_at else None,
    }
```

#### 3.4 POST select 端点

```python
class AvatarSelectRequest(BaseModel):
    index: int = 0  # 0-3

@router.post("/characters/{character_id}/avatar/select")
async def select_avatar(character_id: int, request: AvatarSelectRequest, db: Session = Depends(get_db)):
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(404)
    
    candidates = json.loads(character.avatar_candidates) if character.avatar_candidates else []
    if request.index >= len(candidates):
        raise HTTPException(400, f"index {request.index} 超出候选范围 (0-{len(candidates)-1})")
    
    character.avatar_url = candidates[request.index]
    character.avatar_selected_index = request.index
    db.commit()
    
    return {"status": "ok", "avatar_url": character.avatar_url, "index": request.index}
```

#### 3.5 POST video 端点

```python
@router.post("/characters/{character_id}/avatar/video")
async def generate_video_avatar(
    character_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(404)
    if not character.avatar_url:
        raise HTTPException(400, "请先生成静态头像")
    
    character.avatar_video_status = "pending"
    db.commit()
    
    background_tasks.add_task(_do_video_generation, character_id)
    
    return {"status": "pending", "estimated_seconds": 60}
```

***

### Step 4 — 存储系统

#### 4.1 目录结构

```
usercontext/avatars/
  {character_id}/
    candidates/
      0_20260629_120000.png    # 候选图 0
      1_20260629_120000.png    # 候选图 1
      2_20260629_120000.png    # 候选图 2
      3_20260629_120000.png    # 候选图 3
    selected/
      avatar_20260629_120500.png  # 用户选定的头像
    video/
      avatar_video_20260629_120600.mp4
```

#### 4.2 StaticFiles 挂载

**文件：** `backend/main.py` — 在现有的 `/assets` 挂载之后添加：

```python
# v009: 角色头像静态文件
AVATAR_DIR = Path(__file__).resolve().parent.parent / "usercontext" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=str(AVATAR_DIR)), name="avatars")
```

#### 4.3 存储路径生成

```python
def _build_storage_path(self, character_id: int, sub_dir: str = "candidates") -> Path:
    """usercontext/avatars/{character_id}/{sub_dir}/"""
    base = Path("usercontext/avatars") / str(character_id) / sub_dir
    base.mkdir(parents=True, exist_ok=True)
    return base

def _save_image(self, image_url: str, character_id: int, filename: str) -> str:
    """下载远程图片到本地，返回访问 URL"""
    import httpx
    import aiofiles
    
    local_path = self._build_storage_path(character_id) / filename
    async with httpx.AsyncClient() as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
    async with aiofiles.open(local_path, "wb") as f:
        await f.write(resp.content)
    
    # 返回可访问的 URL（经过 StaticFiles）
    rel = f"/avatars/{character_id}/candidates/{filename}"
    return rel
```

***

### Step 5 — 数据库迁移 v009B（头像字段）

**文件：** `backend/services/db_migration.py`

```python
def migrate_v009B_character_avatar(engine: Engine) -> dict:
    """
    迁移 v009B：给 characters 表新增头像相关列
    
    新增列：
      avatar_url, avatar_candidates (JSON 数组),
      avatar_selected_index, avatar_video_url,
      avatar_video_status, avatar_generation_prompt,
      avatar_generated_at, avatar_video_prompt
    """
    result = {"added_columns": 0}
    # ... 同上模式，ALTER TABLE 逐个添加 ...
```

**文件：** `backend/models.py` L38（`appearance` 之后）

```python
# v009: 头像相关
appearance = Column(Text, nullable=True)               # 外貌描述 JSON
avatar_url = Column(String(500), nullable=True)        # 当前头像 URL
avatar_candidates = Column(Text, nullable=True)        # JSON 候选 URL 数组
avatar_selected_index = Column(Integer, default=0)
avatar_video_url = Column(String(500), nullable=True)
avatar_video_status = Column(String(20), default="none")
avatar_generation_prompt = Column(Text, nullable=True)
avatar_generated_at = Column(DateTime(timezone=True), nullable=True)
avatar_video_prompt = Column(Text, nullable=True)
```

***

### Step 6 — 前端实现

#### 6.1 新建 `CharacterAvatar.vue`

**文件：** `web/src/components/CharacterAvatar.vue`（新建）

```vue
<template>
  <div class="character-avatar" :class="[size]">
    <!-- 视频头像（优先） -->
    <video v-if="videoUrl && !videoError"
           :src="videoUrl" autoplay loop muted playsinline
           @error="videoError = true" />
    
    <!-- 静态头像 -->
    <img v-else-if="avatarUrl" :src="avatarUrl" :alt="name" @error="onImgError" />
    
    <!-- 兜底：CSS 颜色头像 -->
    <div v-else class="avatar-fallback" :style="{ background: color }">
      {{ name?.[0] || '?' }}
    </div>
    
    <!-- 编辑按钮 -->
    <button v-if="editable" class="edit-btn" @click="$emit('edit')" title="生成头像">
      🎨
    </button>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  avatarUrl?: string
  videoUrl?: string
  name?: string
  color?: string
  size?: 'sm' | 'md' | 'lg'
  editable?: boolean
}>()
const videoError = ref(false)
const imgError = ref(false)
</script>
```

#### 6.2 新建 `AvatarGenerator.vue`

**文件：** `web/src/components/AvatarGenerator.vue`（新建）

对话框组件，包含：

- 风格选择（anime / realistic / watercolor / pixel）— Radio buttons
- 表情选择 — 4 个按钮
- 背景选择 — 3 个选项
- 生成进度条 + 状态文字
- 候选图网格（2×2），点击选择
- "生成视频头像"按钮
- "重新生成" / "确认使用"操作按钮

#### 6.3 API 封装

**文件：** `web/src/api/index.ts` — 新增 `avatarApi` 对象：

```typescript
export const avatarApi = {
  generate: (characterId: number, params: AvatarGenerateParams) =>
    request<{ status: string; task_id: string }>(`/characters/${characterId}/avatar/generate`, {
      method: 'POST',
      body: JSON.stringify(params),
    }),
  
  status: (characterId: number) =>
    request<AvatarStatusResponse>(`/characters/${characterId}/avatar/status`),
  
  select: (characterId: number, index: number) =>
    request<{ status: string; avatar_url: string }>(`/characters/${characterId}/avatar/select`, {
      method: 'POST',
      body: JSON.stringify({ index }),
    }),
  
  video: (characterId: number) =>
    request<{ status: string }>(`/characters/${characterId}/avatar/video`, { method: 'POST' }),
}
```

#### 6.4 集成到角色创建流程

**文件：** `web/src/views/CreateView.vue` — `submit()` 成功后：

```typescript
// 创建成功后自动触发头像生成
const showToast = inject('showToast')!
showToast('角色创建成功，正在生成头像...', 'info', 4000)
avatarApi.generate(created.id, { style: 'anime', expression: 'neutral' })
// 后台异步生成，不阻塞用户
```

***

### Step 7 — 配置与依赖

#### 7.1 requirements.txt 新增

```
httpx>=0.27.0          # 异步 HTTP 客户端（Agnes API 调用）
aiofiles>=23.2.1       # 异步文件写入（保存图片）
Pillow>=10.0.0         # 可选：图片格式转换/压缩
```

项目已有 `httpx<0.28.0`（L8），需升级到 `>=0.27.0` 支持异步用法。`aiofiles>=23.2.1` 已有（L9）。

#### 7.2 环境变量（.env）

已有，无需修改：

```bash
AGNES_API_KEY=sk-96cM7u6rvzzDyxIAikNx8Ilpl9sSrPquQe7iFxUi8VIa1GZG
AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
```

#### 7.3 启动时创建目录

**文件：** `backend/main.py` — 在 `lifespan()` 启动阶段：

```python
# v009: 创建头像存储目录
AVATAR_DIR = Path("usercontext/avatars")
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
```

***

## 完整文件改动清单

|  序号 | 文件                                                     |  操作 | 内容                                                       |
| :-: | ------------------------------------------------------ | :-: | -------------------------------------------------------- |
| 0-1 | `backend/models.py`                                    |  修改 | Character 类新增 10 个字段（appearance + 9 个头像字段）               |
| 0-2 | `backend/prompts/creation.txt`                         |  修改 | 新增第 15 个字段 `appearance`                                  |
| 0-3 | `backend/modules/creation.py`                          |  修改 | parse\_response() 新增 appearance 解析                       |
| 0-4 | `backend/api/character_router.py`                      |  修改 | create\_character 端点新增 appearance 持久化                    |
| 0-5 | `backend/services/db_migration.py`                     |  新增 | migrate\_v009A (appearance) + migrate\_v009B (avatar 字段) |
|  1  | **新建** `backend/services/agnes_client.py`              |  新建 | AgnesImageClient + AgnesVideoClient                      |
|  2  | **新建** `backend/services/avatar_generation_service.py` |  新建 | AvatarGenerationService                                  |
|  3  | `backend/api/character_router.py`                      |  修改 | 新增 4 个头像端点                                               |
|  4  | `backend/main.py`                                      |  修改 | 挂载 /avatars 静态文件 + 启动时创建目录                               |
|  5  | **新建** `web/src/components/CharacterAvatar.vue`        |  新建 | 头像展示组件                                                   |
|  6  | **新建** `web/src/components/AvatarGenerator.vue`        |  新建 | 头像生成对话框                                                  |
|  7  | `web/src/views/CreateView.vue`                         |  修改 | 创建成功后触发头像生成                                              |
|  8  | `web/src/views/ChatView.vue`                           |  修改 | 聊天消息旁显示头像                                                |
|  9  | `web/src/api/index.ts`                                 |  修改 | 新增 avatarApi                                             |
|  10 | `requirements.txt`                                     |  修改 | httpx 版本升级                                               |
|  11 | `.env`                                                 |  保持 | 已有的 AGNES\_API\_KEY 不变                                   |

**总计：6 个文件修改 + 5 个文件新建 + 2 个迁移函数。**

***

## 验收标准

|  #  | 测试项               | 步骤                                                  | 预期结果                                                |
| :-: | ----------------- | --------------------------------------------------- | --------------------------------------------------- |
|  1  | 新建角色包含 appearance | 创建角色 → 检查 DB                                        | characters.appearance 字段非空，JSON 完整                  |
|  2  | 头像生成成功            | POST /avatar/generate → 等待 30s → GET /avatar/status | 返回 4 张候选图 URL                                       |
|  3  | 候选图可访问            | 浏览器打开 candidate URL                                 | 显示角色画像，特征与 appearance 一致                            |
|  4  | 选择头像              | POST /avatar/select `{"index": 2}`                  | avatar\_url 更新为第 3 张候选图                             |
|  5  | 视频生成              | POST /avatar/video → 等待 60s → GET /avatar/status    | avatar\_video\_url 有值，可播放                           |
|  6  | 不同风格效果            | style=anime vs style=realistic                      | 两批图风格明显不同                                           |
|  7  | Prompt 含角色信息      | 检查 avatar\_generation\_prompt                       | 包含 name / appearance / personality / world\_setting |
|  8  | 创建后自动触发           | 创建角色 → 不手动操作                                        | avatar\_candidates 自动填充（后台生成）                       |
|  9  | 重新生成覆盖            | regenerate=true → GET status                        | 旧 candidates 被清理，新 4 张 URL 不同                       |
|  10 | 失败不影响角色           | 断网时生成                                               | 角色功能正常，avatar\_candidates 为空，status 显示失败            |
|  11 | Chat 界面显示头像       | 进入对话                                                | 角色头像显示在消息气泡旁                                        |
|  12 | 无 appearance 角色   | 老角色（appearance=NULL）生成头像                            | 使用 description 作为 prompt 兜底                         |

