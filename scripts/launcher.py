#!/usr/bin/env python3
"""CodexXHS 应用壳入口（B 批：macOS .app）。

职责：
- 确定数据目录：打包态默认 ~/Library/Application Support/codex-zhuke/data
  （可被 CODX_XHS_DATA 覆盖）；开发态沿用项目根 data。
- 同进程起 uvicorn（后台线程，优雅退出），端口冲突自动探测替换。
- 服务就绪后打开系统默认浏览器访问 UI。
- 收到 SIGINT/SIGTERM（cmd+Q / kill）时优雅停服。

开发态运行：  python scripts/launcher.py
打包入口：    PyInstaller .spec 指定本文件（sys._MEIPASS 分支生效）。
"""
import os
import pathlib
import signal
import sys
import threading
import time
import webbrowser


def _redirect_logs(data_dir: pathlib.Path) -> None:
    """GUI 模式（console=False）下 print/日志默认丢弃；重定向到 data/logs/app.log。"""
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    f = open(logs / "app.log", "a", encoding="utf-8")  # noqa: SIM115（进程级保持打开）
    os.dup2(f.fileno(), sys.stdout.fileno())
    os.dup2(f.fileno(), sys.stderr.fileno())


def _data_dir() -> pathlib.Path:
    if os.environ.get("CODX_XHS_DATA"):
        return pathlib.Path(os.environ["CODX_XHS_DATA"]).expanduser()
    if getattr(sys, "_MEIPASS", None):  # PyInstaller 打包态
        return pathlib.Path.home() / "Library" / "Application Support" / "codex-zhuke" / "data"
    # 开发态：项目根 data（scripts/launcher.py -> 项目根）
    return pathlib.Path(__file__).resolve().parents[1] / "data"


def main() -> int:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("CZ_KEEP_CONSOLE"):
        _redirect_logs(data_dir)
    os.environ["CODX_XHS_DATA"] = str(data_dir)  # 全局注入：server/config 统一读取

    # 开发态：确保 app/src 在 sys.path
    if not getattr(sys, "_MEIPASS", None):
        src = pathlib.Path(__file__).resolve().parents[1] / "app" / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

    from codex_xhs import config, server  # noqa: PLC0415（先注入环境再 import）

    port = config.find_free_port(8791)
    print(f"[codex-xhs] 数据目录: {data_dir}")
    print(f"[codex-xhs] 服务端口: {port}")

    import uvicorn  # noqa: PLC0415

    srv = uvicorn.Server(
        uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="info")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    for _ in range(100):  # 最多 ~10s 等端口就绪
        if srv.started:
            break
        time.sleep(0.1)
    if not srv.started:
        print("[codex-xhs] 服务启动超时，请查看日志", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(f"[codex-xhs] 已启动: {url}")
    if not os.environ.get("CZ_NO_BROWSER"):  # 自动化冒烟可跳过开浏览器
        webbrowser.open(url)

    stop = threading.Event()

    def _on_signal(_signum, _frame):  # noqa: ANN001
        print("[codex-xhs] 收到退出信号，优雅停服…")
        srv.should_exit = True
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    # 测试钩子（CZ_SELF_EXIT_AFTER 秒后自动触发优雅退出，供无信号环境/壳冒烟用）
    auto_exit = float(os.environ.get("CZ_SELF_EXIT_AFTER", "0") or 0)
    deadline = time.monotonic() + auto_exit if auto_exit > 0 else None
    try:
        while not stop.wait(1.0):
            if not thread.is_alive():
                break
            if deadline and time.monotonic() >= deadline:
                print("[codex-xhs] 测试钩子触发优雅退出")
                srv.should_exit = True
                break
    except KeyboardInterrupt:
        pass
    finally:
        srv.should_exit = True
        thread.join(timeout=8)
    print("[codex-xhs] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
