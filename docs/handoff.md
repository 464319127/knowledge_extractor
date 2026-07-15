# 当前状态

## 目标

使用已实现的知识提取管线处理 `conversation.json`，生成并验证 OKF bundle。

## 已完成

- 建立 Python 3.11+ 包和 `knowledge-extractor` CLI，提供 `normalize`、`extract`、`render`、`validate`、`run` 命令。
- 实现累积式 API JSONL 规范化：只保留 `end_turn` 完整问答，删除运行时噪声，脱敏个人 home 路径和常见 token，并保存源文件 SHA-256、会话、请求和行号。
- 实现 Pydantic 候选模型，校验 Concept/Claim slug、来源、纠正关系、唯一性和审核状态。
- 实现 Anthropic Messages 兼容 API 适配器；通过系统 `curl` 使用内部证书信任，token 从环境变量或权限受限的文件读取且不进入 argv。
- 实现显式审核门禁。模型只能产生 `draft`，`render` 只发布人工设为 `approved` 的 Concept。
- 实现确定性 OKF 渲染、脱敏会话证据、递归 `index.md`、frontmatter/链接/敏感标记校验，以及临时目录验证后原子写入。
- 添加 13 个测试，覆盖规范化、脱敏、API 包装、token 传递、临时文件、审核门禁、OKF 输出和 bundle 路径边界。
- 使用真实 3 MB JSONL 完成端到端验证：15 条 API 调用规范化为 3 个完整问答，OneAPI 生成 7 个草稿 Concept。
- 扩展 `normalize` 兼容 `export-conversation` 的 `{"messages": [...]}` 便携格式：59 条消息规范化为 8 个问答，并新增决策记录 [0004](decisions/0004-portable-messages-input.md)。
- 对 `conversation.json` 完成 OneAPI 提取：生成 3 个 `draft` Concept，候选来源校验通过。
- 完成 3 个 Concept 的人工审核标记并生成 `bundles/conversation`；修正一条会暴露内部字段名的候选表述后，bundle 校验通过。

## 决策

- 管线拆分为确定性规范化、模型提取、人工审核和确定性 OKF 渲染；详见 `docs/decisions/0001-staged-extraction-pipeline.md`。
- 第一版通过独立适配器使用 Anthropic Messages 兼容 API；详见 `docs/decisions/0002-messages-api-adapter.md`。
- 内部端点使用系统 `curl` transport，不关闭 TLS 验证，token 经 stdin config 传入；详见 `docs/decisions/0003-curl-transport.md`。
- `normalize` 兼容便携 `messages` 文档并按消息位置生成稳定来源 ID；详见 `docs/decisions/0004-portable-messages-input.md`。

## 当前状态

- MVP 已实现，离线测试和真实端点提取均通过。
- `person_key` 权限为 `0600` 且被 Git 忽略；真实候选中未发现 token，调用后未遗留请求临时文件。
- `staging/0a7e1ea4/candidates.json` 包含 7 个 `draft` Concept。逐样本路由纠正被识别为 `accepted`，早期统一 gather 建议为 `superseded`，其他大部分技术结论为 `unverified`。
- `bundles/conversation` 已发布并通过校验，包含 3 个 Concept、1 个会话引用和 4 个索引文件。Concept 中仍保留来源对话里的 `unverified` 或 `superseded` Claim，不能把它们当作独立实验结论。
- 当前会话已通过本机 `export-conversation` 技能导出到被 Git 忽略的 `conversation-export/`，便携消息副本为 `conversation.json`。`raw/` 含敏感运行记录，不得提交或作为项目知识分发。
- `staging/conversation/normalized.json` 记录 `conversation.json` 的 SHA-256 和 8 个问答；`staging/conversation/candidates.json` 中 3 个 Concept 已标记为 `approved`。

## 后续步骤

1. 按 `TODO.md` 取得候选引用的源码或实验结果，审核 7 个真实草稿。
2. 根据更多日志样本补充方言兼容性测试，避免在没有真实需求前扩展解析规则。

## 验证

- `.venv/bin/pip install -e '.[dev]'`（通过：在获得网络权限后安装 Pydantic、PyYAML、pytest 和 editable 包；首次 sandbox 内运行因 DNS 限制失败）
- `.venv/bin/pytest -q`（通过：12 项测试）
- `.venv/bin/python -m compileall -q src tests`（通过）
- `.venv/bin/knowledge-extractor normalize /Users/wangning33/Documents/git/knowledge-catalog/user_log_w_cot/0a7e1ea4-638b-481d-9149-911236fae0fa_thinking.jsonl --out staging/0a7e1ea4/normalized.json`（通过：15 条记录生成 3 个完整问答）
- `.venv/bin/knowledge-extractor extract staging/0a7e1ea4/normalized.json --out staging/0a7e1ea4/candidates.json --api-key-file person_key --max-tokens 6000 --timeout 300`（通过：真实 OneAPI 生成 7 个草稿 Concept）
- `.venv/bin/knowledge-extractor render --normalized staging/0a7e1ea4/normalized.json --candidates staging/0a7e1ea4/candidates.json --out staging/0a7e1ea4/bundle`（预期失败：没有 `approved` Concept，审核门禁生效）
- `.venv/bin/python -c 'from knowledge_extractor.models import CandidateBundle,NormalizedCorpus; from knowledge_extractor.storage import read_model; from knowledge_extractor.validation import validate_candidate_sources; c=read_model("staging/0a7e1ea4/candidates.json",CandidateBundle); n=read_model("staging/0a7e1ea4/normalized.json",NormalizedCorpus); validate_candidate_sources(c,n); print(f"valid concepts={len(c.concepts)} turns={sum(len(s.turns) for s in n.sessions)}")'`（通过：7 个 Concept、3 个问答的来源引用有效）
- `.venv/bin/python -c 'from pathlib import Path; key=Path("person_key").read_text().strip(); data=Path("staging/0a7e1ea4/candidates.json").read_text(); print("key_leaked=" + str(key in data).lower())'`（通过：`key_leaked=false`）
- `find /private/tmp /tmp -maxdepth 1 -type f -name 'knowledge-extractor-request-*.json' -print 2>/dev/null`（通过：没有遗留请求临时文件）
- `git diff --check`（通过）
- `rg -n '[[:blank:]]+$' AGENTS.md README.md TODO.md docs src tests pyproject.toml .gitignore`（通过：未发现行尾空白）
- `python3 /Users/wangning33/.codex/skills/export-conversation/scripts/export_conversation.py --output conversation-export`（通过：completed-only 导出 59 条可见事件和 59 条 OpenAI 消息，无警告）
- `python3 /Users/wangning33/.codex/skills/export-conversation/scripts/export_conversation.py --verify conversation-export`（通过：检查 4 个文件，无失败）
- `cmp -s conversation-export/openai/messages.json conversation.json`（通过：便携消息文件逐字节一致）
- `.venv/bin/pytest -q`（通过：13 项测试，包含便携 messages 输入）
- `.venv/bin/python -m compileall -q src tests`（通过）
- `.venv/bin/knowledge-extractor normalize conversation.json --out staging/conversation/normalized.json`（通过：59 条消息生成 8 个问答）
- `.venv/bin/knowledge-extractor extract staging/conversation/normalized.json --out staging/conversation/candidates.json --api-key-file person_key --max-tokens 6000 --timeout 300`（通过：生成 3 个草稿 Concept）
- `.venv/bin/python -c 'from knowledge_extractor.models import CandidateBundle, NormalizedCorpus; from knowledge_extractor.storage import read_model; from knowledge_extractor.validation import validate_candidate_sources; c=read_model("staging/conversation/candidates.json", CandidateBundle); n=read_model("staging/conversation/normalized.json", NormalizedCorpus); validate_candidate_sources(c,n); print(f"concepts={len(c.concepts)} turns={sum(len(s.turns) for s in n.sessions)} statuses={[x.review_status.value for x in c.concepts]}")'`（通过：3 个 Concept、8 个问答、全部 draft）
- `rg -n '/Users/|/home/|Bearer [A-Za-z0-9._~+/-]+|sk-[A-Za-z0-9_-]{8,}|<system-reminder>|<ide_opened_file>|Authorization' staging/conversation/normalized.json staging/conversation/candidates.json`（通过：唯一匹配是已脱敏的 `Authorization: Bearer [REDACTED]`，未发现绝对 home 路径或真实 token）
- `.venv/bin/python -c 'from pathlib import Path; key=Path("person_key").read_text().strip(); data=Path("staging/conversation/candidates.json").read_text(); print("key_leaked=" + str(key in data).lower())'`（通过：`key_leaked=false`）
- `.venv/bin/knowledge-extractor render --normalized staging/conversation/normalized.json --candidates staging/conversation/candidates.json --out bundles/conversation --timestamp 2026-07-15T00:00:00+08:00`（首次失败：候选含禁止的 `cot_plaintext` 字面量；修正候选表述后通过：3 个 Concept、1 个引用、4 个索引）
- `.venv/bin/knowledge-extractor validate bundles/conversation`（通过）
- `rg -n '/Users/|/home/|Bearer [A-Za-z0-9._~+/-]+|sk-[A-Za-z0-9_-]{8,}|<system-reminder>|<ide_opened_file>|cot_plaintext|Authorization' bundles/conversation`（通过：仅命中引用中的已脱敏 `Authorization: Bearer [REDACTED]`，未发现真实 token 或内部字段标记）
