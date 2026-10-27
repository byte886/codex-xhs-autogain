#!/usr/bin/env python3
"""生产环境脚本（macOS）：带参数启动 Google Chrome 开启远程调试，并打开配置页。

减少用户配置难度：用户只需在弹出的 Chrome 配置页里点一次「允许」即可。
用法：
    python3 setup_chrome_mac.py [port] [profile_dir]
    port        调试端口，默认 9223
    profile_dir 独立 profile 目录，默认 ~/.codex-xhs-profile
退出码：
    0   已带参数启动 Chrome（配置页已打开）
    2   Chrome 已在运行（参数对已运行实例不生效，需先退出）
    3   未找到 Chrome
"""
import os
import pathlib
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "9223"
    profile = sys.argv[2] if len(sys.argv) > 2 else str(
        pathlib.Path.home() / ".codex-xhs-profile"
    )
    if not os.path.exists(CHROME):
        print("CHROME_NOT_FOUND")
        return 3
    os.makedirs(profile, exist_ok=True)
    if subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                      timeout=5).returncode == 0:
        print("CHROME_RUNNING")
        return 2
    subprocess.Popen(
        [CHROME, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
         "--no-first-run", "--no-default-browser-check", "--new-window",
         "chrome://inspect/#remote-debugging"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("LAUNCHED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
