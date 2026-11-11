# Spec · Issue #1 每日一瓜（需求文档）

> 来源：Issue #1 正文 + Issue Agent 发布的 Spec v1（comment 6788938941）。本文档是开发执行版需求。

## 1. 目标

每天北京时间 09:00，由 GitHub Actions + LLM Agent 自动发现科技 / AI 圈当天最热的一个「瓜」，
产出「一瓜一页」Markdown 瓜报（`gua/YYYY-MM-DD.md`），自动开当日 Issue，并更新 README 首页与 RSS。

## 2. 用户与场景

| 角色 | 场景 | 诉求 |
| --- | --- | --- |
| 仓库运营者 | 每日例行 | 无人值守出报；失败能看到原因 |
| 读者 | 早上刷 Issue / Pages / RSS | 1 分钟读完，一键回原文 |
| 投瓜用户 | 用 Issue 模板投稿 | 几分钟内拿到「回执」，知道是否入池 |
| 当事人 | 发现错误 | 能提交更正，且更正被置顶 |

## 3. 范围

### In Scope
- Python 3.11 分层骨架：`src/collect` / `src/rank` / `src/agent` / `src/publish` / `src/common` / `tests`。
- 5 个 workflow，全部支持 `workflow_dispatch`：`daily-gua` / `gua-from-issue` / `gua-comment-agent` / `x-share` / `pages`；外加 `ci`。
- 采集：X 官方 API 优先，凭据缺失时走公开镜像 + RSS 兜底。
- 打分：互动增速、争议度（回复+引用 / 点赞）、大号参与度、传播面 → Top1 + Top3 备选。
- LLM：OpenAI 兼容接口，重试 / 超时 / token 预算；失败降级为链接汇总。
- 发布：`gua/*.md`、README 索引、`feed.xml`、当日 Issue（按标题幂等）。
- 交互：`/more`、`/fact-check`、`/vote 🍉|🫠`，按 comment id 幂等。
- Issue 模板：投瓜（`gua-submit`）、更正/申诉（`correction`）。
- 首篇基于示例种子的瓜报；README（架构图 + Secrets + 本地运行）；CI（ruff + pytest）。

### Out of Scope
- 自动发帖到 X（`x-share` 只生成草稿 + intent 链接，人工确认后自行发布）。
- 多语言、用户体系、数据库存储（内容即 Git 文件）。
- X 之外的付费数据源。

## 4. 功能需求

### FR-1 daily-gua
1. 采集候选（种子链接 / 关键词 / 关注列表 / RSS）。
2. 按打分选出 Top1，保留 Top3 备选。
3. 调 LLM 生成：一句话摘要、时间线、关键原文、各方立场、未证实标注、吃瓜指数。
4. 写 `gua/YYYY-MM-DD.md`，重生成 README 索引与 `feed.xml`，保存指标快照。
5. 开当日 Issue「🍉 每日一瓜 YYYY-MM-DD」，正文含摘要 / 时间线 / 原文 / 未证实 / 全文链接 / 互动命令。

### FR-2 gua-from-issue
- 解析 Issue 正文中的链接（≤5 条），逐条核查可达性与公开指标；
- 回帖「投瓜核查回执」（表格 + 结论），通过的 X 帖写入 `state/candidates.json`；
- 打标 `gua-accepted`。

### FR-3 gua-comment-agent
- `/more`：拉最新指标，与上次快照差分 → 表格 + 增速；
- `/fact-check`：未证实项 + 链接可达性 → 逐条结论；
- `/vote 🍉/🫠`：统计有效票（去机器人、每人每票型一次）；
- 所有回复带原始链接；同一 comment id 只回一次；job 超时 5 分钟。

### FR-4 x-share
- 生成 ≤180 字文案 + `https://x.com/intent/tweet?...` 链接 + `dist/x-draft-<date>.md`；
- **不自动发帖**；可选把草稿评论到当日 Issue 供人工取用。

### FR-5 pages
- 把 `gua/*.md` 渲染成 HTML（含 `.md` 原件与 `feed.xml` 副本），按日期倒序索引。

### FR-6 合规
- 每条事实附原链；无源即降级；
- LLM 输出的链接必须命中材料白名单，伪造链接丢弃；
- 隐私字段打码；材料未点名者不指认；更正置顶流程。

## 5. 非功能需求

| 维度 | 要求 |
| --- | --- |
| 可用性 | LLM / X 凭据任一缺失都能出报（降级），不静默失败 |
| 安全 | 零明文密钥：只读 Secrets、日志脱敏、仓库不落盘凭据 |
| 可观测 | CLI 输出结构化 JSON（日期/头条/指数/来源/告警/降级原因），Actions step summary 汇总 |
| 幂等 | 当日 Issue 按标题复用；评论按 comment id 去重；瓜报文件按日期覆盖 |
| 性能 | 单次运行 < 5 分钟（爬取 + 1 次 LLM 调用） |
| 可测 | 全部用例离线（假传输层 / 假 LLM / 固定时钟） |

## 6. 验收标准映射

| 验收项 | 实现位置 | 验证方式 |
| --- | --- | --- |
| 分层就绪、无循环依赖 | `src/*` | `tests/test_*` + 依赖方向检查（publish 不 import agent） |
| 5 个 workflow + dispatch | `.github/workflows/` | 逐个检查 `workflow_dispatch`；**推送受 GitHub `workflow` scope 限制，待维护者添加** |
| Issue 模板 | `.github/ISSUE_TEMPLATE/` | 投瓜 / 更正两个表单 |
| 首篇瓜报含 6 要素 | `gua/2026-11-11.md` | front matter + 小节断言 |
| README 架构图 / Secrets / 本地运行 | `README.md` | 人读 + 命令可复制 |
| ruff + pytest | `ci.yml` | 本地已通过 |
| 手动触发产出瓜报与 Issue | `daily-gua.yml` | `python -m src.cli.daily` 等价路径 + 集成测试 |
| `/fact-check` 5 分钟内回帖 | `gua-comment-agent.yml` | job `timeout-minutes: 5` |
| 零明文密钥 | `src/common/redaction.py` | 单测断言密钥不出现在日志 |
| 凭据缺失仍产出 | `src/collect/service.py` | 单测：镜像 + RSS 兜底 |

## 7. 风险与对策

| 风险 | 对策 |
| --- | --- |
| X API 额度不足 | 公开镜像 + RSS 兜底，采集失败不阻断 |
| LLM 幻觉 / 无源断言 | 引用白名单 + 无源降级 + 未证实标注 |
| Actions token 无写权限 | README 明示开启 `Read and write permissions`；缺失时输出里明确标注「跳过」 |
| 评论重复回帖 | `state/handled_comments.json` 幂等 |
| 隐私/侵权 | 打码 + 不点名 + 更正置顶 + 仅公开内容 |