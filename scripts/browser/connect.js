// scripts/browser/connect.js —— 本地浏览器控制核心（替代 Docker xhs-mcp）
// 开发期：连接日常 Chrome（复用登录态）；交付期：支持拉起独立 Chrome
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { connectDailyChrome, safeDisconnect, newBackgroundPage } = require('/Users/weiqiang/Doubao/skills/mac-system-toolkit/scripts/chrome-cdp/connect_browser.js');

// 交付期：拉起独立 Chrome（真实设备、独立 profile、可见窗口、带调试端口）
async function launchChromeProfile(profileDir, port) {
  const chromePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const args = [
    `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--new-window',
  ];
  const child = spawn(chromePath, args, { stdio: 'ignore', detached: true });
  child.unref();
  // 等待端口就绪
  for (let i = 0; i < 20; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1500) });
      if (r.ok) return { port, pid: child.pid };
    } catch (e) { /* 未就绪 */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('Chrome 调试端口未在 20s 内就绪');
}

// 打开页面（后台标签，不抢前台焦点），返回 page
async function openPage(browser, url, timeout = 40000) {
  const page = await newBackgroundPage(browser);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout }).catch((e) => {
    console.warn('[warn] goto', url.slice(0, 80), ':', e.message.slice(0, 60));
  });
  // 注入提取逻辑（与扩展共用 lib/extract.js 单一来源）
  const extractSrc = fs.readFileSync(path.join(__dirname, '..', '..', 'extension', 'lib', 'extract.js'), 'utf8');
  await page.evaluate(extractSrc).catch((e) => console.warn('[warn] inject:', e.message.slice(0, 60)));
  return page;
}

module.exports = { connectDailyChrome, safeDisconnect, newBackgroundPage, openPage, launchChromeProfile };
