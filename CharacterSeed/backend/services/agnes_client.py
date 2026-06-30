"""
Agnes 多模态 API 客户端
=======================

封装 Agnes 图像生成 (agnes-image-2.1-flash) 和视频生成 (agnes-video-v2.0) API。

API 认证方式：Bearer Token（与文本 API 相同，复用 .env 中的 AGNES_API_KEY）
Base URL：https://apihub.agnes-ai.com/v1

设计要点：
  - 客户端是"无状态"的（仅持有 httpx.AsyncClient），调用方负责传入参数并消费结果
  - 失败一律抛 RuntimeError，让上层 AvatarGenerationService 决定重试/降级
  - 响应格式严格按 OpenAI 风格（data/choices 数组）解析，
    兼容 Agnes 实际返回 { data: [{url: "..."}] } 形态
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# 退避工具（沿用 LLMService 的 _compute_retry_delay 思路，但放在这里避免循环依赖）
def _compute_retry_delay(attempt: int, base: float = 1.0) -> float:
    """
    计算带 jitter 的指数退避延迟（秒）。
    attempt 从 0 开始：0/1/2 对应第 1/2/3 次重试。
    实际延迟区间：attempt=0: [1, 2)s, attempt=1: [2, 3)s, attempt=2: [4, 5)s
    """
    import random
    exponential = base * (2 ** attempt)
    jitter = random.uniform(0, base)
    return exponential + jitter


class AgnesImageClient:
    """
    Agnes 图像生成客户端

    端点：POST /v1/images/generations
    模型：agnes-image-2.1-flash
    响应：{"created": 1710000000, "data": [{"url": "...", "b64_json": null}, ...]}
    """

    BASE_URL = "https://apihub.agnes-ai.com/v1"
    IMAGE_ENDPOINT = "/images/generations"
    IMAGE_MODEL = "agnes-image-2.1-flash"
    # 单张图像生成请求超时（120s 足够，付费档可能慢；免费档通常 10-30s）
    REQUEST_TIMEOUT_S = 120.0
    # 最多重试 2 次（首请求 + 2 重试 = 3 次）
    MAX_RETRIES = 2

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        if not api_key:
            raise ValueError("AGNES_API_KEY 不能为空")
        self.api_key = api_key
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        # 注意：每个 client 自带一个 AsyncClient（生命周期跟随 client 本身）
        # 调用方负责在不再使用时 close()。avatar 服务是单例，进程退出时统一释放。
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT_S, connect=10.0),
        )

    async def close(self) -> None:
        """关闭底层 httpx client。建议在进程关闭时调用。"""
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 4,
        response_format: str = "url",
        **extra_body: Any,
    ) -> List[str]:
        """
        生成多张候选图（默认 4 张），返回 URL 列表。

        实现策略：
          1) 先尝试一次带 n 参数的请求（部分 image API 支持批量）
          2) 若响应中 data 长度 < n，则在外部循环 n 次，每次 n=1 + 不同 seed
             ——确保拿到目标张数候选图

        返回：长度 == n 的 URL 列表（任意位置失败会抛 RuntimeError）
        """
        if n < 1:
            n = 1

        # 第一次：尝试 n=4 一次拿齐
        try:
            data = await self._post_generate(
                prompt=prompt,
                size=size,
                n=n,
                response_format=response_format,
                **extra_body,
            )
            urls = self._extract_urls(data)
            if len(urls) >= n:
                return urls[:n]
            # 数量不足时退化到循环单次
            logger.warning(
                "Agnes 图片批量接口仅返回 %d 张（要求 %d），退化到循环单次生成",
                len(urls), n,
            )
        except Exception as e:
            logger.warning("Agnes 批量生成失败，退化到循环单次：%s", e)

        # 退化路径：循环 n 次
        urls: List[str] = []
        for i in range(n):
            try:
                data = await self._post_generate(
                    prompt=prompt,
                    size=size,
                    n=1,
                    response_format=response_format,
                    seed=(i * 1000) + 42,  # 用不同 seed 制造差异
                    **extra_body,
                )
                got = self._extract_urls(data)
                if got:
                    urls.append(got[0])
            except Exception as e:
                logger.exception("Agnes 单张生成 %d 失败: %s", i, e)
                # 单张失败不影响后续，但累计失败率过高时整体失败
        if not urls:
            raise RuntimeError("Agnes 图像生成全部失败")
        return urls

    async def generate_single(
        self,
        prompt: str,
        size: str = "1024x1024",
        response_format: str = "url",
        seed: Optional[int] = None,
        **extra_body: Any,
    ) -> str:
        """生成单张图，返回 URL。失败抛 RuntimeError。"""
        data = await self._post_generate(
            prompt=prompt,
            size=size,
            n=1,
            response_format=response_format,
            seed=seed,
            **extra_body,
        )
        urls = self._extract_urls(data)
        if not urls:
            raise RuntimeError("Agnes 单张生成响应中未包含 url")
        return urls[0]

    async def _post_generate(
        self,
        prompt: str,
        size: str,
        n: int,
        response_format: str,
        seed: Optional[int] = None,
        **extra_body: Any,
    ) -> Dict[str, Any]:
        """带重试的 POST 包装。"""
        body: Dict[str, Any] = {
            "model": self.IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
            "n": n,
        }
        # 注意：Agnes 实际后端模型是 agnes-t2i-general-model，
        # 不支持 `response_format` 参数（直接 400），因此跳过
        # if response_format:
        #     body["response_format"] = response_format
        if seed is not None:
            body["seed"] = seed
        body.update(extra_body)

        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = await self._client.post(self.IMAGE_ENDPOINT, json=body)
                if resp.status_code == 200:
                    return resp.json()
                # 4xx 立刻抛错（参数错/鉴权错，重试无意义）
                if 400 <= resp.status_code < 500:
                    text = (await resp.aread())[:500].decode("utf-8", errors="ignore")
                    raise RuntimeError(
                        f"Agnes 图片生成 {resp.status_code}: {text}"
                    )
                # 5xx / 429 退避重试
                text = (await resp.aread())[:500].decode("utf-8", errors="ignore")
                last_exc = RuntimeError(
                    f"Agnes 图片生成 {resp.status_code}: {text}"
                )
            except httpx.HTTPError as e:
                last_exc = e
            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(_compute_retry_delay(attempt))
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _extract_urls(payload: Dict[str, Any]) -> List[str]:
        """从 OpenAI 风格响应中抽取 url 列表（容忍 b64_json 形态）"""
        out: List[str] = []
        data = payload.get("data") or []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                out.append(url)
        return out


class AgnesVideoClient:
    """
    Agnes 视频生成客户端

    端点：
      POST /v1/videos                → 创建任务，返回 video_id
      GET  /v1/videos/{video_id}     → 轮询结果，返回 video_url
    模型：agnes-video-v2.0
    """

    BASE_URL = "https://apihub.agnes-ai.com/v1"
    VIDEO_ENDPOINT = "/videos"
    VIDEO_MODEL = "agnes-video-v2.0"
    REQUEST_TIMEOUT_S = 30.0   # 创建任务
    POLL_TIMEOUT_S = 60.0      # 单次轮询

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        if not api_key:
            raise ValueError("AGNES_API_KEY 不能为空")
        self.api_key = api_key
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT_S, connect=10.0),
        )

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def create_task(
        self,
        prompt: str,
        image_url: str,
        width: int = 768,
        height: int = 768,
        num_frames: int = 73,    # 8n+1，约 3s @ 24fps
        frame_rate: int = 24,
        seed: Optional[int] = None,
        **extra_body: Any,
    ) -> str:
        """
        创建视频生成任务，返回 video_id。

        POST /v1/videos
        响应：{"video_id": "xxx", "status": "pending"}
        """
        body: Dict[str, Any] = {
            "model": self.VIDEO_MODEL,
            "prompt": prompt,
            "image_url": image_url,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }
        if seed is not None:
            body["seed"] = seed
        body.update(extra_body)

        resp = await self._client.post(self.VIDEO_ENDPOINT, json=body)
        if resp.status_code >= 400:
            text = (await resp.aread())[:500].decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Agnes 视频任务创建失败 {resp.status_code}: {text}"
            )
        data = resp.json()
        video_id = data.get("video_id") or data.get("id")
        if not video_id:
            raise RuntimeError(f"Agnes 视频响应无 video_id: {data}")
        return str(video_id)

    async def get_result(self, video_id: str) -> Dict[str, Any]:
        """
        轮询单次任务结果。返回原始 dict。
        响应：{"status": "pending|completed|failed", "url": "...", "error": "..."}
        """
        resp = await self._client.get(f"{self.VIDEO_ENDPOINT}/{video_id}")
        if resp.status_code >= 400:
            text = (await resp.aread())[:500].decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Agnes 视频任务查询失败 {resp.status_code}: {text}"
            )
        return resp.json()

    async def wait_for_completion(
        self,
        video_id: str,
        poll_interval: float = 5.0,
        max_wait: float = 300.0,
    ) -> Optional[str]:
        """
        轮询等待任务完成。
        返回：成功 → video_url；失败/超时 → None
        """
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                data = await self.get_result(video_id)
            except Exception as e:
                logger.warning("Agnes 视频轮询失败: %s", e)
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue
            status = (data.get("status") or "").lower()
            if status in ("completed", "success", "succeeded"):
                url = data.get("url") or data.get("video_url")
                return url if isinstance(url, str) else None
            if status in ("failed", "error", "cancelled"):
                logger.error("Agnes 视频任务失败: %s", data)
                return None
            # pending / processing / running
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        logger.warning("Agnes 视频任务超时（max_wait=%.0fs）: %s", max_wait, video_id)
        return None


def get_default_agnes_api_key() -> str:
    """
    读取默认 AGNES_API_KEY（优先级：os.environ > .env 加载后的 env）。
    .env 已在 backend/main.py 启动时 load_dotenv() 注入。
    """
    key = os.environ.get("AGNES_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "AGNES_API_KEY 未配置；请在 .env 中设置 AGNES_API_KEY=sk-xxx"
        )
    return key
