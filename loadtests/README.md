# 负载测试口径

`locustfile.py` 验证 FastAPI HTTP 入口在 300 个并发用户下的可达性、错误率与响应时间，不包含本地 9B 模型、SiliconFlow、A2A、MCP 或医院接口的容量，因此不能把结果表述为“完整 Agent 链路支持 300 并发”。

```powershell
python -m locust -f loadtests/locustfile.py --host http://127.0.0.1:8000 `
  --headless -u 300 -r 100 -t 30s --csv artifacts/locust_api_300
```

完整 Agent 链路压测必须使用隔离的测试患者、真实测试环境医院接口和单独的模型配额，并对 SSE 首 token、完整响应、工具成功率分别统计，禁止向生产医疗系统直接施压。
