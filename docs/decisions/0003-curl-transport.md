# 0003：使用 curl 调用内部 Messages API

日期：2026-07-15
状态：已接受

取代 [0002：使用 Anthropic Messages 兼容 API](0002-messages-api-adapter.md) 中的 `urllib.request` transport；其余 API 和凭据决策继续有效。

## 背景

真实端到端测试表明，Python 3.13 的默认 TLS trust store 无法验证内部 OneAPI 端点的证书链，调用失败并返回 `CERTIFICATE_VERIFY_FAILED`。系统 `curl` 使用 macOS 已配置的证书信任，与该端点的推荐调用方式一致。

关闭 TLS 校验会削弱凭据和日志内容的传输安全。直接把 Authorization header 放进 `curl` 命令参数则可能被进程列表、诊断工具或日志捕获。

## 决策

模型适配器通过 `subprocess.run` 调用系统 `curl`：

- 保持 TLS 验证，不提供 `--insecure` 选项；
- endpoint、HTTP method 和 Authorization header 通过 curl config 从 stdin 传入，Bearer token 不出现在进程参数中；
- JSON 请求正文写入权限为 `0600` 的临时文件，并在成功、失败或超时后删除；
- 使用 `--fail-with-body`、`--silent` 和 `--show-error`，失败时只向调用者返回截断后的 curl 错误，不回显响应正文；
- 不使用 shell，因此密钥和路径不会经过 shell 展开；
- 启动时检查 `curl` 是否可用。

## 理由

该方案复用系统已经配置的内部证书信任，不需要复制企业 CA、关闭 TLS 或引入新的 Python HTTP 依赖。通过 stdin config 传递 header 可以避免最常见的命令参数泄漏风险。

## 影响

- 运行环境必须提供支持 `--fail-with-body` 的 `curl`。
- 请求期间会短暂存在一个只包含已清洗输入的临时 JSON 文件；文件权限为 `0600`，并由 `finally` 清理。
- 如果部署环境不提供 `curl`，需要新增 transport，而不是回退到禁用证书验证。
- 测试必须验证 token 不出现在 subprocess 参数中，并验证临时文件在失败路径也被清理。
