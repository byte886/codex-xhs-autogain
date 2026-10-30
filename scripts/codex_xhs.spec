# -*- mode: python ; coding: utf-8 -*-
"""CodexXHS macOS .app 打包配置（B 批壳）。

要点：
- 入口 scripts/launcher.py（起 uvicorn + 开浏览器 + 优雅退出）。
- pathex 含 app/src，使 `codex_xhs.*` 可被收集。
- datas 打包前端构建产物 app/web（server 静态托管）+ playwright driver（引擎 node 子进程）。
- hiddenimports 全量收集 codex_xhs / uvicorn 子模块，避免动态导入漏包。
- console=False：GUI 应用；launcher 已将 stdout/stderr 重定向到数据目录 logs/app.log。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)           # scripts/
ROOT = SPEC_DIR.parent              # 项目根
APP_NAME = "CodexXHS"

hiddenimports = collect_submodules("codex_xhs") + collect_submodules("uvicorn")
datas = [
    (str(ROOT / "app" / "web"), "app/web"),
    (str(ROOT / "extension" / "lib"), "extension/lib"),          # DOM 提取源 extract.js
    (str(ROOT / "scripts" / "browser"), "scripts/browser"),      # 授权代点 AppleScript
] + collect_data_files("playwright")

a = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(ROOT / "app" / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI 模式：日志由 launcher 落盘
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,
    bundle_identifier="com.codexzhuke.app",
    info_plist={
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
