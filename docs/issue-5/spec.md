# Spec: Issue #5 — 每日瓜报复用同一条旧瓜

## Objective

多日 `gua/YYYY-MM-DD.md` 的 `primary_url` 应随当日素材变化；确无新素材时明示「无新素材 / 复用原因」，不得静默重复。

## 根因

1. `load_settings` 中 `seeds = _csv(SEED_URLS) or DEFAULT_SEED_URLS`，workflow 未配置 `SEED_URLS` → 单条固定 X 帖每日必进候选。
2. `state/snapshots.json` 只存指标，`rank_posts` 纯按互动打分 → 老帖凭累积互动每日压过 RSS / 投瓜条目。
3. `state/candidates.json` 为空、LLM 未配置 → 降级摘要逐字相同。

## Scope

- In：新增 `state/published.json`；`rank_posts` 支持 `exclude_urls`；种子仅作无新内容兜底；渲染可见降级原因；单测覆盖三场景。
- Out：X API 调优、新增数据源、投瓜模板交互、Pages 样式与 feed 结构。

## Acceptance Criteria

- [ ] 连续两日运行（固定种子 + `--date` 回放）产出不同 `primary_url`
- [ ] 唯一候选均已发布时，页面出现「无新素材 / 复用原因」说明，不静默重复
- [ ] `state/published.json` 幂等去重，重复运行不膨胀
- [ ] `pytest` 全绿，新增用例覆盖上述三场景
- [ ] `--no-llm` / `--dry-run` / `--seed` / `--no-issue` 行为不回退

## 关键决策

- 已发布状态独立成 `state/published.json`，不与 `prune_snapshots` 耦合。
- 全部候选已发布时「复用 + 显式标注」兜底，不 `exit 2` 静默失败。
- 排除粒度按规范化 URL，不误杀同作者新帖。