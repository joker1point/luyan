"""
AvatarGenerationService — 角色头像生成服务

职责：
  1. 从 Character 对象构建生图 prompt（appearance + personality + world）
  2. 调用 AgnesImageClient 生成 4 张候选图
  3. 下载图片到本地存储（usercontext/avatars/{id}/...）
  4. 更新 Character 数据库记录（avatar_candidates / avatar_generation_prompt）
  5. (可选) 触发视频头像生成（基于已选头像）

设计要点：
  - Prompt 构建遵循"appearance 优先 → description 兜底 → personality/world 加成"
  - 文件存储走 /usercontext/avatars/{character_id}/candidates|selected|video/ 三级目录
  - 全部走 aiofiles + httpx 异步 IO，不阻塞事件循环
  - 服务单例（不持 DB session，每次操作从调用方注入 session）
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import httpx

from backend.models import Character
from backend.services.agnes_client import (
    AgnesImageClient,
    AgnesVideoClient,
    get_default_agnes_api_key,
)

logger = logging.getLogger(__name__)

# 仓库根（项目根）：CharacterSeed/，与 usercontext/ 同级
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = _PROJECT_ROOT / "usercontext" / "avatars"
STATIC_URL_PREFIX = "/avatars"  # main.py 挂载 StaticFiles 的 prefix


# 风格修饰（计划 2.2 节）
_STYLE_MODIFIERS = {
    "anime": "anime style, Studio Ghibli inspired, soft colors, portrait, half-body shot",
    "realistic": "photorealistic portrait, professional lighting, 85mm lens, sharp focus",
    "watercolor": "watercolor painting, soft edges, artistic, pastel colors, ink wash",
    "pixel": "pixel art, retro game style, limited palette, 16-bit",
    "ink": "Chinese ink painting, sumi-e style, brush strokes, minimalist, traditional",
    "comic": "comic book style, bold lines, vibrant colors, cel-shading",
}

# 表情修饰（追加到 prompt 末尾）
_EXPRESSION_MODIFIERS = {
    "neutral": "neutral expression, calm eyes, soft smile",
    "smile": "warm gentle smile, eyes slightly squinted with joy",
    "serious": "serious expression, focused eyes, no smile",
    "shy": "slightly shy, looking down, faint blush on cheeks",
    "angry": "fierce expression, sharp eyes, frown",
}

# 背景修饰
_BACKGROUND_MODIFIERS = {
    "simple": "simple soft background, solid color, bokeh",
    "scene": "in-world scene with thematic background",
    "transparent": "plain white background, no objects",
}


def _parse_json_field(raw: Optional[str]) -> Optional[Any]:
    """JSON 字符串字段 → Python 对象；空串/None/解析失败均返回 None"""
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


class AvatarGenerationService:
    """
    角色头像生成服务（单例）
    """

    _instance: Optional["AvatarGenerationService"] = None

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_default_agnes_api_key()
        self.image_client = AgnesImageClient(api_key=self.api_key)
        self.video_client = AgnesVideoClient(api_key=self.api_key)
        # 兜底：若目录不存在则创建（启动时也会在 main.py 显式创建一次）
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance(cls) -> "AvatarGenerationService":
        """进程级单例（避免每次创建新 client）"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def close(self) -> None:
        """关闭底层 client（进程退出时调用）"""
        await self.image_client.close()
        await self.video_client.close()

    # ==========================
    # Prompt 构建
    # ==========================
    def build_image_prompt(
        self,
        character: Character,
        style: str = "anime",
        expression: str = "neutral",
        background: str = "simple",
    ) -> str:
        """
        从角色数据构建生图 prompt。

        优先级：
          1) appearance JSON → 最精确的外貌数据（10 字段）
          2) description → 用户原始输入，可能包含外貌信息（兜底）
          3) personality + world_setting → 气质 + 世界背景

        设计要点（v009-fix-2）：
          - Agnes 图像 API 的内容策略对"长 prompt + 多个不常见词"敏感
            （实测：>40 词的 prompt 经常被判违规，即使没有明显敏感词）
          - 改用"短 prompt + 仅保留视觉相关字段"策略：
            1) 只取 hair_color / eye_color / clothing / overall_impression 4 个字段
            2) 每字段限制前 30 字符
            3) 不用 world_setting
            4) 不用 character name（避免跨 prompt 触发）
            5) 整体 prompt 控制在 30 词以内
        """
        parts: List[str] = []

        # 1) 极简外貌（只取 4 个最关键的视觉字段，每字段限长）
        appearance = _parse_json_field(character.appearance)
        if isinstance(appearance, dict) and appearance:
            for key in ("hair_color", "eye_color", "clothing", "overall_impression"):
                v = appearance.get(key)
                if v and isinstance(v, str) and v.strip():
                    safe = self._sanitize_appearance_field(v.strip()[:30])
                    if safe:
                        parts.append(safe)
        elif character.description:
            # 兜底：只取描述的前 60 字 + 替换可能触发策略的敏感词
            desc = self._sanitize_world_setting(character.description[:60])
            if desc:
                parts.append(desc)

        # 2) 风格修饰（短版）
        style_short = {
            "anime": "anime style portrait",
            "realistic": "realistic portrait",
            "watercolor": "watercolor portrait",
            "pixel": "pixel art portrait",
            "ink": "ink painting portrait",
            "comic": "comic style portrait",
        }.get(style, "anime style portrait")
        parts.append(style_short)

        # 3) 表情 + 背景（合并为短句）
        parts.append("soft smile, simple background")

        # 4) 质量约束
        parts.append("high quality, single character, no text")

        return ", ".join(parts)

    @staticmethod
    def _sanitize_world_setting(text: str) -> str:
        """
        对世界观描述做最小化"安全过滤"：
          - 替换可能触发内容策略的中文关键词为中性词
          - 仅做兜底，主要靠 appearance + 风格描述避开策略
        """
        if not text:
            return ""
        # 极简替换：只覆盖经验上触发 400 的词
        replacements = {
            "黑客": "技术专家",
            "破解": "研究",
            "加密": "信息",
            "抵抗组织": "团体",
            "压迫": "压力",
            "法术": "能力",
            "魔法": "特殊能力",
            "悬赏": "通缉",
            "犯罪": "冒险",
            "暴力": "冲突",
            "武器": "道具",
            "爆炸": "事故",
        }
        out = text
        for bad, good in replacements.items():
            out = out.replace(bad, good)
        return out.strip()

    @staticmethod
    def _sanitize_appearance_field(value: str) -> str:
        """
        对 appearance 字段做英文关键词过滤（经验上触发 Agnes 策略）：
          - scar / wound / blood / injury / weapon / gun / sword → 替换为中性词
          - tactical / military / armor / combat → 替换为日常词
          - leather（单独使用可能含 BDSM 暗示）→ 替换为 soft / dark
        """
        if not value:
            return ""
        replacements = {
            "scar": "mark",
            "scars": "marks",
            "wound": "mark",
            "blood": "red",
            "injury": "feature",
            "weapon": "tool",
            "tactical": "structured",
            "military": "structured",
            "armor": "jacket",
            "combat": "outdoor",
            "leather": "dark",
            "gun": "tool",
            "sword": "tool",
            "knife": "tool",
        }
        out = value
        lower = out.lower()
        for bad, good in replacements.items():
            # 不区分大小写替换
            import re
            out = re.sub(rf"\b{re.escape(bad)}\b", good, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _format_appearance(appearance: dict) -> str:
        """
        将 appearance JSON dict 转为英文自然语言片段（Agnes 英文模型更友好）。
        字段顺序与 prompt 中声明一致（height → overall_impression），
        跳过空值字段。
        所有字符串值会过一遍 _sanitize_appearance_field 过滤敏感词。
        """
        order = [
            ("height", "height"),
            ("build", "build"),
            ("hair_color", "hair color"),
            ("hair_style", "hair style"),
            ("eye_color", "eye color"),
            ("skin_tone", "skin tone"),
            ("clothing", "clothing"),
            ("accessories", "accessories"),
            ("distinctive_features", "distinctive features"),
            ("overall_impression", "overall impression"),
        ]
        parts = []
        for key, label in order:
            v = appearance.get(key)
            if v and isinstance(v, str) and v.strip():
                safe = AvatarGenerationService._sanitize_appearance_field(v.strip())
                if safe:
                    parts.append(f"{label}: {safe}")
        return ", ".join(parts) if parts else ""

    # ==========================
    # 图像生成（4 张候选图）
    # ==========================
    async def generate_avatars(
        self,
        character_id: int,
        db: Any,  # Session 类型（避免循环 import）
        style: str = "anime",
        expression: str = "neutral",
        background: str = "simple",
        regenerate: bool = False,
    ) -> Dict[str, Any]:
        """
        生成 4 张候选头像，下载到本地，更新 DB。

        行为：
          - regenerate=False 且已存在 candidates → 直接返回（不重生成）
          - regenerate=True → 清理旧 candidates 后重生
          - 至少返回 1 张图（多张更好），全失败抛 RuntimeError

        Returns: {
          "status": "completed"|"partial"|"failed",
          "candidates": [<rel_url>, ...],
          "prompt": <生成的 prompt>,
          "generated_at": <iso>,
        }
        """
        character = db.query(Character).filter(Character.id == character_id).first()
        if not character:
            raise ValueError(f"角色 {character_id} 不存在")

        # regenerate=True → 清空旧 candidates
        if regenerate:
            character.avatar_candidates = None
            character.avatar_generation_prompt = None
            db.commit()

        # 已有 candidates → 直接返回（避免重复生成）
        existing = _parse_json_field(character.avatar_candidates) or []
        if existing and not regenerate:
            return {
                "status": "completed",
                "candidates": existing,
                "prompt": character.avatar_generation_prompt or "",
                "generated_at": (
                    character.avatar_generated_at.isoformat()
                    if character.avatar_generated_at else None
                ),
            }

        # 1) 构建 prompt
        prompt = self.build_image_prompt(
            character, style=style, expression=expression, background=background,
        )
        logger.info(
            "角色 %d 开始生图：style=%s expression=%s background=%s",
            character_id, style, expression, background,
        )

        # 2) 调 Agnes 生成多张 URL
        try:
            remote_urls = await self.image_client.generate(
                prompt=prompt, size="1024x1024", n=4, response_format="url",
            )
        except Exception as e:
            logger.exception("Agnes 图片生成失败: %s", e)
            character.avatar_generation_prompt = prompt
            db.commit()
            raise RuntimeError(f"图像生成失败: {e}")

        # 3) 下载到本地
        local_urls: List[str] = []
        for idx, url in enumerate(remote_urls):
            try:
                rel = await self._save_image(
                    remote_url=url,
                    character_id=character_id,
                    index=idx,
                )
                local_urls.append(rel)
            except Exception as e:
                logger.warning(
                    "下载第 %d 张候选图失败: %s (url=%s)", idx, e, url,
                )

        if not local_urls:
            character.avatar_generation_prompt = prompt
            db.commit()
            raise RuntimeError("所有候选图下载失败")

        # 4) 写回 DB
        character.avatar_candidates = json.dumps(local_urls, ensure_ascii=False)
        character.avatar_generation_prompt = prompt
        character.avatar_generated_at = datetime.now(timezone.utc)
        if not character.avatar_url:
            # 首次生成：默认选第一张作为当前头像
            character.avatar_url = local_urls[0]
            character.avatar_selected_index = 0
        db.commit()
        db.refresh(character)

        return {
            "status": "completed" if len(local_urls) >= 4 else "partial",
            "candidates": local_urls,
            "prompt": prompt,
            "generated_at": character.avatar_generated_at.isoformat(),
        }

    # ==========================
    # 视频头像
    # ==========================
    async def generate_avatar_video(
        self,
        character_id: int,
        db: Any,
        motion: str = "breathing",
        duration: int = 3,
    ) -> Optional[str]:
        """
        基于已选头像生成动态视频头像。返回视频 URL（已下载到本地）。

        行为：
          - 若无 avatar_url → raise（需先生成静态头像）
          - 异步创建 Agnes 视频任务 → 轮询 → 下载 mp4 → 更新 DB
        """
        character = db.query(Character).filter(Character.id == character_id).first()
        if not character:
            raise ValueError(f"角色 {character_id} 不存在")
        if not character.avatar_url:
            raise ValueError("请先生成静态头像")

        # 1) 状态置 pending
        character.avatar_video_status = "pending"
        db.commit()
        db.refresh(character)

        # 2) 准备 prompt（基于 appearance + name + world）
        prompt = self._build_video_prompt(character, motion=motion, duration=duration)
        character.avatar_video_prompt = prompt
        db.commit()

        # 3) 创建任务
        try:
            video_id = await self.video_client.create_task(
                prompt=prompt,
                image_url=character.avatar_url,
                width=768, height=768,
                num_frames=duration * 24 + 1,  # 8n+1 友好
                frame_rate=24,
            )
        except Exception as e:
            logger.exception("Agnes 视频任务创建失败: %s", e)
            character.avatar_video_status = "failed"
            db.commit()
            raise RuntimeError(f"视频任务创建失败: {e}")

        character.avatar_video_status = "generating"
        db.commit()

        # 4) 轮询等待
        video_remote_url = await self.video_client.wait_for_completion(
            video_id=video_id, poll_interval=5.0, max_wait=300.0,
        )
        if not video_remote_url:
            character.avatar_video_status = "failed"
            db.commit()
            raise RuntimeError("视频生成失败或超时")

        # 5) 下载到本地
        try:
            local_url = await self._save_video(
                remote_url=video_remote_url,
                character_id=character_id,
            )
        except Exception as e:
            logger.exception("视频下载失败: %s", e)
            character.avatar_video_status = "failed"
            db.commit()
            raise RuntimeError(f"视频下载失败: {e}")

        character.avatar_video_url = local_url
        character.avatar_video_status = "completed"
        db.commit()
        db.refresh(character)
        return local_url

    def _build_video_prompt(
        self,
        character: Character,
        motion: str = "breathing",
        duration: int = 3,
    ) -> str:
        """视频 prompt：保留外貌 + 加动作"""
        appearance = _parse_json_field(character.appearance)
        appearance_text = (
            self._format_appearance(appearance)
            if isinstance(appearance, dict) and appearance
            else (character.description or "一个人物")
        )
        motion_text = {
            "breathing": "subtle breathing, gentle chest movement, eyes blinking occasionally",
            "wind": "wind blowing hair, slight head turn",
            "smile": "slowly smiles, eyes warm up",
            "turn": "turns head to look at camera",
        }.get(motion, "subtle breathing")
        return (
            f"{appearance_text}，{motion_text}，"
            f"{duration}秒，循环播放，电影质感"
        )

    # ==========================
    # 存储
    # ==========================
    def _build_storage_path(
        self, character_id: int, sub_dir: str = "candidates",
    ) -> Path:
        """usercontext/avatars/{id}/{sub_dir}/"""
        p = STORAGE_ROOT / str(character_id) / sub_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def _save_image(
        self,
        remote_url: str,
        character_id: int,
        index: int,
    ) -> str:
        """
        下载远程图片到本地，返回可访问的相对 URL。

        文件名：{index}_{timestamp}.png
        路径：usercontext/avatars/{id}/candidates/{filename}
        URL：/avatars/{id}/candidates/{filename}
        """
        ts = int(time.time() * 1000)
        # 推断扩展名：URL 末尾 .png/.jpg/.webp
        ext = "png"
        if ".jpg" in remote_url.lower() or ".jpeg" in remote_url.lower():
            ext = "jpg"
        elif ".webp" in remote_url.lower():
            ext = "webp"
        filename = f"{index}_{ts}.{ext}"
        local_path = self._build_storage_path(character_id, "candidates") / filename

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(remote_url)
            resp.raise_for_status()
            content = resp.content

        async with aiofiles.open(local_path, "wb") as f:
            await f.write(content)
        logger.info(
            "已下载头像：角色 %d → %s (%d bytes)",
            character_id, local_path, len(content),
        )
        rel = f"{STATIC_URL_PREFIX}/{character_id}/candidates/{filename}"
        return rel

    async def _save_video(
        self,
        remote_url: str,
        character_id: int,
    ) -> str:
        """
        下载视频到本地。文件名：avatar_video_{ts}.mp4
        """
        ts = int(time.time() * 1000)
        filename = f"avatar_video_{ts}.mp4"
        local_path = self._build_storage_path(character_id, "video") / filename

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(remote_url)
            resp.raise_for_status()
            content = resp.content

        async with aiofiles.open(local_path, "wb") as f:
            await f.write(content)
        logger.info(
            "已下载视频头像：角色 %d → %s (%d bytes)",
            character_id, local_path, len(content),
        )
        return f"{STATIC_URL_PREFIX}/{character_id}/video/{filename}"

    # ==========================
    # 选定候选图（写入 selected/）
    # ==========================
    async def select_avatar(
        self,
        character_id: int,
        db: Any,
        index: int,
    ) -> Dict[str, Any]:
        """
        把第 N 张候选图"提升"为正式头像（复制到 selected/ 目录，写 DB）。

        返回：{"avatar_url": ..., "index": ..., "candidates_count": ...}
        """
        character = db.query(Character).filter(Character.id == character_id).first()
        if not character:
            raise ValueError(f"角色 {character_id} 不存在")
        candidates = _parse_json_field(character.avatar_candidates) or []
        if not candidates:
            raise ValueError("尚无候选图，请先生成头像")
        if not (0 <= index < len(candidates)):
            raise ValueError(
                f"index {index} 越界（0-{len(candidates)-1}）"
            )

        chosen = candidates[index]

        # 已是本地相对 URL，尝试复制到 selected/ 目录（便于前端的 /avatars/{id}/selected/* 直读）
        if chosen.startswith(STATIC_URL_PREFIX + "/"):
            # 解析 path 段：/avatars/{id}/candidates/{filename} → filename
            tail = chosen[len(STATIC_URL_PREFIX) + 1:]  # "{id}/candidates/{filename}"
            parts = tail.split("/")
            if len(parts) >= 3 and parts[1] == "candidates":
                src_path = STORAGE_ROOT / parts[0] / "candidates" / parts[2]
                if src_path.exists():
                    ts = int(time.time() * 1000)
                    dst_dir = self._build_storage_path(int(parts[0]), "selected")
                    dst_path = dst_dir / f"avatar_{ts}{src_path.suffix}"
                    async with aiofiles.open(src_path, "rb") as f:
                        data = await f.read()
                    async with aiofiles.open(dst_path, "wb") as g:
                        await g.write(data)
                    chosen = (
                        f"{STATIC_URL_PREFIX}/{parts[0]}/selected/{dst_path.name}"
                    )

        character.avatar_url = chosen
        character.avatar_selected_index = index
        db.commit()
        db.refresh(character)
        return {
            "avatar_url": chosen,
            "index": index,
            "candidates_count": len(candidates),
        }
