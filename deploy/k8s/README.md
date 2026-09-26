# Kubernetes 参考清单与验收条件

本目录提供配置参考，本次未部署。生产放行要求见 [生产验收清单](../../docs/production-readiness.md)。清单提供 stable/canary（稳定版/金丝雀版）两套 API、Gateway API（网关接口）90/10 权重、HPA（水平自动扩缩容）、PDB（可用实例保护）及滚动更新。

内部 A2A/MCP 验证独立签名委托，执行前重新校验员工和患者授权。`network-policies.yaml` 限制四类服务入站，API 到 MCP 的健康检查也已放行；需网络插件支持。API 与专科进程使用非 root 用户、只读根文件系统和临时卷。生产仍需内部 TLS/mTLS（传输加密/双向认证）和受控出网；签名本身不加密医疗数据。

`support-services.yaml` 包含 Redis、MCP 和三个 A2A（Agent-to-Agent，智能体间通信）Agent；MySQL、本地 BERT/BGE（双向编码器表示/通用文本向量）推理资源与医院接口按院内已有基础设施接入，地址通过 Secret/ConfigMap 配置。DeepSeek 主模型访问配置的 SiliconFlow 兼容地址；生产应另行配置受控出网代理，当前清单不包含该代理。代码在出站前递归处理直接身份标识，但保留推理所需的症状等医疗内容；规则脱敏不等于完整匿名化，上云前仍需医院的数据授权及安全评审。

API Deployment（部署）只读挂载名为 `medagent-intent-models` 的 PVC（PersistentVolumeClaim，持久卷声明），其中必须预置 `/medical_intent_bert_tob_v3` 与 `/bge-m3` 两个模型目录。模型权重不进入镜像和 Git；该 PVC 需由医院的模型仓库同步流程或运维平台预先创建，并应支持 stable/canary（稳定版/金丝雀版）Pod（容器实例）并发读取。默认资源额度按 CPU 推理估算，目标集群使用 GPU 时还需按其设备插件增加 GPU 资源声明并重新压测。

旧 Ingress-NGINX 控制器于 2026 年 3 月退役，因此改用 Gateway API 参考配置；需要目标控制器及资源定义支持，并验证 SSE 缓冲和超时。[Kubernetes 官方通知](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)、[权重路由说明](https://gateway-api.sigs.k8s.io/guides/user-guides/traffic-splitting/)。

## 目标环境准备

1. 替换镜像、域名、`gatewayClassName` 和 TLS 证书 `medagent-tls`；生产镜像固定摘要。
2. 密钥管理系统提供真实 `medagent-secrets`，员工 JWT 与内部委托密钥不同且各至少 32 字符。禁止把实际密钥提交清单。
3. 配置 MySQL 和真实医院 HTTPS 接口；医院批准数据上云后才开启 `CLOUD_LLM_ALLOWED`。默认 false 会阻止生产 API 启动。
4. 备份数据库，使用同版本镜像执行 `alembic upgrade head`；版本必须是 `20260926_02`，生产启动不自动建表。
5. 模型 PVC 需允许 UID 10001 读取、多副本挂载。默认 CPU 配额需按实测调整；GPU 推理需设备插件和资源声明。
6. 网关及工作台所在受控命名空间设置 `medagent-gateway-access=true`，监控命名空间设置 `medagent-metrics-access=true`；这些标签授权整个命名空间。
7. Redis 示例为单实例持久卷，不是高可用存储。生产需经验证的高可用、ACL（访问控制）、TLS 与恢复流程。
8. 提供 Metrics Server（资源指标服务）、告警、密钥轮换、备份恢复和灰度回滚记录。网络策略只约束入站，出站白名单须另配。

API 使用 `/api/health/live`、`/api/health/ready`，MCP/A2A 使用 `/health/live`。就绪检查覆盖数据库、共享状态及内部服务存活，不能代替真实医院接口和模型业务探测。
