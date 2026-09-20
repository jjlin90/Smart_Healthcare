# MedAgent AI 多 Agent 项目话术与实战追问

> 基于当前仓库的真实代码整理。回答时必须区分“已实现并验证”“代码已预留但缺少外部资源”“生产化规划”，不要把目标指标或设计方案说成线上结果。

## 一、先记住项目事实

### 已实现并验证

- 三层链路：FastAPI API Server → A2A 子 Agent → FastMCP 工具服务。
- 3 个子 Agent：SymptomAgent、DrugAgent、GuideAgent。
- 14 个 MCP 工具：10 个外部医院工具、4 个内部业务工具。
- 10 类意图：症状分析、药品查询、指南检索、检验解读、分诊建议、健康咨询、用药指导、疾病科普、挂号指引、报告解读。
- 简单任务直接路由；复杂任务先 Planning，再串行执行子任务。
- 子 Agent 内部是真实 ReAct：模型生成 Function Call，MCP 执行工具，工具结果作为 Observation 回填，模型再决定继续调用或结束。
- 主模型使用 SiliconFlow `deepseek-ai/DeepSeek-V4-Flash`。
- 本地意图模型使用 LM Studio `qwen/qwen3.5-9b`，OpenAI 兼容地址为 `http://127.0.0.1:1234/v1`。
- FastAPI、JWT、SSE、患者 `patient_id` 隔离、审计日志、MySQL 数据模型、30 分钟 TTL 短期记忆。
- Streamlit 患者/医生工作台，通过 FastAPI 调用业务，不直接绕过 API 操作 Agent。
- 外部模型调用前，对当前问题、历史消息和嵌套工具结果递归脱敏。
- 已完成的自动化验证：6 个后端测试通过；FastMCP 远程发现 14 个工具；A2A Agent Card 可发现；LM Studio 本地意图分类实际调用成功；Streamlit 桌面/移动端渲染和身份切换通过。

### 已有真实代码，但需要甲方资源才能完成联调

- HIS、LIS、EMR、药品库和指南库的 URL、应用 Key、签名 Secret。
- 医院接口的最终字段映射和错误码映射。
- 患者短信验证码服务。
- 医生访问患者档案的 HIS 动态授权关系。
- MySQL 生产实例和正式数据治理策略。

### 已验证结果与仍需明确的口径

- 本地 Qwen3.5-9B 在 33 条工程回归集上的意图严格完全匹配率为 93.94%，micro-F1 为 93.15%，P95 为 2.978 秒，0 个调用错误。该数据集不是医生标注的临床基准，不能外推成真实医疗场景准确率。
- FastAPI HTTP 入口已完成 300 用户、20 秒 Locust 压测：8,107 次请求、0 失败、约 401 RPS、P95 790 ms。测试目标是 `/api/health`，不能表述为完整模型/A2A/MCP 链路支持 300 并发。
- 最终汇总已使用模型原生 `stream=True` 并逐 token 转发 SSE；首 token 仍要等待前置意图识别和 ReAct 工具调用，急症硬规则走确定性输出。
- 已实现 Redis 会话/checkpoint 适配、A2A/医院接口熔断、Prometheus 指标、脱敏 LangSmith trace、Grafana 配置和 K8s stable/canary 清单；是否达到生产可用仍需在目标 Redis、监控平台和 K8s 集群中做故障与回滚验证。
- 不能说所有医院接口已联调；未配置时系统会明确失败，不用模拟医学数据兜底。

---

# 二、面试开场话术

## 40 秒自我介绍中的项目段落

我最近做的是 MedAgent AI 智能医疗多 Agent 项目。系统采用 FastAPI、A2A 和 MCP 三层架构，把症状、药品、指南检验三个业务域拆成独立 Agent。简单问题直接路由，复杂问题先生成受约束计划，再通过 ReAct 循环调用医院工具。主模型使用 SiliconFlow 的 DeepSeek，本地 LM Studio 模型负责 10 类意图识别，患者原始信息和工具结果在出院前会递归脱敏。项目里我重点处理了工具权限、患者隔离、超时重试、审计、SSE 和模型服务兼容问题，而不是只做一个能对话的 Demo。

## 60 秒项目架构介绍

系统入口是 Streamlit 患者/医生工作台，但所有业务必须经过 FastAPI。FastAPI 负责 JWT、患者隔离、会话和审计。输入首先经过紧急症状硬规则，再调用本地 Qwen3.5-9B 做结构化意图识别。简单意图直接选择一个子 Agent，复杂意图由 Planning Agent 生成只允许三个 Agent 的短计划。主协调器通过 A2A 把任务发给 SymptomAgent、DrugAgent 或 GuideAgent。子 Agent 不直接访问医院接口，而是先让 DeepSeek 生成 Function Call，再通过 FastMCP 调用白名单工具，把 Observation 回填给模型，最多 6 轮。MCP 层统一做 HMAC、超时和最多 3 次指数退避。最终结果通过 SSE 返回并写入 MySQL，同时保留审计记录。

## 一句话版本

这是一个“本地意图识别 + 云端脱敏推理 + A2A 分工 + MCP 受控工具调用”的医疗 Agent 系统。

---

# 三、架构与链路

```text
Streamlit 患者/医生端
        │ HTTP + JWT + SSE
        ▼
FastAPI API Server
  ├─ 急症硬规则
  ├─ patient_id 隔离
  ├─ TTL 对话记忆
  ├─ MySQL / 审计
  └─ 本地 LM Studio 意图识别
        │
        ├─ 简单任务：直接路由
        └─ 复杂任务：Planning Agent
                     │ A2A
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  SymptomAgent   DrugAgent    GuideAgent
        └────────────┼────────────┘
                     │ ReAct + MCP Client
                     ▼
                FastMCP Server
        ┌────────────┴─────────────┐
        ▼                          ▼
  HIS/LIS/EMR/药品/指南 API      MySQL 内部工具
```

## 简单任务怎么走

以“阿莫西林有哪些常见不良反应”为例：

1. 本地模型识别为“药品查询”。
2. 路由到 DrugAgent，不创建多步骤计划。
3. DrugAgent 的模型选择 `query_drug_info`。
4. FastMCP 对药品库发起 HMAC 签名请求。
5. 工具结果回填给模型，模型基于真实说明书生成答复。
6. FastAPI 保存问诊记录和审计信息，再通过 SSE 输出。

## 复杂任务怎么走

以“我头痛、恶心，正在吃布洛芬，还想知道挂什么科”为例：

1. 意图模型可能返回症状分析、用药指导、挂号指引，标记为复杂任务。
2. Planning Agent 只能从三个注册 Agent 中生成短计划。
3. 按依赖串行调用：症状分析 → 药物禁忌/相互作用 → 科室建议。
4. 每个子 Agent 内部通过 ReAct 选择自己的 MCP 工具。
5. Planning Agent 对结构化子结果做最终汇总，不允许越过工具编造医学事实。

为什么使用串行而不是全部并行？因为患者病史或症状标准化结果可能是下一步药物检查的输入。没有依赖的检索可以进一步并行，但当前文档场景强调 2～4 步短链，优先保证可解释和可排障。

---

# 四、模型的 ReAct 行为

## 标准回答

ReAct 是 Reasoning and Acting。模型先根据用户任务、上下文和工具 schema 生成 Action；服务端校验参数、注入患者权限范围并执行工具，得到 Observation；随后把 Observation 放回消息上下文，让模型继续决定下一步，直到生成最终答案或达到最大轮次。

当前实现的关键循环可以概括为：

```python
for _ in range(max_iterations):
    response = model(messages, tools=allowed_tools)
    if not response.tool_calls:
        return final_answer
    for tool_call in response.tool_calls:
        args = validate_and_inject_patient_scope(tool_call.arguments)
        observation = mcp_client.call_tool(tool_call.name, args)
        messages.append(observation)
raise MaxIterationsError
```

## 为什么最大 6 轮

项目的大多数任务是 1～4 个工具步骤。设置 6 轮给参数修正和补充查询留出余量，同时限制死循环、延迟和费用。生产环境还应增加总 deadline、重复 action+input 检测和 token 预算。目前代码已有最大轮次，但重复动作检测和总任务 deadline 仍属于待补强项。

## 为什么不直接使用一个大 Prompt

一个 Agent 挂 14 个工具虽然能跑，但工具候选多、业务权限宽、prompt 变长，错误调用更难定位。现在每个子 Agent 只看到自己的白名单工具：症状 Agent 不能随意调用药品替代工具，药品 Agent 也不能直接保存病历，减少误调用和权限扩散。

---

# 五、三个 Agent 的真实边界

## SymptomAgent

- 负责：症状分析、分诊建议、健康咨询、挂号指引。
- 可用工具：`analyze_symptoms`、`suggest_department`、`get_disease_info`、`load_patient_history`、`save_patient_history`、`generate_referral`。
- 风险边界：只能给辅助方向和科室建议，不能确诊；转诊单状态是“待医生确认”。

## DrugAgent

- 负责：药品查询、用药指导。
- 可用工具：`query_drug_info`、`check_drug_interaction`、`check_contraindications`、`query_drug_alternatives`。
- 风险边界：不推荐处方药剂量；禁忌检查所需的过敏史、既往病史和当前用药由服务端注入，不能信任模型自己填写患者状态。

## GuideAgent

- 负责：指南检索、检验解读、疾病科普、报告解读。
- 可用工具：`search_guidelines`、`interpret_lab_results`、`get_treatment_protocol`、`save_medical_record`。
- 风险边界：指南结果要携带版本/日期；检验解读不是诊断。

## 为什么是三个而不是十个

10 类意图是用户语言层，Agent 是稳定业务能力层。把每个意图做成一个 Agent 会导致大量重复工具和 prompt；按症状、药品、指南检验三个领域划分，既覆盖 10 类意图，又保持边界稳定。

---

# 六、意图识别与路由

## 为什么本地识别

患者原始描述可能包含姓名、电话和病情，直接发外部模型会增加合规风险。本地模型先做意图识别和术语标准化，原始数据不离开本机；只有后续需要大模型规划时，才发送递归脱敏后的内容。

## 为什么最终用了 LM Studio Qwen3.5-9B，而不是现成 BERT

实际检查过本机模型：

- `med_rag` 的 BERT 只分 medical/general 两类。
- MedicalTriage 数据是 6 个科室分类，而且没有发现完成训练的对应 BERT 权重。
- TMFCode 的 10 分类 BERT 是新闻分类，标签是财经、体育等。

它们都不能直接承担本项目的 10 类医疗意图。强行复用只是在“模型能加载”，业务标签完全不一致。Qwen3.5-9B 能按 JSON Schema 输出多意图、复杂度和术语标准化，适合先完成本地链路。后续如果拿到医生标注的 10 类数据，可以微调小型 BERT/Qwen，再用离线评测决定是否替换。

## 实际遇到的兼容问题

第一次沿用云端参数 `response_format={"type":"json_object"}`，LM Studio 返回 HTTP 400，提示只接受 `json_schema` 或 `text`。修复方法是用 Pydantic 的 `IntentDecision.model_json_schema()` 生成严格 schema。

第二个问题是 Qwen3.5 默认开启 thinking，一次分类曾耗时 112.63 秒。仅在 prompt 写 `/no_think` 没有完全关闭推理。最后通过 OpenAI 兼容请求增加：

```python
extra_body={"reasoning_effort": "none"}
```

相同类型样例实际降到约 2.55 秒。这个结果仍高于文档中 900ms 的目标，因此不能声称已达标。进一步优化可换 0.5B～4B 分类模型、缩短 prompt、量化、缓存高频输入，或者用项目标注数据微调专用分类器。

## 意图错了怎么办

当前代码会校验输出意图是否属于注册的 10 类，非法值直接失败，不会拿去路由。进一步生产化应增加：置信度字段、Top-2 候选、低置信度澄清、槽位一致性校验、错误样本回流和离线混淆矩阵。不能无限让模型“反思”，最多做一次受约束修复。

---

# 七、A2A、MCP 和 Function Calling 的关系

## 面试标准回答

- A2A 解决 Agent 与 Agent 之间的任务通信。
- MCP 解决 Agent 与工具/资源之间的标准化连接。
- Function Calling 是模型按 JSON Schema 选择工具并生成参数的能力。
- ReAct 是模型反复“选择动作—观察结果—继续决策”的执行范式。

在本项目中，FastAPI 主协调器通过 A2A 找到子 Agent；子 Agent 调主模型得到 Function Call；再用 MCP Client 调 FastMCP；工具结果回到 ReAct 消息循环。

## A2A 通信载荷

当前发送内容包括：

```json
{
  "task": "标准化后的子任务",
  "patient_id": "由服务端授权上下文提供",
  "profile": {
    "allergies": [],
    "conditions": [],
    "medications": []
  },
  "history": []
}
```

子 Agent 返回：

```json
{
  "agent": "DrugAgent",
  "answer": "基于真实工具结果的回答",
  "trace": [
    {"agent": "DrugAgent", "tool": "query_drug_info", "status": "completed"}
  ]
}
```

生产版本还应增加 `task_id、trace_id、schema_version、deadline、retry_count、error_code`，并对 A2A 服务本身增加服务鉴权和 mTLS。

---

# 八、MCP 工具层

## 14 个工具怎么组织

症状域 3 个：症状分析、科室建议、疾病资料。

药品域 4 个：药品说明书、相互作用、禁忌症、替代药。

指南检验域 3 个：指南检索、检验解读、诊疗路径。

内部业务 4 个：保存病史、加载病史、保存问诊记录、生成转诊单。

## 为什么不能把医院 HTTP API 直接写进 Prompt

模型不应该负责签名、网络重试、权限和字段映射。MCP 层把这些工程问题统一收口，让 Agent 只看到稳定 schema。医院接口变化时主要修改适配层，而不是改所有 Agent prompt。

## HMAC 怎么做

请求体先序列化为稳定 JSON，使用医院 Secret 对 `timestamp + "." + body` 做 SHA-256 HMAC，发送 `X-App-Key、X-Timestamp、X-Signature`。时间戳可降低重放风险，但生产端还应校验允许时钟偏差、nonce 和签名版本。

## 工具失败怎么处理

- 未配置 URL：立即失败，不重试，因为这是配置错误。
- 参数、权限或业务校验错误：不应重试。
- HTTP 网络错误、医院临时失败：最多 3 次指数退避。
- 连续失败：当前返回明确错误，模型被提示不得编造；生产版应再接熔断器和告警。
- 写操作：内部保存操作具有相对稳定的 patient_id 范围，但生产版本仍应加入 request_id/幂等键，防止请求重放造成重复记录。

---

# 九、数据、安全与合规

## patient_id 隔离怎么保证

JWT 解码后得到服务端信任的 `patient_id`。查询档案和问诊记录都使用这个 ID。即使模型在 Function Call 里生成另一个 patient_id，服务端也会覆盖为授权 ID。不能依赖 prompt 告诉模型“不要访问别人数据”，隔离必须由代码和数据库查询条件实现。

## 脱敏做了什么

进入 SiliconFlow 前会处理三类内容：当前输入、历史消息、嵌套工具结果。除了替换 profile 中已知的姓名和 ID，还用规则处理手机号和身份证号；字典中的 `patient_id、user_id、username、name、phone、id_card、address` 等敏感键递归替换。

局限是规则脱敏不可能覆盖所有自由文本实体。生产环境应加入医疗 DLP/NER、字段分级、出站网关和抽样审计。

## 为什么紧急症状用规则而不是模型

“胸痛、呼吸困难、大量出血、意识模糊、抽搐、晕厥”等高风险信号必须优先处理。确定性规则延迟低、结果稳定，不受模型服务故障影响。它不是诊断规则，只负责提示 120/急诊。生产环境需要由临床安全团队维护更完整的红旗症状表，并结合年龄、孕产、儿童等人群规则。

## 密钥怎么管理

所有密钥放 `.env`，并由 `.gitignore` 排除。实际开发中出现过截图暴露 API Key 的风险，正确处理是立即在供应商控制台轮换，而不是只删除截图。生产环境不应长期使用 `.env` 明文文件，应使用 Vault、KMS 或容器 Secret，并设置最小权限和定期轮换。

---

# 十、记忆、数据库与状态

## 短期记忆

会话 key 是 `patient_id:conversation_id`，每个会话最多保留 20 条消息。配置 `REDIS_URL` 后使用 Redis List、TTL 和事务 pipeline，支持多 API 实例共享；未配置时才使用单进程 `TTLCache` 作为本地开发模式。生产还需配置 Redis ACL/TLS、备份、容量告警和按患者删除能力。

## 长期数据

MySQL/SQLAlchemy 保存用户、患者档案、问诊记录、审计日志和转诊单。当前启动时使用 `create_all` 建表，适合开发；生产必须使用 Alembic 版本化迁移、备份、主从和数据保留策略。

## 为什么不把所有历史直接塞给模型

长历史会增加成本、延迟和污染风险。当前限制最近 20 条；进一步可以做任务摘要、按需检索和工具结果引用。医疗历史还需要区分患者陈述、医生确认和系统来源，不能把所有文本当同等可信事实。

---

# 十一、FastAPI、SSE 与 Streamlit

## 为什么 Streamlit 不直接调用 Agent Python 对象

界面层直接调用 Agent 会绕过 JWT、patient_id 隔离、审计和统一错误码，也不利于未来替换为小程序/H5。当前 Streamlit 只是 API 客户端，和正式患者端遵循同一接口。

## SSE 当前怎么实现

FastAPI 返回 `text/event-stream`，事件类型包括：

- `session`：conversation_id。
- `meta`：意图和 Agent。
- `trace`：已完成或失败的工具步骤。
- `card`：急诊等结构化卡片。
- `delta`：回答文本片段。
- `done`：科室和免责声明。

当前链路先由 A2A 子 Agent 完成工具调用，再由 Planning Agent 使用模型原生 `stream=True` 汇总，模型 token 通过 SSE `delta` 事件直接转发。首个 token 仍需等待意图识别、ReAct 工具调用完成；急症硬规则不调用模型，而是直接发送确定性的安全提示。

## 用户刷新怎么恢复

运行中任务会按 `patient_id:conversation_id` 保存 started、agents_completed、completed 或 failed 阶段 checkpoint；配置 Redis 后多实例共享。当前仍未保存每个 token 的事件序号，因此页面刷新后可以判断任务阶段，但不能从 `Last-Event-ID` 精确续传未完成文本；完整恢复需要事件日志或消息队列。

---

# 十二、实际踩坑与排障话术

## 1. ReAct 重复调用相同工具

现象：模型在 Observation 信息不足或工具报错时，可能连续生成相同的 Action 和参数，导致无效循环、延迟和费用增加。

根因：模型没有得到可继续推理的结构化错误，或者缺少对重复动作的服务端约束；仅靠提示词要求“不要重复”不可靠。

处理：设置最大 6 轮；工具异常以结构化 Observation 回填，明确禁止编造结果；服务端负责患者参数注入和工具白名单。进一步应记录 `tool_name + canonical_arguments` 指纹，连续重复时终止任务并返回可解释错误。

复盘：ReAct 的可靠性来自“模型决策 + 服务端状态机约束”，不能只依赖模型自觉停止。

## 2. Docker Compose 提示找不到配置

现象：在 `D:\python_envs` 执行 `docker compose up -d mysql`，提示 `no configuration file provided`。

根因：Compose 默认从当前目录查找 compose 文件。

修复：切换到项目根目录，或者显式使用 `-f` 和 `--env-file`。

排障顺序：`Get-Location` → `Test-Path docker-compose.yml` → `docker compose config` → `docker compose ps` → 容器日志。

## 3. LM Studio JSON 格式不兼容

现象：HTTP 400，提示 `response_format.type` 不接受 `json_object`。

修复：改为 `json_schema`，schema 直接来自 Pydantic 模型；返回后再次做 Pydantic 校验。模型输出只是候选数据，不能跳过服务端验证。

## 4. 本地模型意图识别异常慢

现象：Qwen3.5-9B 一次分类超过 100 秒。

根因：模型默认进行长 thinking；简单分类不需要长推理。

修复：请求中设置 `reasoning_effort=none`，并限制最大输出 token。测试样例降到约 2.55 秒。

继续优化：小模型、专用微调、缓存、短 prompt、量化、GPU 层配置和批处理。不能只把超时从 10 秒改成 120 秒，这只是掩盖问题。

## 5. FastMCP 在受限环境启动失败

现象：启动时尝试写用户目录下的版本缓存，权限被拒绝。

修复：关闭非业务必需的启动 banner，MCP HTTP 服务正常启动并远程发现 14 个工具。

经验：区分框架辅助行为和业务行为。版本提示失败不应阻断医疗工具服务。

## 6. 医院接口没有提供怎么办

错误做法：返回写死的药品或指南数据，让 Demo 看起来能跑。

当前做法：配置为空就返回明确错误，MCP 把失败 Observation 交给 Agent，并要求不得编造。这样 UI 可能显示“服务不可用”，但不会把假数据包装成医疗事实。

交付做法：向甲方索要 OpenAPI、测试环境、签名规则、字段字典、错误码、QPS 和脱敏要求；先做契约测试，再做联调。

## 7. 端口已占用

排查：`netstat -ano | Select-String ':8000'` 找 PID，再核实进程路径和启动时间。不要看到端口占用就盲目杀所有 Python 进程。

工程改进：启动脚本应在拉起进程前检查端口，并输出占用进程；生产用容器编排和健康检查。

## 8. A2A 子 Agent 返回错误字符串

当前 A2A 服务会把异常包装成带 `error、answer、trace` 的 JSON。主协调器还应进一步把“协议成功但业务失败”与真正成功区分开，否则只检查有没有 `answer` 可能误判。

改进：统一响应 schema，增加 `success、error_code、retryable、partial_result`，主协调器按字段决定重试、降级或停止。

## 9. 模型生成不存在的工具名

当前做法：只把该 Agent 的 FastMCP 白名单 schema发给模型，显著减少此类问题。仍需在执行前校验工具是否属于白名单；不要用字符串反射调用任意 Python 函数。

## 10. 重试导致重复写数据

当前 HTTP 重试主要用于外部读取接口，但转诊属于写操作。生产环境必须给转诊请求附带幂等键，并让 HIS 返回同一业务结果。不能单纯依赖“模型通常不会再调一次”。

---

# 十三、失败处理矩阵

| 失败类型 | 是否重试 | 当前处理 | 生产改进 |
|---|---:|---|---|
| 缺少配置 | 否 | 启动检查或明确报错 | 配置中心、部署门禁 |
| 参数/schema 错误 | 否/最多修复一次 | Pydantic/工具 schema 拦截 | 统一错误码和坏样本回流 |
| JWT 无效 | 否 | 401 | 刷新令牌、撤销列表 |
| 医院 API 超时 | 是 | 最多 3 次指数退避 | 熔断、隔离舱、备用数据源 |
| 医院业务拒绝 | 否 | 返回失败 Observation | 展示业务原因或转人工 |
| LM Studio 未启动 | 可短暂重试 | 意图服务不可用 | 健康检查、自动拉起、备用本地模型 |
| SiliconFlow 不可用 | 有限重试 | Agent 失败 | 模型网关、备用模型、预算控制 |
| MCP 不可用 | 有限重试 | 子 Agent 失败 | 服务发现、熔断、告警 |
| A2A 返回非法 JSON | 否/修复一次 | 主流程失败 | 严格响应 schema、版本协商 |
| ReAct 超过 6 轮 | 否 | 明确失败 | 重复动作检测、总 deadline |
| SSE 连接断开 | 当前不可恢复 | 用户重试 | task_id + Last-Event-ID |
| 数据库失败 | 谨慎 | 请求失败 | 事务、连接池、读写分离、告警 |

---

# 十四、高频八股题与项目化回答

## 为什么不用单 Agent

不是因为多 Agent 更高级，而是三个领域的工具、风险和 prompt 不同。单 Agent 同时拿 14 个工具会增大错误选择和越权范围。简单任务仍只调用一个子 Agent，避免为了“多 Agent”而多 Agent。

## 为什么不用 LangGraph

当前任务链通常 2～4 步，主要是简单路由或短链串行 ReAct。手写受约束循环更轻、更容易看清 MCP 和 A2A 边界。若未来出现长流程审批、人工节点、动态分支和断点恢复，再引入 LangGraph。选择标准是状态复杂度，不是框架热度。

## 为什么使用 A2A

A2A 让三个 Agent 能独立部署和发布 Agent Card，主服务通过协议调用，而不是 Python 内部函数硬耦合。代价是多一次网络跳转、序列化和服务治理，因此规模很小时直接函数调用更简单。

## 为什么使用 MCP

MCP 提供工具发现、schema 和统一调用方式，把医院 API 的签名、重试和映射封装在工具层。它不能替代权限系统；patient_id 隔离仍由服务端代码保证。

## Function Calling 和 MCP 是一回事吗

不是。Function Calling 发生在模型输出层，决定“调用什么、参数是什么”；MCP 是应用连接工具服务的协议，负责发现和执行。模型完全可以 Function Call 一个非 MCP 函数，也可以由确定性代码调用 MCP。

## Planning 和 ReAct 有什么区别

Planning 先给跨 Agent 的宏观步骤，ReAct 在每个步骤内根据 Observation 决定具体工具和下一步。简单任务跳过 Planning，避免多一次模型调用。

## 结构化输出为什么仍要校验

JSON Schema 能约束格式，但不能保证业务正确。例如合法 JSON 中的意图仍可能不存在、patient_id 仍可能越权、药名仍可能错误。因此还要做白名单、权限、枚举和业务规则校验。

## 温度为什么设置低

意图分类和 Planning 要稳定复现，温度使用 0 或很低；最终自然语言汇总可以略高，但医疗场景也不宜追求创意。低温度不能消除幻觉，事实仍必须来自工具。

## 审计日志记录什么

当前记录 profile 更新、Agent 响应和内部病史保存等动作，包含 patient_id、动作、必要详情和时间。生产日志不能记录完整密钥、身份证或未经脱敏的敏感正文；还要增加 trace_id、操作者、来源 IP/终端和结果码。

## 多 Agent 结果冲突怎么办

先比较数据源权威性和版本。例如药品说明书优先于模型常识，最新医院指南优先于旧缓存。关键事实冲突时展示差异或转医生确认，不能让汇总模型随机选一个。

## 如何防 Prompt Injection

患者输入只作为数据，不作为系统指令；工具白名单和权限在服务端；外部工具返回也视为不可信数据；不能让工具结果要求模型调用未授权接口。生产还需对检索文档做来源标记、指令隔离和输出策略检查。

---

# 十五、评测与压测应该怎么做

## 离线意图评测

每类至少准备真实匿名样本、口语化样本、模糊样本和多意图样本。统计 Accuracy、Macro-F1、每类 Recall、混淆矩阵和低置信度覆盖率。医疗类别不均衡时不能只看 Accuracy。

## 工具评测

- 工具选择正确率。
- 参数完整率和类型正确率。
- patient_id 越权拦截率。
- 外部接口技术成功率与业务成功率。
- 重试后成功率、P95/P99 延迟。
- 写操作幂等成功率。

## 端到端评测

- 简单/复杂任务完成率。
- 无依据回答率和医疗高风险错误率。
- 急诊红旗拦截率与误报率。
- TTFT、总响应时间、SSE 中断率。
- 人工介入率和医生采纳率。

## 300 并发怎么压

不能只压 Streamlit 页面。本项目先对 FastAPI `/api/health` 完成了 300 用户入口基线：20 秒、8,107 次请求、0 失败、约 401 RPS、平均 459 ms、P95 790 ms、P99 2.4 秒。这个结果只验证 HTTP 入口，不代表模型吞吐。下一步应在隔离测试环境分层压登录、问诊 SSE、A2A 和 MCP，并分别统计首 token、完整响应、工具成功率、数据库池和 GPU/模型配额；禁止直接向生产医院接口施压。

---

# 十六、代码审查中仍需补强的点

1. 最终汇总已原生 token streaming，但 A2A 子 Agent 工具阶段仍是请求完成后返回；要进一步降低可见等待，需要把 A2A 中间事件也流式上送。
2. Redis 会话和阶段 checkpoint 已实现；还需补事件序号、断线续传、Redis ACL/TLS 与故障切换演练。
3. `create_all` 应替换成 Alembic migration。
4. A2A 响应需要正式 `success/error_code/retryable` schema。
5. 熔断器和 Prometheus 指标已加入；外部 HTTP 仍需连接池复用、分依赖超时预算和跨实例熔断状态。
6. 转诊等写操作需要幂等键和事务边界。
7. 医生与患者当前是静态绑定，生产应由 HIS 动态授权。
8. 已有会话阶段 checkpoint；长任务还需独立 task_id、幂等恢复和事件日志。
9. 已有 33 条工程回归集和可重复脚本；仍需扩大难例、对抗样本，并由临床人员独立标注。
10. Prometheus 与脱敏 LangSmith 已接入；还需告警规则、Grafana 仪表盘、token 成本和线上 SLO 验证。
11. 已提供 Dockerfile、Compose 监控服务与 K8s 10% canary 清单；目标集群部署、回滚和容量验证尚未执行。
12. 需要补充真实医院契约测试、故障注入和安全测试。

面试中主动承认这些边界不会减分，关键是说明为什么当前阶段这样做、风险是什么、下一步如何演进。

---

# 十七、面试官连续追问示例

## 问：医院 API 一直超时，你重试三次不是让系统更慢吗

答：是，所以重试只适合瞬时错误，并且要受总 deadline 限制。若单次超时 10 秒再重试三次，最坏情况不可接受。生产上会给不同工具设置独立超时，对连接错误快速退避，对关键依赖加熔断；达到截止时间后返回部分结果或明确不可用，不阻塞整个请求。

## 问：主模型已经很强，为什么还要本地意图模型

答：不是只看准确率。本地模型可以让原始患者文本先留在院内，同时降低简单路由的外部调用成本。代价是要维护本地模型和评测集。如果本地模型低置信，再对脱敏内容调用主模型复核，而不是所有请求都出网。

## 问：模型把 patient_id 改了怎么办

答：模型参数不可信。服务端从 JWT 获取授权 patient_id，并覆盖工具参数。数据库查询同样带 patient_id 条件。安全不能靠 prompt。

## 问：模型调用错工具怎么办

答：先通过 Agent 工具白名单缩小候选，再用 schema 校验。执行结果若与意图或槽位明显不一致，可以在预算内重新路由一次；连续错误进入失败而不是无限自省。错误样本进入回归集。

## 问：为什么不在工具失败时让模型凭知识回答

答：医疗事实需要权威来源。工具失败时继续用模型记忆回答，会把“服务不可用”变成“没有依据但很流畅的答案”，风险更大。可以回答通用安全提示和就医建议，但不能伪造药品、指南或患者数据。

## 问：三个 Agent 都是同一个模型，真的算多 Agent 吗

答：Agent 的区别不只在底座模型，而在角色、工具权限、系统约束、输入输出和部署端点。三个 Agent 即使共用模型，也有独立 Agent Card、工具白名单和职责边界。若只是换三个名字但工具和 prompt 一样，那不算有效拆分。

## 问：SSE 中途输出了错误内容怎么办

答：高风险医疗内容不应该直接流出未经审查的内部推理。当前项目是在完整结果形成后再分片，因此牺牲 TTFT 换可控性。真流式版本需要先流进服务端缓冲和安全检查，再对外发送可展示段；已发送错误只能追加更正，不能静默覆盖。

## 问：Streamlit 能用于生产患者端吗

答：当前 Streamlit 是工程验证和院内工作台，优点是 Python 团队能快速打通真实 API；正式患者端更适合小程序/H5。因为界面始终通过 FastAPI，所以替换前端不会重写 Agent 和数据层。

---

# 十八、启动、演示与故障定位

## 启动顺序

1. LM Studio 加载 `qwen/qwen3.5-9b`，本地服务监听 1234。
2. 在项目根目录启动 MySQL：`docker compose up -d mysql`。
3. 运行 `python scripts/check_config.py`。
4. 运行 `python scripts/run_all.py`。
5. 访问 Streamlit `http://127.0.0.1:8501`；FastAPI 文档为 `http://127.0.0.1:8000/docs`。

## 演示前检查

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
Invoke-RestMethod http://127.0.0.1:8000/api/health
docker compose ps
netstat -ano | Select-String ':8000|:8002|:8011|:8012|:8013|:8501'
```

## 演示失败时不要怎么做

- 不要临时写死医学答案。
- 不要把 `.env` 或 API Key 投屏。
- 不要杀掉所有 Python 进程。
- 不要无限提高 timeout。
- 不要把外部接口失败说成模型能力不足。

## 推荐排障顺序

配置检查 → 端口与进程 → `/api/health` → LM Studio `/v1/models` → MCP 工具发现 → A2A Agent Card → 单工具契约测试 → 完整问诊链路 → 数据库与审计记录。

---

# 十九、项目总结话术

这个项目的重点不是做一个会聊天的医疗机器人，而是把模型放进受控工程链路：本地模型负责隐私敏感的意图识别，主模型只接触脱敏数据；A2A 负责三个业务 Agent 的边界，MCP 负责 14 个工具的标准化接入；JWT、patient_id 和数据库条件保证数据隔离；规则处理急诊红线，工具失败时宁可明确不可用也不生成假医学结果。当前版本已经打通真实协议和模型调用，但医院接口联调、真 token 流式、分布式状态和生产观测仍需要真实环境继续完成。

这段话的核心是：清楚说出已经做了什么，也清楚知道离生产系统还差什么。
