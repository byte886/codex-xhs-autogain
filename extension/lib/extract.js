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
    // 标题：新版搜索卡片独立 .title 元素；旧版 .desc/.note-desc 兜底；再兜底封面 alt
    const titleEl = c.querySelector('.title') || c.querySelector('.desc, .note-desc, [class*="desc"]');
    // 视频标记：视频笔记卡片有播放图标（play-icon，实测可靠），图文没有 → note_type 精确区分
    const playEl = c.querySelector('[class*="play"]');
    return {
      id,
      href: href ? (href.startsWith('/') ? 'https://www.xiaohongshu.com' + href : href) : null,
      title: (titleEl ? titleEl.innerText.trim() : '') || (img ? img.alt || '' : ''),
      author: authorEl ? authorEl.innerText.trim() : '',
      img: img ? (img.src || img.getAttribute('data-src')) : null,
      likes: likesEl ? likesEl.innerText.trim() : '',
      type: playEl ? 'video' : 'note',
    };
  }).filter((x) => x.id || x.href);
}

function xhsExtractNoteDetail(root = document) {
  // 笔记详情页：标题/正文/作者/图片（仅取笔记轮播图，排除头像/缩略图/相关推荐）
  // 标题来源优先级：og:title（SEO 元数据，实测可靠）→ #detail-title。
  // 注意：绝不回退到 .title/h1 —— 详情页右侧"相关推荐"流里有大量 .title/h1 文本，
  // 会抓到与笔记无关的推荐标题（实测污染源）。
  const ogTitleEl = root.querySelector('meta[property="og:title"]');
  const ogTitle = ogTitleEl ? (ogTitleEl.content || '').trim() : '';
  const titleEl = root.querySelector('#detail-title');
  const title = (ogTitle ? ogTitle.replace(/\s*-\s*(小红书)?\s*$/, '').trim() : '')
    || (titleEl ? titleEl.innerText.trim() : '');
  const descEl = root.querySelector('#detail-desc, .desc, .note-text');
  const authorEl = root.querySelector('.author-wrapper .name, .user-name, .author .name');
  const dateEl = root.querySelector('#date, .date, .publish-time');
  // 视频判断：主笔记为视频时页面有播放器 video/播放容器（用于二次修正 note_type）
  const videoEl = root.querySelector('video, [class*="video-player"], [class*="player"]');
  // 主图：仅笔记图床（sns-webpic/sns-img/sns-preview）——排除头像(avatar)、静态资源(fe-static/formula-static)、
  // logo、二维码、svg 等页面装饰图（它们 URL 也含 xhscdn.com，此前会混入克隆图片导致多余图）
  const imgs = [...root.querySelectorAll('.swiper-slide img, #noteContainer img, .slide img, .carousel img, [class*="slide"] img')]
    .map((i) => i.src || i.getAttribute('data-src') || null)
    .filter((u) => u && /sns-webpic|sns-img|sns-preview/i.test(u))                       // 仅笔记图床域名
    .filter((u) => !/avatar|fe-static|formula-static|logo|qrcode|svg/i.test(u))          // 排除页面装饰图
    .filter((u, i, arr) => arr.indexOf(u) === i)                                         // 去重
    .slice(0, 9);
  return {
    title,
    desc: descEl ? descEl.innerText.trim() : '',
    author: authorEl ? authorEl.innerText.trim() : '',
    date: dateEl ? dateEl.innerText.trim() : '',
    imgs,
    type: videoEl ? 'video' : 'note',
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

function xhsExtractComments(root = document) {
  // 评论区互动用户提取（P4 采集客户）：昵称 + UID + 评论文本
  // 只在该页面的评论容器内提取（避免把侧边栏"我/首页/关注/作者徽标"等非评论链接当成评论者）；
  // 昵称 a 有文本、头像 a 无文本 → 只取有文本的 a，且必须匹配到评论文本才计入。
  const NAV = new Set(['首页', '点点', 'RED', '直播', '发布', '通知', '消息', '我', '关注', '作者', '更多', '关于我们', '返回顶部', '全部', '最热', '最新']);
  const AREA = new Set(['广东', '北京', '上海', '天津', '重庆', '江苏', '浙江', '四川', '湖南', '湖北', '福建', '山东', '河南', '河北', '安徽', '江西', '陕西', '山西', '辽宁', '吉林', '黑龙江', '云南', '贵州', '广西', '海南', '甘肃', '青海', '宁夏', '新疆', '西藏', '内蒙古', '香港', '澳门', '台湾', '日本', '美国', '加拿大', '英国', '法国', '德国', '澳大利亚', '新加坡', '韩国', '泰国', '马来西亚']);
  const TIME = /^(刚刚|昨天|前天|\d+天前|\d+-\d+|\d+小时前|\d+分钟前)$/;
  const out = [];
  const seen = new Set();
  // 评论区精确容器：新版页面评论用户链 author-wrapper < author-container < interaction-container；
  // 容器 class 随改版可能失效，容器内 profile 链接过少时回退全文档
  // （靠 NAV 导航词 + 评论文本存在性过滤，防止把作者/相关推荐/徽标当评论者）
  let container = root.querySelector('.interaction-container') || root.querySelector('[class*="comment"]');
  const probe = container ? [...container.querySelectorAll('a[href*="/user/profile/"]')] : [];
  if (probe.length < 1) container = null;
  const anchors = [...(container || root).querySelectorAll('a[href*="/user/profile/"]')];
  for (const a of anchors) {
    const m = (a.getAttribute('href') || '').match(/\/user\/profile\/([0-9a-f]{24})/);
    if (!m) continue;
    const uid = m[1];
    const nick = (a.innerText || '').trim();
    if (!nick || NAV.has(nick)) continue;
    // 向上找最近的评论条，取第一条像评论文本的内容（排除时间/IP/纯数字/导航词）
    let cont = a.parentElement;
    let comment = '';
    for (let i = 0; i < 5 && cont; i++) {
      const spans = [...cont.querySelectorAll('span, div, p')]
        .map((e) => e.innerText.trim())
        .filter((t) => t && t.length > 1 && t !== nick);
      const cand = spans.find((t) => (
        t.length >= 2
        && !/^\d+$/.test(t)
        && !TIME.test(t)
        && !NAV.has(t)
        && !AREA.has(t)
      ));
      if (cand) {
        const first = cand.split('\n')[0].trim();
        if (first === nick) continue;                    // 作者徽标条目（无独立评论文本）
        if (/^\d+-\d+/.test(first) || TIME.test(first) || AREA.has(first)) continue; // 图片评论（无文字，首行即时间/IP）
        comment = cand;
        break;
      }
      cont = cont.parentElement;
    }
    if (!comment) continue; // 无评论文本 = 非评论者（导航/关注/作者徽标）
    const key = uid + '|' + nick + '|' + comment.slice(0, 24);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      uid,
      nickname: nick,
      comment: comment.slice(0, 300),
      profile: 'https://www.xiaohongshu.com/user/profile/' + uid,
    });
  }
  return out;
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
    comments: xhsExtractComments,
    isLoggedIn: xhsIsLoggedIn,
  };
}
