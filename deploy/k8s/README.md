# Kubernetes 灰度发布

清单提供 stable/canary 两套 API Deployment、10% NGINX Ingress canary 权重、滚动更新、健康探针和 HPA。部署前必须替换镜像、域名和 Secret，占位值不得进入真实环境。

`support-services.yaml` 包含 Redis、MCP 和三个 A2A（Agent-to-Agent，智能体间通信）Agent；MySQL、本地 BERT/BGE（双向编码器表示/通用文本向量）推理资源与医院接口按院内已有基础设施接入，地址通过 Secret/ConfigMap 配置。DeepSeek 主模型访问配置的 SiliconFlow 兼容地址；生产应另行配置受控出网代理，当前清单不包含该代理。代码在出站前递归处理直接身份标识，但保留推理所需的症状等医疗内容；规则脱敏不等于完整匿名化，上云前仍需医院的数据授权及安全评审。

API Deployment（部署）只读挂载名为 `medagent-intent-models` 的 PVC（PersistentVolumeClaim，持久卷声明），其中必须预置 `/medical_intent_bert_tob_v3` 与 `/bge-m3` 两个模型目录。模型权重不进入镜像和 Git；该 PVC 需由医院的模型仓库同步流程或运维平台预先创建，并应支持 stable/canary（稳定版/金丝雀版）Pod（容器实例）并发读取。默认资源额度按 CPU 推理估算，目标集群使用 GPU 时还需按其设备插件增加 GPU 资源声明并重新压测。

这组清单表示“具备部署配置”，不是“已经在某个生产 K8s（Kubernetes，容器编排平台）集群完成灰度验证”。上线验证还需目标集群的 Ingress-NGINX（入口控制器）、Metrics Server（资源指标服务）、镜像仓库、MySQL、模型持久卷和回滚演练记录。
