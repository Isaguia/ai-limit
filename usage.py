#!/usr/bin/env python3
"""
usage.py — 查看 Claude Code + DeepSeek 的实时剩余额度与 token 消耗

用法：
    python usage.py                 # 最近 7 天（默认）
    python usage.py --days 1        # 只看今天
    python usage.py --all           # 看全部历史
    python usage.py --detail        # 展示每个模型的 token 明细

DeepSeek 余额需要 API Key（任选其一）：
    export DEEPSEEK_API_KEY=sk-xxxx
    或写入 ~/.deepseek/config.json  ->  {"api_key": "sk-xxxx"}
"""
import argparse
import datetime
import locale as _locale
import os
import json
import pathlib
import sys

CLAUDE_BASE = pathlib.Path.home() / ".claude" / "projects"
# DeepSeek 可选的本地 token 日志目录（用于统计消耗）
DEEPSEEK_LOG_DIR = pathlib.Path(
    os.environ.get("DEEPSEEK_LOG_DIR", str(pathlib.Path.home() / ".deepseek"))
)
DEEPSEEK_USAGE_LOG = DEEPSEEK_LOG_DIR / "usage.jsonl"
# 记录上一次余额快照，用于推算「自上次查询以来花了多少钱」
DEEPSEEK_BALANCE_CACHE = pathlib.Path.home() / ".ai-limit-deepseek-balance.json"

TZ_LOCAL = datetime.datetime.now().astimezone().tzinfo
TZ_ABBR = datetime.datetime.now().astimezone().strftime('%Z')
__version__ = "1.0.0"

# ── 外观配置（可直接修改） ────────────────────────────────────────────────────
WARN_THRESHOLD = 20    # 剩余低于此值（%）显示黄色
CRIT_THRESHOLD = 10    # 剩余低于此值（%）显示红色
COLOR_OK   = "\033[32m"   # 绿：正常
COLOR_WARN = "\033[33m"   # 黄：偏低
COLOR_CRIT = "\033[31m"   # 红：告警
# ─────────────────────────────────────────────────────────────────────────────

_C   = sys.stdout.isatty()
_DIM = "\033[2m" if _C else ""
_BOLD= "\033[1m" if _C else ""
_RST = "\033[0m" if _C else ""
_OK  = COLOR_OK   if _C else ""
_WRN = COLOR_WARN if _C else ""
_CRT = COLOR_CRIT if _C else ""

CLAUDE_WEB_TIMEOUT_SEC = 15
DEEPSEEK_TIMEOUT_SEC = 15

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
DEEPSEEK_USAGE_URL = "https://platform.deepseek.com/usage"
DEEPSEEK_BALANCE_API = "https://api.deepseek.com/user/balance"


def _bc(r: float) -> str:
    return _OK if r >= WARN_THRESHOLD else (_WRN if r >= CRIT_THRESHOLD else _CRT)


def _colored_bar(remaining: float, width: int = 20) -> str:
    filled = round(remaining / 100 * width)
    return f"{_bc(remaining)}{'█'*filled}{_DIM}{'░'*(width-filled)}{_RST}"


def _bold_bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return f"{_BOLD}{'█'*filled}{_RST}{_DIM}{'░'*(width-filled)}{_RST}"


# ── 国际化 ────────────────────────────────────────────────────────────────────

def _detect_lang() -> str:
    env = os.environ.get("AI_LIMIT_LANG", "")
    if env:
        return "zh" if env.lower().startswith("zh") else "en"
    try:
        loc = _locale.getlocale()[0] or os.environ.get("LANG", "")
        return "zh" if loc.startswith("zh") else "en"
    except Exception:
        return "en"


LANG = _detect_lang()


def t(zh: str, en: str) -> str:
    return zh if LANG == "zh" else en


# ── 通用工具函数 ──────────────────────────────────────────────────────────────

def remaining_percent(used_pct: float) -> float:
    return max(0, min(100, 100 - used_pct))


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_money(amount: float, currency: str) -> str:
    sym = {"CNY": "¥", "USD": "$"}.get((currency or "").upper(), "")
    return f"{sym}{amount:.2f} {currency}".strip()


def fmt_dt(dt: datetime.datetime) -> str:
    return f"{dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def fmt_reset_dt(dt: datetime.datetime) -> str:
    _bare_zh = ["一", "二", "三", "四", "五", "六", "日"]
    _bare_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    today = datetime.datetime.now(TZ_LOCAL).date()
    target = dt.date()
    days = (target - today).days
    next_week = target.isocalendar()[:2] > today.isocalendar()[:2]
    if LANG == "zh":
        if days == 0:
            wd = "今天  "
        elif days == 1:
            wd = "明天  "
        elif days == 2:
            wd = "后天  "
        elif next_week:
            wd = f"下周{_bare_zh[dt.weekday()]}"
        else:
            wd = f"周{_bare_zh[dt.weekday()]}  "
    else:
        if days == 0:
            wd = "today   "
        elif days == 1:
            wd = "tomorrow"
        elif days == 2:
            wd = "2 days  "
        elif next_week:
            wd = f"next {_bare_en[dt.weekday()]}"
        else:
            wd = f"{_bare_en[dt.weekday()]:<8}"
    return f"{wd} {dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def is_deepseek_model(model: str) -> bool:
    return "deepseek" in (model or "").lower()


# ── Claude Web 额度 ───────────────────────────────────────────────────────────

class ClaudeWebError(Exception):
    """kind: 'generic' | 'cloudflare'（需人机验证）| 'auth'（登录失效）| 'timeout'"""
    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = kind


def _claude_web_context(referer: str) -> tuple:
    try:
        import browser_cookie3
    except ImportError:
        raise ClaudeWebError(t(
            "未安装 browser_cookie3，请先运行: pip install browser-cookie3",
            "browser_cookie3 not installed, run: pip install browser-cookie3",
        ))

    cookies = []
    errs = []
    for name, loader in [("Chrome", browser_cookie3.chrome), ("Firefox", browser_cookie3.firefox)]:
        try:
            jar = loader(domain_name=".claude.ai")
            cookies = [(c.name, c.value) for c in jar]
            if cookies:
                break
        except Exception as e:
            errs.append(f"{name}: {e}")

    if not cookies:
        detail = f" ({'; '.join(errs)})" if errs else ""
        raise ClaudeWebError(t(
            f"无法读取浏览器 cookie{detail}，请先在浏览器登录 claude.ai",
            f"cannot read browser cookies{detail}, please log in to claude.ai first",
        ))

    cookie_dict = dict(cookies)
    org_id = cookie_dict.get("lastActiveOrg", "")
    if not org_id:
        raise ClaudeWebError(t(
            "未能从 cookie 读取 org ID，请先在浏览器打开 claude.ai",
            "could not read org ID from cookie, please open claude.ai in your browser",
        ))

    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    headers = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://claude.ai",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    return org_id, headers


def _claude_web_get(path: str, headers: dict, timeout: int) -> dict:
    import urllib.request
    import urllib.error

    url = f"https://claude.ai{path}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()[:600].decode(errors="replace")
        is_cf = bool(e.headers.get("cf-mitigated"))
        if not is_cf:
            low = raw.lower()
            is_cf = any(m in low for m in (
                "just a moment", "challenge-platform", "/cdn-cgi/", "请验证您是真人"))
        if is_cf:
            raise ClaudeWebError(t(
                "claude.ai 触发了 Cloudflare 人机验证，请在浏览器打开 claude.ai 通过验证后重试",
                "claude.ai is showing a Cloudflare human-verification challenge; "
                "open claude.ai in your browser, pass it, then retry",
            ), kind="cloudflare")
        if e.code in (401, 403):
            raise ClaudeWebError(t(
                "claude.ai 登录态已失效，请在浏览器重新登录",
                "claude.ai session expired, please re-login in your browser",
            ), kind="auth")
        raise ClaudeWebError(f"HTTP {e.code}: {raw[:300]}")
    except Exception as e:
        raise ClaudeWebError(str(e))

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ClaudeWebError(f"非 JSON 响应: {body[:300].decode(errors='replace')}")


def live_claude_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> dict:
    """通过浏览器 session cookie 调用 claude.ai usage 接口。"""
    org_id, headers = _claude_web_context("https://claude.ai/settings/usage")
    return _claude_web_get(f"/api/organizations/{org_id}/usage", headers, timeout)


def live_claude_plan(timeout: int = CLAUDE_WEB_TIMEOUT_SEC):
    """读取 Claude 活跃组织能力，映射为用户可见套餐名；不可得时返回 None。"""
    org_id, headers = _claude_web_context("https://claude.ai/settings/billing")
    data = _claude_web_get(f"/api/organizations/{org_id}", headers, timeout)
    capabilities = set(data.get("capabilities") or [])
    raven_type = data.get("raven_type")
    if raven_type == "enterprise":
        return "Enterprise"
    if raven_type == "team":
        return "Team"
    if "claude_max" in capabilities:
        return "Max"
    if "claude_pro" in capabilities:
        return "Pro"
    if "raven" in capabilities:
        return "Enterprise"
    if "chat" in capabilities:
        return "Free"
    return None


# ── 本地 token 解析（Claude Code 日志） ───────────────────────────────────────

def collect_claude(since: datetime.datetime) -> dict:
    """
    返回 {model: {input, cache_create, cache_read, output, calls, days: set}}
    扫描 ~/.claude/projects 下所有 jsonl。DeepSeek 若作为 Claude Code 后端运行，
    其模型记录（deepseek-*）也会出现在这里，由调用方按模型名拆分。
    """
    totals: dict = {}
    if not CLAUDE_BASE.exists():
        return totals
    since_ts = since.timestamp()
    for jf in sorted(CLAUDE_BASE.rglob("*.jsonl")):
        try:
            if jf.stat().st_mtime < since_ts:
                continue
            _parse_claude_file(jf, since, totals)
        except Exception:
            pass
    return totals


def _parse_claude_file(jf: pathlib.Path, since: datetime.datetime, totals: dict):
    with open(jf, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            ts_raw = rec.get("timestamp", "")
            if not ts_raw:
                continue
            ts = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts < since:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            model = msg.get("model", "unknown")
            d = totals.setdefault(model, {
                "input": 0, "cache_create": 0, "cache_read": 0,
                "output": 0, "calls": 0, "days": set(),
            })
            d["input"] += usage.get("input_tokens", 0)
            d["cache_create"] += usage.get("cache_creation_input_tokens", 0)
            d["cache_read"] += usage.get("cache_read_input_tokens", 0)
            d["output"] += usage.get("output_tokens", 0)
            d["calls"] += 1
            d["days"].add(ts.astimezone(TZ_LOCAL).date())


# ── DeepSeek：API Key / 余额 / 消耗 ───────────────────────────────────────────

class DeepSeekError(Exception):
    """kind: 'generic' | 'no_key' | 'auth' | 'timeout'"""
    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = kind


def resolve_deepseek_key():
    """按优先级解析 DeepSeek API Key，找不到返回 None。

    顺序：DEEPSEEK_API_KEY 环境变量 → ~/.deepseek/config.json →
          ~/.config/deepseek/config.json → ~/.deepseek_api_key
    """
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env
    for cfg in (
        DEEPSEEK_LOG_DIR / "config.json",
        pathlib.Path.home() / ".config" / "deepseek" / "config.json",
    ):
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            key = (data.get("api_key") or data.get("apiKey") or "").strip()
            if key:
                return key
        except Exception:
            pass
    try:
        key = (pathlib.Path.home() / ".deepseek_api_key").read_text(encoding="utf-8").strip()
        if key:
            return key
    except Exception:
        pass
    return None


def live_deepseek_balance(api_key: str, timeout: int = DEEPSEEK_TIMEOUT_SEC) -> dict:
    """调用 DeepSeek /user/balance，返回原始 JSON：
    {"is_available": bool, "balance_infos": [{currency, total_balance, ...}]}"""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        DEEPSEEK_BALANCE_API,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "ai-limit/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise DeepSeekError(t(
                "DeepSeek API Key 无效或已过期，请检查 DEEPSEEK_API_KEY",
                "DeepSeek API key invalid or expired, check DEEPSEEK_API_KEY",
            ), kind="auth")
        raise DeepSeekError(f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")
    except Exception as e:
        raise DeepSeekError(str(e), kind="timeout")

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise DeepSeekError(t("非 JSON 响应", "non-JSON response"))


def _load_balance_snapshot():
    try:
        return json.loads(DEEPSEEK_BALANCE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_balance_snapshot(total: float, currency: str):
    try:
        DEEPSEEK_BALANCE_CACHE.write_text(json.dumps({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total": total,
            "currency": currency,
        }), encoding="utf-8")
    except Exception:
        pass


def collect_deepseek_tokens(since: datetime.datetime, claude_totals: dict) -> dict:
    """
    汇总 DeepSeek 的 token 消耗，返回 {model: {input, cache_read, output, calls, days}}。

    两个来源合并：
      1. Claude Code 日志里 model 名包含 deepseek 的记录（DeepSeek 作后端时）
      2. ~/.deepseek/usage.jsonl 本地用量日志（DeepSeek 原生 usage 格式）
    """
    totals: dict = {}

    def _add(model, inp, cache, out, day):
        d = totals.setdefault(model, {
            "input": 0, "cache_read": 0, "output": 0, "calls": 0, "days": set(),
        })
        d["input"] += inp
        d["cache_read"] += cache
        d["output"] += out
        d["calls"] += 1
        if day:
            d["days"].add(day)

    # 来源 1：从已扫描的 Claude 日志里挑出 deepseek 模型
    for model, d in (claude_totals or {}).items():
        if is_deepseek_model(model):
            tgt = totals.setdefault(model, {
                "input": 0, "cache_read": 0, "output": 0, "calls": 0, "days": set(),
            })
            tgt["input"] += d.get("input", 0) + d.get("cache_create", 0)
            tgt["cache_read"] += d.get("cache_read", 0)
            tgt["output"] += d.get("output", 0)
            tgt["calls"] += d.get("calls", 0)
            tgt["days"] |= d.get("days", set())

    # 来源 2：本地 usage.jsonl（DeepSeek 原生 usage 字段）
    if DEEPSEEK_USAGE_LOG.exists():
        try:
            with open(DEEPSEEK_USAGE_LOG, errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_raw = rec.get("timestamp") or rec.get("created")
                    day = None
                    if ts_raw:
                        try:
                            ts = (datetime.datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                                  if not str(ts_raw).isdigit()
                                  else datetime.datetime.fromtimestamp(int(ts_raw), datetime.timezone.utc))
                            if ts < since:
                                continue
                            day = ts.astimezone(TZ_LOCAL).date()
                        except Exception:
                            pass
                    usage = rec.get("usage") or rec
                    model = rec.get("model", "deepseek")
                    # DeepSeek 原生 prompt_tokens 已含缓存命中部分（与 Claude 的
                    # 分离字段不同），需拆出非缓存部分，避免在渲染时重复计入。
                    cache = (usage.get("prompt_cache_hit_tokens")
                             or usage.get("cache_read_input_tokens") or 0)
                    prompt = usage.get("prompt_tokens")
                    if prompt is not None:
                        inp = max(0, prompt - cache)  # 非缓存输入 = 总输入 - 缓存命中
                    else:
                        inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
                    _add(model, inp, cache, out, day)
        except Exception:
            pass

    return totals


# ── 渲染 ─────────────────────────────────────────────────────────────────────

SEP = "─" * 52


def _render_token_block(totals: dict, label_in_net="净输入(非缓存)", detail=False):
    """共用：渲染 token 总量 + 每模型明细 + 输出占比。totals 需含
    input/cache_read/output/calls/days 字段。返回是否有数据。"""
    active = {m: d for m, d in totals.items() if m != "<synthetic>"}
    if not active:
        return False
    grand_out = sum(d["output"] for d in active.values())
    grand_in_net = sum(d["input"] for d in active.values())
    show_ratio = len(active) > 1 and grand_out > 0

    if detail:
        for model in sorted(active.keys()):
            d = active[model]
            total_in = d["input"] + d.get("cache_read", 0)
            cache_pct = d.get("cache_read", 0) / total_in * 100 if total_in else 0
            print(f"  {model}")
            print(f"    {t('调用次数', 'Calls')}: {d['calls']:,}")
            print(f"    {t('输入合计', 'Input')}: {fmt_tokens(total_in):>8}  ({t(f'缓存命中 {cache_pct:.0f}%', f'cache hit {cache_pct:.0f}%')})")
            print(f"    {t('输出合计', 'Output')}: {fmt_tokens(d['output']):>8}")
            actual_days = len(d.get("days", set()))
            if actual_days > 0:
                rate = d["output"] / actual_days
                print(f"    {t('日均输出', 'Daily avg')}: {fmt_tokens(int(rate)):>8}  ({t(f'共 {actual_days} 天有记录', f'{actual_days} days recorded')})")
            print()

    print(f"  {t('总输出', 'Total output')}: {_BOLD}{fmt_tokens(grand_out)}{_RST}  |  {t(label_in_net, 'Net input (non-cache)')}: {_BOLD}{fmt_tokens(grand_in_net)}{_RST}")
    if show_ratio:
        print(f"\n  {_BOLD}{t('输出占比', 'Output share')}{_RST}")
        name_w = max(len(m.replace("claude-", "")) for m in active)
        for m in sorted(active.keys(), key=lambda x: active[x]["output"], reverse=True):
            pct = active[m]["output"] / grand_out * 100
            pct_str = "<1%" if pct < 1 else f"{pct:.0f}%"
            short = m.replace("claude-", "")
            print(f"  {short:<{name_w}}  {_bold_bar(pct)}  {pct_str}")
    return True


def render_claude(totals: dict, since: datetime.datetime, days_count: int,
                  web_data: dict = None, web_error: str = None, detail: bool = False):
    title = "Claude Code"
    print(f"\n{_DIM}{SEP}{_RST}")
    print(f"{_BOLD}{title.center(52)}{_RST}")
    print()
    since_local = since.astimezone(TZ_LOCAL)
    print(f"  {_DIM}{t('统计自', 'Since')}: {fmt_dt(since_local)}  ({t(f'近 {days_count} 天', f'last {days_count} days')}){_RST}")

    # 只统计非 deepseek 模型（deepseek 归入 DeepSeek 区块）
    claude_only = {m: d for m, d in (totals or {}).items() if not is_deepseek_model(m)}

    # 把 collect_claude 的字段对齐到 _render_token_block 期望的格式
    norm = {}
    for m, d in claude_only.items():
        norm[m] = {
            "input": d["input"] + d["cache_create"],
            "cache_read": d["cache_read"],
            "output": d["output"],
            "calls": d["calls"],
            "days": d["days"],
        }

    if not norm:
        print(t("  （该时间段无记录）", "  (no records in this period)"))
    else:
        print()
        _render_token_block(norm, detail=detail)

    # 实时额度
    if web_data is not None:
        five_h = web_data.get("five_hour") or {}
        seven_d = web_data.get("seven_day") or {}
        if five_h or seven_d:
            print(f"\n  {_BOLD}{t('实时额度', 'Live quota')}{_RST}  {_DIM}{t('(与 --days 统计范围无关)', '(independent of --days range)')}{_RST}")
            print(f"  {_DIM}{t('数据来源', 'Source')}: claude.ai usage API  ({t('浏览器登录态', 'browser session')}){_RST}")
            print()
            for win_key, label, win in [
                ("5h", t("5小时滚动窗", "5-hour window"), five_h),
                ("7d", t("7天滚动窗  ", "7-day window "), seven_d),
            ]:
                if not win:
                    continue
                used = float(win.get("utilization", 0))
                remaining = remaining_percent(used)
                r_str = f"{_bc(remaining)}{_BOLD}{remaining:.0f}%{_RST}"
                print(f"  {label}  {_colored_bar(remaining)}  {t(f'剩余 {r_str}  {_DIM}(已用 {used:.0f}%){_RST}', f'left {r_str}  {_DIM}(used {used:.0f}%){_RST}')}")
                resets_at = win.get("resets_at")
                if resets_at:
                    try:
                        reset_dt = datetime.datetime.fromisoformat(resets_at).astimezone(TZ_LOCAL)
                        print(f"  {_DIM}{t('重置时间', 'Resets at')}: {fmt_reset_dt(reset_dt)}{_RST}")
                    except Exception:
                        pass
                print()
        else:
            print(f"\n  {t('claude.ai usage 原始响应', 'claude.ai usage raw response')}: {json.dumps(web_data, ensure_ascii=False)[:400]}")
            print(f"  →  {CLAUDE_USAGE_URL}")
    elif web_error:
        print(f"\n  {t('实时额度  (与 --days 统计范围无关)', 'Live quota  (independent of --days range)')}")
        print(f"  ⚠️  {t('读取失败', 'Failed to fetch')}: {web_error}")
        print(f"  →  {CLAUDE_USAGE_URL}")
    else:
        print(f"\n  ⚠️  {t('Claude 周额度本地不可得', 'Claude quota unavailable locally')}  →  {CLAUDE_USAGE_URL}")


def render_deepseek(ds_totals: dict, since: datetime.datetime, days_count: int,
                    balance: dict = None, balance_error=None,
                    spent_delta=None, detail: bool = False):
    title = "DeepSeek"
    print(f"\n{_DIM}{SEP}{_RST}")
    print(f"{_BOLD}{title.center(52)}{_RST}")
    print()

    # 实时余额
    print(f"  {_BOLD}{t('实时余额', 'Live balance')}{_RST}")
    print(f"  {_DIM}{t('数据来源', 'Source')}: api.deepseek.com/user/balance{_RST}")
    if balance is not None:
        infos = balance.get("balance_infos") or []
        available = balance.get("is_available")
        if not infos:
            print(t("  （未返回余额信息）", "  (no balance info returned)"))
        for info in infos:
            cur = info.get("currency", "")
            total = float(info.get("total_balance", 0) or 0)
            granted = float(info.get("granted_balance", 0) or 0)
            topped = float(info.get("topped_up_balance", 0) or 0)
            print()
            print(f"  {t('可用余额', 'Total balance')}: {_BOLD}{fmt_money(total, cur)}{_RST}")
            print(f"  {_DIM}{t('赠送额度', 'Granted')}: {fmt_money(granted, cur)}   {t('充值余额', 'Topped-up')}: {fmt_money(topped, cur)}{_RST}")
            if spent_delta is not None and spent_delta > 0:
                print(f"  {_DIM}{t('自上次查询已消耗', 'Spent since last check')}: {fmt_money(spent_delta, cur)}{_RST}")
        status = (t("可调用 ✅", "available ✅") if available
                  else t("余额不足 ⚠️", "insufficient ⚠️"))
        print(f"  {t('账户状态', 'Status')}: {status}")
    elif balance_error:
        print(f"  ⚠️  {t('读取失败', 'Failed to fetch')}: {balance_error}")
        print(f"  →  {DEEPSEEK_USAGE_URL}")

    # token 消耗
    print(f"\n  {_BOLD}{t('Token 消耗', 'Token usage')}{_RST}  {_DIM}({t(f'近 {days_count} 天', f'last {days_count} days')}){_RST}")
    print(f"  {_DIM}{t('数据来源', 'Source')}: {t('本地日志', 'local logs')} (~/.claude, ~/.deepseek/usage.jsonl){_RST}")
    print()
    if not ds_totals:
        print(t("  （未找到 DeepSeek 本地用量记录）", "  (no local DeepSeek usage records found)"))
        print(f"  {_DIM}{t('提示：DeepSeek 作为 Claude Code 后端或写入 ~/.deepseek/usage.jsonl 时可统计', 'Tip: tracked when DeepSeek is used as a Claude Code backend or logged to ~/.deepseek/usage.jsonl')}{_RST}")
    else:
        _render_token_block(ds_totals, detail=detail)


def render_footer():
    print(f"\n{_DIM}{SEP}{_RST}\n")


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=t("查看 Claude Code / DeepSeek 实时额度与 token 消耗",
                      "Show Claude Code / DeepSeek live quota and token usage"),
    )
    parser.add_argument("--days", type=int, default=7,
                        help=t("统计最近 N 天（默认 7）", "show last N days (default: 7)"))
    parser.add_argument("--all", action="store_true",
                        help=t("统计全部历史（忽略 --days）", "show all history (overrides --days)"))
    parser.add_argument("--detail", action="store_true",
                        help=t("展示每个模型的详细 token 统计", "show per-model token breakdown"))
    parser.add_argument("--only", choices=["claude", "deepseek"], default=None,
                        help=t("只显示某个服务", "show only one service"))
    args = parser.parse_args()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if args.all:
        since = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        days_count = (now_utc - since).days
    else:
        since = now_utc - datetime.timedelta(days=args.days)
        days_count = args.days

    now_local = datetime.datetime.now(TZ_LOCAL)
    _wd_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    _wd_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    wd_now = _wd_zh[now_local.weekday()] if LANG == "zh" else _wd_en[now_local.weekday()]
    print(f"\n{_DIM}{t('查询时间', 'Queried at')}: {wd_now} {now_local.strftime('%m-%d %H:%M')} {TZ_ABBR}{_RST}")

    claude_totals = collect_claude(since)

    # ── Claude Code 区块 ──
    if args.only != "deepseek":
        web_data, web_error = None, None
        try:
            web_data = live_claude_usage()
        except ClaudeWebError as e:
            web_error = str(e)
        render_claude(claude_totals, since, days_count,
                      web_data=web_data, web_error=web_error, detail=args.detail)

    # ── DeepSeek 区块 ──
    if args.only != "claude":
        ds_totals = collect_deepseek_tokens(since, claude_totals)
        balance, balance_error, spent_delta = None, None, None
        api_key = resolve_deepseek_key()
        if not api_key:
            balance_error = t(
                "未配置 API Key（设置 DEEPSEEK_API_KEY 或写入 ~/.deepseek/config.json）",
                "no API key (set DEEPSEEK_API_KEY or ~/.deepseek/config.json)",
            )
        else:
            try:
                balance = live_deepseek_balance(api_key)
                infos = balance.get("balance_infos") or []
                if infos:
                    cur = infos[0].get("currency", "")
                    total = float(infos[0].get("total_balance", 0) or 0)
                    snap = _load_balance_snapshot()
                    if snap and snap.get("currency") == cur:
                        delta = float(snap.get("total", total)) - total
                        if delta > 0:
                            spent_delta = delta
                    _save_balance_snapshot(total, cur)
            except DeepSeekError as e:
                balance_error = str(e)
        render_deepseek(ds_totals, since, days_count,
                        balance=balance, balance_error=balance_error,
                        spent_delta=spent_delta, detail=args.detail)

    render_footer()


if __name__ == "__main__":
    main()
