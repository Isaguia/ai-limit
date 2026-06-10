#!/usr/bin/env python3
"""ai-limit 菜单栏 App（rumps 版）

独立 macOS App，菜单栏实时显示 Claude Code 额度 + DeepSeek 余额。
py2app 打包：cd menubar && python3 setup.py py2app
"""
import datetime
import json
import pathlib
import sys
import threading
import webbrowser

import rumps
import AppKit

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from usage import (
    __version__,
    live_claude_plan,
    live_claude_usage,
    live_deepseek_balance,
    resolve_deepseek_key,
    fmt_money,
    ClaudeWebError,
    DeepSeekError,
    TZ_LOCAL,
)


def _detect_system_lang() -> str:
    try:
        langs = AppKit.NSLocale.preferredLanguages()
        if langs and str(langs[0]).lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


_SYSTEM_LANG = _detect_system_lang()

# ── 常量 ─────────────────────────────────────────────────────────────────────

_STATE_PATH   = pathlib.Path.home() / ".ai-limit-menubar.json"
_CACHE_PATH   = pathlib.Path.home() / ".ai-limit-menubar-cache.json"
_CACHE_TTL    = 55
_REFRESH_SEC  = 60
_DISPLAY_MODES = ("5h", "7d")
_LANGS         = ("zh", "en", "auto")
_SERVICES      = ("claude", "deepseek")
_MENU_MIN_WIDTH = 290
_ZH_WEEKDAYS   = "一二三四五六日"
_EN_WEEKDAYS   = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_PROJECT_URL   = "https://github.com/aguithub/ai-limit"
_AUTHOR_URL    = "https://github.com/aguithub"
_CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
_DEEPSEEK_USAGE_URL = "https://platform.deepseek.com/usage"
_LAUNCH_AGENT_LABEL = "com.aguithub.ai-limit"
_LAUNCH_AGENT_PLIST = pathlib.Path.home() / "Library/LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"
_APP_EXECUTABLE     = pathlib.Path("/Applications/ai-limit.app/Contents/MacOS/ai-limit")

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _login_item_enabled():
    return _LAUNCH_AGENT_PLIST.exists()

def _set_login_item(enabled: bool):
    if enabled:
        _LAUNCH_AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        _LAUNCH_AGENT_PLIST.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_APP_EXECUTABLE}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
""",
            encoding="utf-8",
        )
    else:
        try:
            _LAUNCH_AGENT_PLIST.unlink()
        except FileNotFoundError:
            pass

def _tr(lang, zh, en):
    return en if lang == "en" else zh

def _native_bar(pct, width=4):
    filled = round(max(0, min(100, pct)) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)

def _fmt_plan(plan, lang="zh"):
    if not plan or plan == "?":
        return ""
    plan = str(plan).replace("_", " ").title()
    return f" Plan: {plan}" if lang == "en" else f" 方案：{plan}"

def _fmt_reset_dt(dt, lang):
    today = datetime.datetime.now(TZ_LOCAL).date()
    target = dt.date()
    days = (target - today).days
    next_week = target.isocalendar()[:2] > today.isocalendar()[:2]
    if lang == "en":
        if days == 0:    wd = "today"
        elif days == 1:  wd = "tomorrow"
        elif days == 2:  wd = "2 days"
        elif next_week:  wd = f"next {_EN_WEEKDAYS[dt.weekday()]}"
        else:            wd = _EN_WEEKDAYS[dt.weekday()]
        return f"{dt:%H:%M}  {wd}"
    if days == 0:    wd = "今天"
    elif days == 1:  wd = "明天"
    elif days == 2:  wd = "后天"
    elif next_week:  wd = f"下周{_ZH_WEEKDAYS[dt.weekday()]}"
    else:            wd = f"周{_ZH_WEEKDAYS[dt.weekday()]}"
    if len(wd) < 3:
        wd += "　" * (3 - len(wd))
    return f"{wd} {dt:%H:%M}"

def _fmt_reset_iso(iso, lang="zh"):
    try:
        return _fmt_reset_dt(datetime.datetime.fromisoformat(iso).astimezone(TZ_LOCAL), lang)
    except Exception:
        return "?"

# ── 状态 / 缓存 ──────────────────────────────────────────────────────────────

def _load_state():
    state = {"global": "5h", "lang": "auto", "services": list(_SERVICES)}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
            if raw.get("lang") in _LANGS:
                state["lang"] = raw["lang"]
            if isinstance(raw.get("services"), list):
                svc = [s for s in raw["services"] if s in _SERVICES]
                if svc:
                    state["services"] = svc
    except Exception:
        pass
    return state

def _save_state(state):
    try:
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

def _load_cache():
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.datetime.now().timestamp() - float(raw.get("cached_at", 0))
        if age <= _CACHE_TTL:
            return raw.get("claude"), raw.get("deepseek")
    except Exception:
        pass
    return None, None

def _save_cache(claude, deepseek):
    try:
        _CACHE_PATH.write_text(
            json.dumps({
                "cached_at": datetime.datetime.now().timestamp(),
                "claude": claude,
                "deepseek": deepseek,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

# ── 数据获取 ─────────────────────────────────────────────────────────────────

def _fetch_claude(lang):
    import socket, urllib.error
    try:
        data = live_claude_usage()
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        try:
            plan = live_claude_plan()
        except Exception:
            plan = None
        return {
            "5h_left":  int(round(100 - float(five_h.get("utilization", 0)))),
            "7d_left":  int(round(100 - float(seven_d.get("utilization", 0)))),
            "5h_reset": five_h.get("resets_at"),
            "7d_reset": seven_d.get("resets_at"),
            "plan":     plan,
        }
    except ClaudeWebError as e:
        kind = getattr(e, "kind", "generic")
        if kind == "cloudflare":
            msg = _tr(lang, "需在浏览器通过 claude.ai 人机验证", "Pass claude.ai human-check in browser")
        elif kind == "auth":
            msg = _tr(lang, "需在浏览器重新登录 claude.ai", "Re-login at claude.ai in browser")
        else:
            msg = str(e)
            if "JSON" in msg or "DOCTYPE" in msg or "html" in msg.lower():
                msg = _tr(lang, "网络不可用或需重新登录 claude.ai", "Network error or re-login at claude.ai required")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _fetch_deepseek(lang):
    import socket, urllib.error
    api_key = resolve_deepseek_key()
    if not api_key:
        return {"error": _tr(lang,
            "未配置 API Key（设置 DEEPSEEK_API_KEY）",
            "No API key (set DEEPSEEK_API_KEY)")}
    try:
        data = live_deepseek_balance(api_key)
        infos = data.get("balance_infos") or []
        if not infos:
            return {"error": _tr(lang, "未返回余额信息", "no balance info returned")}
        info = infos[0]
        return {
            "currency":  info.get("currency", ""),
            "total":     float(info.get("total_balance", 0) or 0),
            "granted":   float(info.get("granted_balance", 0) or 0),
            "topped_up": float(info.get("topped_up_balance", 0) or 0),
            "available": bool(data.get("is_available")),
        }
    except DeepSeekError as e:
        kind = getattr(e, "kind", "generic")
        if kind == "auth":
            return {"error": _tr(lang, "API Key 无效或已过期", "API key invalid or expired")}
        if kind == "timeout":
            return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
        return {"error": str(e)}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

# ── AppKit 辅助 ───────────────────────────────────────────────────────────────

def _status_button(app):
    candidates = ("_status_item", "_status_bar_item", "_nsstatusitem")
    for attr in candidates:
        item = getattr(app, attr, None)
        if item and hasattr(item, "button"):
            return item.button()
    nsapp = getattr(app, "_nsapp", None)
    if nsapp is not None:
        item = getattr(nsapp, "nsstatusitem", None)
        if item and hasattr(item, "button"):
            return item.button()
    for name in dir(app):
        if name.startswith("__"):
            continue
        try:
            item = getattr(app, name)
        except Exception:
            continue
        if item is not None and hasattr(item, "button") and callable(getattr(item, "button", None)):
            try:
                btn = item.button()
                if hasattr(btn, "setTitle_") and hasattr(btn, "setImage_"):
                    return btn
            except Exception:
                continue
    return None


def _set_bar_title(app, text):
    btn = _status_button(app)
    if btn is not None:
        btn.setImage_(None)
        btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_(""))
        btn.setTitle_(text)
        btn.setImagePosition_(0)
        return
    app.title = text


def _sf_battery_image(pct, point_size=14):
    if pct >= 88:
        name = "battery.100"
    elif pct >= 63:
        name = "battery.75"
    elif pct >= 38:
        name = "battery.50"
    elif pct >= 13:
        name = "battery.25"
    else:
        name = "battery.0"
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size, AppKit.NSFontWeightMedium
    )
    return img.imageWithSymbolConfiguration_(cfg)


def _battery_attachment(pct, font):
    bat = _sf_battery_image(pct)
    if bat is None:
        return None
    bat.setTemplate_(True)
    attach = AppKit.NSTextAttachment.alloc().init()
    attach.setImage_(bat)
    sz = bat.size()
    y_offset = (font.capHeight() - sz.height) / 2
    attach.setBounds_(AppKit.NSMakeRect(0, y_offset, sz.width, sz.height))
    return AppKit.NSAttributedString.attributedStringWithAttachment_(attach)


def _render_attributed_title(items):
    """items: list of dicts. Claude 项带电池图标；DeepSeek 项纯文字（余额）。
    每项: {"label":..., "kind": "pct"|"text"|"err", "pct":int, "text":str}"""
    font = AppKit.NSFont.menuBarFontOfSize_(0)
    text_attrs = {AppKit.NSFontAttributeName: font}
    mas = AppKit.NSMutableAttributedString.alloc().init()

    def append_text(s):
        mas.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(s, text_attrs)
        )

    for i, it in enumerate(items):
        prefix = "  " if i > 0 else ""
        if it["kind"] == "err":
            append_text(f"{prefix}{it['label']} ⚠️")
        elif it["kind"] == "pct":
            append_text(f"{prefix}{it['label']} {it['pct']}% ")
            bat = _battery_attachment(it["pct"], font)
            if bat is not None:
                mas.appendAttributedString_(bat)
        else:  # text
            append_text(f"{prefix}{it['label']} {it['text']}")

    if mas.length() == 0:
        append_text("ai-limit ⚠️")
    return mas


def _set_bar_with_items(app, items):
    btn = _status_button(app)
    if btn is None:
        raise RuntimeError("no status button")
    btn.setImage_(None)
    btn.setTitle_("")
    btn.setAttributedTitle_(_render_attributed_title(items))

def _noop(_):
    pass


def _disable(menu_item):
    menu_item._menuitem.setEnabled_(False)
    return menu_item


def _inert(menu_item):
    menu_item.set_callback(_noop)
    return menu_item

def _detail_text(mode, pct, reset, lang):
    if lang == "en":
        return f"  {mode}\t{pct:>3}% left   \t↻ {reset}"
    return f"  {mode}\t{pct:>3}% 剩余\t↻ {reset}"

# ── 主 App ────────────────────────────────────────────────────────────────────

class AiLimitApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self._state = _load_state()
        self._claude = None
        self._deepseek = None
        self._pending = None
        self._pending_lock = threading.Lock()
        self._build_menu()

    def _lang(self):
        choice = self._state["lang"]
        return choice if choice in ("zh", "en") else _SYSTEM_LANG

    # ── 菜单构建 ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        lang = self._lang()

        self._claude_header = _inert(rumps.MenuItem("Claude Code"))
        self._claude_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._claude_7d     = _inert(rumps.MenuItem("  7d  …"))

        self._ds_header = _inert(rumps.MenuItem("DeepSeek"))
        self._ds_total  = _inert(rumps.MenuItem("  …"))
        self._ds_detail = _inert(rumps.MenuItem("  …"))

        self._last_refresh = _disable(rumps.MenuItem("…"))

        self._mode_5h = rumps.MenuItem("5 小时" if lang == "zh" else "5 hours",
                                       callback=self._set_mode_5h)
        self._mode_7d = rumps.MenuItem("7 天" if lang == "zh" else "7 days",
                                       callback=self._set_mode_7d)
        mode_label = "Claude 菜单栏窗口" if lang == "zh" else "Claude bar window"
        self._mode_menu = rumps.MenuItem(mode_label)
        self._mode_menu.add(self._mode_5h)
        self._mode_menu.add(self._mode_7d)

        self._lang_auto = rumps.MenuItem(_tr(lang, "跟随系统", "Follow System"), callback=self._set_lang_auto)
        self._lang_zh   = rumps.MenuItem("中文", callback=self._set_lang_zh)
        self._lang_en   = rumps.MenuItem("English", callback=self._set_lang_en)
        lang_label = "语言" if lang == "zh" else "Language"
        self._lang_menu = rumps.MenuItem(lang_label)
        self._lang_menu.add(self._lang_auto)
        self._lang_menu.add(self._lang_zh)
        self._lang_menu.add(self._lang_en)

        self._svc_claude = rumps.MenuItem("Claude Code", callback=self._toggle_claude)
        self._svc_ds     = rumps.MenuItem("DeepSeek",    callback=self._toggle_deepseek)
        svc_label = "监控服务" if lang == "zh" else "Services"
        self._svc_menu = rumps.MenuItem(svc_label)
        self._svc_menu.add(self._svc_claude)
        self._svc_menu.add(self._svc_ds)

        self._login_item = rumps.MenuItem(
            "开机自启" if lang == "zh" else "Launch at Login",
            callback=self._toggle_login_item,
        )
        self._update_login_item_check()

        self._refresh_item = rumps.MenuItem(
            "立即刷新" if lang == "zh" else "Refresh now",
            callback=self._force_refresh,
        )
        self._ds_dash = rumps.MenuItem(
            "打开 DeepSeek 用量页" if lang == "zh" else "Open DeepSeek usage",
            callback=lambda _: webbrowser.open(_DEEPSEEK_USAGE_URL),
        )
        self._claude_dash = rumps.MenuItem(
            "打开 Claude 用量页" if lang == "zh" else "Open Claude usage",
            callback=lambda _: webbrowser.open(_CLAUDE_USAGE_URL),
        )

        about_label = f"关于（ai-limit {__version__}）" if lang == "zh" else f"About (ai-limit {__version__})"
        self._about_menu   = rumps.MenuItem(about_label)
        self._about_ver    = rumps.MenuItem(f"ai-limit {__version__}",
                                            callback=lambda _: webbrowser.open(_PROJECT_URL))
        self._about_desc   = _disable(rumps.MenuItem(
            "Claude Code / DeepSeek 额度监控" if lang == "zh" else "Claude Code / DeepSeek quota monitor"
        ))
        self._about_src    = _disable(rumps.MenuItem(
            "数据来源：本地日志 + 官方接口" if lang == "zh" else "Source: local logs + official APIs"
        ))
        self._about_menu.add(self._about_ver)
        self._about_menu.add(self._about_desc)
        self._about_menu.add(self._about_src)

        self._star_item = rumps.MenuItem(
            "⭐ 给个 Star" if lang == "zh" else "⭐ Star on GitHub",
            callback=lambda _: webbrowser.open(_PROJECT_URL),
        )
        self._about_menu.add(self._star_item)

        self._quit_item = rumps.MenuItem(
            "退出" if lang == "zh" else "Quit",
            callback=rumps.quit_application,
        )

        self.menu = [
            self._claude_header,
            self._claude_5h,
            self._claude_7d,
            None,
            self._ds_header,
            self._ds_total,
            self._ds_detail,
            None,
            self._last_refresh,
            None,
            self._mode_menu,
            self._lang_menu,
            self._svc_menu,
            self._login_item,
            None,
            self._refresh_item,
            self._ds_dash,
            self._claude_dash,
            None,
            self._about_menu,
            None,
            self._quit_item,
        ]
        self.menu._menu.setMinimumWidth_(_MENU_MIN_WIDTH)
        self._update_mode_checks()
        self._update_lang_checks()
        self._update_service_checks()

    # ── 数据更新 ──────────────────────────────────────────────────────────────

    @rumps.timer(0.3)
    def _init_render(self, sender):
        self._refresh_from_cache()
        self._kick_background_fetch()
        sender.stop()

    @rumps.timer(_REFRESH_SEC)
    def _auto_refresh(self, _):
        self._kick_background_fetch()

    @rumps.timer(0.4)
    def _apply_pending(self, _):
        with self._pending_lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        claude, deepseek = pending
        if claude is not None:
            self._claude = claude
        if deepseek is not None:
            self._deepseek = deepseek
        _save_cache(self._claude, self._deepseek)
        self._render()

    def _refresh_from_cache(self):
        claude, deepseek = _load_cache()
        if claude is not None:
            self._claude = claude
        if deepseek is not None:
            self._deepseek = deepseek
        self._render()

    def _kick_background_fetch(self):
        th = threading.Thread(target=self._async_refresh, daemon=True)
        th.start()

    def _async_refresh(self):
        lang = self._lang()
        services = self._state.get("services") or list(_SERVICES)
        claude = _fetch_claude(lang) if "claude" in services else None
        deepseek = _fetch_deepseek(lang) if "deepseek" in services else None
        with self._pending_lock:
            self._pending = (claude, deepseek)

    def _render(self):
        lang     = self._lang()
        mode     = self._state["global"]
        services = self._state.get("services") or list(_SERVICES)
        show_claude = "claude" in services
        show_ds     = "deepseek" in services
        claude = self._claude or {}
        deepseek = self._deepseek or {}

        bar_items = []
        if show_claude:
            if "error" in claude:
                bar_items.append({"label": "Claude", "kind": "err"})
            elif claude:
                pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
                bar_items.append({"label": "Claude", "kind": "pct", "pct": pct})
        if show_ds:
            if "error" in deepseek:
                bar_items.append({"label": "DS", "kind": "err"})
            elif deepseek:
                bal = fmt_money(deepseek["total"], deepseek["currency"])
                bar_items.append({"label": "DS", "kind": "text", "text": bal})
        try:
            _set_bar_with_items(self, bar_items)
        except Exception:
            parts = []
            for it in bar_items:
                if it["kind"] == "err":
                    parts.append(f"{it['label']} ⚠️")
                elif it["kind"] == "pct":
                    parts.append(f"{it['label']} {it['pct']}% {_native_bar(it['pct'])}")
                else:
                    parts.append(f"{it['label']} {it['text']}")
            _set_bar_title(self, "  ".join(parts) if parts else "ai-limit ⚠️")

        # Claude 区块
        self._claude_header._menuitem.setHidden_(not show_claude)
        self._claude_5h._menuitem.setHidden_(not show_claude)
        self._claude_7d._menuitem.setHidden_(not show_claude)
        if show_claude:
            if "error" in claude:
                self._claude_header.title = "Claude Code ⚠️"
                self._claude_5h.title = f"  {claude['error'][:60]}"
                self._claude_7d._menuitem.setHidden_(True)
            elif claude:
                plan = _fmt_plan(claude.get("plan"), lang)
                self._claude_header.title = f"Claude Code{plan}"
                c5_reset = _fmt_reset_iso(claude["5h_reset"], lang)
                c7_reset = _fmt_reset_iso(claude["7d_reset"], lang)
                self._claude_5h.title = _detail_text("5h", claude["5h_left"], c5_reset, lang)
                self._claude_7d.title = _detail_text("7d", claude["7d_left"], c7_reset, lang)

        # DeepSeek 区块
        self._ds_header._menuitem.setHidden_(not show_ds)
        self._ds_total._menuitem.setHidden_(not show_ds)
        self._ds_detail._menuitem.setHidden_(not show_ds)
        if show_ds:
            if "error" in deepseek:
                self._ds_header.title = "DeepSeek ⚠️"
                self._ds_total.title = f"  {deepseek['error'][:60]}"
                self._ds_detail._menuitem.setHidden_(True)
            elif deepseek:
                avail = (_tr(lang, "可调用", "available") if deepseek.get("available")
                         else _tr(lang, "余额不足", "insufficient"))
                self._ds_header.title = f"DeepSeek  ({avail})"
                cur = deepseek["currency"]
                self._ds_total.title = _tr(lang,
                    f"  可用余额\t{fmt_money(deepseek['total'], cur)}",
                    f"  Balance\t{fmt_money(deepseek['total'], cur)}")
                self._ds_detail.title = _tr(lang,
                    f"  赠送 {fmt_money(deepseek['granted'], cur)}  充值 {fmt_money(deepseek['topped_up'], cur)}",
                    f"  Granted {fmt_money(deepseek['granted'], cur)}  Topped {fmt_money(deepseek['topped_up'], cur)}")

        now = datetime.datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
        self._last_refresh.title = _tr(lang, f"上次刷新: {now}", f"Last refresh: {now}")

    # ── 模式 / 语言切换 ──────────────────────────────────────────────────────

    def _set_mode_5h(self, _):
        self._state["global"] = "5h"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()

    def _set_mode_7d(self, _):
        self._state["global"] = "7d"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()

    def _update_mode_checks(self):
        lang = self._lang()
        mode = self._state["global"]
        self._mode_5h.title = ("✓ " if mode == "5h" else "  ") + _tr(lang, "5 小时", "5 hours")
        self._mode_7d.title = ("✓ " if mode == "7d" else "  ") + _tr(lang, "7 天", "7 days")
        sel = _tr(lang, "5 小时", "5 hours") if mode == "5h" else _tr(lang, "7 天", "7 days")
        self._mode_menu.title = _tr(lang, f"Claude 菜单栏窗口（{sel}）", f"Claude bar window ({sel})")

    def _set_lang_auto(self, _):
        self._state["lang"] = "auto"
        _save_state(self._state)
        self._after_lang_change()

    def _set_lang_zh(self, _):
        self._state["lang"] = "zh"
        _save_state(self._state)
        self._after_lang_change()

    def _set_lang_en(self, _):
        self._state["lang"] = "en"
        _save_state(self._state)
        self._after_lang_change()

    def _after_lang_change(self):
        self._update_lang_checks()
        self._update_mode_checks()
        self._update_service_checks()
        self._refresh_static_labels()
        self._render()

    def _refresh_static_labels(self):
        lang = self._lang()
        self._refresh_item.title = _tr(lang, "立即刷新", "Refresh now")
        self._ds_dash.title     = _tr(lang, "打开 DeepSeek 用量页", "Open DeepSeek usage")
        self._claude_dash.title = _tr(lang, "打开 Claude 用量页", "Open Claude usage")
        self._about_menu.title  = _tr(lang,
            f"关于（ai-limit {__version__}）",
            f"About (ai-limit {__version__})",
        )
        self._about_desc.title   = _tr(lang,
            "Claude Code / DeepSeek 额度监控",
            "Claude Code / DeepSeek quota monitor",
        )
        self._about_src.title    = _tr(lang,
            "数据来源：本地日志 + 官方接口",
            "Source: local logs + official APIs",
        )
        self._update_login_item_check()
        self._star_item.title    = _tr(lang, "⭐ 给个 Star", "⭐ Star on GitHub")
        self._quit_item.title    = _tr(lang, "退出", "Quit")

    def _update_lang_checks(self):
        choice = self._state["lang"]
        lang = self._lang()
        self._lang_auto.title = ("✓ " if choice == "auto" else "  ") + _tr(lang, "跟随系统", "Follow System")
        self._lang_zh.title   = ("✓ " if choice == "zh"   else "  ") + "中文"
        self._lang_en.title   = ("✓ " if choice == "en"   else "  ") + "English"
        sel_zh = {"zh": "中文", "en": "English"}.get(choice, "跟随系统")
        sel_en = {"zh": "中文", "en": "English"}.get(choice, "Follow System")
        self._lang_menu.title = _tr(lang, f"语言（{sel_zh}）", f"Language ({sel_en})")

    # ── 监控服务切换 ────────────────────────────────────────────────────────

    def _toggle_claude(self, _):
        self._toggle_service("claude")

    def _toggle_deepseek(self, _):
        self._toggle_service("deepseek")

    def _toggle_service(self, service):
        svc = list(self._state.get("services") or list(_SERVICES))
        if service in svc:
            svc.remove(service)
        else:
            svc.append(service)
        if not svc:
            svc = [service]
        self._state["services"] = svc
        _save_state(self._state)
        self._update_service_checks()
        self._render()
        self._kick_background_fetch()

    def _toggle_login_item(self, _):
        _set_login_item(not _login_item_enabled())
        self._update_login_item_check()

    def _update_login_item_check(self):
        lang = self._lang()
        enabled = _login_item_enabled()
        suffix = " ✓" if enabled else ""
        self._login_item.title = _tr(lang, "开机自启", "Launch at Login") + suffix

    def _update_service_checks(self):
        lang = self._lang()
        svc = self._state.get("services") or list(_SERVICES)
        self._svc_claude.title = ("✓ " if "claude" in svc else "  ") + "Claude Code"
        self._svc_ds.title     = ("✓ " if "deepseek" in svc else "  ") + "DeepSeek"
        summary = _tr(lang, "全部", "All") if len(svc) == 2 else (
            "Claude Code" if "claude" in svc else "DeepSeek"
        )
        self._svc_menu.title = _tr(lang, f"监控服务（{summary}）", f"Services ({summary})")

    # ── 立即刷新 ──────────────────────────────────────────────────────────────

    def _force_refresh(self, _):
        try:
            _CACHE_PATH.unlink()
        except Exception:
            pass
        self._kick_background_fetch()


if __name__ == "__main__":
    AiLimitApp().run()
