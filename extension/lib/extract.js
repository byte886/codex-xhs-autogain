// lib/extract.js —— 页面数据提取（DOM 层，避开签名问题）
// 与内容脚本共享：搜索页/笔记页/用户页提取逻辑

function xhsExtractSearchNotes(root = document) {
  // 小红书搜索页笔记卡片：section.note-item（data-note-id + xsec_token 链接）
  const cards = [...root.querySelectorAll('section.note-item')];
  return cards.map((c) => {
    // 链接：带 xsec_token 的详情链接（/explore/ 或 /search_result/ 前缀）
    const a = c.querySelector('a.cover[href], a[href*="xsec_token"]') || c.querySelector('a[href*="/explore/"], a[href*="/search_result/"]');
    const href = a ? a.getAttribute('href') : null;
    const id = c.getAttribute('data-note-id') || (href ? (href.match(/[0-9a-f]{24}/) || [])[0] || null : null);
    const img = c.querySelector('img.cover') || c.querySelector('img');
    const authorEl = c.querySelector('.author');
    const likesEl = c.querySelector('.like-wrapper .count, .like-wrapper span, .footer [class*="like"], [class*="like"] .count');
    const descEl = c.querySelector('.desc, .note-desc, [class*="desc"]');
    return {
      id,
      href: href ? (href.startsWith('/') ? 'https://www.xiaohongshu.com' + href : href) : null,
      // 新版搜索卡片标题在封面图上，DOM 无独立标题元素 → 取 desc/图 alt 兜底
      title: (descEl ? descEl.innerText.trim() : '') || (img ? img.alt || '' : ''),
      author: authorEl ? authorEl.innerText.trim() : '',
      img: img ? (img.src || img.getAttribute('data-src')) : null,
      likes: likesEl ? likesEl.innerText.trim() : '',
    };
  }).filter((x) => x.id || x.href);
}

function xhsExtractNoteDetail(root = document) {
  // 笔记详情页：标题/正文/作者/图片（仅取笔记轮播图，排除头像/缩略图/相关推荐）
  const titleEl = root.querySelector('#detail-title, .title, h1');
  const descEl = root.querySelector('#detail-desc, .desc, .note-text');
  const authorEl = root.querySelector('.author-wrapper .name, .user-name, .author .name');
  const dateEl = root.querySelector('#date, .date, .publish-time');
  const imgs = [...root.querySelectorAll('.swiper-slide img, #noteContainer img, .slide img, .carousel img, [class*="slide"] img')]
    .map((i) => i.src || i.getAttribute('data-src') || null)
    .filter((u) => u && /sns-webpic|sns-img|xhscdn\.com|sns-preview/i.test(u))  // 仅笔记图域名（排除头像/静态资源）
    .filter((u, i, arr) => arr.indexOf(u) === i)                                 // 去重
    .slice(0, 9);
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    desc: descEl ? descEl.innerText.trim() : '',
    author: authorEl ? authorEl.innerText.trim() : '',
    date: dateEl ? dateEl.innerText.trim() : '',
    imgs,
  };
}

function xhsExtractUser(root = document) {
  // 用户主页：昵称/小红书号/简介/关注数/粉丝数/获赞数
  const nickEl = root.querySelector('.user-name, .nickname, .name');
  const idEl = root.querySelector('.user-id, .red-id, .xiaohongshu-id, .user-screen-name');
  const descEl = root.querySelector('.user-desc, .desc, .introduction');
  // 统计区：关注/粉丝/获赞与收藏（新版：.user-stats 或 .info .count 等）
  const fansEl = root.querySelector('[class*="fans"] .count, [class*="follower"] .count, [class*="followers"] .count');
  const followEl = root.querySelector('[class*="follow"] .count, [class*="following"] .count');
  const likesEl = root.querySelector('[class*="like"] .count, [class*="interaction"] .count');
  const counts = [...root.querySelectorAll('.count, .num, .stat, [class*="count"]')].map((e) => e.innerText.trim()).filter(Boolean).slice(0, 6);
  return {
    nickname: nickEl ? nickEl.innerText.trim() : '',
    userId: idEl ? idEl.innerText.trim() : '',
    desc: descEl ? descEl.innerText.trim() : '',
    fans: fansEl ? fansEl.innerText.trim() : '',
    following: followEl ? followEl.innerText.trim() : '',
    likes: likesEl ? likesEl.innerText.trim() : '',
    counts,
  };
}

// 登录态检测：页面特征（不依赖签名 API）
function xhsIsLoggedIn(root = document) {
  // 未登录特征：出现登录弹层/登录按钮/被重定向到登录页
  const t = root.body ? root.body.innerText : '';
  const url = location.href;
  if (/website-login|login|captcha/i.test(url)) return false;
  if (root.querySelector('.login-container, .login-modal, [class*="login-modal"]')) return false;
  // 已登录特征：头像/用户菜单
  if (root.querySelector('.avatar, .user-menu, [class*="user-avatar"], header img[class*="avatar"]')) return true;
  // 兜底：推荐流正常渲染且无登录弹层 → 视为可采集
  if (root.querySelector('section.note-item, .feeds-container, .feed')) return true;
  return t.includes('登录') && t.includes('注册') ? false : true;
}

if (typeof window !== 'undefined') {
  window.xhsExtract = {
    searchNotes: xhsExtractSearchNotes,
    noteDetail: xhsExtractNoteDetail,
    user: xhsExtractUser,
    isLoggedIn: xhsIsLoggedIn,
  };
}
