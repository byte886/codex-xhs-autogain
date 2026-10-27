#!/usr/bin/env python3
"""UI E2E（重启后精简版）：打开 8791 页面，验证页面加载、登录弹窗、一键读取入口、Tab 结构。
复用系统 Chrome（channel=chrome），不下载浏览器。除发布外全链路。"""
from playwright.sync_api import sync_playwright
import time, json

URL = "http://127.0.0.1:8791/"
results = {}

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    page.goto(URL, wait_until="networkidle", timeout=25000)
    time.sleep(2.5)

    body = page.inner_text("body")
    results["页面标题含产品名"] = "codex" in page.title().lower() or "小红书" in body
    results["页面正文非空"] = len(body.strip()) > 50

    # 登录弹窗（登录态失效应弹出）
    results["登录弹窗出现"] = "登录小红书" in body
    results["一键读取Chrome入口"] = "一键读取" in body
    results["扫码引导可见"] = "扫码" in body

    # Tab 结构
    tab_hits = [t for t in ["搜索", "克隆", "发布", "采集", "客户"] if t in body]
    results["核心Tab"] = tab_hits

    # 截图
    page.screenshot(path="/tmp/ui_e2e_shot.png", full_page=False)
    results["截图"] = "saved /tmp/ui_e2e_shot.png"

    # 控制台错误（过滤网络/资源类噪音）
    real_errs = [e for e in console_errors if "favicon" not in e.lower() and "net::" not in e.lower()]
    results["控制台错误数"] = len(real_errs)
    if real_errs:
        results["控制台错误示例"] = real_errs[:3]

    browser.close()

print(json.dumps(results, ensure_ascii=False, indent=2))
