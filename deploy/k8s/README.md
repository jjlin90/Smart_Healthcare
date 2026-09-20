# Kubernetes 灰度发布

清单提供 stable/canary 两套 API Deployment、10% NGINX Ingress canary 权重、滚动更新、健康探针和 HPA。部署前必须替换镜像、域名和 Secret，占位值不得进入真实环境。

`support-services.yaml` 包含 Redis、MCP 和三个 A2A Agent；MySQL 与 LM Studio/GPU 推理服务按医院已有基础设施接入，地址通过 Secret/ConfigMap 配置。

这组清单表示“具备部署配置”，不是“已经在某个生产 K8s 集群完成灰度验证”。上线验证还需目标集群的 Ingress-NGINX、Metrics Server、镜像仓库、MySQL、持久卷和回滚演练记录。
