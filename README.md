# MedAgent AI 医院内部临床辅助多 Agent 平台

面向医院医生、药师、医务管理人员和信息科的院内多服务项目。工作台与对外 API 要求员工登录和授权患者范围；内部 A2A/MCP 服务当前仍依赖可信网络隔离，尚未独立验证调用方身份。应用服务可部署在院内；主模型通过配置的 SiliconFlow 云端接口调用，因此不能将整条推理链称为完全私有化部署。运行链路为：

```text
Streamlit 工作台 → FastAPI/JWT/SSE → 急症规则 → 医疗范围守卫 → 四层意图识别 → 关键槽位澄清
                                                   ↓
                          Planning Agent（复杂任务）
                                  ↓ A2A
             SymptomAgent / DrugAgent / GuideAgent
                                  ↓ MCP
          HIS / 药品库 / 指南库 / LIS / EMR / MySQL
```

系统没有医学 Mock 数据或规则式正常临床辅助回答。模型、A2A、MCP、医院接口或数据库未配置时会明确失败，不会生成伪造医学结果。紧急症状拦截属于安全规则，会在调用模型前提示拨打 120。

项目讲解、面试追问、真实踩坑和生产化边界见 [MedAgent AI 多 Agent 项目话术](docs/MedAgent_AI_多Agent项目话术.md)；旧模型清理、指标复核和待补强项见 [2026-09-24 核查记录](docs/audit-2026-09-24.md)。

常用术语：Agent（智能体）、A2A（智能体间通信）、MCP（模型上下文协议）、JWT（身份令牌）、SSE（服务器发送事件）、BERT（双向编码器表示模型）、BGE-M3（文本向量模型）、HMAC（基于密钥的消息认证码）、TTL（过期时间）。更多缩写及中文含义见项目话术末尾的术语表。

## 文档要求对应

- 主模型：SiliconFlow OpenAI 兼容接口，默认 `deepseek-ai/DeepSeek-V4-Flash`
- 意图识别：正则 → 本地十分类医疗 BERT → 本地 BGE-M3 意图原型相似度 → SiliconFlow DeepSeek 兜底；BERT 和向量层使用接受阈值，模型兜底结果经过意图枚举校验。分数不是经过校准的正确率，四层级联也不保证所有表达都能正确识别
- 范围与澄清：意图识别前先区分医疗、非医疗、混合和不确定请求；非医疗固定拒识，混合请求只传递经原文校验的医疗片段，任务关键槽位不足时先追问且不调用 A2A/MCP
- A2A：`python-a2a` 独立运行 SymptomAgent、DrugAgent、GuideAgent
- MCP：FastMCP 独立服务，注册外部接口工具 11 个、内部数据工具 3 个；外部转诊和内部病史/临床辅助记录保存均属于有副作用的操作
- ReAct：三个专科 Agent 使用 LangChain `create_agent` 执行模型 Function Calling → MCPAdapter → FastMCP 工具 → Observation 回填；模型调用和工具调用分别限制最多 6 次
- 复杂任务：Planning Agent 生成短链计划并按文档串行执行
- 外部工具：HMAC 鉴权、单次 HTTP 请求 10 秒超时和进程内熔断器；只读查询遇到网络异常或 HTTP 429/500/502/503/504 时最多尝试 3 次。业务拒绝不自动重试；转诊写接口不自动重试，以免超时后重复创建
- 隐私：当前问题、历史消息、嵌套工具结果发送外部主模型前统一递归脱敏
- 记忆：Redis 多实例会话/阶段 checkpoint（未配置时使用本地 TTLCache）+ MySQL 长期档案/临床辅助记录
- Agent 上下文：专科 Agent 无独立长期记忆，每次运行接收同一患者会话的只读快照；运行上下文和工具调用指纹按 Agent 任务隔离，协调器去重计划并只在最终答复后统一写回会话
- 可观测：Prometheus 指标、Grafana 数据源和隐藏医疗输入/输出的 LangSmith trace
- 发布：Dockerfile、K8s stable/canary、10% NGINX Ingress 灰度和 HPA
- 认证：院内员工工号 + PBKDF2 密码，预留医院 SSO/OIDC 对接边界
- 授权：员工—患者访问关系、JWT、patient_id 服务端校验、审计日志和急诊拦截

内部 A2A/MCP 端点目前没有独立的服务身份认证；若部署到多租户或不可信网络，必须在这些端点增加服务认证与授权，不能只依赖工作台入口的 JWT（身份令牌）。

## 配置

复制可提交的 [.env.example](.env.example) 为本地 `.env` 后填写真实配置。`.env` 已在 `.gitignore` 中排除，禁止提交。

必须配置：

- `SILICONFLOW_API_KEY`：SiliconFlow API Key
- `SILICONFLOW_BASE_URL`、`SILICONFLOW_MODEL`
- `INTENT_BERT_MODEL_PATH`：由 `scripts/train_intent_bert.py` 生成的本项目十分类模型目录
- `INTENT_VECTOR_MODEL_PATH`：本地 BGE-M3 / SentenceTransformer 模型目录
- `INTENT_BERT_THRESHOLD`、`INTENT_VECTOR_THRESHOLD`、`INTENT_VECTOR_MARGIN`
- `DATABASE_URL` 与 MySQL Docker 参数
- `DRUG_API_BASE_URL`、`GUIDELINE_API_BASE_URL`、`LIS_API_BASE_URL`、`EMR_API_BASE_URL`、`HIS_API_BASE_URL`
- `HOSPITAL_APP_KEY`、`HOSPITAL_APP_SECRET`
- 高强度随机 `SECRET_KEY`

可选生产配置包括 `REDIS_URL`、`LANGSMITH_API_KEY`、`LANGSMITH_TRACING` 与 `GRAFANA_ADMIN_PASSWORD`。LangSmith 仅记录经过处理的外层步骤摘要；LangChain 子运行已在代码中关闭远端追踪，避免上传工具参数、Observation、患者原文、患者 ID 和医疗答复。

检查配置：

```powershell
conda activate Smart_Healthcare
python scripts/check_config.py
```

## 安装与启动

先激活环境并安装依赖，再执行后面的训练、检查和启动命令：

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements.txt
```

首次使用前，基于本地 `bert-base-chinese` 训练当前项目的十类医疗意图分类头。仓库提供 3,800 条可复现的合成启动数据：训练 2,000 条、验证 400 条、测试 1,000 条、专项挑战 400 条；数据生成脚本会检查类别平衡和跨集合精确重复。合成数据只用于启动训练和工程回归，正式指标仍需独立人工复核的匿名真实测试集：

```powershell
$BERT_BASE_MODEL = "请替换为本机 bert-base-chinese 模型目录"
python scripts/train_intent_bert.py `
  --base-model $BERT_BASE_MODEL `
  --dataset evaluation\datasets\intent_train.jsonl `
  --validation-dataset evaluation\datasets\intent_validation.jsonl `
  --output models\medical_intent_bert_tob_v3
```

需要重新生成数据时运行：

```powershell
python scripts/build_intent_dataset.py
```

`python scripts/check_config.py` 只检查本地核心链路，医院接口未配置时会给出提示但不阻止启动；生产联调前使用 `python scripts/check_config.py --strict`，要求 HIS/LIS/EMR、药品和指南服务全部配置。未配置的真实工具会明确失败，不会用模拟医学数据兜底。

向量层从独立的 `evaluation/intent_prototypes.jsonl` 加载单意图样本作为版本化原型，避免与 `evaluation/intent_eval.jsonl` 回归集直接重合造成数据泄漏；使用 `INTENT_VECTOR_MODEL_PATH` 指向的本地 SentenceTransformer 模型计算余弦相似度。多意图提示、低置信度和冲突样本会进入 SiliconFlow 主模型兜底。该级联提高覆盖率，但不承诺所有真实表达都能 100% 正确分类。

阈值校准与完整回归：

```powershell
python scripts/calibrate_intent_vector.py
python scripts/evaluate_intent.py --output artifacts/intent_eval_tob_v3.json
```

意图体系已调整为十类院内任务，并重新生成 3,800 条 ToB 合成启动数据。`medical_intent_bert_tob_v3` 已从原始 `bert-base-chinese` 重新初始化分类头并训练；不符合当前十类标签集合的权重会被运行时拒绝加载。当前 `0.95` 阈值下，1,000 条合成测试集覆盖率为 98.8%、接受样本准确率为 100%；100 条否定/模糊单意图挑战集覆盖率为 24%、接受样本准确率为 100%，50 条分布外样本全部拒绝。向量层在 236 条残余挑战样本上以 `0.78` 阈值接受 10 条且均判断正确。以上只用于工程校准，不能外推为临床准确率。范围守卫会在意图分类前终止非院内工作请求。

在挑战集上复核已确定的 `0.78` 阈值（不要用该集重新调参）：

```powershell
python scripts/calibrate_intent_vector.py --dataset evaluation/datasets/intent_challenge.jsonl --thresholds 0.78
```

BERT 独立评测命令：

```powershell
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_tob_v3 --dataset evaluation\datasets\intent_test.jsonl
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_tob_v3 --dataset evaluation\datasets\intent_challenge.jsonl
```

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements.txt
docker compose up -d mysql
docker compose up -d redis
python -m alembic upgrade head
python scripts/run_all.py
```

`run_all.py` 会启动：

- FastMCP：`127.0.0.1:8002/mcp`
- SymptomAgent A2A：`127.0.0.1:8011`
- DrugAgent A2A：`127.0.0.1:8012`
- GuideAgent A2A：`127.0.0.1:8013`
- FastAPI：`127.0.0.1:8000`（接口文档 `/docs`）
- Streamlit：`127.0.0.1:8501`（院内员工工作台）

也可以分别启动 API 和界面：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

## 账号

院内员工账号由医院管理员创建。角色支持医生、药师、医务管理员和系统管理员；医生/药师可重复传入多个授权患者。医务管理员可按医院级范围查看患者，系统管理员默认不具备病历读取权限。现有数据库先运行 Alembic（数据库迁移工具）迁移；本地联调档案必须来自明确输入，脚本不会生成医学数据：

```powershell
python scripts/upsert_patient.py --patient-id patient_001 --name patient_name --age 40 --gender 未知
python scripts/create_staff.py --employee-id D10086 --username doctor_name --role doctor --patient-id patient_001
```

生产环境中员工账号应接入医院统一身份认证，患者访问授权由 HIS/EMR 动态下发；服务端会在读取档案、历史记录和执行 Agent 前再次校验授权范围。

## 验证

```powershell
python -m pytest -q
python -m compileall -q backend scripts streamlit_app.py
```

测试会确认：14 个 FastMCP 工具真实注册、未配置的医院接口明确失败、敏感标识脱敏、急诊边界优先执行、员工—患者授权、多意图保留和患者会话隔离。

意图回归评测：

```powershell
python scripts/evaluate_intent.py --output artifacts/intent_eval_tob_v3.json
```

当前 33 条工程回归集已迁移到纯 ToB 意图标签，并在当前模型与阈值下重新运行：严格匹配率、复杂任务标记准确率和 micro-F1 均为 100%，0 个运行错误（32 条由正则层完成，1 条由向量层完成）。该小型工程集只用于防回退，不代表临床泛化。300 用户 HTTP 入口压测命令和口径见 [压测说明](loadtests/README.md)。本地报告生成到已被 Git 忽略的 `artifacts/` 目录；该报告不代表完整 Agent 链路吞吐。

## GitHub 上传前检查

仓库内置不依赖第三方扫描器的预检脚本，会分别扫描工作区候选文件和 Git 暂存内容中的疑似密钥，并检查忽略规则、私钥文件、超大文件、Python 语法和 `.env.example` 配置项完整性。完整模式还会运行 pytest 与 `pip check`。模式匹配不是完整的数据泄露防护，上传前仍应人工审阅暂存差异；已泄露的密钥需要撤销重置，不能只删除文件：

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
