// scripts/browser/actions.js —— 采集动作（登录态检查、搜索、笔记详情、cookie 读取）
// 替代 Docker 容器的 MCP 工具调用；执行路径：页面 DOM 层（避开签名问题）
'use strict';
const { connectDailyChrome, safeDisconnect } = require('./connect.js');
const XHS = 'https://www.xiaohongshu.com';

function usage() {
  return '用法: node actions.js <cmd> [arg]\n' +
    '  login-check               检查小红书登录态（cookie + 页面特征）\n' +
    '  cookies                   读取小红书 cookie 摘要（只读）\n' +
    '  search <keyword>          搜索并提取笔记列表（最多 N 条）\n' +
    '  note <noteId>             提取单篇笔记详情\n' +
    '  user <userId>             提取作者主页信息（昵称/粉丝数）\n';
}

async function cmdLoginCheck(browser) {
  const { openPage } = require('./connect.js');
  const page = await openPage(browser, XHS + '/explore');
  await new Promise((r) => setTimeout(r, 6000));
  const state = await page.evaluate(() => {
    const logged = !!(window.xhsExtract && window.xhsExtract.isLoggedIn());
    return {
      logged_in: logged,
      url: location.href,
      hasLoginUi: /website-login|captcha/i.test(location.href),
      noteCards: (window.xhsExtract ? window.xhsExtract.searchNotes() : []).length,
    };
  });
  console.log('LOGIN_STATE:' + JSON.stringify(state));
  await page.close().catch(() => {});
}

async function cmdCookies(browser) {
  const page = await (require('./connect.js').openPage)(browser, XHS + '/explore');
  await new Promise((r) => setTimeout(r, 3000));
  const client = await page.target().createCDPSession();
  const { cookies } = await client.send('Network.getAllCookies');
  const xhs = cookies.filter((c) => /xiaohongshu|xhscdn/i.test(c.domain || ''));
  const info = {
    count: xhs.length,
    web_session: xhs.find((c) => c.name === 'web_session'),
    names: xhs.map((c) => c.name),
    top: xhs.slice(0, 15).map((c) => `${c.domain} ${c.name}=${(c.value || '').slice(0, 10)}…`),
  };
  console.log('COOKIES:' + JSON.stringify(info, null, 1));
  await page.close().catch(() => {});
}

async function cmdSearch(browser, keyword, limit = 30) {
  const { openPage } = require('./connect.js');
  const url = XHS + '/search_result?keyword=' + encodeURIComponent(keyword) + '&source=web_search_result_notes';
  const page = await openPage(browser, url);
  // 滚动加载，直到够数或 12 次
  let items = [];
  for (let i = 0; i < 12; i++) {
    await new Promise((r) => setTimeout(r, 2500));  // 采集节奏 1.8-2.5s（红线）
    items = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.searchNotes() : []));
    if (items.length >= limit) break;
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
  }
  items = items.slice(0, limit);
  console.log('SEARCH:' + JSON.stringify({ keyword, count: items.length, items }));
  await page.close().catch(() => {});
}

async function cmdNote(browser, noteId, xsecToken) {
  const { openPage } = require('./connect.js');
  // 新版小红书：笔记详情必须带 xsec_token（/explore/{id} 直接访问会 404）
  const url = xsecToken
    ? `${XHS}/search_result/${noteId}?xsec_token=${encodeURIComponent(xsecToken)}&xsec_source=pc_search`
    : `${XHS}/explore/${noteId}`;
  const page = await openPage(browser, url);
  await new Promise((r) => setTimeout(r, 5000));
  const detail = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.noteDetail() : {}));
  console.log('NOTE:' + JSON.stringify(detail, null, 1));
  await page.close().catch(() => {});
}

async function cmdUser(browser, userId) {
  const { openPage } = require('./connect.js');
  const page = await openPage(browser, `${XHS}/user/profile/${userId}`);
  await new Promise((r) => setTimeout(r, 5000));
  const info = await page.evaluate(() => (window.xhsExtract ? window.xhsExtract.user() : {}));
  console.log('USER:' + JSON.stringify(info, null, 1));
  await page.close().catch(() => {});
}

(async () => {
  const [, , cmd, arg, arg2] = process.argv;
  if (!cmd || !['login-check', 'cookies', 'search', 'note', 'user'].includes(cmd)) {
    console.log(usage());
    process.exit(0);
  }
  const browser = await connectDailyChrome();
  try {
    if (cmd === 'login-check') await cmdLoginCheck(browser);
    else if (cmd === 'cookies') await cmdCookies(browser);
    else if (cmd === 'search') await cmdSearch(browser, arg, 30);
    else if (cmd === 'note') await cmdNote(browser, arg, arg2);
    else if (cmd === 'user') await cmdUser(browser, arg);
  } finally {
    await safeDisconnect(browser);
  }
})().catch((e) => { console.error('[FAIL]', e.message); process.exit(1); });
