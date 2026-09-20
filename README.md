# MedAgent AI 智能医疗问诊 Agent

按《智能医疗 Agent》文档实现的真实多服务项目。运行链路为：

```text
Streamlit 工作台 → FastAPI/JWT/SSE → 本地 LM Studio 意图分类
                                  ↓
                          Planning Agent（复杂任务）
                                  ↓ A2A
             SymptomAgent / DrugAgent / GuideAgent
                                  ↓ MCP
          HIS / 药品库 / 指南库 / LIS / EMR / MySQL
```

系统没有医学 Mock 数据或规则式正常问诊回答。模型、A2A、MCP、医院接口或数据库未配置时会明确失败，不会生成伪造医学结果。紧急症状拦截属于安全规则，会在调用模型前提示拨打 120。

项目讲解、面试追问、真实踩坑和生产化边界见 [MedAgent AI 多 Agent 项目话术](docs/MedAgent_AI_多Agent项目话术.md)。

## 文档要求对应

- 主模型：SiliconFlow OpenAI 兼容接口，默认 `deepseek-ai/DeepSeek-V4-Flash`
- 意图识别：本机 LM Studio 的 `qwen/qwen3.5-9b` OpenAI 兼容接口，覆盖 10 类医疗意图
- A2A：`python-a2a` 独立运行 SymptomAgent、DrugAgent、GuideAgent
- MCP：FastMCP 独立服务，注册外部工具 10 个、内部工具 4 个
- ReAct：模型 Function Calling → MCP 工具 → 工具结果回填，最多 6 轮
- 复杂任务：Planning Agent 生成短链计划并按文档串行执行
- 外部工具：HMAC 鉴权、10 秒超时、失败指数退避、最多重试 3 次并带熔断器
- 隐私：当前问题、历史消息、嵌套工具结果发送外部主模型前统一递归脱敏
- 记忆：Redis 多实例会话/阶段 checkpoint（未配置时使用本地 TTLCache）+ MySQL 长期档案/问诊记录
- 可观测：Prometheus 指标、Grafana 数据源和隐藏医疗输入/输出的 LangSmith trace
- 发布：Dockerfile、K8s stable/canary、10% NGINX Ingress 灰度和 HPA
- 认证：患者手机号+真实短信验证码；医生工号+PBKDF2 密码
- 合规：JWT、patient_id 隔离、审计日志、安全免责声明和急诊拦截

## 配置

复制可提交的 [.env.example](.env.example) 为本地 `.env` 后填写真实配置。`.env` 已在 `.gitignore` 中排除，禁止提交。

必须配置：

- `SILICONFLOW_API_KEY`：SiliconFlow API Key
- `SILICONFLOW_BASE_URL`、`SILICONFLOW_MODEL`
- `LOCAL_INTENT_BASE_URL`、`LOCAL_INTENT_MODEL`
- `DATABASE_URL` 与 MySQL Docker 参数
- `DRUG_API_BASE_URL`、`GUIDELINE_API_BASE_URL`、`LIS_API_BASE_URL`、`EMR_API_BASE_URL`、`HIS_API_BASE_URL`
- `HOSPITAL_APP_KEY`、`HOSPITAL_APP_SECRET`
- `SMS_VERIFY_API_URL`、`SMS_APP_KEY`、`SMS_APP_SECRET`
- 高强度随机 `SECRET_KEY`

可选生产配置包括 `REDIS_URL`、`LANGSMITH_API_KEY`、`LANGSMITH_TRACING` 与 `GRAFANA_ADMIN_PASSWORD`。LangSmith trace 只记录步骤、意图、Agent 和耗时，患者原文、患者 ID 及医疗答复会在上传前隐藏。

检查配置：

```powershell
conda activate Smart_Healthcare
python scripts/check_config.py
```

## 安装与启动

先在 LM Studio 中加载 `qwen/qwen3.5-9b`，进入 Developer → Local Server，确认服务地址为 `http://127.0.0.1:1234` 且状态为 Running。项目通过 `/v1/chat/completions` 调用该本地模型，并关闭推理思考以降低意图分类延迟。

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements.txt
docker compose up -d mysql
docker compose up -d redis
python scripts/run_all.py
```

`run_all.py` 会启动：

- FastMCP：`127.0.0.1:8002/mcp`
- SymptomAgent A2A：`127.0.0.1:8011`
- DrugAgent A2A：`127.0.0.1:8012`
- GuideAgent A2A：`127.0.0.1:8013`
- FastAPI：`127.0.0.1:8000`（接口文档 `/docs`）
- Streamlit：`127.0.0.1:8501`（用户界面）

也可以分别启动 API 和界面：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

## 账号

患者首次用手机号和短信验证码登录时自动创建隔离档案。短信验证码必须由 `.env` 配置的真实验证码服务验证。

医生账号由医院管理员创建：

```powershell
python scripts/create_doctor.py --employee-id D10086 --username doctor_name --patient-id patient_xxx
```

生产环境中医生对患者的访问授权应由 HIS 动态下发，不能长期静态绑定。

## 验证

```powershell
python -m pytest -q
python -m compileall -q backend scripts streamlit_app.py
```

测试会确认：14 个 FastMCP 工具真实注册、未配置的医院接口明确失败、敏感标识脱敏、急诊边界优先执行、患者会话严格隔离。

意图回归评测：

```powershell
python scripts/evaluate_intent.py --output artifacts/intent_eval_report.json
```

当前 33 条工程回归集实测严格完全匹配率 93.94%，它不是临床标注基准。300 用户 HTTP 入口压测命令和口径见 [压测说明](loadtests/README.md)。本地报告生成到已被 Git 忽略的 `artifacts/` 目录；该报告不代表完整 Agent 链路吞吐。

## GitHub 上传前检查

仓库内置不依赖第三方扫描器的预检脚本，会检查 Git 忽略规则、疑似密钥、私钥文件、超大文件、Python 语法和 `.env.example` 配置项完整性。完整模式还会运行 pytest 与 `pip check`：

```powershell
conda activate Smart_Healthcare
python scripts/preflight_git.py
```

只执行快速静态检查：

```powershell
python scripts/preflight_git.py --quick
```

执行一次 `python scripts/install_git_hooks.py` 可启用仓库内的 hooks：提交前运行快速检查，推送前运行完整检查。真实 `.env`、数据库、运行日志、模型权重、压测报告和简历产物均不会进入 Git。

启动监控服务：

```powershell
docker compose --profile observability up -d prometheus grafana
```

## 重要边界

本项目不会自行提供处方剂量，也不会把模型输出当作确诊结果。医院 API 字段结构需要按甲方 OpenAPI 文档对 `backend/mcp_tools.py` 中的路径与字段映射做最后适配；在这些真实接口尚未提供前，对应功能会返回“接口未配置”，不会使用假数据替代。
