# Task: Issue #5

- [x] T1 写失败测试：`rank_posts` 排除已发布 URL、`published.json` 幂等/保留最近 N。
- [x] T2 实现 `src/rank/published.py` 与 `rank_posts(exclude_urls=...)`。
- [x] T3 写失败测试：连续两日不同 `primary_url`、全已发布复用可见、`published.json` 不膨胀。
- [x] T4 `config` 拆出 `fallback_seed_urls`，`collect` 标注兜底来源。
- [x] T5 `daily` 串联兜底、复用、幂等记录；`render` 顶部展示降级原因。
- [x] T6 `pytest` + `ruff` 全绿，自审查、建 PR、通知 QA。