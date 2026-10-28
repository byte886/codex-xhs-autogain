# UI 自动化测试（headless Chrome + Playwright）

一键回归：`bash scripts/ui_tests/run_all.sh`

## 前置

- 后端已启动在 `127.0.0.1:8791`（见仓库 README「后端启停」）
- Python venv `app/.venv`（含 playwright）
- 本机装有 Google Chrome（macOS 标准路径自动探测；也可 `CHROME=/path/chrome` 指定）

## 运行

```bash
# 全部（数据依赖脚本自动 SKIP）
bash scripts/ui_tests/run_all.sh

# 只跑指定编号（可多个）
bash scripts/ui_tests/run_all.sh t01 t03 t08
```

退出码：全部 PASS → 0；有 FAIL → 1（SKIP 不计失败）。

## 脚本清单

| 编号 | 文件 | 验证内容 | 数据依赖 |
|---|---|---|---|
| t01 | t01_ai_buttons.py | AI 工具条 5 按钮未配 Key 灰态 + AI 设置入口 | 无 |
| t02 | t02_tabs_nav.py | 4-Tab 导航切换 | 无 |
| t03 | t03_regress_smoke.py | 4-Tab/发布记录区/设置页冒烟 | 无 |
| t04 | t04_publish_qc.py | 发布前质检：违规词/绝对化/结构命中 + 强确认红线（取消不真发，LOGS_DELTA=0） | 无 |
| t05 | t05_filters.py | 采集筛选：默认态 + 排序/时间选择 → 已选提示 | 无 |
| t06 | t06_score.py | 评分：分数徽标 / hover 构成 / 四维弹窗 | notes 有数据（否则 SKIP） |
| t07 | t07_dedup.py | 发布图片去重：草稿恢复后重复 URL 只剩 1 张 | drafts 有数据（否则 SKIP） |
| t08 | t08_pubstats.py | 发布记录区：展开自动加载 + 回写结果展示 | publish_logs 有数据（否则 SKIP） |
| t09 | t09_clients_agg.py | 客户库聚合：意向分布/属地 TOP/近7天产出 | clients 有数据（否则 SKIP） |
| t10 | t10_clients_list.py | 客户线索列表：总数/联系方式命中/导出 CSV | clients 有数据（否则 SKIP） |
| t11 | t11_clients_guide.py | 获客指引风险卡（不做话术：私信限额/导流词/被动回复） | clients 有数据（否则 SKIP） |

## 说明

- 脚本统一连接外部 headless Chrome（`--remote-debugging-port=9333`），由 run_all.sh 负责启停清理，脚本自身不启动/杀 Chrome
- SKIP 语义：依赖测试数据的脚本（评分/去重/客户/发布日志）在无数据时以退出码 2 报告 SKIP，不误报回归失败；`run_all.sh` 统计时 SKIP 不计入失败
- DB 路径均用 `Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db'` 可移植写法（scripts/ui_tests/ 向上 2 级到仓库根），无 `/Users/<用户名>` 硬编码

## 关键方法论（血泪教训，改脚本必读）

1. **切 Tab 严禁 `evaluate` 内 DOM `click()`**：Vue 的 `@click` 不会响应 JS 合成的 DOM click，页面实际不会切换——早期 t01/t02 全部「假 PASS」。必须用 Playwright 原生 `pg.locator('.tab', has_text='创作').first.click()`。
2. **断言可见性用 `is_visible()`，不要用 `count()`**：Vue `v-show` 隐藏的元素仍在 DOM 中（如 `.ai-bar`），`count()>0` 不代表用户能看到。
3. **`?stage=pick` 有 onMounted 竞态**：页面加载后 onMounted 会异步拉数据并把 Tab 拉回「采集」。点击 Tab 前必须先等 `flow-hint` 显示「关键词采集」稳定（onMounted 完成），点完再 `wait_for_function` 等 hint 变为目标 Tab 文案，确认切换真正生效。
4. **`wait_for_function` 也会竞态误过**：若点击后 hint 短暂变为目标值、随后又被 onMounted 覆盖，等待可能提前通过。因此初始等待（第 3 条）是必须的，不能只靠点击后的等待。
5. 全部断言必须真实（早期 t09/t10 无 assert 是「假 PASS」）；种子数据档位用后端真实值（intent 为 `high/upper/middle/low`，`mid` 会让 `/api/clients_agg` 500）。
