# 本地监控验证：2026-09-30

修复前，容器抓取 `host.docker.internal:8000`，与宿主 API 的回环监听不匹配；专科进程的 MCP 调用指标也未被抓取。

修复后，`scripts/run_all.py --observability` 启动指标专用入口 9100，转发固定的 API 和三个 A2A 指标路径。业务接口仍监听回环。Prometheus 配置按四个 job 抓取；MCP 服务进程本身尚无独立指标入口。

使用真实 Prometheus v3.5.0 Docker 容器，以及当前 API、三个 A2A 应用和指标转发进程验证：

| job | health | lastError |
|---|---|---|
| medagent-api | up | 空 |
| medagent-symptom | up | 空 |
| medagent-drug | up | 空 |
| medagent-guide | up | 空 |

结果来自 `/api/v1/targets`。验证期间仅访问指标接口，API 使用 `--lifespan off` 跳过数据库初始化和模型预热；不是完整启动验收或真实医院业务联调，也未验证 Grafana 展示。结束后停止了本次启动的服务与容器。

上述首次监控验证时的自动化测试快照为 115 项通过，包含转发目标限制、非指标路由拒绝、上游失败返回 503、A2A 指标可访问且业务身份认证仍生效。后续新增测试数见 README；此处保留该轮历史记录。保留两个已知依赖警告。

旧 `scripts/create_doctor.py` 是 `create_staff.py` 的兼容入口，无须删除；README 已说明用途。生产清单日期按最近局部更新标注，不宣称本轮重新核查全部生产条件。
