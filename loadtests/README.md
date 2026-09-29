# 负载测试口径

`locustfile.py` 仅请求 FastAPI 的 `/api/health`，验证 300 个并发用户下该 HTTP 入口的可达性、错误率与响应时间。它不调用本地 BERT/BGE-M3、SiliconFlow 主模型、A2A、MCP 或医院接口，因此不能把结果表述为“完整 Agent 链路支持 300 并发”。

历史原始证据已保存为 [汇总 CSV](results/2026-09-20_health_stats.csv) 和 [时间序列 CSV](results/2026-09-20_health_stats_history.csv)，克隆仓库即可查阅。这两份仅含健康接口汇总，不含患者、账号或密钥信息。时间序列记录跨度为 20 秒，汇总为 8,107 次请求、0 失败、约 401 RPS（每秒请求数）、P95（第 95 百分位响应时间）790 ms（毫秒）。这是 2026-09-20 的历史测试，不是本轮重测。下方命令使用相同的 `-t 20s` 时长；重新运行所得数值仍取决于当时环境，不能保证与历史报告相同。

```powershell
python -m locust -f loadtests/locustfile.py --host http://127.0.0.1:8000 `
  --headless -u 300 -r 100 -t 20s --csv artifacts/locust_api_300
```

完整 Agent（智能体）链路压测必须使用隔离的测试患者、测试环境医院接口和单独的模型配额，并分别统计 SSE（服务器发送事件）首段文本时间、完整响应时间和工具成功率，不应向生产医疗系统直接施压。
