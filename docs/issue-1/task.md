# Task · Issue #1 每日一瓜（任务拆解）

状态：✅ 已完成（对应 PR `feat/issue-1`）。每项标注产出文件与验证方式。

## T1 项目骨架
- [x] `pyproject.toml`（Python 3.11、runtime 零依赖、ruff/pytest 配置）
- [x] `requirements.txt`（空）/ `requirements-dev.txt`（pytest、ruff）
- [x] `.gitignore`（忽略 `.env`、`_site/`、`dist/`、缓存）
- [x] `.env.example`（占位，不含真实密钥）
- 验证：`pip install -r requirements-dev.txt` + `ruff check .`

## T2 src/common
- [x] `config.py`：`Settings`/`load_settings`（LLM/X/GitHub 凭据、预算、种子、RSS、站点地址）
- [x] `http.py`：`HttpClient`（超时、指数退避、4xx 不重试、JSON 解析错误）
- [x] `budget.py`：`estimate_tokens`/`truncate_to_tokens`/`TokenBudget`
- [x] `redaction.py`：`Redactor`/`RedactingFilter`/`get_logger`/`setup_logging`
- [x] `privacy.py`：手机号/座机/邮箱/身份证打码
- [x] `models.py`：`Post`/`Metrics`/URL 规范化/状态 id 解析
- [x] `frontmatter.py`：瓜报 front matter 解析 + `ReportDigest` 回读
- 验证：`tests/test_common.py`（18 例）

## T3 src/collect
- [x] `x_api.py`：`recent_search` / `user_timeline`
- [x] `x_mirror.py`：公开镜像单帖采集（兼容 fx/vxtwitter 两种返回）
- [x] `rss.py`：RSS/Atom 解析（命名空间无关、坏 feed 容错）
- [x] `web.py`：通用网页标题/摘要探测
- [x] `service.py`：三通道合并 + 去重 + 告警 + `degraded` 标记
- 验证：`tests/test_collect.py`（11 例，含「凭据缺失仍产出」）

## T4 src/rank
- [x] `scorer.py`：增速/争议度/大号/传播面加权，Top1 + Top3，确定性排序
- [x] `snapshots.py`：快照读写、过期淘汰、时间差
- 验证：`tests/test_rank.py`（12 例）

## T5 src/agent
- [x] `melons.py`：确定性吃瓜指数（4 分量 + 阈值 + 理由）
- [x] `llm.py`：OpenAI 兼容调用（预算扣减、重试、错误分类）
- [x] `prompts.py`：只喂材料、强制引用、不点名、不猜身份
- [x] `parse.py`：JSON 优先（含代码块/前后缀），Markdown 段落回退
- [x] `report.py`：链接白名单清洗、无源降级、未证实默认标注、候选表
- [x] `render.py`：瓜报 Markdown / Issue 正文渲染
- [x] `generate.py`：编排 + 三种降级路径
- [x] `commands.py`：`/more` `/fact-check` `/vote` 渲染与取数
- 验证：`tests/test_agent.py`（16 例）、`tests/test_commands.py`（15 例）

## T6 src/publish
- [x] `writer.py`：原子写、按日期列报表
- [x] `readme.py`：标记区间幂等替换索引
- [x] `feed.py`：RSS 2.0（转义、pubDate、无条目也合法）
- [x] `issue.py`：`GitHubClient`（标题幂等建 Issue、评论、标签、更新正文）
- [x] `pages.py`：极简 Markdown→HTML、站点构建
- 验证：`tests/test_publish.py`（17 例）

## T7 src/cli
- [x] `daily.py`：采集→打分→生成→发布→开 Issue，结构化输出
- [x] `from_issue.py`：投瓜核查回执 + 候选池
- [x] `comment_agent.py`：命令分发 + 幂等状态
- [x] `x_share.py`：草稿 + intent 链接（不自动发帖）
- [x] `pages.py`：`_site` 构建
- [x] `env.py`：`.env` 读取、北京日期、事件载荷、结构化输出
- 验证：`tests/test_cli.py`（16 例离线 e2e）

## T8 workflows 与模板
- [x] workflow 内容与校验：`daily-gua.yml`（cron + dispatch，提交回仓库，失败留痕）、`gua-from-issue.yml`、
      `gua-comment-agent.yml`（超时 5 分钟 + 幂等状态）、`x-share.yml`、`pages.yml`、`ci.yml`；
      全部 YAML 可解析、均含 `workflow_dispatch`
- [ ] **待维护者添加**：平台 git 凭据缺少 GitHub `workflow` scope，`git push` 被 GitHub 拒绝
      （`refusing to allow a Personal Access Token to create or update workflow`），
      故本 PR 不含 `.github/workflows/`；文件全文见 PR 说明，网页端添加或换凭据即可
- [x] `.github/ISSUE_TEMPLATE/gua-submit.yml`、`correction.yml`、`config.yml`（已随 PR 推送）
- 验证：`python -c "import yaml,glob; [yaml.safe_load(open(p)) for p in glob.glob('.github/**/*.yml', recursive=True)]"`

## T9 文档与首篇瓜报
- [x] `README.md`：架构图（ASCII + mermaid）、模块表、打分公式、workflow 表、Secrets、本地运行、合规
- [x] `docs/issue-1/spec.md` / `plan.md` / `task.md`
- [x] `gua/2026-11-11.md`：基于示例种子的首篇瓜报（摘要 / 时间线 / 原链 / 未证实 / 吃瓜指数）
- [x] `feed.xml` + README 首页索引 + `state/snapshots.json`
- 验证：`python -m src.cli.pages`（站点可构建）+ front matter 回读断言

## 后续（非本 PR 范围）
- [ ] 维护者配置 Secrets（LLM / X）、Actions 写权限、Pages 源
- [ ] 首次手动触发 `daily-gua` 验证当日产出
- [ ] QA 审查通过后合入