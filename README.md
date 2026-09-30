# MedAgent AI 医院内部临床辅助多 Agent 平台

面向医院医生、药师、医务管理人员和信息科的院内多服务项目。工作台与对外 API 要求员工登录和授权患者范围；内部 A2A/MCP 服务验证短期签名委托，并在执行前复核员工状态、患者和工具范围。应用服务可部署在院内；主模型通过配置的 SiliconFlow 云端接口调用，因此不能将整条推理链称为完全私有化部署。运行链路为：

```text
Streamlit 工作台 → FastAPI/JWT/SSE → 急症规则 → 医疗范围守卫 → 四层意图识别 → 关键槽位澄清
                                                   ↓
                          Planning Agent（复杂任务）
                                  ↓ A2A
             SymptomAgent / DrugAgent / GuideAgent
                                  ↓ MCP
          HIS / 药品库 / 指南库 / LIS / MySQL
```

系统没有医学 Mock 数据或规则式正常临床辅助回答。模型、A2A、MCP、医院接口或数据库未配置时会明确失败，不会生成伪造医学结果。紧急症状拦截属于安全规则，会在调用模型前提示医务人员启动院内急救流程。

项目流程详解、面试追问和工程细节按顺序写在 [MedAgent AI 多 Agent 项目话术](docs/MedAgent_AI_多Agent项目话术.md) 中；也可单独阅读 [从零理解项目指南](docs/MedAgent_项目理解指南.md)。最新修复与验证见 [2026-09-28 核查记录](docs/audit-2026-09-28.md)，真实上线条件见 [生产验收清单](docs/production-readiness.md)。

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
- 发布参考：Dockerfile、K8s stable/canary（稳定版/金丝雀版）、Gateway API（网关接口）90/10 权重与 HPA（水平自动扩缩容）；未执行部署
- 认证：院内员工工号 + PBKDF2 密码，预留医院 SSO/OIDC 对接边界
- 授权：员工—患者访问关系、JWT、patient_id 服务端校验、审计日志和急诊拦截

内部服务使用独立 `INTERNAL_SERVICE_SECRET`，校验令牌有效期、目标服务和委托范围。生产仍需 TLS 或 mTLS、网络策略和密钥轮换；共享服务密钥不能抵御已经取得该密钥的服务主体。

## 配置

复制可提交的 [.env.example](.env.example) 为本地 `.env` 后填写真实配置。`.env` 已在 `.gitignore` 中排除，禁止提交。

必须配置：

- `SILICONFLOW_API_KEY`：SiliconFlow API Key
- `SILICONFLOW_BASE_URL`、`SILICONFLOW_MODEL`
- `INTENT_BERT_MODEL_PATH`：由 `scripts/train_intent_bert.py` 生成的本项目十分类模型目录
- `INTENT_VECTOR_MODEL_PATH`：本地 BGE-M3 / SentenceTransformer 模型目录
- `INTENT_BERT_THRESHOLD`、`INTENT_VECTOR_THRESHOLD`、`INTENT_VECTOR_MARGIN`
- `DATABASE_URL` 与 MySQL Docker 参数
- `DRUG_API_BASE_URL`、`GUIDELINE_API_BASE_URL`、`LIS_API_BASE_URL`、`HIS_API_BASE_URL`
- `HOSPITAL_APP_KEY`、`HOSPITAL_APP_SECRET`
- 两个不同的高强度随机密钥 `SECRET_KEY` 和 `INTERNAL_SERVICE_SECRET`，生产各至少 32 字符

生产需要 `APP_ENV=production`、共享 `REDIS_URL`、MySQL、HTTPS 医院接口及经过医院批准的 `CLOUD_LLM_ALLOWED=true`。`LANGSMITH_API_KEY`、`LANGSMITH_TRACING` 与 `GRAFANA_ADMIN_PASSWORD` 按实际监控启用；默认关闭远端追踪。LangSmith 仅记录经过处理的外层步骤摘要；LangChain 子运行已在代码中关闭远端追踪，避免上传工具参数、Observation、患者原文、患者 ID 和医疗答复。

检查配置：

```powershell
conda activate Smart_Healthcare
python scripts/check_config.py
```

## 安装与启动

先激活环境并安装依赖，再执行后面的训练、检查和启动命令：

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements.txt -c constraints.txt
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

`python scripts/check_config.py` 只检查本地核心链路，医院接口未配置时会给出提示但不阻止启动；生产联调前使用 `python scripts/check_config.py --strict`，要求 HIS/LIS、药品和指南服务全部配置。未配置的真实工具会明确失败，不会用模拟医学数据兜底。

### 克隆后的模型准备与指标复核

模型权重不随 Git 分发。克隆后需准备基础 BERT、训练本项目十分类模型，并下载 BGE-M3；不能只克隆代码就复现历史模型指标。下载公开基础模型的示例（需联网；该命令会下载较大权重）：

```powershell
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='google-bert/bert-base-chinese', local_dir='models/bert-base-chinese')"
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', local_dir='models/bge-m3')"
```

将前文 `$BERT_BASE_MODEL` 设置为 `models/bert-base-chinese`，执行训练命令；在本地 `.env` 中设置 `INTENT_BERT_MODEL_PATH=models/medical_intent_bert_tob_v3`、`INTENT_VECTOR_MODEL_PATH=models/bge-m3`。公开下载例子取得当时的模型版本；若要求固定版本，需记录并传入 Hugging Face 的 `revision` 提交号。当前旧模型未记录上游提交，不能补造一个版本号。

[模型文件指纹](evaluation/model_artifacts.json) 记录当前本地推理文件的大小与 SHA-256；[历史 BERT 指标摘要](evaluation/bert_historical_summary.json) 保存普通测试、挑战集各自的分母、阈值和数据集指纹。当前模型指纹是在 2026-09-29 补记，不等于历史评测当时已经完整记录环境。原训练脚本默认 seed=42、8 轮、batch=8、学习率 2e-5、最大长度 128；不同基础权重、依赖与设备可能产生不同结果，不承诺重新训练得到完全相同分数。

```powershell
python scripts/verify_model_artifacts.py --bert models/medical_intent_bert_tob_v3 --vector models/bge-m3
python scripts/evaluate_intent_bert.py --model models/medical_intent_bert_tob_v3 --dataset evaluation/datasets/intent_test.jsonl --output artifacts/bert_test_new.json
python scripts/evaluate_intent_bert.py --model models/medical_intent_bert_tob_v3 --dataset evaluation/datasets/intent_challenge.jsonl --output artifacts/bert_challenge_new.json
```

以上路径对应前文下载布局；若使用已有模型目录，请将 `--bert`、`--vector` 分别替换为本地 `.env` 中 `INTENT_BERT_MODEL_PATH`、`INTENT_VECTOR_MODEL_PATH` 指向的实际目录，脚本不会自动读取这两个配置。指纹不匹配不代表文件一定损坏，可能是新下载或新训练的版本；应以新评测报告描述该版本，不能沿用历史覆盖率。下载方法见 [Hugging Face 官方说明](https://huggingface.co/docs/huggingface_hub/en/guides/download)。

向量层从独立的 `evaluation/intent_prototypes.jsonl` 加载单意图样本作为版本化原型，2026-09-28 替换了与回归集重合的两条原型，并以测试检查原型与回归及四份合成数据集无精确文本重合；精确去重不能证明不存在语义或模板泄漏；使用 `INTENT_VECTOR_MODEL_PATH` 指向的本地 SentenceTransformer 模型计算余弦相似度。多意图提示、低置信度和冲突样本会进入 SiliconFlow 主模型兜底。该级联提高覆盖率，但不承诺所有真实表达都能 100% 正确分类。

阈值校准与完整回归：

```powershell
python scripts/calibrate_intent_vector.py
python scripts/evaluate_intent.py --output artifacts/intent_eval_tob_v3.json
```

意图体系已调整为十类院内任务，并重新生成 3,800 条 ToB 合成启动数据。`medical_intent_bert_tob_v3` 已从原始 `bert-base-chinese` 重新初始化分类头并训练；不符合当前十类标签集合的权重会被运行时拒绝加载。[历史 BERT 摘要](evaluation/bert_historical_summary.json) 中，`0.95` 阈值下的 1,000 条合成测试覆盖率为 98.8%、接受样本准确率为 100%；100 条否定/模糊单意图挑战集覆盖率为 24%、接受样本准确率为 100%，50 条分布外样本全部拒绝。2026-09-29 使用当前本地模型重新运行向量单标签 Top-1 阈值校准：236 条残余挑战样本中，以 `0.78` 阈值接受 10 条且均判断正确，[逐条记录及输入指纹](evaluation/vector_challenge_summary.json) 可查。该校准不等于完整多意图运行时评测。以上只用于工程验证，不能外推为临床准确率。范围守卫会在意图分类前终止非院内工作请求。

在挑战集上复核已确定的 `0.78` 阈值（不要用该集重新调参）：

```powershell
python scripts/calibrate_intent_vector.py --dataset evaluation/datasets/intent_challenge.jsonl --thresholds 0.78 --output evaluation/vector_challenge_summary.json
```

BERT 独立评测命令：

```powershell
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_tob_v3 --dataset evaluation\datasets\intent_test.jsonl
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_tob_v3 --dataset evaluation\datasets\intent_challenge.jsonl
```

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements.txt -c constraints.txt
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

生产的统一身份认证和 HIS/EMR 动态授权需按医院契约另行对接，当前使用本地员工及授权表。API、A2A 和 MCP 均重新核对实时权限；医务管理员为医院级读取角色，写工具仅对已确认动作及允许的临床角色开放。

## 写操作与会话安全

界面每次请求单独勾选病史保存、辅助材料保存或转诊创建；切换患者和新建任务会清除勾选。医生/药师可保存内部材料，转诊只允许医生。确认只绑定动作范围，不是最终参数逐项审批。

`request_id` 与员工、工具组成持久化操作键，同键同参数复用完成结果，冲突或未知状态拒绝重放。异常后用 `GET /api/operations/{request_id}` 核对本人写操作；下游 HIS 没有契约保证时不声称全局恰好一次。

会话按员工、患者、会话隔离，整轮锁防止并发覆盖；数据库提交、消息成对写入、阶段保存后发送 SSE 完成事件。默认整轮 120 秒、模型单次 30 秒、输出 2,048 词元；没有工具证据或存在工具失败时不返回正常临床结论。

A2A 传输历史采用最近消息优先裁剪：每条内容最多 4 KiB，历史 JSON 的 UTF-8 总量最多 16 KiB；完整会话存储仍最多 20 条。裁剪只用于可选上下文，当前任务单独传递，专科从数据库重新读取可信患者档案。裁剪可能丢失较早上下文，不应把历史作为关键临床参数的唯一来源。

SSE 异常会记录阶段、请求标识、异常类型和栈位置，不记录原始异常消息或临床内容。健康检查的相对模型路径按仓库根目录解析。

## 验证

```powershell
python -m pytest -q
python -m compileall -q backend scripts streamlit_app.py
```

2026-09-28 本地 103 项测试通过；2026-09-30 补充临床预约背景与写参数预检回归后，113 项测试通过；同日增加监控回归后为 115 项，再补充预检及异常指标回归后为 119 项；增加取消、流式中断与转发总超时测试后为 122 项；补充 Redis 锁及委托有效期回归后共 127 项通过。pytest 默认仅收集 `tests/`；如系统 pytest 临时目录无权限，使用新的 `--basetemp`。测试会确认：14 个 FastMCP 工具真实注册、未配置的医院接口明确失败、敏感标识脱敏、急诊边界优先执行、员工—患者授权、多意图保留和患者会话隔离。

意图回归评测：

```powershell
python scripts/evaluate_intent.py --output artifacts/intent_eval_tob_v3.json
```

当前 33 条工程回归集已迁移到纯 ToB 意图标签，并在当前模型与阈值下重新运行：严格匹配率、复杂任务标记准确率和 micro-F1 均为 100%，0 个运行错误（32 条由正则层完成，1 条由向量层完成）。该小型工程集只用于防回退，不代表临床泛化。300 用户 HTTP 入口压测命令和口径见 [压测说明](loadtests/README.md)。本地报告生成到已被 Git 忽略的 `artifacts/` 目录；该报告不代表完整 Agent 链路吞吐。

## GitHub 上传前检查

仓库内置不依赖第三方扫描器的预检脚本，会分别扫描工作区候选文件和 Git 暂存内容中的疑似密钥，并检查忽略规则、私钥文件、超大文件、Python 语法和 `.env.example` 配置项完整性。预检还会拒绝跟踪 `output/`、`.mimosa/` 和本地简历生成脚本。完整模式还会运行 pytest 与 `pip check`。模式匹配不是完整的数据泄露防护，上传前仍应人工审阅暂存差异；已泄露的密钥需要撤销重置，不能只删除文件：

```powershell
conda activate Smart_Healthcare
python scripts/preflight_git.py
```

只执行快速静态检查：

```powershell
python scripts/preflight_git.py --quick
```

在项目环境执行一次 `python scripts/install_git_hooks.py` 可启用 hooks 并将当前解释器保存到本地 Git 配置；切换或迁移环境后需重新安装：提交前运行快速检查，推送前运行完整检查。真实 `.env`、数据库、运行日志、模型权重、压测报告、简历产物和 `.mimosa/` 运行状态应留在本地；`.gitignore` 与预检约束当前 Git 索引。预检会提示这些本地文件是否曾进入 Git 历史，但不审查历史内容或远端可见性。已经进入历史或远端的文件不会因后来取消跟踪而消失。

启动监控时，用以下命令替代普通的 `python scripts/run_all.py`（已有进程需先停止），在另一终端启动容器：

```powershell
python scripts/run_all.py --observability
# 另一终端：
docker compose --profile observability up -d prometheus grafana
```

`--observability` 启动只读指标转发入口 `0.0.0.0:9100`，只允许固定的 API 和三个 A2A 进程指标路径；业务接口仍监听回环地址。9100 应仅允许可信监控网络访问。Prometheus 通过 `host.docker.internal:9100` 抓取四个 job，在 `http://127.0.0.1:9090/targets` 检查是否全部 UP；若连接失败，检查 Docker 到宿主机的路由和防火墙。

API 进程提供 HTTP、路由与 A2A 调用指标，三个专科进程分别提供其发起的 MCP 工具调用指标。当前未单独暴露 MCP 服务进程指标，也不宣称所有下游均有独立监控。各进程的同名指标需按 job 区分。旧 `scripts/create_doctor.py` 仅为兼容命令入口；新部署统一使用 `scripts/create_staff.py --role doctor`。

HTTP 延迟指标 `medagent_http_request_duration_seconds` 统计响应创建至响应头可用或普通异常抛出的时间，不包含 SSE 正文传输时长，也不等于首个模型 token 延迟或整轮耗时。响应创建阶段未处理的普通异常计入 500；响应头之前的任务取消不计入该 HTTP 状态指标，响应头之后的流式取消保留已记录的状态，流式错误需结合 SSE 事件与日志判断。指标入口的 `0.0.0.0` 绑定也可能允许局域网访问，访问范围由宿主机防火墙控制，并非代码自动限制为 Docker 网络。

指标转发总截止时间为 5 秒，Prometheus `scrape_timeout` 为 10 秒；上游耗时超过 5 秒会由转发入口返回 503，而非等待抓取端超时。Git 预检要求 `backend/`、`scripts/`、`tests/`、`migrations/` 内的新 Python 文件入索引；其他候选 Python 文件仍扫描语法、敏感内容与静态依赖，但不因根目录存在临时脚本就要求其入库。动态拼接依赖仍需独立检查。

Redis 锁已完成真实 Redis 容器验证；子 MCP 委托不得延长父 A2A 委托有效期。整轮预算尚未作为统一截止时间传播到专科，API 超时也不保证远端写入停止，具体见 [Redis 与超时核查](docs/redis-timeout-verification.md)。

## 重要边界

本项目不会自行提供处方剂量，也不会把模型输出当作确诊结果。医院 API 字段结构需要按甲方 OpenAPI 文档对 `backend/mcp_tools.py` 中的路径与字段映射做最后适配；在这些真实接口尚未提供前，对应功能会返回“接口未配置”，不会使用假数据替代。

EMR 配置曾是未调用的预留项，现已删除。14 个工具中没有独立 EMR 连接器；报告工具对接 LIS，正式电子病历写入、影像识别与参数级审批需按医院需求独立验收。

## 兼容性与验证边界

当前 A2A 使用已弃用的 Starlette WSGI 桥；constraints 固定 Starlette 1.6.0、Flask 3.1.3，迁移适配器前须复测真实 HTTP 链路。Docker 冷启动探针宽限为 600 秒。固定窗口登录限流不保证任意滚动 300 秒均只有 10 次。pending/unknown 写操作需人工核对 HIS，不自动重试。

评测记录：2026-09-28 原型替换后已重新评测；最新本地明细为 `artifacts/intent_eval_tob_v3.json`，可入库摘要为 [33 条回归记录](evaluation/intent_regression_summary.json)。摘要包含生成时间、数据集与原型 SHA-256、逐条结果；未包含本机模型路径。
