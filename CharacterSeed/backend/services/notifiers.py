"""
Notifiers — 告警通知渠道实现。

支持 3 种渠道：
  - console:  打到 stderr（开发模式默认）
  - webhook:  POST 到指定 URL（兼容飞书 / 钉钉 / Slack / 自定义网关）
              如果传 secret，会带 X-CS-Signature 头（hmac-sha256）
  - email:    通过 SMTP 发送（依赖可选；缺 smtplib 时降级为 console）

设计：
  - 单入口 dispatch(entry, channels) → 按 type 字段路由到具体实现
  - 每个渠道独立 try/except，单一渠道失败不影响其他渠道
  - 超时 5s，避免 webhook 卡死 worker 线程
"""
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List

import httpx

# SMTP 配置从环境变量读取（可选；缺省时 email 渠道降级为 console）
SMTP_HOST = os.environ.get("CS_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("CS_SMTP_PORT", "465"))
SMTP_USER = os.environ.get("CS_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("CS_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("CS_SMTP_FROM", "")
SMTP_USE_SSL = os.environ.get("CS_SMTP_SSL", "1") != "0"

WEBHOOK_TIMEOUT_S = 5.0


# -------------------------------------------------------------------
# 单个渠道
# -------------------------------------------------------------------
def _send_console(entry: Dict[str, Any], channel: Dict[str, Any]) -> None:
    print(
        f"\n[ALERT][{entry['level']}] {entry['error_type']} {entry['source']}\n"
        f"  message : {entry['message']}\n"
        f"  path    : {entry['request_path']}\n"
        f"  user    : {entry['user_id']}\n"
        f"  ts      : {entry['ts']}\n"
        f"  stack   : {(entry.get('stack_trace') or '')[:300]}\n",
        flush=True,
    )


def _send_webhook(entry: Dict[str, Any], channel: Dict[str, Any]) -> None:
    url = channel.get("url")
    if not url:
        print("[alert] webhook channel missing url; skip", flush=True)
        return
    secret = channel.get("secret") or ""
    payload = {
        "level": entry["level"],
        "error_type": entry["error_type"],
        "source": entry["source"],
        "message": entry["message"],
        "request_path": entry["request_path"],
        "user_id": entry["user_id"],
        "ts": entry["ts"],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-CS-Signature"] = sig
    with httpx.Client(timeout=WEBHOOK_TIMEOUT_S) as client:
        client.post(url, content=body, headers=headers)


def _send_email(entry: Dict[str, Any], channel: Dict[str, Any]) -> None:
    to = channel.get("to") or ""
    if not to:
        print("[alert] email channel missing 'to'; skip", flush=True)
        return
    if not SMTP_HOST or not SMTP_FROM:
        # 无 SMTP 配置 → 降级为 console（让开发期也能看到）
        print(f"[alert] SMTP 未配置，邮件降级为 console 输出 → to={to}", flush=True)
        _send_console(entry, channel)
        return
    subject = f"[CharacterSeed][{entry['level']}] {entry['source']} - {entry['message'][:60]}"
    body = json.dumps(entry, ensure_ascii=False, indent=2)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(["CharacterSeed Alerts", SMTP_FROM])
    msg["To"] = to
    if SMTP_USE_SSL:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=WEBHOOK_TIMEOUT_S) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_FROM, [x.strip() for x in to.split(",") if x.strip()], msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=WEBHOOK_TIMEOUT_S) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_FROM, [x.strip() for x in to.split(",") if x.strip()], msg.as_string())


# -------------------------------------------------------------------
# 入口
# -------------------------------------------------------------------
def dispatch(entry: Dict[str, Any], channels: List[Dict[str, Any]]) -> None:
    """
    按 channels 中的 type 字段路由到对应渠道。
    任一渠道失败仅 stderr 报错，不影响其他渠道。
    """
    if not channels:
        _send_console(entry, {"type": "console"})
        return
    for ch in channels:
        t = (ch.get("type") or "console").lower()
        try:
            t0 = time.time()
            if t == "webhook":
                _send_webhook(entry, ch)
            elif t == "email":
                _send_email(entry, ch)
            else:
                _send_console(entry, ch)
            # 调试用：渠道耗时
            _ = time.time() - t0
        except Exception as e:
            print(f"[alert] channel={t} failed: {e}", flush=True)
