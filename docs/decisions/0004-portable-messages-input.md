# 0004：兼容便携式 conversation messages 输入

日期：2026-07-15
状态：已接受

## 背景

`export-conversation` 生成的 `conversation.json` 只有顶层 `messages` 数组，
不包含 API JSONL 中的 `session_id`、`request_id` 和 `stop_reason`。用户需要直接
使用该便携副本进入现有知识提取管线，而不是手工生成中间 JSONL。

## 决策

让 `normalize` 同时接受现有累积式 JSONL 和 `{"messages": [...]}` 文档：

- 以每个 user 消息开始，到下一个 user 消息之前，保留最后一条有文本的 assistant 消息；
- 通过 `conversation.id` 或 `conversation_id` 作为会话 ID；缺失时使用源文件 SHA-256 前缀；
- 通过 assistant 消息位置生成稳定的 `message-NNNN` 请求 ID，并将消息位置写入 `source_line`；
- 复用现有文本脱敏和 `NormalizedCorpus` 校验，不改变 JSONL 的 `end_turn` 规则。

## 理由

便携导出可能把一次助手回合拆成多条可见进度消息。保留每个用户问题前的最后一条助手文本，可以去掉大部分过程性消息，同时不需要猜测导出器内部的回合 ID。确定性 ID 使候选来源和后续审核可以重复生成。

## 影响

- `normalize` 的输入扩展为两种格式，旧 JSONL 行为保持兼容。
- 没有 API 回合元数据的便携消息无法判断 `end_turn`，因此以 user/assistant 分组作为完整问答边界；导出中未完成的最后问题会被警告或跳过。
- 便携消息中的助手文本仍只是候选知识来源，必须经过现有 Pydantic 校验和人工审核门禁。
