# Plan: Issue #5

## 方案

1. `src/rank/published.py`（新增）：`PublishedEntry(url/date/first_published_at)` 读写 `state/published.json`；URL 规范化去重、幂等追加、保留最近 `MAX_PUBLISHED` 条、原子写。
2. `src/rank/scorer.py`：`rank_posts(..., exclude_urls=None)`，打分前按 `canonical_url` 剔除已发布，保持纯函数、默认空集。
3. `src/common/config.py`：`seed_urls` 仅保留显式配置；新增 `fallback_seed_urls`（仅在 `SEED_URLS` 未配置时取 `DEFAULT_SEED_URLS`）。
4. `src/collect/service.py`：`collect_seed_posts(..., fallback=False)` 标注 `seed:fallback` 来源；`_merge` 提升为可复用的 `merge_results`。
5. `src/cli/daily.py`：
   - 正常采集 = `--seed` + 显式 `SEED_URLS` + 候选池 + X/RSS；
   - 打分时排除 `published`；
   - 无新候选 → 兜底采集 `fallback_seed_urls`；
   - 仍无新候选 → 复用全部候选中最高分者并写 `degrade_reason`（含「无新素材」）；
   - 非 dry-run 出报后幂等追加 `primary_url` 到 `published.json`。
6. `src/agent/render.py`：降级原因在正文顶部以醒目 blockquote 展示（同时保留数据来源说明小节）。

## 测试 seam

- `rank_posts` 公开参数 `exclude_urls`（纯函数）。
- `published` 模块 `load_published/record_published/published_urls`（固定时钟、临时文件）。
- `daily.run` CLI 端到端（FakeTransport + 临时 state/gua 目录，`--date` 回放）。

## 风险

- 全部候选被排除致当日无报 → 复用 + 标注兜底。
- 集合写冲突 → 原子写、URL 去重、保留最近 N 条。