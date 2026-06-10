#!/usr/bin/env python3
"""deepseek_logger — 把 DeepSeek API 调用的 token 用量写入本地日志

ai-limit 的「Token 消耗」统计有两个来源：
  1. Claude Code 日志（DeepSeek 作为 Claude Code 后端时自动覆盖）
  2. ~/.deepseek/usage.jsonl —— 本模块负责写入

如果你直接用 OpenAI 兼容 SDK 调 DeepSeek，调用后把响应的 usage 传进来即可：

    from openai import OpenAI
    from deepseek_logger import log_usage

    client = OpenAI(api_key="sk-...", base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(model="deepseek-chat", messages=[...])
    log_usage(resp.model, resp.usage)            # 记一笔，供 ai-limit 统计

也可手动记录：

    log_usage("deepseek-chat", {"prompt_tokens": 1200, "completion_tokens": 800,
                                "prompt_cache_hit_tokens": 400})
"""
import datetime
import json
import os
import pathlib

LOG_DIR = pathlib.Path(os.environ.get("DEEPSEEK_LOG_DIR", str(pathlib.Path.home() / ".deepseek")))
LOG_PATH = LOG_DIR / "usage.jsonl"


def _to_dict(usage):
    """兼容 dict 与 OpenAI/DeepSeek SDK 的 usage 对象。"""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    out = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        v = getattr(usage, k, None)
        if v is not None:
            out[k] = v
    return out


def log_usage(model: str, usage, *, timestamp: str = None) -> None:
    """追加一条用量记录到 ~/.deepseek/usage.jsonl。失败时静默（不影响主流程）。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model": model or "deepseek",
            "usage": _to_dict(usage),
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    # 自测：写一条示例记录
    log_usage("deepseek-chat", {"prompt_tokens": 100, "completion_tokens": 50,
                                "prompt_cache_hit_tokens": 20})
    print(f"wrote sample record to {LOG_PATH}")
