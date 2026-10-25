# Step3 · Google/GitHub 外部调研发现落盘

> 时间：2026-10-25 | 渠道：Google（代理7890）/ general_search / GitHub
> 目标：技术底座、竞品工具、发布风控

## 一、直接竞品（对标工具）

### 1. MediaClaw 社媒虾（⭐Codex+MCP 正统玩法，疑似视频工具同思路产品）
- 官网：https://mediaclaw.app/en/features/codex-integration
- 形态：Chrome 扩展 + MCP server，给 Codex/WorkBuddy/豆包工作提供 40+ 工具
- 能力：采集小红书/抖音帖子、视频、评论、账号数据，去重成结构化结果；内容研究 skill（12 种方法做拆解/选题/起草）
- 用法：自然语言描述目标 → 按方法执行 → 可读本地知识库 → 采集前报告来源/数量/积分成本并等确认
- 计费：积分制（如视频拆解 16 credits/次），需会员
- 亮点：把"采集→分析→知识库"做成商品；浏览器扩展授权 + 每页访问都在自己浏览器

### 2. 玫瑰克隆工具（⭐功能最全的国产同类，已商品化）
- 官网：https://www.dcxh7.com/（OpenI收录）
- 定位：爆款笔记克隆系统/爆款文案生成器，一键采集、去重、改写、矩阵分发，"5分钟变成伪原创笔记"
- 价格：月卡45元 / 年卡98元 / 永久卡168元
- 27项功能分6类：爆款克隆对标（图文/抖音/三位文案大师）、产品→内容全自动（我有产品/MAX/爆款创作）、AI生成重制（视频生成/视频转图文/图片焕新/一句话改图）、文案标题加速器、运营辅助（笔记诊断/敏感词检测/分析同行/同行观测/热门搜索）、视觉私域（大字封面/私域卡片/批量水印/爆款模板）
- 效果案例：600粉小号视频转图文 7→126赞涨200粉；家居品牌改爆款 72小时87万曝光2300私信；矩阵号1主9子日发120条 爆款率1.2%→5.7%
- 不适合：强人设IP、医疗金融功效护肤、要100%原创的品牌号
- ⚠️合规提醒（工具方自己披露）：小红书2025Q4起有**多模态指纹检测**，光改滤镜/调亮度不够；建议克隆比例<50%，混剪自己素材；新号0-3天别发克隆内容先养号；私域卡片可能触发"交易导流"扣分

### 3. 其他生态
- 红薯侠：粘贴链接一比一复刻爆款结构、批量处理、可打包对标作者整个主页选题（https://videogpt.qingyu.club/products/hongshu）
- 八爪鱼RPA：小红书文案一键克隆发布（AI改写开关）
- 红鸦AI：小红书 AI 图文生成 skill，可装 WorkBuddy/Codex/Claude Code/Cursor
- 推舟AI网页版：https://note.limyai.com/dashboard/open/publish_note
- AnyToCopy：素材库爆款带入AI对话改写仿写

## 二、技术底座（开源/生态，可直接复用）

| 项目 | 语言 | 能力 | 链接 |
|---|---|---|---|
| xiaohongshu-mcp（xpzouying） | Go | 小红书 MCP server：搜索/浏览/留言/点赞/发布，给 Claude/Cursor/Codex 用 | clawhub.ai/borye/xiaohongshu-mcp |
| rednote-mcp（@sykuang） | Node.js | 同上 npm 包 | npmjs.com/package/@sykuang/rednote-mcp |
| XHS-Downloader（JoeanAmier） | Python | 链接提取/作品采集/无水印下载，Releases 可直接下载 | github.com/JoeanAmier/XHS-Downloader |
| mcxiaoxiao/xiaohongshuCrawler | Python+油猴 | 三步实现获取标题/正文/话题标签 | github.com/mcxiaoxiao/xiaohongshuCrawler |
| xiaohongshu-cli（jackwener） | Python | CLI 搜索/读取/交互（逆向 API） | pypi.org/project/xiaohongshu-cli |
| redbooks（xiaofuqing13） | - | 小红书爬虫（OpenClaw skill 生态推荐） | github.com/xiaofuqing13/redbooks |
| upJiang/jiang-xiaohongshu-crawler | - | 采集+AI分析 | github.com/upJiang |
| BrowserAct | - | 反检测浏览器自动化 CLI，AI Agent 用，stealth-extract 拿小红书首页内容 | juejin.cn/post/7644061630558388276 |

## 三、发布风控关键情报（多账号矩阵必读）
1. **多模态指纹检测**（小红书2025Q4起）：改滤镜/调亮度不再有效，克隆内容需混剪/降比例
2. **单IP单账号**：每账号独立住宅IP；高频切IP反而触发风控；DNS泄露会被发现
3. **指纹浏览器**（比特浏览器/其他）：每账号独立浏览器指纹环境
4. **养号**：新号0-3天不发克隆内容，先浏览+点赞养号进初级流量池
5. **发布节奏**：频繁切换账号、短时间大量操作触发风控
6. **引流**：微信外链被举报会触发"交易导流"扣分，先引小号/粉丝群再转化

## 四、对复刻方案的影响
- 视频工具（Codex 自动获客）底层 = 本地 Web UI + MCP/浏览器自动化采集 + AI改写 + 多账号发布，**技术底座全部可复用开源组件**
- 合规视角（AI亦仁警告）：采集+改写他人内容属灰产边缘 → 产品定位应转向"辅助创作/原创提效"或明确告知用户合规边界（参考玫瑰克隆的做法：主动披露风控与版权风险）
- 竞品价格带：玫瑰克隆 45-168元（永久买断）、社媒虾积分制会员、推舟月卡——验证大周调研的"几百元档"判断
