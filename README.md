# ai-limit

English | [中文说明](README.zh-CN.md)

A lightweight tool to monitor real-time **Claude Code** quota and **DeepSeek** balance, plus token consumption — so you can adjust your AI usage before running out of quota or balance. Available as a CLI or a macOS menu bar app.

> Adapted from [zhuchenxi113/ai-limit](https://github.com/zhuchenxi113/ai-limit) (which monitors Claude Code + Codex), reworked to monitor **Claude Code + DeepSeek**.

## What it shows

| Service | Live quota / balance | Token consumption |
|---------|----------------------|-------------------|
| **Claude Code** | 5-hour & 7-day rolling windows (% remaining) via `claude.ai` usage API | Per-model token breakdown from local logs |
| **DeepSeek** | Account **balance** (granted + topped-up) via the official `/user/balance` API; spend since last check | Per-model token breakdown from local logs |

---

## CLI

### Requirements

- Python 3.8+
- For Claude live quota: Chrome or Firefox signed in to [claude.ai](https://claude.ai), and `pip install browser-cookie3`
- For DeepSeek balance: a DeepSeek API key

### Install

```bash
git clone https://github.com/aguithub/ai-limit.git ~/ai-limit
cd ~/ai-limit
pip install -r requirements.txt   # browser-cookie3 only needed for Claude live quota
```

Add an alias (optional), e.g. in `~/.zshrc` / `~/.bashrc`:

```bash
alias ai-limit="python3 ~/ai-limit/usage.py"
```

### Configure DeepSeek API key

The balance endpoint needs your API key (any one of these):

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxx
```

or write `~/.deepseek/config.json`:

```json
{ "api_key": "sk-xxxxxxxx" }
```

(also accepted: `~/.config/deepseek/config.json`, or a plain `~/.deepseek_api_key` file)

> Get a key at <https://platform.deepseek.com/api_keys>. The key is only sent to `api.deepseek.com` and never leaves your machine otherwise.

### Usage

```bash
ai-limit                 # last 7 days (default)
ai-limit --days 1        # today only
ai-limit --all           # full history
ai-limit --detail        # per-model token breakdown
ai-limit --only deepseek # show only one service (claude | deepseek)
```

Output language auto-detects from your system locale. Override with `AI_LIMIT_LANG=en` / `AI_LIMIT_LANG=zh`.

---

## Token consumption tracking for DeepSeek

DeepSeek does not expose a usage-history API, so token consumption is read from local logs:

1. **Claude Code backend** — if you run DeepSeek through Claude Code (an Anthropic-compatible proxy), the `deepseek-*` model entries in `~/.claude/projects/**/*.jsonl` are picked up automatically.
2. **Your own API calls** — log them to `~/.deepseek/usage.jsonl` with the bundled helper:

   ```python
   from openai import OpenAI
   from deepseek_logger import log_usage

   client = OpenAI(api_key="sk-...", base_url="https://api.deepseek.com")
   resp = client.chat.completions.create(model="deepseek-chat", messages=[...])
   log_usage(resp.model, resp.usage)   # one line per call; ai-limit aggregates it
   ```

The **balance** view always works with just an API key — token tracking is optional on top of it. ai-limit also records the balance after each run and shows how much you spent since the last check.

---

## macOS Menu Bar App

Lives in the menu bar, shows Claude quota % and DeepSeek balance at a glance.

**Build from source** (Homebrew / python.org Python required — Anaconda Python causes dylib conflicts):

```bash
cd menubar
/opt/homebrew/bin/python3.13 setup.py py2app
bash make-dmg.sh
```

### Auto-build via GitHub Actions

`.github/workflows/build-dmg.yml` builds the DMG on a macOS runner automatically:

- **Push to `main` / PR / manual dispatch** → builds and uploads the DMG as a workflow artifact (kept 30 days).
- **Push a `v*` tag** (e.g. `git tag v1.0.0 && git push --tags`) → also creates a GitHub Release with the DMG attached.

Once a release exists, users can one-line install:

```bash
curl -fsSL https://raw.githubusercontent.com/aguithub/ai-limit/main/install.sh | bash
```

Menu bar features: Chinese/English toggle · 5h/7d window toggle for Claude · per-service show/hide · manual refresh · launch at login.

---

## Data Sources

### Claude Code

| Data | Source |
|------|--------|
| Token usage details | `~/.claude/projects/**/*.jsonl` |
| Live quota | Browser cookie → `claude.ai/api/organizations/{orgId}/usage` |

Live quota requires an active browser session on claude.ai. Falls back gracefully with an error and a direct link if cookies are missing/expired.

### DeepSeek

| Data | Source |
|------|--------|
| Live balance | API key → `https://api.deepseek.com/user/balance` |
| Token usage | `~/.claude/projects/**/*.jsonl` (deepseek-* models) + `~/.deepseek/usage.jsonl` |
| Spend since last check | Local balance snapshot (`~/.ai-limit-deepseek-balance.json`) |

## Notes

- **Unofficial Claude API**: Claude quota is fetched from an internal claude.ai endpoint, not an official API — it may break with future updates.
- DeepSeek's `/user/balance` is an official, documented endpoint: <https://api-docs.deepseek.com/api/get-user-balance>.
- `<synthetic>` model entries (error placeholders written by Claude Code on API failures) are excluded from all statistics.

## License

Project code: [Apache License 2.0](LICENSE).
Third-party dependency `browser-cookie3` is licensed under LGPL.
