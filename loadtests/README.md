# 负载测试口径

`locustfile.py` 仅请求 FastAPI 的 `/api/health`，验证 300 个并发用户下该 HTTP 入口的可达性、错误率与响应时间。它不调用本地 BERT/BGE-M3、SiliconFlow 主模型、A2A、MCP 或医院接口，因此不能把结果表述为“完整 Agent 链路支持 300 并发”。

现有 `artifacts/locust_api_300_stats_history.csv` 的记录时间跨度为 20 秒，汇总为 8,107 次请求、0 失败、约 401 RPS（每秒请求数）、P95（第 95 百分位响应时间）790 ms（毫秒）。下方命令使用相同的 `-t 20s` 时长；重新运行所得数值仍取决于当时环境，不能保证与历史报告相同。

```powershell
python -m locust -f loadtests/locustfile.py --host http://127.0.0.1:8000 `
  --headless -u 300 -r 100 -t 20s --csv artifacts/locust_api_300
```

完整 Agent（智能体）链路压测必须使用隔离的测试患者、测试环境医院接口和单独的模型配额，并分别统计 SSE（服务器发送事件）首段文本时间、完整响应时间和工具成功率，不应向生产医疗系统直接施压。
