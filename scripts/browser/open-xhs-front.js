// scripts/browser/open-xhs-front.js —— 在 Chrome 中打开小红书首页（前台标签）
// 用途：Cookie 失效时前端引导用户扫码（登录发生在外部 Chrome 内）
'use strict';
const { connectDailyChrome, safeDisconnect, newBackgroundPage } = require('./connect.js');

(async () => {
  const browser = await connectDailyChrome();
  try {
    const page = await newBackgroundPage(browser);
    await page.goto('https://www.xiaohongshu.com/explore', { waitUntil: 'domcontentloaded', timeout: 40000 }).catch((e) => {
      console.warn('[warn] goto:', e.message.slice(0, 60));
    });
    await new Promise((r) => setTimeout(r, 4000));
    await page.bringToFront().catch(() => {});
    console.log('OPENED: 小红书 explore 前台标签');
  } finally {
    await safeDisconnect(browser);
  }
})().catch((e) => {
  console.error('[FAIL]', e.message);
  process.exit(1);
});
