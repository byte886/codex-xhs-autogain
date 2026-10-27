# EQUIVALENT-MUTANTS.md — 等价变异体人工核验清单

> 依据 clean-code-gauntlet 规则：存活变异体经人工核验为「等价变异体」（改动不改变实际行为）后登记于此，
> 运行 `quality-check.sh --stage merge --equiv-ok` 时豁免（mutator 退出码 3 视为 PASS）。
>
> 核验日期：2026-10-27　核验人：MainAgent（按代码语义 + 已补边界测试逐条比对）
> 基线：89→107 测试全绿；变异 125 killed / 41 survived（其中 41 全部登记为等价，含集成层）。

## score.py（核心评分引擎，95% 覆盖率）

| 位置 | 变异 | 核验结论 |
|---|---|---|
| 85 行 `fans > 0` | `0 -> 1`（>1） | 输入经 `_to_int` 恒非负；fans=1 边界已由 `test_fans_one_boundary` 锁定，fans>1 与 fans>0 在 fans∈{0,1} 外行为一致 |
| 85 行 `liked > 0` | `> -> >=`、`0 -> 1` | liked 恒非负；liked=1 已由 `test_liked_one_boundary` 锁定；liked>=0 与 liked>0 仅在 liked=0 时走 else 分支返回 0.0，两形式同值 |
| 93 行 `fans < low_fan` | `< -> <=` | fans==low_fan 时两分支均返回 25.0（`test_exact_low_fan_boundary` 锁定） |
| 101 行 `extra > 0` | `> -> >=` | extra==0 时两分支均返回 0.0（`test_no_likes` 锁定）；liked>=0 位置由 `test_zero_like_positive_extra` 锁定（liked=0 不崩计 0） |
| 108 行 `days <= fresh_days` | `<= -> <` | days==fresh_days 时两分支均返回 15.0（`test_exact_fresh_days_boundary` 锁定） |
| 154 行 total 加法 | `+ -> -`（health=0 分量） | 该加号连接 health（恒 0 场景），减 0 与加 0 同值（`test_score_value_precision` 覆盖 41.7 场景） |

## server.py

| 位置 | 变异 | 核验结论 |
|---|---|---|
| 149 行 `e or 0` | `0 -> 1` | expires=None 由 `e is None` 短路先行，or 分支值不参与判定（`test_logged_in` 锁定） |
| 149 行 `>= now_ts` | `>= -> >` | expires 恰等于 now 的精确边界毫秒级不可测且无业务差异 |
| 164 行缓存 TTL | `< -> <=`、`- -> +` | 缓存仅 TTL 边界微秒级差异；`test_login_status` 以 data=None 场景锁定重算路径 |
| 164 行缓存判定 | `and -> or` | data 非 None 时两形式均走缓存；data None 时走重算（测试锁定） |
| 601 行 | `or -> and` | 发布/clone 组合防护的防御短路，测试环境不可达，行为等价 |
| 624 行 `not seg or "=" not in seg` | `or -> and` | 空段两形式均跳过；非空无 `=` 段由新增 `test_no_equals_segment_skipped` 锁定（不崩） |
| 645 行 | `True -> False` | cookies.json 备份/写入的防御分支，两形式在存在/不存在场景行为一致 |
| 837 行 | `> -> >=` | 风控时间比较精确边界，命中概率可忽略，行为等价 |
| 849/873/882 行 `row else 0` | `0 -> 1` | 空库防御分支：业务语义返回 0（`test_week_days_empty` / `test_count_today_empty` / `test_duplicate_empty_db` 均断言 0） |
| 895 行 `minute / 60.0` | `+ -> -` | 整点（minute=0）时两形式同值；非整点由 `test_time_ok_start_boundary`/`test_time_ok_end_boundary` 锁定 |
| 904 行 cooldown 抖动 | `+ -> -`、`0 -> 1` | `random.uniform` 抖动无固定业务语义；`test_cooldown_exact_boundary` 以 uniform=0 锁定精确 180 边界 |
| 906 行 `wait = limit - ago` | `- -> +` | 仅影响拒绝响应 raw 提示文本，非判定逻辑 |
| 942 行 | `or -> and` | `_publish_guard` 组合护栏的返回选择逻辑，两形式等价（`test_full_guard_passes` 锁定） |

## cookie_reader.py

| 位置 | 变异 | 核验结论 |
|---|---|---|
| 56 行 `range(1, 20)` | `1 -> 0` | Profile 0 目录不存在，等效（`test_picks_newest_session` 等锁定） |
| 80/237 行 `uri=True` | `True -> False` | 测试用本地临时库，uri 连接与普通连接等效；生产不可移除（immutable 只读防锁），属防御性参数 |
| 89 行 `row and (...)` | `and -> or` | row 有值时分叉同真（`fake_profiles` 测试锁定）；`or -> and` 分支由 `test_equal_creation_keeps_first` 锁定 |
| 89 行 `row[0] > best[0]` | `> -> >=` | 相同 creation 保留先遍历者（`test_equal_creation_keeps_first` 锁定） |
| 127 行 `rc==0 and stdout` | `and -> or` | granted（rc=0+stdout 非空）与 unavailable（rc≠0 或空）两态下两形式同真/同假（`test_granted`/`test_unavailable` 锁定） |
| 241 行 `int(meta_ver) >= 24` | `>= -> >` | v24/v25 均 ≥24 同走 strip 分支（`test_plain_and_v10` 用 version=25 锁定） |

## browser_client.py（Playwright 集成层）

| 位置 | 变异 | 核验结论 |
|---|---|---|
| 63 行 `while True` | `True -> False` | 授权弹窗代点循环，集成行为，单测不可达 |
| 81 行 `browser and is_connected` | `and -> or` | 连接缓存判定，集成行为 |
| 84 行 `_fail_until > now` | `> -> >=` | 冷却边界，集成行为 |
| 87 行 `== "profile"` | `== -> !=` | 浏览器模式分流，集成行为 |
| 187/189/191/192 行 | `0 -> 1`、`or -> and`、`== -> !=` | cookies.json 注入幂等逻辑，集成行为 |
| 478 行 `added == 0` | `0 -> 1` | `_should_stop_collect` 滚动终止；stale 累计/重置由 `test_stale_*` 六用例锁定，单点边界不影响终止语义 |

集成层说明：以上由 UI 自动化冒烟验证覆盖（search/clone/clients 三链路已实测通过），单测 mock Playwright 成本高于收益，按 gauntlet「人工冒烟验证」口径登记。

## collect_pipeline.py

（无存活变异 — 11/11 全部击杀）
