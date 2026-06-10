"""py2app 构建脚本

构建命令（必须用 homebrew / python.org Python，**不能用 Anaconda Python**）：

    /opt/homebrew/bin/python3.13 setup.py py2app

为什么：Anaconda Python 的 C 扩展（_sqlite3 / _ssl / lz4 等）依赖 Anaconda
私有 dylib，py2app 默认不打包这些 dylib，导致 bundle 运行时找不到符号。
homebrew / python.org 的 Python 用系统级 lib，可以直接打包成可分发的 .app。
"""
import sys
import pathlib

# 让 py2app 看到项目根的 usage.py
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from setuptools import setup

APP = ["ai-limit-app.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "ai-limit.icns",
    "packages": ["rumps", "browser_cookie3", "Cryptodome"],
    "includes": ["usage"],
    "plist": {
        "LSUIElement": True,                          # 不在 Dock 显示
        "CFBundleName": "ai-limit",
        "CFBundleDisplayName": "ai-limit",
        "CFBundleIdentifier": "com.aguithub.ai-limit",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "© 2026 aguithub",
    },
}

setup(
    name="ai-limit",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
