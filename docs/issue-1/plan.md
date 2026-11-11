# Plan · Issue #1 每日一瓜（方案设计）

## 1. 技术选型

| 维度 | 选项 | 选择 | 理由 |
| --- | --- | --- | --- |
| 语言 | Python / Node | **Python 3.11** | Actions 原生支持；pytest/ruff 成熟；团队可维护 |
| 运行时依赖 | requests/pyyaml/feedgen vs stdlib | **stdlib only** | Actions 少一层安装失败风险；JSON/XML/HTTP 都用标准库够用 |
| 开发依赖 | pytest / ruff | 只这两个 | CI 快、确定性强 |
| X 数据 | X API / 公开镜像 / RSS | **API 优先 → 镜像 → RSS** | 无凭据也能出报；镜像只取单帖公开元数据 |
| 存储 | DB / Git 文件 | **Git 文件**（`gua/*.md` + `state/*.json`） | 零运维、天然版本历史、Pages 直接消费 |
| LLM 输出 | 自由 Markdown / 强约束 JSON | **JSON 优先，文本回退** | 结构化字段可校验；回退保证兼容不同网关 |
| LLM 失败 | 中断 / 降级 | **降级为链接汇总** | 验收要求「每日必有产出」 |
| 瓜报格式 | 数据库 / Markdown + front matter | **Markdown + 极简 front matter** | Pages/feed/评论机器人共用同一份文件 |

## 2. 分层与依赖

```
common（模型/Settings/HTTP/预算/脱敏/frontmatter）
   ▲        ▲         ▲          ▲
collect   rank      agent      publish
              ◀── agent ──▶ (只单向依赖 common)
   cli：组装五条链路（daily / from_issue / comment_agent / x_share / pages）
```

- `publish` 不 import `agent`（渲染在 agent，落盘在 publish）；
- `agent.commands` 读取已发布文件走 `common.frontmatter`，避免 agent ↔ publish 环；
- 所有层级只依赖 `common`，无循环。

## 3. 关键流程

### 3.1 daily

```
collect(settings, seeds)                     # 三通道合并 + 去重 + 告警
  → prune/load snapshots(state/snapshots.json)
  → rank_posts(posts, snapshots)             # Top1 + Top3
  → generate_report(primary, evidence, candidates)
        ├─ melon_index(primary)              # 确定性指数
        ├─ LLMClient.complete(messages)      # 预算 + 重试
        ├─ parse_llm_payload(raw)            # JSON 优先 / 文本回退
        └─ build_report(...)                 # 链接白名单校验 → 通过 or 降级
  → render_markdown → write_report            # gua/YYYY-MM-DD.md
  → save_snapshots / update_readme / render_feed
  → GitHubClient.ensure_issue(标题幂等)
  → 输出结构化 JSON（ok/date/headline/melon/generated_by/issue/warnings）
```

### 3.2 降级矩阵

| 缺失项 | 行为 |
| --- | --- |
| `LLM_BASE_URL` / `LLM_API_KEY` | 瓜报降级为链接汇总，Issue 正文注明降级原因 |
| LLM 超时 / 5xx / 空响应 | 同上，且 `notes` 记录原因 |
| LLM 输出无源（不引用材料链接） | 丢弃叙述，降级为链接汇总 |
| `X_BEARER_TOKEN` | 跳过 X API，走镜像 + RSS，`degraded=True` 但正常出报 |
| `GITHUB_TOKEN` / `GITHUB_REPOSITORY` | 跳过发帖，输出 `issue.skipped` 原因 |
| 所有通道都失败 | 退出码 2 + 告警，workflow 失败并在 step summary 留痕 |

### 3.3 打分公式

```
加权互动 E = 赞 + 2×转 + 2×回 + 2×引
增速 v = （有快照）ΔE / 小时  |  （无快照）E / 发布至今小时
争议度 c = min(1, ((回 + 1.5×引) / 赞) / 0.5)
大号 i = min(1, log10(1+粉丝) / 6)
传播 r = min(1, log10(1+阅读) / 6)
排序分 = 100 × (0.45·log1p(v)/log1p(200) + 0.30·c + 0.15·i + 0.10·r)
吃瓜指数 = 35·r' + 30·log10(1+E)/log10(5001) + 25·c + 10·新鲜度
```

## 4. 接口契约

### 4.1 瓜报 front matter（极简 YAML 子集）

```yaml
---
date: "2026-11-11"
title: "🍉 每日一瓜 2026-11-11"
headline: "…"
melon_score: 74
melon_level: "热"
generated_by: "llm"      # llm | manual | degraded
primary_url: "https://x.com/…/status/…"
sources:
  - "https://…"
---
```

### 4.2 CLI 输出（stdout JSON）

```json
{"ok": true, "date": "2026-11-11", "headline": "…", "melon": {"score": 74, "level": "热"},
 "generated_by": "llm", "degraded": false, "report_file": "gua/2026-11-11.md",
 "issue": {"number": 7, "url": "…", "created": true},
 "sources_used": ["x_mirror:seed", "rss"], "warnings": [], "notes": []}
```

### 4.3 状态文件

| 文件 | 结构 | 用途 |
| --- | --- | --- |
| `state/snapshots.json` | `{version, captured_at, posts:{key:{url,author,captured_at,metrics}}}` | 次日差分算增速 |
| `state/candidates.json` | `{version, updated_at, candidates:[{url,author,text,added_at,origin_issue}]}` | 投瓜候选池 |
| `state/handled_comments.json` | `{version, handled:{comment_id:{command,issue,at}}}` | 评论幂等 |

## 5. 安全设计

1. 凭据只经 `Settings`（环境变量）读取，任何异常信息都不含 key；
2. 日志 handler 挂 `RedactingFilter`：已知密钥值 + `Bearer/sk-/ghp_/URL token` 模式统一掩码；
3. LLM prompt 只喂材料，不喂凭据；请求头在 `HttpClient` 内构造，不落日志；
4. 报告与回帖渲染前过 `mask_personal`（手机号/座机/邮箱/身份证）；
5. 严禁自动发帖到 X，`x-share` 只产草稿。

## 6. 测试策略

| 层 | 用例重点 |
| --- | --- |
| common | 脱敏（含日志 filter）、HTTP 重试/不重试 4xx、预算超限、URL 规范化、隐私打码 |
| collect | 镜像两种返回形状、RSS/Atom 解析、坏 feed 容错、三通道降级、去重 |
| rank | 增速（快照差分 / 代理增速）、争议度排序、大号权重、Top1+Top3、确定性、快照读写 |
| agent | 指数区间与阈值、JSON/文本解析、伪造链接丢弃、无源降级、渲染小节、三种生成路径 |
| publish | 原子写、README 幂等替换、feed 合法 XML + 转义、Issue 幂等、Pages 构建与转义 |
| commands | 命令解析、投票统计（去重/去机器人）、三类回复、取数失败降级 |
| cli | 五条链路离线 e2e（无凭据出报、LLM 路径、开 Issue、投瓜 dry-run、评论 dry-run、草稿、站点） |

全部用例不依赖外网与真实凭据；时间字段用固定时钟 `FIXED_NOW` 与偏移断言（不做毫秒级相等比较）。

## 7. 交付与人工配置

| 项 | 由谁做 |
| --- | --- |
| 代码 / workflow / 模板 / README / 首篇瓜报 / CI | 本 PR |
| Secrets（LLM、X） | 仓库维护者（Settings → Secrets） |
| Actions 写权限、Pages 源 | 仓库维护者（Settings → Actions / Pages） |
| 首次 `daily-gua` 手动验证 | 仓库维护者（或 QA 跑一次 `workflow_dispatch`） |