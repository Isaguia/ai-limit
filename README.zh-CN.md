# ai-limit

[English](README.md) | 中文说明

一个轻量工具，实时查看 **Claude Code** 额度与 **DeepSeek** 余额，以及二者的 token 消耗——在额度或余额用尽前及时调整 AI 使用节奏。提供命令行（CLI）和 macOS 菜单栏 App 两种形态。

> 基于 [zhuchenxi113/ai-limit](https://github.com/zhuchenxi113/ai-limit)（监控 Claude Code + Codex）改造，改为监控 **Claude Code + DeepSeek**。

## 能看到什么

| 服务 | 实时额度 / 余额 | Token 消耗 |
|------|----------------|-----------|
| **Claude Code** | 5 小时 / 7 天滚动窗剩余百分比（来自 `claude.ai` usage 接口） | 按模型统计的 token 明细（本地日志） |
| **DeepSeek** | 账户**余额**（赠送 + 充值），来自官方 `/user/balance` 接口；并显示自上次查询以来的消耗 | 按模型统计的 token 明细（本地日志） |

---

## 命令行（CLI）

### 环境要求

- Python 3.8+
- Claude 实时额度：浏览器（Chrome / Firefox）已登录 [claude.ai](https://claude.ai)，并 `pip install browser-cookie3`
- DeepSeek 余额：一个 DeepSeek API Key

### 安装

```bash
git clone https://github.com/aguithub/ai-limit.git ~/ai-limit
cd ~/ai-limit
pip install -r requirements.txt   # browser-cookie3 仅 Claude 实时额度需要
```

可选：添加别名，写入 `~/.zshrc` / `~/.bashrc`：

```bash
alias ai-limit="python3 ~/ai-limit/usage.py"
```

### 配置 DeepSeek API Key

余额接口需要 API Key（任选其一）：

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxx
```

或写入 `~/.deepseek/config.json`：

```json
{ "api_key": "sk-xxxxxxxx" }
```

（也支持 `~/.config/deepseek/config.json`，或纯文本文件 `~/.deepseek_api_key`）

> 在 <https://platform.deepseek.com/api_keys> 获取 Key。Key 只会发往 `api.deepseek.com`，不会上传到任何其它地方。

### 用法

```bash
ai-limit                 # 最近 7 天（默认）
ai-limit --days 1        # 只看今天
ai-limit --all           # 全部历史
ai-limit --detail        # 每个模型的 token 明细
ai-limit --only deepseek # 只看某个服务（claude | deepseek）
```

输出语言按系统 locale 自动判定。可用 `AI_LIMIT_LANG=zh` / `AI_LIMIT_LANG=en` 强制。

---

## DeepSeek 的 token 消耗统计

DeepSeek 没有公开的「用量历史」接口，所以 token 消耗从本地日志读取，两个来源：

1. **作为 Claude Code 后端**——若你通过 Claude Code（Anthropic 兼容代理）调用 DeepSeek，`~/.claude/projects/**/*.jsonl` 里的 `deepseek-*` 模型记录会被自动统计。
2. **你自己的 API 调用**——用自带的 helper 写入 `~/.deepseek/usage.jsonl`：

   ```python
   from openai import OpenAI
   from deepseek_logger import log_usage

   client = OpenAI(api_key="sk-...", base_url="https://api.deepseek.com")
   resp = client.chat.completions.create(model="deepseek-chat", messages=[...])
   log_usage(resp.model, resp.usage)   # 每次调用记一行，ai-limit 自动汇总
   ```

只要有 API Key，**余额**视图始终可用；token 统计是在它之上的可选增强。ai-limit 每次运行还会记录余额快照，下次显示「自上次查询已消耗多少」。

---

## macOS 菜单栏 App

常驻菜单栏，一眼看到 Claude 剩余百分比和 DeepSeek 余额。

**从源码构建**（需 Homebrew / python.org 的 Python，Anaconda Python 会有 dylib 冲突）：

```bash
cd menubar
/opt/homebrew/bin/python3.13 setup.py py2app
bash make-dmg.sh
```

### 通过 GitHub Actions 自动构建

`.github/workflows/build-dmg.yml` 会在 macOS runner 上自动构建 DMG：

- **push 到 `main` / PR / 手动触发** → 构建并上传 DMG 为 workflow artifact（保留 30 天）。
- **push 形如 `v1.0.0` 的 tag**（`git tag v1.0.0 && git push --tags`）→ 额外创建 GitHub Release 并附上 DMG。

有 Release 之后，用户即可一键安装：

```bash
curl -fsSL https://raw.githubusercontent.com/aguithub/ai-limit/main/install.sh | bash
```

菜单栏功能：中英文切换 · Claude 的 5h/7d 窗口切换 · 按服务显示/隐藏 · 立即刷新 · 开机自启。

---

## 数据来源

### Claude Code

| 数据 | 来源 |
|------|------|
| Token 用量明细 | `~/.claude/projects/**/*.jsonl` |
| 实时额度 | 浏览器 cookie → `claude.ai/api/organizations/{orgId}/usage` |

实时额度依赖浏览器在 claude.ai 的登录态。cookie 缺失/过期时会给出错误提示与直达链接，不影响其它功能。

### DeepSeek

| 数据 | 来源 |
|------|------|
| 实时余额 | API Key → `https://api.deepseek.com/user/balance` |
| Token 用量 | `~/.claude/projects/**/*.jsonl`（deepseek-* 模型）+ `~/.deepseek/usage.jsonl` |
| 自上次查询的消耗 | 本地余额快照（`~/.ai-limit-deepseek-balance.json`） |

## 说明

- **Claude 非官方接口**：Claude 额度取自 claude.ai 内部端点，非官方 API，未来可能失效。
- DeepSeek 的 `/user/balance` 是官方文档化接口：<https://api-docs.deepseek.com/api/get-user-balance>。
- `<synthetic>` 模型记录（Claude Code 在 API 失败时写入的占位）已从所有统计中排除。

## 许可

项目代码：[Apache License 2.0](LICENSE)。
第三方依赖 `browser-cookie3` 采用 LGPL 许可。
