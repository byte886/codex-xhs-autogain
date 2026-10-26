// scripts/browser/daemon.js —— 常驻浏览器守护进程（连接一次 Chrome，复用全程）
//
// 为什么需要它：
//   每次操作都新起进程连 Chrome → 每次连接都弹「远程调试授权」窗（Chrome 官方强制，
//   无法永久关闭）→ 高频/批量操作时弹窗连环、连接互相抢授权锁、90s 超时堆积。
//   守护进程在启动时连接一次（弹一次窗，代点脚本自动点），此后所有命令复用同一连接，
//   全程不再弹窗。CDP 只承担「取 Cookie + 页面 DOM 采集」的调试/采集职能。
//
// 协议：stdin 收 JSON 行 {id, cmd, args}，stdout 回 JSON 行 {id, ok, data|error}
// 命令：login-check | cookies | search <kw> | note <id> <xsecToken> | user <uid>
'use strict';

const readline = require('readline');
const os = require('os');
const path = require('path');
const {
  connectDailyChrome, connectProfileChrome, launchChromeProfile,
  safeDisconnect, openPage, findPage,
} = require('./connect.js');
const XHS = 'https://www.xiaohongshu.com';

let browser = null;

// 连接模式可插拔（生产不依赖豆包/CDP 调试通道）：
//   XHS_BROWSER_MODE=daily    开发/调试：连日常 Chrome（复用登录态，弹授权窗由代点处理）
//   XHS_BROWSER_MODE=profile  生产/交付：APP 自拉独立 profile 真实 Chrome（无授权弹窗）
//   XHS_PROFILE_DIR / XHS_PROFILE_PORT 指定交付模式的 profile 目录与调试端口
async function ensureBrowser() {
  if (browser && browser.connected !== false) return browser;
  const mode = process.env.XHS_BROWSER_MODE || 'daily';
  if (mode === 'profile') {
    const profileDir = process.env.XHS_PROFILE_DIR || path.join(os.tmpdir(), 'codex-xhs-profile');
    const port = Number(process.env.XHS_PROFILE_PORT || 9223);
    try {
      browser = await connectProfileChrome(port);
    } catch {
      await launchChromeProfile(profileDir, port);
      browser = await connectProfileChrome(port);
    }
  } else {
    browser = await connectDailyChrome();
  }
  return browser;
}

async function cmdLoginCheck(b) {
  const page = await openPage(b, XHS + '/explore');
  await new Promise((r) => setTimeout(r, 6000));
  const state = await page.evaluate(() => ({
    logged_in: !!(window.xhsExtract && window.xhsExtract.isLoggedIn()),
    url: location.href,
    hasLoginUi: /website-login|captcha/i.test(location.href),
    noteCards: (window.xhsExtract ? window.xhsExtract.searchNotes() : []).length,
  }));
  await page.close().catch(() => {});
  return state;
}

async function cmdCookies(b) {
  const page = await openPage(b, XHS + '/explore');
  await new Promise((r) => setTimeout(r, 3000));
  const client = await page.target().createCDPSession();
  const { cookies } = await client.send('Network.getAllCookies');
  const xhs = cookies.filter((c) => /xiaohongshu|xhscdn/i.test(c.domain || ''));
  await page.close().catch(() => {});
  return {
    count: xhs.length,
    web_session: xhs.find((c) => c.name === 'web_session')?.value || null,
    names: xhs.map((c) => c.name),
    top: xhs.slice(0, 15).map((c) => `${c.domain} ${c.name}=${(c.value || '').slice(0, 10)}…`),
  };
}

async function cmdSearch(b, keyword, limit = 30) {
  const url = XHS + '/search_result?keyword=' + encodeURIComponent(keyword) + '&source=web_search_result_notes';
  const page = await openPage(b, url);
  let items = [];
  for (let i = 0; i < 12; i++) {
    await new Promise((r) => setTimeout(r, 2500)); // 采集节奏红线
    items = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.searchNotes() : []));
    if (items.length >= limit) break;
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
  }
  await page.close().catch(() => {});
  return { keyword, count: items.slice(0, limit).length, items: items.slice(0, limit) };
}

async function cmdNote(b, noteId, xsecToken) {
  const url = xsecToken
    ? `${XHS}/search_result/${noteId}?xsec_token=${encodeURIComponent(xsecToken)}&xsec_source=pc_search`
    : `${XHS}/explore/${noteId}`;
  const page = await openPage(b, url);
  await new Promise((r) => setTimeout(r, 5000));
  const detail = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.noteDetail() : {}));
  await page.close().catch(() => {});
  return detail;
}

async function cmdUser(b, userId) {
  const page = await openPage(b, `${XHS}/user/profile/${userId}`);
  await new Promise((r) => setTimeout(r, 5000));
  const info = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.user() : {}));
  await page.close().catch(() => {});
  return info;
}

async function cmdOpenFront(b) {
  // 打开小红书首页前台标签（引导扫码）；复用已有 explore 标签避免堆积
  const existing = await findPage(b, 'xiaohongshu.com/explore');
  const page = existing || (await openPage(b, XHS + '/explore'));
  await page.bringToFront().catch(() => {});
  return { opened: true, url: page.url().slice(0, 60) };
}

const HANDLERS = {
  'login-check': () => cmdLoginCheck(browser),
  cookies: () => cmdCookies(browser),
  search: (args) => cmdSearch(browser, args[0], parseInt(args[1] || '30', 10)),
  note: (args) => cmdNote(browser, args[0], args[1]),
  user: (args) => cmdUser(browser, args[0]),
  'open-front': () => cmdOpenFront(browser),
};

async function main() {
  // 启动即连接 Chrome（弹一次授权窗，connectDailyChrome 自动代点）
  try {
    browser = await ensureBrowser();
    console.error('[daemon] Chrome 已连接，就绪');
  } catch (e) {
    console.error('[daemon] 初始连接失败:', e.message);
    // 继续运行：命令执行时再重试
  }
  process.stdout.write(JSON.stringify({ id: 0, ok: true, ready: !!browser }) + '\n');

  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on('line', async (line) => {
    let req;
    try { req = JSON.parse(line); } catch { return; }
    const { id, cmd, args = [] } = req;
    try {
      const b = await ensureBrowser();
      const fn = HANDLERS[cmd];
      if (!fn) throw new Error(`未知命令: ${cmd}`);
      const data = await fn(args);
      process.stdout.write(JSON.stringify({ id, ok: true, data }) + '\n');
    } catch (e) {
      process.stdout.write(JSON.stringify({ id, ok: false, error: String(e.message).slice(0, 300) }) + '\n');
    }
  });
  rl.on('close', async () => {
    await safeDisconnect(browser).catch(() => {});
    process.exit(0);
  });
  process.on('SIGTERM', async () => {
    await safeDisconnect(browser).catch(() => {});
    process.exit(0);
  });
}

main().catch((e) => { console.error('[daemon] 崩溃:', e.message); process.exit(1); });
