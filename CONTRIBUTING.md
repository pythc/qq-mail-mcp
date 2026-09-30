# 贡献

欢迎通过 issue 和 PR 改进 QQ 邮箱兼容性、测试和文档。先查看现有 issue，保持改动聚焦。

```bash
uv sync --extra dev --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

请使用构造的测试邮件和模拟服务器，不提交个人邮箱、授权码、实际邮件或 token。
发送能力必须保持可配置的只读、网页批准和直接发送模式、固定草稿、客户端隔离及防重复提交行为。
不要把默认关闭发送或默认需要网页批准的行为悄悄改成直接发送。
PR 请说明变化、验证方式和是否需要重新授权或迁移数据库。

漏洞请通过 [GitHub 私密漏洞报告](https://github.com/pythc/qq-mail-mcp/security/advisories/new) 提交。
普通改进请在公开 issue / PR 中讨论；不要提交真实授权码、邮件、数据库或运行时配置。
