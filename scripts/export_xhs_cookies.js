// export_xhs_cookies.js
// 连接日常 Chrome（复用已登录的小红书），经 CDP 导出 .xiaohongshu.com cookie
// → /tmp/xhs_cookies_export.json（xiaohongshu-mcp v2 sessionFile 格式，供容器 COOKIES_PATH 使用）
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const SKILL_CDP = '/Users/weiqiang/Doubao/skills/mac-system-toolkit/scripts/chrome-cdp/connect_browser.js';
const { connectDailyChrome, safeDisconnect, newBackgroundPage } = require(SKILL_CDP);

const OUT_PATH = '/tmp/xhs_cookies_export.json';
const SEED = 1943702947;

(async () => {
  const browser = await connectDailyChrome();
  try {
    const page = await newBackgroundPage(browser);
    const client = await page.target().createCDPSession();

    // 先确保在小红书域下拿 cookie（不同源时 getCookies 拿不到 httpOnly 全量时用 getAllCookies）
    await page.goto('https://www.xiaohongshu.com/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch((e) => {
      console.warn('[warn] 打开小红书页失败（不影响 cookie 读取）:', e.message);
    });
    await new Promise((r) => setTimeout(r, 2500));

    const { cookies } = await client.send('Network.getAllCookies');
    const xhs = cookies.filter((c) =>
      (c.domain || '').includes('xiaohongshu') || (c.domain || '').includes('xhscdn')
    );
    if (!xhs.length) {
      console.error('[FAIL] 未在 Chrome 中找到小红书 cookie');
      process.exit(1);
    }

    const session = {
      version: 2,
      seed: SEED,
      saved_at: new Date().toISOString(),
      cookies: xhs.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path || '/',
        expires: c.expires || 0,
        httpOnly: !!c.httpOnly,
        secure: !!c.secure,
        sameSite: c.sameSite || 'Lax',
      })),
    };
    fs.writeFileSync(OUT_PATH, JSON.stringify(session, null, 2));
    console.log(`✅ 已导出 ${xhs.length} 条小红书 cookie → ${OUT_PATH}`);
    for (const c of xhs.slice(0, 12)) {
      const v = c.value ? c.value.slice(0, 6) + '…' : '…';
      console.log(`  ${c.domain}  ${c.name}  ${v}`);
    }
  } finally {
    await page.close().catch(() => {});
    await safeDisconnect(browser);
  }
})().catch((e) => {
  console.error('[FAIL]', e.message);
  process.exit(1);
});
