// scripts/browser/connect.js —— 本地浏览器控制核心（项目自包含，不依赖外部技能路径）
//
// 两种模式：
//   开发期  connectDailyChrome()   连用户已开的日常 Chrome（复用登录态；授权弹窗自动代点）
//   交付期  launchChromeProfile()   APP 自己拉起独立 Profile 的真实 Chrome（带调试端口，无需 AI 环境）
//
// 交付提醒：本模块只依赖 node + puppeteer-core（scripts/browser/node_modules，npm install 安装），
//          不 require 任何豆包/外部技能路径；客户机上无 AI 环境也能用（交付模式走 launchChromeProfile）。
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile, execFileSync, spawn } = require('child_process');

const PRESS_SCRIPT = path.join(__dirname, 'cdp', 'press_allow.applescript');
const PRESS_LOCKED = path.join(__dirname, 'cdp', 'press_allow_locked.sh');
const USER_DATA_DIR = process.env.CHROME_USER_DATA_DIR
  || path.join(os.homedir(), 'Library/Application Support/Google/Chrome');
const DEFAULT_ACTIVE_PORT = path.join(USER_DATA_DIR, 'DevToolsActivePort');
const CHROME_APP_NAME = 'Google Chrome';
const PROCESS_MATCH = 'Google Chrome.app/Contents/MacOS/Google Chrome';

// ---------------------------------------------------------------------------
// 一、CDP 端点读取
// ---------------------------------------------------------------------------

function readWSEndpoint(activePortFile = process.env.CHROME_DEVTOOLS_ACTIVE_PORT || DEFAULT_ACTIVE_PORT) {
  if (!fs.existsSync(activePortFile)) {
    throw new Error(
      `找不到 DevToolsActivePort 文件：${activePortFile}\n` +
      '请确认：① 日常 Chrome 已打开；② 该 Profile 已在 chrome://inspect/#remote-debugging 开启远程调试（勾选一次即持久化）。'
    );
  }
  const raw = fs.readFileSync(activePortFile, 'utf8').trim();
  const [portLine, wsPath] = raw.split('\n');
  const port = Number((portLine || '').trim());
  if (!port || !wsPath || !wsPath.trim().startsWith('/devtools/')) {
    throw new Error(`DevToolsActivePort 内容异常：\n${raw}`);
  }
  return `ws://127.0.0.1:${port}${wsPath.trim()}`;
}

function loadPuppeteer() {
  try {
    return require(path.join(__dirname, 'node_modules', 'puppeteer-core'));
  } catch (e) {
    throw new Error(
      '缺少依赖 puppeteer-core。请在 scripts/browser 下执行 `npm install`。\n' +
      `原始错误：${e.message}`
    );
  }
}

// ---------------------------------------------------------------------------
// 二、Chrome 生命周期（开发期：连已开的日常 Chrome）
// ---------------------------------------------------------------------------

function isChromeRunning() {
  try {
    const out = execFileSync('pgrep', ['-f', PROCESS_MATCH], { encoding: 'utf8' });
    return out.trim().length > 0;
  } catch {
    return false;
  }
}

function pickProfile() {
  const localState = path.join(USER_DATA_DIR, 'Local State');
  try {
    const data = JSON.parse(fs.readFileSync(localState, 'utf8'));
    const prof = data.profile || {};
    if (prof.last_used && typeof prof.last_used === 'string') return prof.last_used;
    const keys = Object.keys(prof.info_cache || {});
    if (keys.length) return keys.includes('Default') ? 'Default' : keys.sort()[0];
  } catch { /* 兜底 */ }
  return 'Default';
}

function launchChrome(profileDir) {
  const args = ['-a', CHROME_APP_NAME];
  if (profileDir) args.push('--args', `--profile-directory=${profileDir}`);
  execFileSync('open', args, { stdio: 'ignore' });
}

async function ensureChromeRunning(opt = {}) {
  const {
    profile,
    launchIfMissing = true,
    waitMs = 25000,
    activePortFile = process.env.CHROME_DEVTOOLS_ACTIVE_PORT || DEFAULT_ACTIVE_PORT,
  } = opt;

  if (isChromeRunning()) {
    return { alreadyRunning: true, started: false, profile: profile || pickProfile() };
  }
  if (!launchIfMissing) {
    throw new Error('Chrome 未运行，且 launchIfMissing=false（不自动拉起）。');
  }
  const useProfile = profile || pickProfile();
  launchChrome(useProfile);
  const t0 = Date.now();
  while (Date.now() - t0 < waitMs) {
    if (fs.existsSync(activePortFile)) {
      try {
        readWSEndpoint(activePortFile);
        await new Promise((r) => setTimeout(r, 1500));
        return { alreadyRunning: false, started: true, profile: useProfile };
      } catch { /* 继续轮询 */ }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(
    `已启动 Chrome（Profile=${useProfile}），但 ${waitMs}ms 内未等到调试端口。\n` +
    '常见原因：该 Profile 从未在 chrome://inspect/#remote-debugging 勾选过远程调试。'
  );
}

// ---------------------------------------------------------------------------
// 三、授权弹窗自动化（开发期连日常 Chrome 需要；交付期 launchChromeProfile 不需要）
// ---------------------------------------------------------------------------

function pressConsentOnce(restoreTo = '') {
  return new Promise((resolve) => {
    execFile('bash', [PRESS_LOCKED, restoreTo || ''], { timeout: 4000 }, (err, stdout) => {
      if (err) return resolve({ pressed: false, detail: err.message });
      resolve({ pressed: /pressed=true/.test(stdout || ''), detail: (stdout || '').trim() });
    });
  });
}

function startPressLoop({ intervalMs = 800, pressTimeoutMs = 4000, debug = !!process.env.DEBUG_CDP, restoreTo = '' } = {}) {
  let stopped = false;
  let inFlight = false;
  let timer = null;
  let currentChild = null;
  const runOnce = () => new Promise((resolve) => {
    const t0 = Date.now();
    currentChild = execFile('bash', [PRESS_LOCKED, restoreTo || ''], { timeout: pressTimeoutMs }, (err, stdout) => {
      currentChild = null;
      resolve({
        pressed: !err && /pressed=true/.test(stdout || ''),
        cost: Date.now() - t0,
      });
    });
  });
  async function tick() {
    if (stopped) return;
    if (inFlight) return schedule();
    inFlight = true;
    try { await runOnce(); } finally { inFlight = false; schedule(); }
  }
  function schedule() { if (!stopped) timer = setTimeout(tick, intervalMs); }
  tick();
  return function stop() {
    stopped = true;
    if (timer) clearTimeout(timer);
    if (currentChild) { try { currentChild.kill(); } catch { /* 忽略 */ } currentChild = null; }
  };
}

// ---------------------------------------------------------------------------
// 四、连接日常 Chrome（开发期）
// ---------------------------------------------------------------------------

async function connectDailyChrome(opt = {}) {
  const {
    ensureRunning = true,
    profile,
    timeoutMs = 30000,
    retries = 3,
    retryGapMs = 1200,
    autoPress = true,
    activePortFile,
  } = opt;
  const puppeteer = loadPuppeteer();

  if (ensureRunning) {
    await ensureChromeRunning({ profile, activePortFile, launchIfMissing: opt.launchIfMissing !== false });
  }

  const consentFront = autoPress ? captureFrontmost() : null;
  const stopPress = autoPress ? startPressLoop({ debug: !!process.env.DEBUG_CDP, restoreTo: consentFront ? consentFront.name : '' }) : null;
  try {
    let lastErr = null;
    for (let attempt = 1; attempt <= retries; attempt += 1) {
      const browserWSEndpoint = readWSEndpoint(activePortFile);
      try {
        return await puppeteer.connect({
          browserWSEndpoint,
          defaultViewport: null,
          targetFilter: () => true,
          handleDevToolsAsPage: true,
          protocolTimeout: timeoutMs,
        });
      } catch (e) {
        lastErr = e;
        if (attempt === retries) break;
        await new Promise((r) => setTimeout(r, retryGapMs));
      }
    }
    throw new Error(`连接日常 Chrome 失败（已尝试 ${retries} 次）：${lastErr && lastErr.message}`);
  } finally {
    if (stopPress) stopPress();
  }
}

// ---------------------------------------------------------------------------
// 五、交付期：APP 拉起独立 Profile 的真实 Chrome（客户可见窗口、可扫码；不依赖 AI/技能环境）
// ---------------------------------------------------------------------------

/**
 * 交付期入口：APP 用独立 user-data-dir + 调试端口拉起一个「真实 Chrome」窗口。
 * 客户在这个窗口里扫码登录；本程序经 http://127.0.0.1:<port> 用 puppeteer-core 连接控制。
 * 自己启动的实例连接 CDP 无「远程调试授权弹窗」（该弹窗只出现在连「已运行的日常 Chrome」时）。
 *
 * @param {string} profileDir 独立 profile 目录（如 os.tmpdir()/codex-xhs-profile）
 * @param {number} port       调试端口（如 9223）
 * @returns {Promise<{port:number,pid:number}>}
 */
async function launchChromeProfile(profileDir, port) {
  const chromePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (!fs.existsSync(chromePath)) {
    throw new Error('未找到 Google Chrome：' + chromePath);
  }
  fs.mkdirSync(profileDir, { recursive: true });
  const args = [
    `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--new-window',
  ];
  const child = spawn(chromePath, args, { stdio: 'ignore', detached: true });
  child.unref();
  for (let i = 0; i < 30; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1500) });
      if (r.ok) return { port, pid: child.pid };
    } catch { /* 未就绪 */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`Chrome 调试端口未在 30s 内就绪（port=${port}）`);
}

/** 经 HTTP 端点连接 APP 拉起的独立 Chrome（交付期用） */
async function connectProfileChrome(port) {
  const puppeteer = loadPuppeteer();
  return puppeteer.connect({ browserURL: `http://127.0.0.1:${port}`, defaultViewport: null });
}

// ---------------------------------------------------------------------------
// 六、通用工具
// ---------------------------------------------------------------------------

async function safeDisconnect(browser) {
  try {
    if (browser && browser.connected !== false) await browser.disconnect();
  } catch { /* 已断开则忽略 */ }
}

async function findPage(browser, match) {
  const pages = await browser.pages();
  const hit = (u) => (match instanceof RegExp ? match.test(u) : u.includes(match));
  for (const p of pages) {
    let u = '';
    try { u = p.url(); } catch { continue; }
    if (hit(u)) return p;
  }
  return null;
}

async function newBackgroundPage(browser) {
  try {
    const browserSession = await browser.target().createCDPSession();
    const { targetId } = await browserSession.send('Target.createTarget', {
      url: 'about:blank', background: true, newWindow: false,
    });
    await browserSession.detach().catch(() => {});
    const target = await browser.waitForTarget(
      (t) => t._targetId === targetId || (typeof t.targetId === 'function' && t.targetId() === targetId),
      { timeout: 8000 },
    );
    const page = await target.page();
    if (page) return page;
    throw new Error('后台 target 转 Page 失败');
  } catch (e) {
    console.warn(`[cdp] 后台建标签不可用，退回普通 newPage：${e && e.message}`);
    return browser.newPage();
  }
}

// 打开页面（后台标签）+ 注入提取逻辑；返回 page
async function openPage(browser, url, timeout = 40000) {
  const page = await newBackgroundPage(browser);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout }).catch((e) => {
    console.warn('[warn] goto', url.slice(0, 80), ':', e.message.slice(0, 60));
  });
  const extractSrc = fs.readFileSync(path.join(__dirname, '..', '..', 'extension', 'lib', 'extract.js'), 'utf8');
  await page.evaluate(extractSrc).catch((e) => console.warn('[warn] inject:', e.message.slice(0, 60)));
  return page;
}

function runOsa(appleScript, timeoutMs = 1500) {
  try {
    return execFileSync('osascript', ['-e', appleScript], { timeout: timeoutMs, encoding: 'utf8' }).trim();
  } catch {
    return null;
  }
}

function captureFrontmost() {
  const out = runOsa(
    'tell application "System Events"\n'
    + 'set ps to (processes whose frontmost is true)\n'
    + 'if (count of ps) is 0 then return ""\n'
    + 'set p to item 1 of ps\n'
    + 'set n to name of p\n'
    + 'set b to ""\n'
    + 'try\nset b to bundle identifier of p\nend try\n'
    + 'return n & "\\t" & b\n'
    + 'end tell',
  );
  if (!out) return null;
  const [name, bundleId = ''] = out.split('\t');
  return name ? { name, bundleId: bundleId || '' } : null;
}

function restoreFrontmost(prev) {
  if (!prev || !prev.name || prev.name === CHROME_APP_NAME) return false;
  const cur = runOsa('tell application "System Events" to name of first process whose frontmost is true');
  if (cur !== CHROME_APP_NAME) return false;
  const name = String(prev.name).replace(/"/g, '');
  const script = 'tell application "System Events"\n'
    + 'try\n'
    + `set frontmost of (first process whose name is "${name}") to true\n`
    + 'end try\n'
    + 'end tell';
  return runOsa(script, 2000) !== null;
}

module.exports = {
  readWSEndpoint,
  loadPuppeteer,
  isChromeRunning,
  pickProfile,
  launchChrome,
  ensureChromeRunning,
  pressConsentOnce,
  startPressLoop,
  connectDailyChrome,
  launchChromeProfile,
  connectProfileChrome,
  findPage,
  newBackgroundPage,
  openPage,
  captureFrontmost,
  restoreFrontmost,
  safeDisconnect,
  USER_DATA_DIR,
};
