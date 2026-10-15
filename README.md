# MedAgent(医疗智能体项目) AI(人工智能) 医院内部临床辅助多 Agent(智能体) 平台

面向医院医生、药师、医务管理人员和信息科的院内多服务项目。工作台与对外 API(应用程序接口) 要求员工登录和授权患者范围；内部 A2A(智能体间通信协议)/MCP(模型上下文协议) 服务验证短期签名委托，并在执行前复核员工状态、患者和工具范围。应用服务可部署在院内；主模型通过配置的 SiliconFlow(硅基流动模型服务) 云端接口调用，因此不能将整条推理链称为完全私有化部署。运行链路为：

```text
Streamlit 工作台 → FastAPI/JWT/SSE → 独立安全入口（规则＋按需语义复核）→ 医疗范围守卫 → 三路融合意图识别 → 关键槽位澄清
                                                   ↓
                          协调器调用 Planner.plan（复杂任务）
                                  ↓ A2A
             SymptomAgent / DrugAgent / GuideAgent
                                  ↓ MCP
          HIS / 药品库 / 指南库 / LIS / MySQL
```

**示例术语与字段译注：** `A2A`(智能体间通信协议)；`DrugAgent`(药品智能体)；`FastAPI`(Python接口服务框架)；`GuideAgent`(指南智能体)；`HIS`(医院信息系统)；`JWT`(身份认证令牌)；`LIS`(检验信息系统)；`MCP`(模型上下文协议)；`MySQL`(关系型数据库)；`Planner`(规划器)；`SSE`(服务器推送事件)；`Streamlit`(Python交互页面框架)；`SymptomAgent`(症状智能体)；`plan`(计划)。

这里是**同一配置的云端大语言模型支撑三个专科 Agent(智能体)**。SymptomAgent(症状智能体)、DrugAgent(药品智能体)、GuideAgent(指南智能体) 分别运行在三个 A2A(智能体间通信协议) 服务中；每个专科任务创建自己的工具调用循环，使用对应的任务、专科名称、工具白名单和本轮状态。三个专科都读取 `SILICONFLOW_MODEL`(硅基流动模型配置)，默认调用同一个模型标识，分别发起模型请求，没有分别训练三个专科大模型。`MedicalCoordinator`(医疗主协调器) 负责流程调度，`Planner.plan`(规划器；计划) 和汇总方法在协调器进程内调用同一配置的模型，没有另行部署规划或汇总 A2A(智能体间通信协议) 服务。本地 BERT(双向编码语言模型) 和 BGE-M3(多语言多粒度多功能嵌入模型) 则用于意图路由。

系统没有医学 Mock(模拟对象) 数据或规则式正常临床辅助回答。模型、A2A(智能体间通信协议)、MCP(模型上下文协议)、医院接口或数据库未配置时会明确失败，不会生成伪造医学结果。明确急症关键词命中时立即提示医务人员启动院内急救流程；疑似风险表达先独立做 LLM(大语言模型) 风险复核，不确定或失败则暂停普通查询。安全结果与业务意图分开，详见[独立安全入口](docs/intent-fusion.md#独立安全入口职责字段与限制)。

项目流程详解、面试追问和工程细节见 [MedAgent(医疗智能体项目) AI(人工智能) 面试讲稿与完整题库](docs/MedAgent_AI_多Agent项目话术.md)，速读版见 [项目理解指南](docs/MedAgent_项目理解指南.md)。当前工程措施及真实上线条件见 [生产验收清单](docs/production-readiness.md)；后续专项验证见 [2026-09-30 Redis(内存键值数据库) 锁与超时核查](docs/redis-timeout-verification.md)、[2026-09-30 本地监控验证](docs/monitoring-verification.md)。[2026-09-28 核查记录](docs/audit-2026-09-28.md)保留该轮历史修复与验证。

常用术语：Agent（智能体）、A2A（智能体间通信）、MCP（模型上下文协议）、JWT（JSON Web Token，JSON 网络令牌；本项目用于员工身份认证或受限委托）、SSE（服务器发送事件）、BERT（双向编码器表示模型）、BGE-M3（文本向量模型）、HMAC（基于密钥的消息认证码）、TTL（生存时间，表示缓存等数据的有效时长）。更多缩写及中文含义见项目话术末尾的术语表。

## 文档要求对应

- 主模型：SiliconFlow(硅基流动模型服务) OpenAI(人工智能服务提供商) 兼容接口，仓库默认配置值为 `deepseek-ai/DeepSeek-V4-Flash`(人工智能；深度求索模型)；实际模型标识和目标账号可用性需联调确认
- 意图识别：Regex(正则表达式) + 本地十标签 BERT(双向编码语言模型)/Sigmoid(逐标签概率) + BGE-M3(多语言多粒度多功能嵌入模型) 三路识别，统一融合与冲突检测，按需 LLM(大语言模型) 复核；规则不提前退出、原始分数不跨通道比较。详见[路由、训练与评测说明](docs/intent-fusion.md)。
- 范围与澄清：意图识别前先区分医疗、非医疗、混合和不确定请求；非医疗固定拒识，混合请求只传递经原文校验的医疗片段，任务关键槽位不足时先追问且不调用 A2A(智能体间通信协议)/MCP(模型上下文协议)
- A2A(智能体间通信协议)：`python-a2a`(Python智能体通信库) 分别运行 SymptomAgent(症状智能体)、DrugAgent(药品智能体)、GuideAgent(指南智能体) 三个专科服务；三者默认调用同一配置的云端模型，各自执行专科任务与工具循环
- MCP(模型上下文协议)：FastMCP(MCP服务框架) 独立服务，注册外部接口工具 11 个、内部数据工具 3 个；外部转诊和内部病史/临床辅助记录保存均属于有副作用的操作
- ReAct(推理与行动交替)：三个专科 Agent(智能体) 使用 LangChain(大模型应用开发框架) `create_agent`(创建智能体) 执行模型 Function Calling(函数调用) → MCPAdapter(MCP工具适配器) → FastMCP(MCP服务框架) 工具 → Observation(工具观察结果) 回填；模型调用和工具调用分别限制最多 6 次
- 复杂任务：协调器调用 `Planner.plan`(规划器；计划)，让同一配置的云端模型生成短链计划；代码校验计划后，通过 A2A(智能体间通信协议) 按顺序调用所需专科，前步结果不会自动加入后步专科上下文
- 外部工具：HMAC(基于密钥的消息认证码) 鉴权、单次 HTTP(超文本传输协议) 请求 10 秒超时和进程内熔断器；只读查询遇到网络异常或 HTTP(超文本传输协议) 429/500/502/503/504 时最多尝试 3 次。业务拒绝不自动重试；转诊写接口不自动重试，以免超时后重复创建
- 隐私：当前问题、历史消息、嵌套工具结果发送外部主模型前统一递归脱敏
- 记忆：配置 Redis(内存键值数据库) 时跨实例保存会话、阶段记录和会话锁，未配置时使用进程内 TTLCache；关系数据库保存患者档案与临床辅助记录，生产参考使用 MySQL(关系型数据库)，本地默认使用 SQLite(轻量关系型数据库)。阶段记录不支持任意步骤自动恢复
- Agent(智能体) 上下文：三个专科没有各自的长期会话记忆，每次任务接收同一员工、同一患者会话的只读历史快照，并重新读取可信患者档案；每个专科任务分别维护模型消息、工具结果、调用计数和工具调用指纹。协调器收集结果，业务入口在最终答复后统一写回会话；本轮状态隔离不表示患者事实或历史输入互不共享
- 可观测：Prometheus(监控指标系统) 指标、Grafana(监控展示平台) 数据源和隐藏医疗输入/输出的 LangSmith(大模型链路观测平台) trace(链路追踪)
- 发布参考：Dockerfile、K8s(容器编排平台) stable(稳定状态)/canary（稳定版/金丝雀版）、Gateway API（网关接口）90/10 权重与 HPA（水平自动扩缩容）；未执行部署
- 认证：院内员工工号 + PBKDF2 密码，预留医院 SSO/OIDC 对接边界
- 授权：员工—患者访问关系、JWT(身份认证令牌)、patient_id(患者标识) 服务端校验、审计日志和急诊拦截

内部委托签名使用 `INTERNAL_SERVICE_SECRET`(内部服务签名密钥配置)，与员工 JWT(身份认证令牌) 使用的 `SECRET_KEY`(身份令牌签名密钥配置) 分开；当前内部服务共享这一委托签名密钥，并非每个专科使用独立签名密钥。服务端校验令牌有效期、目标服务和委托范围。子 MCP(模型上下文协议) 委托不得改变父委托的专科，也不得扩大父委托工具范围；专科按固定白名单、本轮动作批准与父工具范围的交集绑定工具。生产仍需 TLS(传输层安全协议) 或 mTLS(双向传输层认证)、网络策略和密钥轮换；共享服务密钥不能抵御已经取得该密钥的服务主体。

## 配置

复制可提交的 [.env(运行环境).example(示例)](.env.example) 为本地 `.env`(运行环境) 后填写真实配置。`.env`(运行环境) 已在 `.gitignore` 中排除，禁止提交。

必须配置：

- `SILICONFLOW_API_KEY`(硅基流动模型服务应用程序接口键)：SiliconFlow(硅基流动模型服务) API(应用程序接口) Key(键)
- `SILICONFLOW_BASE_URL`(硅基流动模型服务基础配置网址)、`SILICONFLOW_MODEL`(硅基流动模型配置)
- `INTENT_BERT_MODEL_PATH`(意图双向编码语言模型模型路径)：由 `scripts/train_intent_bert.py` 生成的本项目十分类模型目录
- `INTENT_VECTOR_MODEL_PATH`：本地 BGE-M3(多语言多粒度多功能嵌入模型) / SentenceTransformer 模型目录
- `INTENT_BERT_THRESHOLD`(意图双向编码语言模型阈值)、`INTENT_BERT_THRESHOLDS`、`INTENT_BERT_UNCERTAINTY_MARGIN`、`INTENT_VECTOR_THRESHOLD`、`INTENT_VECTOR_THRESHOLDS`（旧 `INTENT_VECTOR_MARGIN` 仅兼容配置）
- `DATABASE_URL` 与 MySQL(关系型数据库) Docker(容器平台) 参数
- `DRUG_API_BASE_URL`(药品应用程序接口基础配置网址)、`GUIDELINE_API_BASE_URL`(指南服务基础地址配置)、`LIS_API_BASE_URL`(检验信息系统应用程序接口基础配置网址)、`HIS_API_BASE_URL`(医院信息系统应用程序接口基础配置网址)
- `HOSPITAL_APP_KEY`、`HOSPITAL_APP_SECRET`
- 两个不同的高强度随机密钥 `SECRET_KEY`(身份令牌签名密钥配置) 和 `INTERNAL_SERVICE_SECRET`(内部服务签名密钥配置)，生产各至少 32 字符

生产需要 `APP_ENV=production`(应用运行环境)、共享 `REDIS_URL`(内存键值数据库网址)、MySQL(关系型数据库)、HTTPS(加密超文本传输协议) 医院接口及经过医院批准的 `CLOUD_LLM_ALLOWED=true`(真)。`LANGSMITH_API_KEY`(大模型链路观测平台应用程序接口键)、`LANGSMITH_TRACING` 与 `GRAFANA_ADMIN_PASSWORD` 按实际监控启用；默认关闭远端追踪。生产上云批准开关在 API(应用程序接口) 启动和实际模型调用入口均校验，独立启动专科服务也不能跳过这一检查。LangSmith(大模型链路观测平台) 仅记录经过处理的外层步骤摘要；LangChain(大模型应用开发框架) 子运行已在代码中关闭远端追踪，避免上传工具参数、Observation(工具观察结果)、患者原文、患者 ID(标识) 和医疗答复。

检查配置：

```powershell
conda activate Smart_Healthcare
python scripts/check_config.py
```

**示例术语与字段译注：** `Smart_Healthcare`(本医疗智能体代码仓库)；`python`(编程语言)。

## 安装与启动

本地开发先激活环境并安装 requirements-dev.txt，再执行训练、预检和启动命令。该文件包含全部运行依赖，以及 PyYAML、pytest(Python测试框架)、pytest(Python测试框架)-asyncio(Python异步并发库) 和 locust 开发工具；与 pyproject.toml 的 dev 分组保持一致。生产 Docker(容器平台) 安装 requirements.txt，不直接安装上述开发工具。提交钩子运行 quick 预检，同样需要开发依赖：

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements-dev.txt -c constraints.txt
```

**示例术语与字段译注：** `Smart_Healthcare`(本医疗智能体代码仓库)；`constraints`(约束)；`python`(编程语言)；`txt`(纯文本文件扩展名)。

首次使用前，运行多标签数据生成脚本，再基于本地 `bert-base-chinese`(双向编码语言模型；基础配置) 重新训练。新版本训练/验证/测试分别为 3,100/640/1,340 条合成样本，含独立多意图和否定样本；旧 3,800 条启动数据与历史成绩保留。合成评测只能用于工程验证。

```powershell
$BERT_BASE_MODEL = "请替换为本机 bert-base-chinese 模型目录"
python scripts/train_intent_bert.py `
  --base-model $BERT_BASE_MODEL `
  --dataset evaluation\datasets\multilabel_v4\intent_train.jsonl `
  --validation-dataset evaluation\datasets\multilabel_v4\intent_validation.jsonl `
  --output models\medical_intent_bert_multilabel_v4
```

**示例术语与字段译注：** `BERT_BASE_MODEL`(双向编码语言模型基础配置模型)；`base`(基础配置)；`bert`(双向编码语言模型)；`evaluation`(评估)；`intent_train`(意图训练)；`intent_validation`(意图校验)；`model`(模型)；`models`(模型集合)；`multilabel_v4`(第四版多标签意图配置)；`output`(输出)；`python`(编程语言)；`train_intent_bert`(训练意图双向编码语言模型)；`validation`(校验)。

训练脚本会在加载模型前拒绝训练/验证集合之间经首尾空白和大小写归一化后重复的文本。没有独立验证文件时可显式使用 `--split-validation`(校验) 从训练集内部拆分，按全部标签覆盖选择样本并保留训练集每类至少一条；共现约束无法满足时明确要求独立验证集，不只按第一个标签分组；精确文本隔离不等于没有语义或模板泄漏。

需要重新生成数据时运行：

```powershell
python scripts/build_intent_dataset.py
python scripts/build_multilabel_intent_dataset.py
```

**示例术语与字段译注：** `python`(编程语言)。

`python scripts/check_config.py`(编程语言) 检查模型目录、BERT(双向编码语言模型) 多标签配置、逐类阈值文件及十类原型覆盖。两个本地模型都需要准备；即使规则命中，模型不可用也会触发 LLM(大语言模型) 复核，复核失败停止路由。因此部署需配置可用的云端模型凭据。该检查只检查本地核心链路，医院接口未配置时会给出提示但不阻止启动；生产联调前使用 `python scripts/check_config.py --strict`(编程语言；严格模式)，要求 HIS(医院信息系统)/LIS(检验信息系统)、药品和指南服务全部配置。未配置的真实工具会明确失败，不会用模拟医学数据兜底。

### 克隆后的模型准备与指标复核

模型权重不随 Git(版本控制系统) 分发。克隆后需准备基础 BERT(双向编码语言模型)、训练本项目十类多标签模型，并下载 BGE-M3(多语言多粒度多功能嵌入模型)；不能只克隆代码就复现历史模型指标。下载公开基础模型的示例（需联网；该命令会下载较大权重）：

```powershell
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='google-bert/bert-base-chinese', local_dir='models/bert-base-chinese')"
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', local_dir='models/bge-m3')"
```

**示例术语与字段译注：** `base`(基础配置)；`bert`(双向编码语言模型)；`bge-m3`(多语言多粒度多功能嵌入模型)；`google`(谷歌)；`models`(模型集合)；`python`(编程语言)。

将前文 `$BERT_BASE_MODEL`(双向编码语言模型基础配置模型) 设置为 `models/bert-base-chinese`(模型集合；双向编码语言模型；基础配置)，执行训练命令；在本地 `.env`(运行环境) 中设置 `INTENT_BERT_MODEL_PATH=models/medical_intent_bert_multilabel_v4`(模型集合；意图双向编码语言模型模型路径)、`INTENT_VECTOR_MODEL_PATH=models/bge-m3`(模型集合；多语言多粒度多功能嵌入模型)。公开下载例子取得当时的模型版本；若要求固定版本，需记录并传入 Hugging Face(模型与数据集平台) 的 `revision`(修订号) 提交号。当前旧模型未记录上游提交，不能补造一个版本号。

[当前模型文件指纹](evaluation/model_artifacts.json)记录 v4 多标签权重、分词器、逐类阈值与 BGE-M3(多语言多粒度多功能嵌入模型) 文件的大小和 SHA-256(安全哈希摘要算法)；[旧 v3 指纹](evaluation/model_artifacts_v3_historical.json)与[旧 BERT(双向编码语言模型) 指标](evaluation/bert_historical_summary.json)分别保留。v4 训练使用 seed=42、8 轮、batch(批次)=8、学习率 2e-5、最大长度 256，按验证集 Micro-F1(微平均F1) 保存最佳权重。不同基础权重、依赖和设备可能产生不同结果，不承诺重新训练得到完全相同分数。

当前 v4 数据集清单的 `sha256`(安全哈希摘要) 也保留训练时 CRLF(回车加换行) 原始字节，`repository_sha256`(仓库快照哈希) 可核对仓库 LF(换行符) 字节；新 BERT(双向编码语言模型) 摘要相应使用 `repository_dataset_sha256`(仓库数据集哈希)。换行规范化不改写训练或历史评测指纹，后续数据生成脚本显式输出 LF(换行符)。

BERT(双向编码语言模型) 与[向量挑战摘要](evaluation/vector_challenge_summary.json) 中的历史 `dataset_sha256`(数据集哈希) 对应本机 CRLF(回车加换行) 数据集字节，不能直接与 Git(版本控制系统) 仓库中的 LF(换行符) 文件比较。2026-10-07 已验证两种文件仅换行不同；新增 `repository_dataset_sha256`(仓库数据集哈希) 记录仓库 LF(换行符) 文件的指纹，可直接与克隆后的 `evaluation/datasets/intent_test.jsonl`(评估)、`intent_challenge.jsonl` 原始字节 SHA-256(安全哈希摘要算法) 核对，比较前请确认文件仍为 LF(换行符)。历史指纹和评测数字均保留，未重新评测；本机保留的 `artifacts/intent_bert_test_tob_v3.json` 和 `artifacts/intent_bert_challenge_tob_v3.json` 原始字节已于本轮校验，均匹配 `source_report_sha256`(来源报告安全哈希摘要)。它们是被 Git(版本控制系统) 忽略的本地产物，未随仓库提供；仅凭克隆仍不能独立验证这两个来源报告，详见摘要中的 `source_report_verification_note`。

本机普通测试报告还有同字节副本 `artifacts/intent_bert_test_audit_20260924.json`，挑战报告还有同字节副本 `artifacts/intent_bert_challenge_audit_20260924.json`。两组各自的两份文件均匹配摘要中的来源指纹；哈希只能确认内容一致，不能判定哪份最早生成或哪份是事后复制文件。

重新训练会生成新的权重、阈值 JSON(结构化数据格式) 字段和字节指纹。以下步骤针对前文默认的独立验证文件训练：先创建新清单，再依据新清单校验并重新评测；原清单和历史分数保留。`--record-output`(记录；输出) 拒绝覆盖已有文件，重复执行时选择新的文件名。记录命令验证输入行及原字节来源与模型阈值文件吻合，不会为了通过检查跳过来源校验。

```powershell
python scripts/verify_model_artifacts.py --bert models/medical_intent_bert_multilabel_v4 --vector models/bge-m3 --validation-dataset evaluation/datasets/multilabel_v4/intent_validation.jsonl --record-output artifacts/model_artifacts_retrained.json
python scripts/verify_model_artifacts.py --manifest artifacts/model_artifacts_retrained.json --bert models/medical_intent_bert_multilabel_v4 --vector models/bge-m3
python scripts/evaluate_intent_bert.py --model models/medical_intent_bert_multilabel_v4 --dataset evaluation/datasets/multilabel_v4/intent_test.jsonl --output artifacts/bert_test_new.json
python scripts/evaluate_intent_bert.py --model models/medical_intent_bert_multilabel_v4 --dataset evaluation/datasets/intent_challenge.jsonl --output artifacts/bert_challenge_new.json
```

**示例术语与字段译注：** `bert`(双向编码语言模型)；`bert_test_new`(双向编码语言模型测试新任务)；`bge-m3`(多语言多粒度多功能嵌入模型)；`evaluation`(评估)；`intent_test`(意图测试)；`intent_validation`(意图校验)；`json`(结构化数据格式)；`manifest`(清单)；`model`(模型)；`models`(模型集合)；`multilabel_v4`(第四版多标签意图配置)；`output`(输出)；`python`(编程语言)；`record`(记录)；`validation`(校验)。

只校验旧 v3 文件时，使用 `python scripts/verify_model_artifacts.py --manifest evaluation/model_artifacts_v3_historical.json --bert models/medical_intent_bert_tob_v3 --vector models/bge-m3`(编程语言；清单；评估；双向编码语言模型；模型集合；多语言多粒度多功能嵌入模型)。当前评测脚本要求 Sigmoid(逐标签概率) 多标签权重，不能用它重跑旧 Softmax(归一化概率) 评测。

以上路径对应前文下载布局；若使用已有模型目录，请将 `--bert`(双向编码语言模型)、`--vector` 分别替换为本地 `.env`(运行环境) 中 `INTENT_BERT_MODEL_PATH`(意图双向编码语言模型模型路径)、`INTENT_VECTOR_MODEL_PATH` 指向的实际目录，脚本不会自动读取这两个配置。指纹不匹配不代表文件一定损坏，可能是新下载或新训练的版本；应以新评测报告描述该版本，不能沿用历史覆盖率。下载方法见 [Hugging Face(模型与数据集平台) 官方说明](https://huggingface.co/docs/huggingface_hub/en/guides/download)。

BGE-M3(多语言多粒度多功能嵌入模型) 从 `evaluation/intent_prototypes.jsonl`(评估) 加载版本化单意图原型，返回各类相似度作为补充证据；所有请求都经过三路识别。多任务、否定、边界分数、候选分歧或模型不可用时 LLM(大语言模型) 复核，失败停止路由。详见[融合策略](docs/intent-fusion.md)。

阈值校准与完整回归：

```powershell
python scripts/calibrate_intent_vector.py
python scripts/evaluate_intent.py --output artifacts/intent_fusion_eval.json
```

**示例术语与字段译注：** `json`(结构化数据格式)；`output`(输出)；`python`(编程语言)。

**历史记录（旧单标签级联，不代表当前融合效果）：** [BERT(双向编码语言模型) 历史摘要](evaluation/bert_historical_summary.json)、[旧向量残余挑战校准](evaluation/vector_challenge_summary.json)与[33 条级联回归](evaluation/intent_regression_summary.json)保留用于版本追溯。新方案需要独立评测，不沿用旧 0.95 阈值及历史准确率。当前评测记录见[融合说明](docs/intent-fusion.md)。

在挑战集上复核已确定的 `0.78` 阈值（不要用该集重新调参）：

```powershell
python scripts/calibrate_intent_vector.py --dataset evaluation/datasets/intent_challenge.jsonl --thresholds 0.78 --output artifacts/vector_challenge_v4.json
```

**示例术语与字段译注：** `evaluation`(评估)；`json`(结构化数据格式)；`output`(输出)；`python`(编程语言)。

BERT(双向编码语言模型) 独立评测命令：

```powershell
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_multilabel_v4 --dataset evaluation\datasets\multilabel_v4\intent_test.jsonl
python scripts/evaluate_intent_bert.py --model models\medical_intent_bert_multilabel_v4 --dataset evaluation\datasets\intent_challenge.jsonl
```

**示例术语与字段译注：** `evaluation`(评估)；`intent_test`(意图测试)；`model`(模型)；`models`(模型集合)；`multilabel_v4`(第四版多标签意图配置)；`python`(编程语言)。

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements-dev.txt -c constraints.txt
docker compose up -d mysql
docker compose up -d redis
python -m alembic upgrade head
python scripts/run_all.py
```

**示例术语与字段译注：** `Smart_Healthcare`(本医疗智能体代码仓库)；`alembic`(数据库迁移工具)；`compose`(多容器编排工具)；`constraints`(约束)；`docker`(容器平台)；`head`(当前提交指针)；`mysql`(关系型数据库)；`python`(编程语言)；`redis`(内存键值数据库)；`txt`(纯文本文件扩展名)。

`run_all.py` 会启动：

- FastMCP(MCP服务框架)：`127.0.0.1:8002/mcp`(模型上下文协议)
- SymptomAgent(症状智能体) A2A(智能体间通信协议)：`127.0.0.1:8011`
- DrugAgent(药品智能体) A2A(智能体间通信协议)：`127.0.0.1:8012`
- GuideAgent(指南智能体) A2A(智能体间通信协议)：`127.0.0.1:8013`
- FastAPI(Python接口服务框架)：`127.0.0.1:8000`（接口文档 `/docs`）
- Streamlit(Python交互页面框架)：`127.0.0.1:8501`（院内员工工作台）

也可以分别启动 API(应用程序接口) 和界面：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

**示例术语与字段译注：** `host`(宿主程序)；`python`(编程语言)；`run`(运行)；`server`(服务端)；`streamlit`(Python交互页面框架)。

## 账号

院内员工账号由医院管理员创建。角色支持医生、药师、医务管理员和系统管理员；医生/药师可重复传入多个授权患者。医务管理员可按医院级范围查看患者，系统管理员默认不具备病历读取权限。现有数据库先运行 Alembic（数据库迁移工具）迁移；本地联调档案必须来自明确输入，脚本不会生成医学数据：

```powershell
python scripts/upsert_patient.py --patient-id patient_001 --name patient_name --age 40 --gender 未知
python scripts/create_staff.py --employee-id D10086 --username doctor_name --role doctor --patient-id patient_001
```

**示例术语与字段译注：** `age`(年龄)；`gender`(性别)；`id`(标识)；`name`(名称)；`patient`(患者)；`patient_name`(患者名称)；`python`(编程语言)；`upsert_patient`(插入或更新记录患者)。

`upsert_patient.py` 在写库前复用档案字段约束。更新已有患者时，不传 `--allergy`、`--condition`、`--medication` 会保留对应病史；需要清空时显式使用 `--clear-allergies`、`--clear-conditions`、`--clear-medications`，传入列表则替换对应列表。

生产的统一身份认证和 HIS(医院信息系统)/EMR(电子病历) 动态授权需按医院契约另行对接，当前使用本地员工及授权表。API(应用程序接口)、A2A(智能体间通信协议) 和 MCP(模型上下文协议) 均重新核对实时权限；医务管理员为医院级读取角色，写工具仅对已确认动作及允许的临床角色开放。

## 写操作与会话安全

三个 MCP(模型上下文协议) 写工具分别是病史保存、辅助材料保存和转诊草稿创建。聊天 approved_actions(本轮批准动作) 只批准前两项，仍是动作级确认。转诊改走独立 POST(提交请求) /api(应用程序接口)/referral(院内转诊)-drafts：医生填写患者、目标科室和原因并明确确认，服务签发仅允许 generate_referral(创建转诊草稿) 的限时委托，绑定 confirmed_arguments_sha256(医生确认参数摘要)；MCP(模型上下文协议) 重新核对业务服务身份、最终参数、医生角色与患者授权。症状智能体和聊天均无权创建草稿，不代表完整转诊协同已经实现。

患者档案还可由医生/药师在工作台“编辑档案”表单中直接保存，走独立 REST(表述性状态转移接口风格) 接口 `PUT /api/patients/{patient_id}/profile`(更新请求；应用程序接口；患者列表；患者标识；患者档案)。接口校验身份、写角色、患者授权与 `ProfileUpdate`(档案更新数据) 字段约束，在同一数据库事务中更新档案并写入 `profile_updated`(档案已更新) 审计。过敏史、既往病史与当前用药的条目约束同样复用 `ClinicalEntry`(临床入口)。该路径不使用聊天请求的 `approved_actions`(本轮批准的写动作)，也不进入 MCP(模型上下文协议) 工具的操作键和状态表。

三个 MCP(模型上下文协议) 写工具使用 `request_id`(请求标识) 与员工、工具组成持久化操作键，同键同参数复用完成结果，冲突或未知状态拒绝重放。异常后用 `GET /api/operations/{request_id}`(读取请求；应用程序接口；请求标识) 核对本人 MCP(模型上下文协议) 写工具操作；直接档案编辑应核对档案及审计记录。下游 HIS(医院信息系统) 没有契约保证时不声称全局恰好一次。

工作台编辑表单按患者标识隔离，切换患者会清除另一患者未保存的表单输入。会话按员工、患者、会话隔离，整轮锁防止并发覆盖；本地锁容量满时拒绝新任务，避免驱逐仍在执行的锁；数据库提交、消息成对写入、阶段保存后发送 SSE(服务器推送事件) 完成事件。默认整轮 120 秒、模型单次 30 秒、输出 2,048 词元；没有工具证据或存在工具失败时不返回正常临床结论。

A2A(智能体间通信协议) 传输历史采用最近消息优先裁剪：每条内容最多 4 KiB(1024字节单位)，历史 JSON(结构化数据格式) 的 UTF-8 总量最多 16 KiB(1024字节单位)；完整会话存储仍最多 20 条。裁剪只用于可选上下文，当前任务单独传递，专科从数据库重新读取可信患者档案。裁剪可能丢失较早上下文，不应把历史作为关键临床参数的唯一来源。

SSE(服务器推送事件) 异常会记录阶段、请求标识、异常类型和栈位置，不记录原始异常消息或临床内容；范围、规划及本地意图通道的失败日志也只记录异常类型。最终汇总必须有实际答复且正常结束，截断、过滤、空白或缺少结束标记按失败处理，不进入正常答复保存，也不发送 `done`(完成事件)。已执行的 MCP(模型上下文协议) 写入需另行核对，不会因汇总失败撤销。汇总输入包含专科回答、状态与脱敏工具 Observation(工具观察结果)；目标工具覆盖不等于医学结论正确。健康检查的相对模型路径按仓库根目录解析。

## 验证

```powershell
python -m pytest -q
python -m compileall -q backend scripts streamlit_app.py
```

**示例术语与字段译注：** `pytest`(Python测试框架)；`python`(编程语言)。

前轮完整测试记录（2026-10-13，工程修复）：**479 项自动化测试通过，耗时 24.11 秒**。新增覆盖独立只读并发、依赖结果传递、批准写动作串行、同批失败等待、任务更正与版本拒绝、关键槽位依据、空或截断工具结果阻断，以及医务人员反馈的授权与归属。历史 2026-10-12 的 399 项测试、20 项完整预检及 18 项快速预检是当时的记录，不表示本次重新执行了 Git(版本控制系统) 交付预检。存在 LangChain(大模型应用开发框架) MCP(模型上下文协议) beta(测试阶段) 和 Starlette WSGI(Web服务器网关接口) 弃用警告。档案编辑 REST(表述性状态转移接口风格) 回归仍覆盖字段约束、角色权限与事务回滚，不生成 MCP(模型上下文协议) ToolExecution(工具执行)。

2026-10-13：新增[全部 14 份文档逐题适配](docs/MedAgent_逐题适配/README.md)与[工程改进对照](docs/MedAgent_文档对照与工程改进_2026-10-13.md)。当前最多三步计划支持 `depends_on`(依赖步骤)：独立只读步骤同批并行，依赖步骤传递脱敏依据，批准写动作的计划串行。任务选择、A2A(智能体间通信协议) 回包和 Redis(内存键值数据库) 活跃任务校验绑定任务版本；按意图核对真实工具依据，只有只读覆盖不匹配允许一次纠错重路由。详细证据和参数来源入授权审计，工作台反馈保存待临床复核，不自动训练。

2026-10-07 增加 27 项回归，验证父子委托范围、活动锁容量、真实 MCPAdapter(MCP工具适配器) 的 `isError`(是否为工具错误) 处理、JSON(结构化数据格式) 文本块脱敏、证件号替换顺序、导入病史保留/显式清空、无效配置、训练集隔离和患者表单切换。详情见 [2026-10-07 全面核查记录](docs/audit-2026-10-07.md)。

历史快照：2026-09-28 本地 103 项测试通过；2026-09-30 补充临床预约背景与写参数预检回归后，113 项测试通过；同日增加监控回归后为 115 项，再补充预检及异常指标回归后为 119 项；增加取消、流式中断与转发总超时测试后为 122 项；补充 Redis(内存键值数据库) 锁及委托有效期回归后共 127 项通过。pytest(Python测试框架) 默认仅收集 `tests/`；如系统 pytest(Python测试框架) 临时目录无权限，使用新的 `--basetemp`。测试会确认：14 个 FastMCP(MCP服务框架) 工具真实注册、未配置的医院接口明确失败、敏感标识脱敏、急诊边界优先执行、员工—患者授权、多意图保留和患者会话隔离。当前新增 43 项独立安全入口回归，覆盖急症优先、语义复核、不确定/超时阻断和风险元信息脱敏；测试桩不能替代临床风险标注验收。

意图回归评测：

```powershell
python scripts/evaluate_intent.py --output artifacts/intent_fusion_eval.json
```

**示例术语与字段译注：** `json`(结构化数据格式)；`output`(输出)；`python`(编程语言)。

旧级联历史：33 条工程回归集在 2026-09-28 的模型与阈值下运行：严格匹配率、复杂任务标记准确率和 micro-F1(微平均F1) 均为 100%，0 个运行错误（32 条由正则层完成，1 条由向量层完成）。该小型工程集只用于防回退，不代表临床泛化。300 用户 HTTP(超文本传输协议) 入口压测命令和口径见 [压测说明](loadtests/README.md)。本地报告生成到已被 Git(版本控制系统) 忽略的 `artifacts/` 目录；该报告不代表完整 Agent(智能体) 链路吞吐。

## GitHub(代码托管平台) 上传前检查

2026-10-11 姓名路径与私人提交邮箱的历史清理已完成。旧报告中的 SHA(安全哈希摘要) 保留核对时刻的原值，改写后的对应提交见[脱敏 SHA(安全哈希摘要) 映射](docs/git-history-rewrite-map.json)；仓库外备份位置、实际恢复核验、全局及本地默认邮箱配置和远端清理范围见[历史个人信息清理说明](docs/history-cleanup.md#2026-10-11-姓名与提交邮箱清理)。

仓库内置不依赖第三方扫描器的预检脚本，会分别扫描工作区候选文件和 Git(版本控制系统) 暂存内容中的疑似密钥，并检查忽略规则、私钥文件、超大文件、Python(编程语言) 语法和 `.env.example`(运行环境；示例) 配置项完整性。普通模式和 `--quick` 均检查工作区 Markdown(轻量文档标记格式) 的内联、Wiki 本地文件链接与章节锚点，并要求本地链接目标已纳入 Git(版本控制系统) 索引、声明 LF(换行符) 的工作区文件不含 CR(上下文召回率)，因此提交钩子也会拦截这类断链及换行问题；不请求外部网站，不核对桌面文件或标准引用式链接。预检还会拒绝跟踪 `output`(输出)/、`.mimosa/` 和本地简历生成脚本。完整模式还会运行 pytest(Python测试框架) 与 `pip check`。模式匹配不是完整的数据泄露防护，上传前仍应人工审阅暂存差异；已泄露的密钥需要撤销重置，不能只删除文件：

2026-10-07 的“30 份文档链接检查”为历史快照（仓库 14 份、桌面 16 份）。2026-10-10 当时范围为 31 份（仓库 15 份、桌面 16 份）；提交钩子检查的是仓库内候选 Markdown(轻量文档标记格式)，不包含桌面。历史额外核查脚本、桌面收据和备份保存在被忽略的 `artifacts/audit_20261007/` 中，融合修复证据位于 `artifacts/review_20261010/`，独立安全入口证据位于 `artifacts/safety_routing_20261010/`，2026-10-11 桌面核对收据位于 `artifacts/history_backup_path_followup_20261011/latest_desktop_receipt.json`；该收据只对应记录时刻的目标字节，后续变化需重新核对，均不随克隆分发。

```powershell
conda activate Smart_Healthcare
python -m pip install -r requirements-dev.txt -c constraints.txt
python scripts/preflight_git.py
```

**示例术语与字段译注：** `Smart_Healthcare`(本医疗智能体代码仓库)；`constraints`(约束)；`python`(编程语言)；`txt`(纯文本文件扩展名)。

只执行快速静态检查：

```powershell
python scripts/preflight_git.py --quick
```

**示例术语与字段译注：** `python`(编程语言)。

先在项目环境安装 requirements-dev.txt，再执行一次 `python scripts/install_git_hooks.py`(编程语言) 可启用 hooks 并将当前解释器保存到本地 Git(版本控制系统) 配置；切换或迁移环境后需重新安装：提交前运行快速检查，推送前运行完整检查。真实 `.env`(运行环境)、数据库、运行日志、模型权重、压测报告、简历产物和 `.mimosa/` 运行状态应留在本地；`.gitignore` 与预检约束当前 Git(版本控制系统) 索引。预检会提示这些本地文件是否曾进入 Git(版本控制系统) 历史，但不审查历史内容或远端可见性。已经进入历史或远端的文件不会因后来取消跟踪而消失。

启动监控时，用以下命令替代普通的 `python scripts/run_all.py`(编程语言)，在另一终端启动容器：

```powershell
python scripts/run_all.py --observability
# 另一终端：
docker compose --profile observability up -d prometheus grafana
```

**示例术语与字段译注：** `compose`(多容器编排工具)；`docker`(容器平台)；`grafana`(监控展示平台)；`observability`(可观测性)；`profile`(患者档案)；`prometheus`(监控指标系统)；`python`(编程语言)。

`--observability`(可观测性) 启动只读指标转发入口 `0.0.0.0:9100`，只允许固定的 API(应用程序接口) 和三个 A2A(智能体间通信协议) 进程指标路径；业务接口仍监听回环地址。9100 应仅允许可信监控网络访问。Prometheus(监控指标系统) 通过 `host.docker.internal:9100`(宿主程序；容器平台) 抓取四个 job，在 `http://127.0.0.1:9090/targets` 检查是否全部 UP；若连接失败，检查 Docker(容器平台) 到宿主机的路由和防火墙。

API(应用程序接口) 进程提供 HTTP(超文本传输协议)、路由与 A2A(智能体间通信协议) 调用指标，三个专科进程分别提供其发起的 MCP(模型上下文协议) 工具调用指标。当前未单独暴露 MCP(模型上下文协议) 服务进程指标，也不宣称所有下游均有独立监控。各进程的同名指标需按 job 区分。旧 `scripts/create_doctor.py` 仅为兼容命令入口；新部署统一使用 `scripts/create_staff.py --role doctor`。

HTTP(超文本传输协议) 延迟指标 `medagent_http_request_duration_seconds`(医疗智能体项目超文本传输协议请求持续时间秒) 统计响应创建至响应头可用或普通异常抛出的时间，不包含 SSE(服务器推送事件) 正文传输时长，也不等于首个模型 token(词元) 延迟或整轮耗时。响应创建阶段未处理的普通异常计入 500；响应头之前的任务取消不计入该 HTTP(超文本传输协议) 状态指标，响应头之后的流式取消保留已记录的状态，流式错误需结合 SSE(服务器推送事件) 事件与日志判断。指标入口的 `0.0.0.0` 绑定也可能允许局域网访问，访问范围由宿主机防火墙控制，并非代码自动限制为 Docker(容器平台) 网络。

指标转发总截止时间为 5 秒，Prometheus(监控指标系统) `scrape_timeout` 为 10 秒；上游耗时超过 5 秒会由转发入口返回 503，而非等待抓取端超时。Git(版本控制系统) 预检要求 `backend/`、`scripts/`、`tests/`、`migrations/` 内的新 Python(编程语言) 文件入索引；其他候选 Python(编程语言) 文件仍扫描语法、敏感内容与静态依赖，但不因根目录存在临时脚本就要求其入库。动态拼接依赖仍需独立检查。

Redis(内存键值数据库) 锁已完成真实 Redis(内存键值数据库) 容器验证；子 MCP(模型上下文协议) 委托不得延长父 A2A(智能体间通信协议) 委托有效期。入口绝对 deadline_at(绝对截止时间) 已通过签名委托传到专科与 MCP(模型上下文协议)，新调用受剩余预算限制；API(应用程序接口) 超时仍不保证已提交的远端写入停止，具体见 [Redis(内存键值数据库) 与超时核查](docs/redis-timeout-verification.md)。

## 重要边界

本项目不会自行提供处方剂量，也不会把模型输出当作确诊结果。医院 API(应用程序接口) 字段结构需要按甲方 OpenAPI 文档对 `backend/mcp_tools.py` 中的路径与字段映射做最后适配；在这些真实接口尚未提供前，对应功能会返回“接口未配置”，不会使用假数据替代。

EMR(电子病历) 配置曾是未调用的预留项，现已删除。14 个工具中没有独立 EMR(电子病历) 连接器；报告工具对接 LIS(检验信息系统)，正式电子病历写入、影像识别与参数级审批需按医院需求独立验收。

## 兼容性与验证边界

当前 A2A(智能体间通信协议) 使用已弃用的 Starlette WSGI(Web服务器网关接口) 桥；constraints(约束) 固定 Starlette 1.6.0、Flask 3.1.3，迁移适配器前须复测真实 HTTP(超文本传输协议) 链路。Docker(容器平台) 冷启动探针宽限为 600 秒。固定窗口登录限流不保证任意滚动 300 秒均只有 10 次。pending(待处理)/unknown(未知) 写操作需人工核对 HIS(医院信息系统)，不自动重试。

评测记录：2026-09-28 原型替换后已重新评测；当时的本地明细为 `artifacts/intent_eval_tob_v3.json`，可入库摘要为 [33 条回归记录](evaluation/intent_regression_summary.json)。摘要包含生成时间、数据集与原型 SHA-256(安全哈希摘要算法)、逐条结果；未包含本机模型路径。

2026-10-10 更新：已切换三路融合与 Sigmoid(逐标签概率) 多标签模型，首轮 203 项、融合修复后 221 项、独立安全入口完成后 264 项、本轮部署与指纹修复后 275 项自动化测试通过。真实供应商的 33 条意图路由回归全部通过（28 条融合、5 条 LLM(大语言模型)）；原需复核的 5 条请求各重复 3 次，15/15 通过。已修复 LLM(大语言模型) 辅助字段格式兼容问题，并保留此前 HTTP(超文本传输协议) 402 与修复前失败记录。新 BERT(双向编码语言模型) 测试及路由验证边界详见[本次实测](docs/intent-fusion.md#本次实测2026-10-10)。

部署交付补充：Kubernetes(容器编排平台) 参考清单与 PVC 文档已同步到 v4 多标签 BERT(双向编码语言模型) 和 0.5 默认阈值；quick/完整预检均检查实际清单。当前模型指纹脚本同时验证 LF(换行符) 验证数据、原 CRLF(回车加换行) 指纹及有序训练输入，训练脚本后续自动输出对应字段。急症提示记录为安全事件，未冒充工具执行。详见[复核修复记录](docs/intent-fusion.md#2026-10-10-部署与指纹复核修复)。

开发与预检环境使用 `python -m pip install -r requirements-dev.txt -c constraints.txt`(编程语言)；Docker(容器平台) 继续只安装运行依赖 requirements.txt。PyYAML 是 Kubernetes(容器编排平台) 清单检查的开发依赖，固定为本轮已验证的 6.0.3；CI 同样使用开发依赖入口。预检分别检查直接依赖是否固定，以及新增文件的暂存内容是否落后于工作区；完整 pytest(Python测试框架) 验证工作区，不等于验证任意已有文件的整个暂存快照。

开发环境补强：缺少 PyYAML 时，quick、完整预检及提交钩子明确提示用同一 Python(编程语言) 安装 requirements-dev.txt，不把环境缺失混写成 Kubernetes(容器编排平台) 清单错误。直接依赖固定门只检查精确版本声明是否齐全，不证明固定值落在声明范围内；安装时的 pip 解析负责范围兼容性，pip check 检查已安装包的依赖一致性。临时目录与历史核查产物的管理统一见[测试临时数据与历史核查产物](docs/intent-fusion.md#测试临时数据与历史核查产物)。当前验证结果见[验证](#验证)。

依赖分类与安装入口的检查范围、支持与受限写法、回归注入校验及证据范围、工具名单维护要求及最新验证结果统一见[交付预检说明](docs/intent-fusion.md#2026-10-10-依赖安装入口复核)。


2026-10-13 完整话术补充：本地保留的 14 份 .txt 原件的 1,260 处适配问答完整保存在[面试话术主文](docs/MedAgent_AI_多Agent项目话术.md#全文档逐题正文)；2026-10-15 重新组织原理、项目做法与例子，另提供[72 题理解导读](docs/MedAgent_逐题适配/核心题理解导读.md)，不缩减原题；原件位于 artifacts/medagent_upgrade_20261013/原始资料/，桌面同名 .md 为适配稿。新增统一时间预算传播和独立档案的内容 revision(修订号) 条件更新；旧档案客户端须先 GET(读取请求) 取得 revision(修订号)，再以 expected_revision(预期状态修订号) 保存，缺版本返回 422、内容冲突返回 409。工作台已更新，无数据库迁移。

2026-10-13 复核修复：全部 136 个示例仍收录于完整面试话术；流程图在 docs/MedAgent_项目流程图.svg。生产委托与 Redis(内存键值数据库) 围栏见工程对照。历史新增 16 项回归保留原始口径。当前链接审计为仓库 33 份及桌面 19 份，共 52 份。独立 RAG(检索增强生成) 稿不计入本项目交付。同步收据为 artifacts/medagent_upgrade_20261013/desktop_sync_receipt.json；链接审计入口为 artifacts/medagent_upgrade_20261013/latest_link_audit.json。

2026-10-13 文档追溯复核：原文行号指本机保留的 artifacts/medagent_upgrade_20261013/原始资料/*.txt，适配稿与原件分开；生成器读取已有原件时不重写，异源输入不同则拒绝覆盖。111 个示例属于问答，放置为 66 个紧随引导语、45 个置于问答后；另 8 个概览和 17 个附录。桌面直接更新，不新建备份。

英文译注刷新入口：scripts/annotate_medagent_documents.py；分章适配使用统一术语表，话术合并继承分章已有译注，刷新器不向主文和人工导读重复插入注释。新增的通俗说明由人工编写，需同时检查专业含义与中文表达；代码、路径及原始资料保留原样。主文由生成器重建，原题答案应编辑分章，项目开场应编辑导读，新增原理补充在生成器问答补充表维护。覆盖清单的 source(来源) 是适配映射键，original_local_path(本地原件路径) 才是本机原件路径。链接审计检查本地文件及锚点；Git(版本控制系统) 索引可分发检查另由预检执行，external_urls(外部网址清单) 仅列举外部网址。审计的 worktree_dirty(工作区存在未提交改动) 表示未提交改动，输入哈希识别实际检查内容。

<!-- BEGIN MEDAGENT DOC REVIEW -->
## 本轮译注与文档一致性校正

2026-10-13：在话术、14 份题库、理解指南、工程对照和流程图补充中文译注；术语、工具名、参数名逐项说明，代码与路径不改写。文件格式集中说明：.md 是 Markdown(轻量文档标记格式) 文档，.txt 是纯文本原件，.json 是结构化数据文件，.svg 是矢量图，.png 是位图。已去除文件后缀的重复译注。17 个独立段落示例从上一题分离为章节附录，问答和示例总量不变。刷新器按唯一段落标记更新，缺失或重复会报错；保留历史验证口径。原件读取不再重写，mtime 不能证明首次捕获时间。

本轮代码与依赖回归 611 项通过；记录器采集耗时 54.172 秒，包含元数据采集、子进程启动、测试执行与临时目录清理；pytest(Python测试框架) 自报的测试耗时见原始日志，两者不等同，也不作接口性能指标。执行标识 `eb274ff2-6c51-495a-99b2-f391b4868698`。测试命令、代码指纹及原始结果见 artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json 指向的记录。该计数核验代码与依赖；生成后的当前文档另行核验 1,260 处引用、136 个示例内容和 52 份本地链接，不能用代码回归代替文档核验。当前文档独立核验入口为 artifacts/medagent_upgrade_20261013/latest_document_delivery.json，所指报告逐项保存当前文档哈希与覆盖清单哈希。当前核验使用随仓库分发的 scripts/verify_delivery_evidence.py，传入 --delivery-pointer 和上述入口；该命令重算文档、原件、示例与桌面正文，并核对文档宣称的测试数。桌面写入与核验共用链接映射，默认同步清单已排除独立 RAG(检索增强生成)，不依赖 --medagent(医疗智能体项目)-only 才排除。本仓库本轮未修改独立 med_rag(医疗检索增强生成项目) 项目的正文，其他会话对该项目或共享简历的更新不属于本轮交付状态。默认文档同步排除共享简历 HTML(网页标记语言)/PDF(便携文档格式)，简历另有需求时单独交付；不能把旧简历覆盖回桌面以消除历史收据漂移。artifacts/medagent_upgrade_20261013/med_rag_面试全解.md 与桌面同名稿属于前轮生成副本，不作为独立项目原件或当前交付。2026-10-14职责调整：九类临床辅助意图交三个专科，兼容标签“转诊协同”转为独立医生确认的转诊草稿业务入口，不增加第四个智能体。新增独立表单/API(应用程序接口)、确认参数摘要绑定、转诊写入审计与权限/去重/未知状态回归；症状专科和聊天均不能创建转诊。业务仍为院内 ToB(面向机构业务)；没有新增患者自助功能，没有真实医院写入。
<!-- END MEDAGENT DOC REVIEW -->
