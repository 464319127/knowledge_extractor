# 0002：使用 Anthropic Messages 兼容 API

日期：2026-07-15
状态：已取代

HTTP transport 部分已由 [0003：使用 curl 调用内部 Messages API](0003-curl-transport.md) 取代。Messages API、凭据来源和供应商隔离决策继续有效。

取代 [0001：采用分阶段、可审核的知识提取管线](0001-staged-extraction-pipeline.md) 中关于 Gemini SDK 的供应商选择；其余管线与安全决策继续有效。

## 背景

运行环境提供一个 Anthropic Messages 兼容端点 `https://oneapi-comate.baidu-int.com/v1/messages`，通过 Bearer token 鉴权，并支持项目需要使用的模型。项目根目录中的 `person_key` 提供本地测试凭据。

提取阶段只需要单次结构化文本生成，不需要 Agent 工具调用。把密钥放入命令行参数会进入 shell 历史或进程列表；把供应商响应直接写入日志也可能泄漏服务内部信息。

## 决策

第一版实现独立 `MessagesAPIExtractor`：

- 最初计划使用 Python 标准库 `urllib.request` 调用 Messages 兼容 HTTPS 端点，不增加 HTTP 客户端依赖；
- 请求体包含 `model`、`max_tokens` 和单条用户消息，提示模型只返回符合 Pydantic schema 的 JSON；
- 同时兼容 Anthropic `content[].text` 和 OpenAI `choices[].message.content` 两种常见响应包装；
- 密钥只从 `KNOWLEDGE_EXTRACTOR_API_KEY` 环境变量或显式 `--api-key-file` 读取，不接受明文命令行密钥；
- `person_key` 和 `*.key` 必须被 Git 忽略，本地密钥文件权限应为 `0600`；
- HTTP 错误只报告状态和简短原因，不记录 Authorization header、完整请求或完整响应。

默认端点为上述内部 HTTPS 地址，默认模型为 `gpt-5.5`，两者都可以通过 CLI 参数覆盖。

## 理由

直接调用兼容 API 与现有基础设施一致，减少凭据和网络配置工作。标准库足以处理当前单次 JSON 请求，避免为简单调用增加依赖。供应商逻辑继续受 `Extractor` 协议隔离，未来增加其他端点时不影响规范化、审核或 OKF 渲染。

## 影响

- 运行 `extract` 和 `run` 需要能够访问内部端点，并提供环境变量或密钥文件。
- 服务端不提供客户端强制结构化输出能力时，模型偶尔可能返回 Markdown 代码围栏；适配器会剥离单层 JSON 围栏后再用 Pydantic 严格校验。
- 模型名称和端点属于部署配置，不应散落在解析和渲染模块中。
- 密钥文件不得提交；CI 应使用密钥管理系统注入环境变量。
