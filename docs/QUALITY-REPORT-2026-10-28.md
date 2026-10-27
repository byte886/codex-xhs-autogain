# codex-zhuke 全项目 Clean Code 质检报告（2026-10-28 凌晨）

> 范围：后端（app/src/codex_xhs/*）+ 前端（app/frontend/src/*）+ 生产脚本（app/scripts/*）+ 浏览器提取（extension/lib/extract.js）+ 测试（tests/*）。
> 方式：本轮为**可执行检查项**（pytest / import-linter / radon / 构建 / 语法），结合 Clean Code Gauntlet 六维度门（CRAP / 变异 / 覆盖率 / 架构 / DRY）。gauntlet 的 crapper / mutator 在本机因工具链环境（tree-sitter 语法包未生效）无法产出，改用等价轻量工具补齐。

## 一、六维度结果

| 维度 | 工具 | 结果 | 说明 |
|---|---|---|---|
| 测试（全量） | pytest | ✅ 8 文件全绿 EXIT=0 | score 38 / cookie_reader 15+23 / browser_client 7 / collect_pipeline 6 / server / server_routes 60 / server_api 10 |
| 架构约束 | import-linter | ✅ 2/2 contracts kept, 0 broken | leaf 纯净（score/cookie_reader）；server 顶层（无环） |
| 圈复杂度 | radon | ⚠️ 5 处超标（阈值 ≤8） | 见下方清单 |
| 覆盖率 | pytest --cov | ❌ 全量跑挂起（37% 处） | coverage × asyncio 后台任务组合疑似死锁，待专项排查 |
| CRAP / 变异 | crapper / mutator | ⛔ 工具链不可用 | crapper 卡死 30s+ 无输出；疑 tree-sitter 语法包缺失（install 后需 patch-treesitter.sh，本机未生效） |
| DRY | dryer | ⛔ 同工具链不可用 | — |
| 前端构建 | npm run build | ✅ 通过 | 产出 ../web/assets/index-CnNw2ilS.js / CRKb7nSt.css |
| 脚本语法 | node --check | ✅ extract.js 通过 | 生产链路注入源 |

## 二、圈复杂度超标清单（radon，C 级 >8）

| 文件 | 函数 | 复杂度 |
|---|---|---|
| server.py | `cdp_setup` | 15 |
| server.py | `_read_cookie_status` | 11 |
| server.py | `_enrich_notes` | 11 |
| server.py | `notes` | 11 |
| collect_pipeline.py | `get_feed_detail` | 15 |

建议：拆分 `cdp_setup` 的「就绪探测→平台脚本拉起→轮询确认」三段；`get_feed_detail` 拆「详情补全 / 数据修正 / 互动回填」。**重构涉及 CDP 链路与浏览器交互，需平台恢复后实机验证，暂缓执行。**

## 三、本轮已修复

- **test_server_routes.py collect 测试挂起**：collect_start worker 新增 `_enrich_notes`（真实浏览器详情补全）步骤后，测试未同步 mock → TestClient 内真实调用浏览器导致全量 pytest 挂起。已补 `fake_enrich` monkeypatch（commit `07b11c9`）。
- 附带发现：**多个 pytest 进程并发会互相抢 DB/资源**，串行分文件执行稳定。

## 四、遗留待办（需用户决策/专项）

1. **覆盖率全量挂起**：`pytest --cov` 在约 37%（browser_client/collect_pipeline 附近）卡死。疑 coverage sysmon 与 playwright 驱动/后台 asyncio 任务组合死锁。待专项：分文件 `--cov` 定位首个挂起文件 → 单测该文件 → 决定是否给 browser_client 测试加隔离。
2. **gauntlet 工具链**：crapper/mutator/dryer 需 tree-sitter 语法包（patch-treesitter.sh）或工具链重装；否则 CRAP/变异/DRY 三维度无法机器执行（可用 radon + 人工评审替代）。
3. **复杂度重构**：见上清单，待平台恢复后与实机验证一并做。
4. **夜间平台探测**（2026-10-28 05:13）：CDP 4 tab 无 captcha，但 `/api/search` 探测 180s 超时 → **平台仍限流中**，未连环重试；待白天低频再探后跑真实链路（采集→评分→作者榜→克隆验证提取增强→客户库→草稿）。
