#!/usr/bin/env bash
# 一键打包 macOS .app（B 批壳）
# 用法：bash scripts/build_mac.sh [--no-frontend-build]
set -euo pipefail
cd "$(dirname "$0")/.."

# 1) 前端构建产物（app/web）——默认先 build；--no-frontend-build 可跳过（仅重打壳）
if [[ "${1:-}" != "--no-frontend-build" ]]; then
  echo ">> 构建前端 (Vite → app/web)"
  (cd app/frontend && npm run build)
else
  echo ">> 跳过前端构建（复用现有 app/web）"
fi

# 2) PyInstaller 打包 .app
echo ">> PyInstaller 打包 .app"
app/.venv/bin/python -m PyInstaller --noconfirm --clean scripts/codex_xhs.spec

echo ">> 产物: dist/CodexXHS.app"
ls -d dist/CodexXHS.app
