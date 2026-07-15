# knowledge_extractor

从累积式模型 API JSONL 日志中提取可审核知识，并生成符合 Open Knowledge Format（OKF）的 Markdown bundle。

项目不会把 JSONL 的每一行直接视为一条知识。管线先识别完整问答并删除重复历史和运行时内部内容，再让模型提取候选 Concept。候选内容必须由人显式批准，才能进入最终 bundle。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

模型调用使用系统 `curl`，不需要额外 HTTP SDK。`curl` 继续执行 TLS 证书校验，项目不提供关闭校验的选项。

## 工作流

### 1. 规范化日志

```bash
.venv/bin/knowledge-extractor normalize \
  ../knowledge-catalog/user_log_w_cot/example.jsonl \
  --out staging/example/normalized.json
```

输出只包含：

- `end_turn` 完整问答；
- `session_id`、`request_id`、来源行和源文件 SHA-256；
- 清洗后的真实用户问题和最终回答。

不会保留 `thinking`、`cot_plaintext`、签名、系统提示、工具定义、工具调用和工具结果。

### 2. 提取候选知识

将 Bearer token 放在 Git 忽略的本地文件中，并限制文件权限：

```bash
chmod 600 person_key

.venv/bin/knowledge-extractor extract \
  staging/example/normalized.json \
  --out staging/example/candidates.json \
  --api-key-file person_key
```

也可以使用环境变量，环境变量优先于文件：

```bash
export KNOWLEDGE_EXTRACTOR_API_KEY='...'
```

默认配置：

- endpoint：`https://oneapi-comate.baidu-int.com/v1/messages`
- model：`gpt-5.5`
- max tokens：`8192`
- timeout：`300` 秒

可通过 `--endpoint`、`--model`、`--max-tokens` 和 `--timeout` 覆盖。

### 3. 人工审核

打开 `candidates.json`，检查每个 Claim 的事实准确性、来源、状态和纠正关系。只把确认可以发布的 Concept 改为：

```json
"review_status": "approved"
```

`draft` 和 `rejected` Concept 不会被渲染。项目没有自动批准选项。

### 4. 生成 OKF bundle

```bash
.venv/bin/knowledge-extractor render \
  --normalized staging/example/normalized.json \
  --candidates staging/example/candidates.json \
  --out bundles/example
```

生成内容包括：

```text
bundles/example/
├── index.md
├── concepts/
│   ├── index.md
│   └── <concept>.md
└── references/
    ├── index.md
    └── conversations/
        ├── index.md
        └── <session-id>.md
```

会话证据文档只保存来源哈希、请求定位和清洗后的问题，不复制完整回答或原始日志。

### 一次运行到待审核状态

```bash
.venv/bin/knowledge-extractor run \
  ../knowledge-catalog/user_log_w_cot/example.jsonl \
  --staging staging/example \
  --api-key-file person_key
```

`run` 只生成 `normalized.json` 和 `candidates.json`，不会绕过审核生成 bundle。

## 验证

```bash
.venv/bin/pytest
.venv/bin/knowledge-extractor validate bundles/example
```

校验器检查 Concept frontmatter、内部 Markdown 链接和已知敏感标记。

## 安全约束

- 不要提交 `person_key`、`*.key`、staging 文件或原始私人日志。
- 不要通过命令行参数直接传递密钥。
- 模型输出是候选知识，不是已验证事实。
- 无代码或实验依据的性能结论应保持 `unverified`。
- 发布前通过 Git diff 或 Pull Request 审核最终 Markdown。

架构和数据处理理由见 [docs/decisions/0001-staged-extraction-pipeline.md](docs/decisions/0001-staged-extraction-pipeline.md) 与 [docs/decisions/0002-messages-api-adapter.md](docs/decisions/0002-messages-api-adapter.md)。
