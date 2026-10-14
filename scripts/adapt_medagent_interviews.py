"""Build a source-indexed ToB interview library from every supplied document.

Originals are preserved. Each primary question and follow-up has a stable source
location, a project-specific answer, a design boundary and code references.
"""

from pathlib import Path
import argparse
import hashlib
import json
import re
import os
import sys
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.medagent_terminology import annotate_markdown, annotate_text
DEST = ROOT / "docs" / "MedAgent_逐题适配"
PRESERVED = ROOT / "artifacts" / "medagent_upgrade_20261013" / "原始资料"

# Implementation cards are shared only where the same mechanism is relevant.
# The actual question's source explanation is retained separately as a design
# explanation, so theoretical knowledge is not mistaken for implemented code.
CARDS = {
"overview": (
    "我做的是面向院内医生、药师和管理人员的临床辅助工作台。FastAPI 入口核对员工与患者权限，主协调器检查安全、范围、意图和计划，再交给症状、药品、指南三个专科，通过 MCP 查询真实数据，最后保存咨询和审计后发送完成事件。最终诊疗由医生复核。",
    "设计上优先固定授权和执行边界，模型只在范围内做分类、规划、工具选择和解释。新能力必须有明确院内使用人、数据来源、权限与验收标准；患者挂号、预约、缴费、退费均不接入。",
    "backend/main.py,backend/agents.py,backend/access.py"),
"harness": (
    "我把 Agent 的运行约束落在服务端：任务输入结构、专科和工具白名单、患者授权、模型与工具调用上限、超时熔断、重复调用阻断、工具依据覆盖和持久化完成确认。Prompt 描述职责，代码决定一次动作能否执行。",
    "进一步的长任务运行环境需要统一任务预算、持久执行队列、节点状态和验收器。框架能提供组件，业务权限和副作用状态仍由应用负责，不能仅靠装一个框架完成生产治理。",
    "backend/agents.py,backend/mcp_security.py,backend/resilience.py"),
"roles": (
    "项目是一个主协调器加三个临床专科：SymptomAgent 做症状辅助评估、风险提示、疾病信息及科室建议，不创建转诊草稿；DrugAgent 查药品和审核用药；GuideAgent 处理指南、检验、报告、随访与临床知识。医生在独立表单确认患者、科室、原因后，由 ReferralDraftService 经受限 MCP 调用创建转诊草稿；它是业务服务，不是第四个智能体。Planning、SafetyGuard、DomainGuard 是协调流程中的职责模块，没有另建检索、通知或独立审查服务。",
    "按业务目标、可信数据和工具权限划分，而不是按每个 API 拆 Agent。增加角色前，在同一任务集上比较完成率、交接损失、耗时与成本；不能假设多角色天然准确。",
    "backend/agents.py,backend/a2a_server.py"),
"intent": (
    "十类院内意图由正则、本地 BERT Sigmoid 多标签和 BGE-M3 三路都识别，再统一融合。BERT 当前逐类阈值 0.5，向量默认 0.78，BERT 边界范围 0.08。分歧、否定、边界、空候选或通道不可用才触发 LLM 复核；不是三路必须完全一致，也不是按分数投票。",
    "阈值通过独立验证集选择，测试和挑战集只评估冻结结果。医疗急症由独立安全入口处理；意图置信分数不代表患者低风险。LLM 复核失败停止分派，不悄悄回到冲突候选。",
    "backend/intent_fusion.py,backend/agents.py,backend/config.py"),
"intent_error": (
    "我按识别前、执行中和执行后处理：前三路发现分歧时二次识别；执行前校验关键参数和权限；执行后检查每个意图是否有对应的成功工具依据，例如病史读取不能代替药品说明书查询。只读任务覆盖不匹配时最多复核并重路由一次；批准写动作或出现写调用时禁止自动重跑。",
    "工作台已能提交意图纠错，服务端重新核验患者权限和原请求所属员工，保存为待临床复核记录，不自动作为训练标签。完整闭环还需要结论与目标的语义核验、复核工单及标注发布流程。工具成功不是医学正确证明；写入不明先查状态、核对 HIS，跨系统补偿取决于医院接口。",
    "backend/evidence.py,backend/agents.py,backend/mcp_security.py,backend/main.py"),
"slots": (
    "可信身份和患者档案由服务端读取。业务参数由模型按工具 schema 提出，执行前代码检查空值、占位值、列表和检验内容；药名必须能在当前任务原始输入或可信用药档案中找到依据，相互作用检查至少两种药。参数记录 raw_value、normalized_value、source、turn_id、evidence_span、schema_version 和 validation_status。",
    "现在没有训练过医疗 NER，也没有声称语义校验能识别全部错槽。扩展方案是为十类意图注册必填字段，规则抽固定格式，LLM 只提取有原文跨度的实体，再用药品目录校正通用名、单位和枚举。缺关键字段询问医生或药师，临床数值和药名不凭默认值补齐。",
    "backend/clinical_slots.py,backend/schemas.py,backend/agents.py"),
"turns": (
    "每轮有 request_id，任务还有 task_id 和 plan_version。员工可选新任务、补充或更正；自动模式只续接待澄清问题，明确新请求切新任务并隔离旧历史。完整更正递增版本、丢弃旧业务输入；只有指代或局部含糊更正时先追问。A2A 返回必须匹配签名委托中的任务、计划版本和步骤，旧结果不能写回新计划。",
    "当前补充仍是有界的原问题与本轮信息组合，并非完整按字段合并。设计上把槽位按任务保存来源和时效，同任务只继承仍适用的字段，明确新值只覆盖对应字段；换患者或换任务不继承业务槽，身份始终重新从可信会话核验。",
    "backend/task_context.py,backend/main.py,backend/a2a_server.py"),
"plan": (
    "简单明确任务直接匹配专科；多意图或复杂任务由 Planner 生成最多三步计划。服务端检查专科白名单、去重、意图要求的专科覆盖以及依赖只能指向已列出的前序步骤。结构或依赖非法时改用确定性短链，不能让模型添加未注册服务。",
    "计划能表达依赖与独立查询，当前没有无限自主改计划：只读依据缺失最多一次重新识别，普通工具故障不等于意图错误。若扩展更多节点，增加节点超时、验收条件、输入引用和版本化节点状态，同时限制重规划预算。",
    "backend/agents.py,backend/evidence.py"),
"communication": (
    "主协调器用 python-a2a 向三个专科发送 task、patient_id、有界历史和原始任务。短期签名委托绑定员工、患者、请求、目标专科和工具范围，并用摘要绑定原始输入。专科重读患者档案，返回回答、调用轨迹、脱敏工具 Observation 以及 task_id、plan_version、step_id；主协调器检查身份、格式与版本。",
    "不用 A2A 也可通过进程内异步函数、自定义 HTTP 或消息队列分派，核心仍是任务契约、授权、超时和业务状态。当前统一 deadline_at 通过签名委托传到专科和 MCP，新调用只用剩余预算；服务是主从式，专科不能任意互相调用。队列、回调和长期任务是扩展项。",
    "backend/a2a_server.py,backend/service_auth.py,backend/agents.py"),
"parallel": (
    "带依赖的计划按依赖调度，独立只读步骤用 asyncio.gather 同批执行；批准了写动作时整个计划保持串行。前序的脱敏工具依据在预算内传给依赖节点，过长时明确提示重新查询，不截断后冒充完整资料。同批出错仍等待在途调用结束，再传播失败，避免解锁后留后台动作。",
    "并发只解决互不依赖的 IO 等待，不能把串行数据依赖打散。CPU 推理要在线程或独立模型服务中运行，并控制 GPU 与连接池并发；后台长任务应采用持久队列和状态查询，不能把 async 函数当成可靠任务队列。",
    "backend/agents.py,backend/a2a_context.py"),
"async": (
    "当前院内工作台通过 SSE 持续展示同一请求的结果，服务代码使用 async，A2A 同步客户端在线程执行；近期状态在 Redis 或本地 TTL 中。没有已经部署的长期后台报告队列、邮件通知或断点输出重放服务。",
    "若做院内月度审核报告，入口登记员工与数据权限，创建持久 task_id 后入队；worker 保存节点结果并按 operation_id 去重，工作台通过授权状态接口或 SSE 查看进度。断线不等于任务取消；取消要先判断有没有已经提交的院内写操作。",
    "backend/main.py,backend/memory.py,backend/agents.py"),
"errors": (
    "把权限失败、参数缺失、依赖故障和写入未知分开。权限失败拒绝；缺信息先询问；安全只读医院查询对瞬时网络错误及 429/部分 5xx 最多三次指数退避，转诊写调用不自动重试；连续依赖失败进入熔断。专科或工具失败不会被包装成成功答案。",
    "备用模型、备用数据源或跳过非关键步骤必须有已验证契约，不能凭空说存在。只有确实可逆的本地事务能回滚；外部写入走查询、对账、补偿或责任医生复核。对于无法确认的临床问题，应给出失败原因和人工核对路径。",
    "backend/mcp_tools.py,backend/resilience.py,backend/mcp_security.py"),
"timeout": (
    "默认整轮 120 秒、模型 30 秒、医院接口单次 10 秒，专科模型与工具各最多六次。入口设置绝对 deadline_at，签名委托传到 A2A 与 MCP；新调用取阶段上限和剩余时间的较小值，过期任务不再执行工具。已有只读退避、超时和熔断。超时不证明已提交的医院写入未发生，未知结果先核对。",
    "上线按节点 p95/p99 与医院契约配置预算，并同步服务时钟；还需并发配额、准入控制和统一成本预算。取消只能结束本地等待，外部已提交动作能否取消取决于医院接口，不承诺自动回滚。",
    "backend/task_budget.py,backend/service_auth.py,backend/mcp_security.py,backend/resilience.py"),
"loops": (
    "专科由 LangChain create_agent 运行工具循环，模型和工具调用各有上限；同一专科本轮相同工具与参数被指纹去重。主计划最多三个不同专科，不允许 A 调 B、B 再调 A。修正意图也只有一次只读重路由预算。",
    "更长任务还应检测相同业务状态下的重复动作和证据没有增量，达到步数、时间或费用预算就停止。验收检查真实工具状态和目标覆盖，不能只让同一模型判断‘我已经成功’。",
    "backend/agents.py,backend/evidence.py"),
"state": (
    "全局层保存当前员工、患者、会话、请求与任务版本，局部层由各专科维护本轮消息和工具轨迹。会话缓存按员工关联会话与患者隔离，并用会话锁阻止同一会话同时执行。专科只返回结果，不能随意覆盖入口的授权上下文。",
    "独立档案编辑已要求先读取 revision，再以 expected_revision 保存，服务端核对内容快照并以 SQL 比较全部原字段后更新；冲突返回 409，重载并核对后再保存。会话锁和档案保护是两层机制。图框架的 Reducer 可以管理 state，但不替代数据库并发与写操作幂等。",
    "backend/memory.py,backend/task_context.py,backend/main.py"),
"checkpoint": (
    "现在保存 started、agents_completed、completed、failed 阶段以及任务标识、版本、缺失字段等近期快照。生产委托必须带任务、会话、版本、步骤和期限；MCP 必须借助 Redis 核对任务仍在执行且版本相符，缺配置或任务域拒绝。开发环境未配置 Redis 时使用本地 TTL，不提供跨进程围栏；这不能撤销已提交的医院写入。会话默认 1800 秒，checkpoint 默认一天，尚未实现节点持久恢复或 SSE 重放。",
    "节点恢复要持久保存计划版本、已完成步骤、输入摘要、工具依据引用和操作状态，恢复时先重新鉴权。只读证据核验时效后才能复用；写任务先核对 operation_id，结果未知绝不盲目再次执行。Checkpoint 不提供跨 HIS 的恰好一次保证。",
    "backend/memory.py,backend/main.py,backend/mcp_security.py"),
"context": (
    "可信患者档案来自数据库，近期对话与任务快照分开保存；工作台切换患者会清理上下文与未提交编辑。新任务隔离旧历史，待澄清任务只复用有界原问题和本轮补充，不把上轮助手猜测当临床事实。A2A 历史有条数、单条字节和总字节预算。",
    "进一步做 Context Builder：固定职责与授权先保留，当前目标和关键临床事实次之，然后按任务需要选工具证据，最后才是历史摘要。摘要保留来源、时间和原文引用，压缩策略要用多轮任务完成率和错误继承率验证。",
    "backend/a2a_context.py,backend/memory.py,backend/task_context.py"),
"compression": (
    "现有对话保留最近 20 条，A2A 历史单条最多 4 KiB、总预算 16 KiB。新加工具 Observation 有长度上限、内容摘要和 truncated 标记，过长前序证据不会作为完整原文传递。当前没有实现可靠的长期摘要器、历史向量召回和父子块文档压缩。",
    "医疗场景压缩必须保留药名、否定词、单位、参考范围、患者对象和版本来源。大文档放授权产物库，state 保存引用；对截断或摘要缺失的关键依据重新检索。用固定模型做原文与压缩对照，而不是拿模型升级掩盖压缩损失。",
    "backend/a2a_context.py,backend/evidence.py,backend/agents.py"),
"tools": (
    "模型通过 Function Calling 选择工具并生成结构化参数，LangChain 的 MCPAdapter 和 FastMCP 客户端通过 MCP 调用工具服务。FastMCP 注册 14 个工具，包括真实医院查询以及本地病史、辅助材料存储；服务端再次检查委托、员工状态、患者范围和写动作权限。两者在同一链路中使用。",
    "工具注册要同时维护参数、输出、错误、权限、读写属性和版本契约；新增工具先做契约测试和权限回归，再开放给对应专科。不能把工具描述中的 readOnlyHint 当真正的服务端授权。",
    "backend/mcp_tools.py,backend/mcp_security.py,backend/agents.py"),
"protocol": (
    "项目实际用的是 MCP 工具发现与调用以及 A2A 专科通信，没有部署 MCP Sampling、Elicitation、完整 Skill 加载器或协议长期任务回调。协议提供连接方式，医疗数据访问和操作确认仍由服务端代码执行。",
    "问到协议新增能力时先说明项目锁定的依赖和实际用法，再根据官方版本确认细节。Skill 可以封装院内审核步骤、说明与按需资源，但不能替代权限；协议升级需要旧客户端与新 schema 的兼容测试，不按‘最新’名词猜版本。",
    "backend/a2a_server.py,backend/mcp_tools.py,constraints.txt"),
"rag": (
    "MedAgent 目前通过 GuideAgent 的 search_guidelines、get_treatment_protocol 等工具调用医院知识接口，不是在这个仓库内完成切块、建向量库和 rerank。BGE-M3 在本项目用于意图原型匹配。简历里的 med_rag 是独立项目，不能把它的指标直接算作 MedAgent 结果。",
    "接入 RAG 时将授权后的医学检索封装为 GuideAgent 工具，返回原文片段、文档标识、页码、版本日期和检索状态；按员工和院内数据范围过滤后再召回。正常空结果可改写或询问，依赖故障停止生成，缺证据不让模型凭记忆替代。",
    "backend/mcp_tools.py,backend/agents.py"),
"output": (
    "专科返回辅助回答和脱敏的真实工具 Observation；协调器汇总时优先依据 Observation，并查看截断标记和调用状态。执行前后都限制权限和格式，每个意图检查对应工具覆盖。最终医学结论仍需要医生复核，当前没有独立临床审查模型证明结论正确。",
    "完整审查要核对结论逐条引用、药名、指标单位、指南版本和请求目标。来源冲突按数据类型与版本处理：当前患者事实看授权业务库，医学规范看有效指南；冲突无法消除就展示差异并要求医生确认，不能一律由用户最新输入覆盖权威事实。",
    "backend/evidence.py,backend/agents.py"),
"stream": (
    "FastAPI 通过 SSE 输出 session、meta、trace、card、delta 和 done。文字增量是展示状态；入口收到协调器完成后，还要保存咨询、审计、会话与快照，才发送最终 done。断流、工具失败或模型输出截断不会确认成功。",
    "刷新恢复需要持久事件序号和已保存输出，再按员工、患者与任务授权查询或重放。当前没有这套事件重放，因此不能说刷新会自动接着输出。优化首字延迟可以先显示阶段状态，同时实测全链路首字、总耗时和模型/工具耗时。",
    "backend/main.py,backend/agents.py,streamlit_app.py"),
"evaluation": (
    "我把路由、工具执行、端到端完成与临床结论质量分开评估。BERT 的 1340 条合成测试集合匹配率 100%，400 条合成挑战为 87%，Micro-F1 0.9461；多意图子集 250 条匹配率 97.2%。这些是不同任务分组，不能代替真实医院准确率。",
    "系统评测按单意图、多意图、否定、短句、长上下文、范围外、权限撤销和工具故障分层。定义 gold 输入、期望路由、参数、证据与允许副作用；固定模型、数据与预算再比较。临床结论由有依据的标注和医生抽检验证，模型裁判也要独立校准。",
    "evaluation/bert_multilabel_challenge_summary.json,tests/test_task_contracts.py,docs/bert-threshold-evaluation-2026-10-12.md"),
"cost": (
    "项目用本地路由减少不必要的云端意图复核，简单任务直接派专科；每轮限制最多三步专科以及模型、工具调用次数，并限制工具依据和历史长度。专科和汇总当前使用同一云端模型配置，未实测完整任务降本百分比。",
    "进一步按角色实测模型分级，统计每个成功院内任务的 Token、费用和耗时，并同时看漏检、错误路由与人工纠错量。缓存必须包含权限、患者对象、输入与数据版本，患者数据和临床实时状态不能跨用户复用。",
    "backend/config.py,backend/agents.py,backend/observability.py"),
"training": (
    "项目实际训练的是十类意图 BERT 多标签分类器，使用多热标签、BCEWithLogitsLoss 和 Sigmoid，训练/验证/测试为 3100/640/1340 条合成数据。独立验证集扫描统一与逐类阈值后，当前保留每类 0.5；没有在这个项目训练专科 LLM、RLHF 或 GRPO。",
    "若路由错，先看规则、训练覆盖、原型、阈值和否定边界；若生成错，先核对工具资料与上下文，再考虑 SFT 改稳定行为。实时知识用受控检索，不把医院事实靠微调固化；训练、调参和最终测试必须隔离。",
    "scripts/train_intent_bert.py,scripts/calibrate_intent_bert.py,docs/bert-threshold-evaluation-2026-10-12.md"),
"rl": (
    "这道是训练原理题：我的 MedAgent 没有运行强化学习训练，实际是本地 BERT 监督分类加云端模型调用。我会把 RL 方法作为后续工具选择策略的研究方案说明，不把文档中的算法当成已有项目成果。",
    "若用于院内工具选择，先用无真实写入的模拟环境和独立验收集，把任务完成、错误患者访问、错误写入和费用分别计分；高奖励不能代替临床目标完成。规则与权限不由奖励学习覆盖，训练出的策略仍要过现有执行守卫和人工复核。",
    "scripts/train_intent_bert.py,backend/mcp_security.py,backend/evidence.py"),
"inference": (
    "本地 BERT 和 BGE 使用已有 GPU/CPU 推理；云端生成由服务商提供。代码对同步本地推理和 A2A 客户端使用线程，不在入口协程中长时间阻塞；模型连接复用。没有把 vLLM、量化、连续批处理或云端 KV Cache 描述为本项目已部署能力。",
    "自建模型服务时才比较批处理、量化与 KV Cache，并测首字、p95、吞吐、显存和任务完成率。多进程可能重复加载权重，模型服务与 API 分离并限制并发；健康接口压测不能代表完整多 Agent 链路吞吐。",
    "backend/agents.py,backend/main.py,backend/config.py"),
"voice": (
    "当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。",
    "院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。",
    "streamlit_app.py,backend/main.py,backend/access.py"),
"security": (
    "权限在入口、A2A 专科和 MCP 工具执行点重复核验；数据库中的员工状态、角色和患者授权优先于旧令牌。专科只得到受限工具，患者 ID 服务端注入；原始任务摘要与签名委托绑定。外部文档和工具文字是数据，不允许改变授权、目标患者或写动作范围。",
    "Prompt 可以提示忽略注入，但真正防线是白名单、schema、可信身份、固定数据源、写入确认和审计。进一步对最终参数做审批绑定、限制外部域名和文件权限、加入注入回归；不将未测试的‘万能注入检测’写成已有能力。",
    "backend/service_auth.py,backend/mcp_security.py,backend/access.py,backend/agents.py"),
"writes": (
    "病史更新和辅助材料保存仍由聊天 approved_actions 批准动作范围。转诊草稿移到独立 POST /api/referral-drafts：医生填写患者、科室和原因并 confirmed=true 后，业务服务签发仅含 generate_referral 的限时委托，绑定 confirmed_arguments_sha256；MCP 再核对服务身份、最终参数摘要、医生角色和患者授权。工具层按员工＋请求标识＋工具建立唯一 operation_id；同键同参数完成结果可重放，冲突拒绝，pending/unknown 阻止重发。聊天不能批准或创建转诊草稿，也不声称完成完整转诊协同。",
    "参数级确认目前只覆盖独立转诊草稿动作；聊天病史和辅助材料保存仍是动作级确认，不能说所有写入都已逐参数审批。独立档案表单已有 expected_revision 条件更新及409冲突拒绝。转诊结果未知先查看操作状态并核对医院，不更换请求标识盲目重发；外部 HIS 的一致性仍需医院契约和人工核对。",
    "backend/referral_service.py,backend/mcp_security.py,backend/main.py,backend/schemas.py"),
"monitor": (
    "项目有 HTTP、A2A、MCP 调用指标和脱敏排障日志，可以按请求标识关联阶段。新增意图复核原因、候选分数与路线随成功咨询审计保存。HTTP 指标主要是响应建立，不应当作整个 SSE 任务耗时；完整长任务与依赖状态要另外统计。",
    "生产方案按员工/院内业务做脱敏 Trace，区分路由、工具、模型与保存失败，监控超时、错误、熔断与任务成功率，并配置告警、值班与反馈闭环。不能把代码里有 /metrics 说成医院已部署线上监控。",
    "backend/observability.py,backend/main.py,backend/agents.py"),
"delivery": (
    "我把交付验收拆成院内权限、安全边界、工具契约、路由覆盖、故障分支和真实医院接口联调。仓库有配置、部署和工程回归记录，说明已有实现可核对；真实医院试点、临床收益和全链路容量不能直接从合成评测推导。",
    "上线时明确医生/药师典型任务与责任人，先小范围灰度，再测成功率、复核耗时、人工纠错及院内流程接受度；准备模型/Prompt/工具联合回滚和数据恢复演练。范围变更先评估权限、状态和验收，不把新增患者服务混入 ToB 交付。",
    "docs/production-readiness.md,README.md,deploy/k8s/medagent-api.yaml"),
"coding": (
    "我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。",
    "Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。",
    "tests/test_task_contracts.py,tests/test_enterprise_boundaries.py,tests/test_audit_regressions.py"),
"personal": (
    "这类题不能由代码证明个人经历。我的项目表达围绕院内需求、具体负责模块和可展示交付物：意图路由、专科编排、工具权限、回归与文档；实际只参与修订的部分就说明参与了哪些改动。离职、薪资、团队分工与对客经历要按本人事实回答。",
    "如果被问客户交付，结合医生/药师用药审核流程讲需求澄清、原型确认、范围变更、验收样本和复核反馈；没有真实医院客户案例时，用工程方案或本地修复案例回答，不编造合同、线上事故、采购成果或任职年限。",
    "docs/MedAgent_AI_多Agent项目话术.md,README.md"),
}


def card_key(chapter: str, number: int, question: str) -> str:
    if chapter == "00": return {1:"context",2:"harness",3:"loops",4:"plan",5:"plan",6:"plan",7:"parallel",8:"output",9:"overview"}[number]
    if chapter == "01":
        return {1:"harness",2:"roles",3:"roles",4:"roles",5:"plan",6:"tools",17:"delivery",18:"intent_error",19:"evaluation",20:"coding",21:"personal"}.get(number, "personal")
    if chapter == "02": return {1:"slots",2:"training",3:"parallel",4:"intent_error",5:"overview",6:"async",7:"errors",8:"tools",9:"delivery",10:"voice",11:"rag",12:"delivery"}[number]
    if chapter == "03":
        return {1:"overview",2:"intent",3:"intent",4:"intent_error",5:"slots",6:"turns",7:"slots",8:"slots",9:"turns",10:"slots",11:"slots",12:"slots",13:"plan",14:"communication",15:"tools",16:"intent",17:"intent",18:"parallel",19:"errors",20:"cost"}[number]
    if chapter == "04":
        return {1:"roles",2:"roles",3:"communication",4:"state",5:"errors",6:"security",7:"parallel",8:"parallel",9:"async",10:"parallel",11:"async",12:"parallel",13:"parallel",14:"parallel",15:"plan",16:"plan",17:"plan",18:"plan",19:"intent_error",20:"plan",21:"loops",22:"plan",23:"communication",24:"communication",25:"plan",26:"communication",27:"monitor",28:"loops",29:"errors",30:"communication",31:"context",32:"context",33:"harness",34:"harness",35:"writes",36:"loops",37:"evaluation",38:"roles"}[number]
    if chapter == "05": return {1:"errors",2:"slots",3:"errors",4:"errors",5:"security",6:"output",7:"timeout",8:"timeout",9:"errors",10:"errors",11:"loops",12:"monitor",13:"context",14:"delivery",15:"checkpoint",16:"checkpoint",17:"checkpoint",18:"stream",19:"checkpoint",20:"checkpoint"}[number]
    if chapter == "06": return {1:"context",2:"turns",3:"compression",4:"turns",5:"context",6:"state",7:"context",8:"turns",9:"output",10:"state",11:"compression",12:"rag",13:"context",14:"compression",15:"cost",16:"compression",17:"compression",18:"rag",19:"compression",20:"state",21:"state",22:"state",23:"state",24:"checkpoint",25:"compression",26:"context",27:"security",28:"evaluation",29:"context"}[number]
    if chapter == "07": return {1:"tools",2:"plan",3:"tools",4:"tools",5:"tools",6:"protocol",7:"protocol",8:"protocol",9:"rag",10:"rag",11:"rag",12:"rag",13:"rag",14:"rag"}[number]
    if chapter == "08": return {1:"output",2:"output",3:"output",4:"output",5:"tools",6:"stream",7:"stream",8:"stream",9:"stream",10:"stream",11:"errors",12:"evaluation",13:"evaluation",14:"monitor",15:"monitor",16:"evaluation",17:"evaluation",18:"evaluation",19:"evaluation",20:"cost"}[number]
    if chapter == "10":
        if number in range(1,9) or number in (12,13,22,23): return "rl"
        return {9:"training",10:"inference",11:"inference",14:"compression",15:"output",16:"tools",17:"rag",18:"training",19:"cost",20:"voice",21:"training"}[number]
    if chapter == "11": return "coding"
    if chapter == "12": return "voice"
    if chapter == "13": return "writes" if number == 2 else "security"
    raise ValueError((chapter, number, question))


def normalize(s):
    s = re.sub(r"^(?:Q\d+|追问\s*\d*)\s*[｜|：:]", "", s)
    return re.sub(r"[\s*#‘’\"“”`？?，、：:（）()\-]", "", s).lower()


def explanation(block: list[str]) -> str:
    """Keep question-specific conceptual prose; exclude code and foreign flows."""
    kept, fenced = [], False
    excluded = re.compile(r"订单|退款|扣款|航班|机票|物流|深圳|上海|目的地|收件人|售后|客服|客户采购|你们公司|上一家公司")
    for line in block:
        if line.strip().startswith("```"): fenced = not fenced; continue
        if fenced or not line.strip() or line.startswith(("#", "|", ">")) or "参考答案" in line: continue
        if excluded.search(line): continue
        if re.match(r"^[\s{}\[\]]*(?:def |class |async |return |import |from )", line): continue
        line = re.sub(r"\[\[.*?\]\]", "对应主题", line)
        line = line.replace("我们项目", "这类设计").replace("我们", "设计团队").replace("我会", "设计上可以").replace("你们", "实施团队")
        line = line.replace("跨专科按顺序执行", "跨专科按依赖关系执行，独立只读步骤可并行，包含批准写动作的计划串行")
        line = line.replace("症状评估、院内分诊和转诊协同", "症状辅助评估、风险提示与科室建议；转诊草稿由独立业务服务在医生确认参数后创建")
        line = re.sub(r"\*\*", "", line)
        kept.append(line)
        if sum(map(len, kept)) >= 600: break
    return "\n\n".join(kept)[:900]


def source_examples(path: Path) -> list[dict]:
    """Account for every fenced example, including material before Q1.

    These are teaching examples, never additional executable application code.
    Foreign transaction examples are consistently recast as staff-led referral
    assessment; the adapted answer above remains the implementation authority.
    """
    text = path.read_text(encoding="utf-8-sig")
    replacements = {
        "refund_assessment_agent": "referral_assessment_agent",
        "RefundAssessment": "ReferralAssessment", "order_query": "patient_record_query",
        "order_agent": "patient_record_agent", "query_order": "query_patient_record",
        "order_id": "patient_id", "read_order": "read_patient_record",
        "refund_reason": "referral_reason", "refund_request": "referral_request",
        "refund_agent": "referral_agent", "refund-policy": "referral-policy",
        "refund-order": "referral-patient", "order-12345": "patient-12345",
        "submit_refund": "submit_referral", "assess_refund": "assess_referral",
        "verify_refund_operation": "verify_referral_operation", "refund-task": "referral-task",
        '"refund"': '"referral"', "退款规则": "院内转诊规则", "退款": "转诊申请",
        "订单域/用户域/商品域/财务域/售后域": "症状域/药品域/指南域",
        "订单服务": "患者记录服务", "订单号": "患者标识", "订单": "患者记录",
        "物流信息": "转诊状态", "这个月销售额": "本科室本月用药审核记录",
        "查北京到上海的机票": "查患者阿司匹林说明书",
        "改成深圳": "改查华法林说明书", "目的地": "药品名称",
        "华东地区的售后政策": "本院的转诊协同规范", "华东地区售后政策": "本院转诊协同规范",
        "售后系统设计": "院内转诊协同系统设计", "售后": "院内协同",
        "发消息": "提交院内通知", "用户": "院内员工",
    }
    rows = []
    for n, match in enumerate(re.finditer(r"^```([^\n]*)\n(.*?)^```\s*$", text, re.M | re.S), 1):
        language, content = match[1].strip(), match[2].rstrip()
        original_content = content
        for old, new in replacements.items():
            content = content.replace(old, new)
        if not language:
            try:
                json.loads(content)
                language = "json"
            except ValueError:
                language = "text"
        prefix = text[:match.start()]
        headings = re.findall(r"(?m)^#{1,6}\s+(.+)$", prefix)
        nonempty = [line for line in prefix.splitlines() if line.strip()]
        rows.append({"anchor": f"example-{n:03d}", "line": prefix.count("\n") + 1,
                     "language": language, "content": content,
                     "lead": explanation([nonempty[-1]]) if nonempty else "",
                     "title": headings[-1] if headings else "章节概览",
                     "source_sha256": hashlib.sha256(match[0].encode()).hexdigest(),
                     "source_content_sha256": hashlib.sha256(original_content.encode()).hexdigest(),
                     "adaptation": "unchanged" if content == original_content else "tob_rewrite",
                     "adapted_sha256": hashlib.sha256(content.encode()).hexdigest()})
    return rows


def original_reference(path: Path) -> str:
    return (Path("artifacts/medagent_upgrade_20261013/原始资料") / (path.stem + ".txt")).as_posix()


def preserve_original(path: Path) -> None:
    """Keep the captured original immutable, including when it is the input."""
    target = PRESERVED / (path.stem + '.txt')
    if target.resolve() == path.resolve():
        return
    data = path.read_bytes()
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError(f'已有原件与输入不同，拒绝覆写：{path.name}')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(data)


def question_source_position(raw: str, question: str, table_column: int | None) -> dict:
    """Separate table cell numbers from character spans in template text lines."""
    offset = 0
    if table_column is not None:
        separators = [index for index, char in enumerate(raw) if char == '|']
        offset = separators[table_column - 1] + 1
    start = raw.index(question, offset)
    return {'column': table_column, 'source_position_kind': 'table_cell' if table_column else 'text_line',
            'source_start_column': start + 1, 'source_end_column': start + len(question)}


def append_examples(body: list[str], path: Path, questions: list[dict]) -> list[dict]:
    rows = source_examples(path)
    if not rows:
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    boundaries, in_code = [], False
    for line_number, line in enumerate(lines, 1):
        if line.strip().startswith('```'):
            in_code = not in_code
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)', line) if not in_code else None
        if heading and (len(heading[1]) <= 2 or re.search(r'课堂伪代码|面试串讲|追问模板', heading[2])):
            boundaries.append((line_number, heading[2]))
    for row in rows:
        previous = [q for q in questions if q["line"] < row["line"]]
        # A thematic section after a question is not part of that question.
        intervening = [title for line, title in boundaries
                       if previous and previous[-1]['line'] < line < row['line']]
        row["question_anchor"] = previous[-1]["anchor"] if previous and not intervening else None
        row["original_local_path"] = original_reference(path)
        row["association"] = ("chapter_appendix" if intervening else
                              "question" if previous else "chapter_overview")
        if intervening:
            row['section_title'] = intervening[-1]
    def render(row):
        text = [f"### {row['anchor']}", "", f"**{row['title']}**", "",
                f"原文位置：仓库根目录下的本地原件 `{original_reference(path)}` 第 {row['line']} 行。"
                "该路径指保留的 .txt 原件，当前同名 .md 是适配稿；本地原件不随克隆分发。", "",
                "用途：ToB 原理示例 / 扩展设计，不表示代码已落地。", ""]
        if row["question_anchor"]:
            text += [f"[返回对应问答](#{row['question_anchor']})", ""]
        elif row['association'] == 'chapter_overview':
            text += ["归属：章节概览，位于本章首题之前；无对应问答，不计为漏关联。", ""]
        else:
            text += [f"归属：章节附录「{row['section_title']}」；不强行关联前一题。", ""]
        return "\n".join(text + [f"```{row['language']}", row["content"], "```", ""])
    # Place a block immediately after its source lead-in where that prose was
    # retained; otherwise keep it beside the relevant answer rather than only
    # in a chapter-end gallery. Every block still has exactly one stable anchor.
    for question in reversed(questions):
        associated = [row for row in rows if row["question_anchor"] == question["anchor"]]
        heading = f"## {question['anchor']}"
        if associated and heading in body:
            start = body.index(heading) + 1
            end = next((i for i in range(start, len(body)) if body[i].startswith("## q-")), len(body))
            for row in associated:
                lead = row["lead"]
                location = next((i for i in range(start, end)
                                 if lead and lead in body[i] and "```" not in body[i]), None)
                if location is not None:
                    body[location] = body[location].replace(lead, lead + "\n\n" + render(row), 1)
                    row["placement"] = "after_source_lead"
                else:
                    body[end:end] = [render(row), ""]
                    end += 2
                    row["placement"] = "beside_question"
            body[end:end] = ["**本题图与结构示例：** " + "、".join(
                f"[{row['anchor']}](#{row['anchor']})" for row in associated), ""]
    for association, title in [('chapter_overview', '章节概览'), ('chapter_appendix', '章节附录')]:
        unassociated = [row for row in rows if row['association'] == association]
        if unassociated:
            body += [f"## {title}示例（无对应问答）", "",
                     "以下示例按原文独立章节保留，属于 ToB 原理或扩展设计，不表示当前产品功能。", ""]
            for row in unassociated:
                row["placement"] = association
                body += [render(row), ""]
    return [{**row, "source": path.with_suffix('.md').name, "output": path.with_suffix('.md').name}
            for row in rows]


def parse_questions(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    entries, primary = [], 0
    matcher = re.compile(r"^(?:#{1,6}\s*)?(?:\*\*)?(Q(\d+)\s*[｜|：:]|追问\s*\d*\s*[｜|：:])(.+?)\s*(?:\*\*)?$")
    in_code = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"): in_code = not in_code
        match = matcher.match(line.strip()) if not in_code else None
        if match:
            if match.group(2): primary = int(match.group(2))
            entries.append({"line": i+1, "question": match.group(3).rstrip("* "), "primary": primary,
                            "followup": not bool(match.group(2))})
    for index, entry in enumerate(entries):
        start = entry["line"]
        end = entries[index+1]["line"]-1 if index+1 < len(entries) else len(lines)
        # Do not include the next thematic chapter's introductory paragraphs.
        section = lines[start:end]
        boundary = next((j for j, line in enumerate(section) if line.startswith("## ")), len(section))
        entry["explanation"] = explanation(section[:boundary])
    return entries


SPECIAL = {
    ("03",4): "我把意图错误作为一个受控闭环处理：发现问题、判断副作用、修正识别、重建计划、核对结果，再由院内人员复核。事前看三路分歧和阈值边界，事中看参数缺失、权限或工具异常，事后看目标所需工具是否覆盖，最终看医生或药师纠正。置信度只能表示可疑，接口失败也可能是服务故障，不能单凭一个信号判定识别错。",
    ("03",3): "本地模型保留十类标签，九类临床辅助任务映射到三个专科；旧标签“转诊协同”只用于兼容既有模型，对外称转诊草稿辅助创建。主协调器将该业务诉求从临床计划中分离，只提示医生确认入口；医生填写并确认参数后，独立业务服务经 MCP 创建草稿，不由症状专科或模型开单。科室建议仍是症状评估的辅助结果，不提供患者自助挂号、预约或自动接收协同。",
    ("02",2): "如果对院内医学材料做清洗，固定格式、编码、单位、日期和去重用确定性代码；异常病例描述才让模型协助分类，并保留原文和人工抽检。当前 BERT 数据处理脚本是已实现例子，医院批量数据清洗 Agent 需要另建任务和验收，不能改写患者原始记录后直接覆盖。",
    ("02",5): "这题问的是跨业务架构设计。我会借鉴主协调器、业务专科和受控工具的分层，但在 MedAgent 内只实现院内临床辅助；售后退款、订单处理不是这个项目的功能。如果面试官要求独立设计售后系统，可以在另一个系统中按身份、业务查询、问题分类、权限动作和人工服务设计。",
    ("04",14): "对一般启用 GIL 的 CPython，线程适合等待网络 IO，CPU 密集的 Python 计算通常用进程或专门推理服务；PyTorch 等原生算子与 GPU 执行还取决于底层实现。Lock 不可同线程重复获取，RLock 可重入，但异步协程临界区应用 asyncio.Lock，不能拿线程锁替代。项目用 to_thread 隔离同步客户端和本地推理，不声称线程等于多核推理加速。",
    ("07",8): "我会先报项目实际使用的 MCP 工具发现与调用方式，再核对锁定依赖和官方协议版本。Resources、Prompts、Sampling、Elicitation 等是协议能力题，是否支持取决于具体版本、实现和协商结果；当前项目没有把它们全部接入。生产看鉴权、能力协商、结构兼容和回归，不把‘最新版本’当未经核实的固定结论。",
    ("10",20): "当前项目没有视觉 caption 生产流水线或上游反馈数据。我可以把这题迁移成医疗工具答复的反馈方法：收集责任医生指出的漏项、来源错误、单位问题和不适用结论，保留原始问题、证据与版本，先定位检索还是生成问题，再固定测试集比较改动。不能编造自己收到过 caption 客户反馈。",
    ("13",4): "当前项目没有接入 OpenClaw。我会按可迁移的安全边界回答：员工身份和患者授权、固定院内数据源、专科工具最小权限、输入与工具返回当作不可信数据、写动作审批和审计。指令清洗只能降低风险，不能授予权限；具体产品是否具备这些机制，需要核对其版本与实现。",
}


def build(source: Path):
    paths = sorted(source.glob("*.md")) or sorted(source.glob("*.txt"))
    if len(paths) != 14: raise ValueError(f"预期读取14份原文，实际{len(paths)}份；先核对来源")
    for path in paths:
        preserve_original(path)
    DEST.mkdir(parents=True, exist_ok=True)
    manifest, lookup, examples = [], [], []
    index = ["# MedAgent：全部文档逐题适配", "", "核查日期：2026-10-13。用途：准备院内 ToB 多 Agent 项目的面试与连续追问。原件是仓库根目录下 artifacts/medagent_upgrade_20261013/原始资料/*.txt 的 14 份本地文件，不随克隆分发；桌面同名 .md 及本目录均为适配稿。", "",
             "每题先读项目口述，再看设计展开与实现边界。设计展开来自原文的原理；只有项目口述和代码依据描述当前实现。未部署的功能按扩展方案回答。个人经历、任职、客户和采购成果由本人填写。", "",
             "范围固定：院内医生、药师、医务及系统管理人员；不新增患者登录、挂号、预约、缴费、退费、自动诊疗。", "", "## 章节与覆盖数", ""]
    index[6:6] = ["原始资料编号为 00—08、10—13，加一份横向题表，共 14 份。来源本身没有 09 号文件，编号跳过不是遗漏。", ""]
    for path in paths:
        if not re.match(r"\d\d-", path.name): continue
        source_name = path.with_suffix(".md").name
        chapter = path.name[:2]
        entries = parse_questions(path)
        body = [f"# {path.stem}：MedAgent 逐题口述", "", "核查日期：2026-10-13。所有答案以院内 ToB 边界为前提。一般理论与未落地的扩展方案明确分开。", ""]
        for n, entry in enumerate(entries, 1):
            key = card_key(chapter, entry["primary"], entry["question"])
            current, design, refs = CARDS[key]
            anchor = f"q-{n:03d}"
            body += [f'## {anchor}', "", f"### {'追问' if entry['followup'] else '主问题'}｜{entry['question']}", "",
                     f"原文位置：仓库根目录下的本地原件 `{original_reference(path)}` 第 {entry['line']} 行。"
                     "当前同名 .md 是适配稿；本地原件不随克隆分发。", "",
                     "**项目口述：** " + (SPECIAL.get((chapter,entry["primary"]), current) if not entry["followup"] else current), ""]
            if entry["explanation"]:
                body += ["**针对本题的设计展开（原文原理，作为方案解释）：**", "", entry["explanation"], ""]
            body += ["**在 MedAgent 中怎么补全：** " + design, "", "**代码依据：** " + "、".join(
                f"[{name}](../../{name})" for name in refs.split(",")), ""]
            row = {"source": source_name, "line": entry["line"], "question": entry["question"], "output": source_name,
                   "anchor": anchor, "topic": key, "kind": "followup" if entry["followup"] else "primary"}
            manifest.append(row)
            lookup.append((normalize(entry["question"]), row))
        examples.extend(append_examples(body, path, [q for q in manifest if q['source'] == source_name]))
        (DEST/source_name).write_text(annotate_markdown("\n".join(body).rstrip()+"\n"), encoding="utf-8", newline="\n")
        index.append(f"- [{path.stem}](<{source_name}>)：{len(entries)} 道主问题与追问。")
    table_path = next(path for path in paths if not re.match(r"\d\d-", path.name))
    table_name = table_path.with_suffix(".md").name
    lines = table_path.read_text(encoding="utf-8-sig").splitlines()
    body = ["# 横向题表与连续追问：MedAgent 对照", "", "每个表格单元和连问都保留来源，链接到本目录的项目适配答案。表中概念短语也按问题处理，避免漏掉不带问号的追问。", ""]
    heading = ""
    table_questions = []
    fenced = False
    for i, line in enumerate(lines):
        if line.startswith("#"): heading = line.lstrip("# ")
        if line.strip().startswith("```"): fenced = not fenced; continue
        if line.startswith("|") and not re.match(r"\|\s*[-:]", line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 3 and cells[0] not in {"主问题", "问题", "主题", "核心问题", "一级问题"}:
                if not any("？" in cell or "?" in cell for cell in cells): continue
                for col, cell in enumerate(cells): table_questions.append((i+1, cell, col, heading, cells[0]))
        elif fenced and (line.startswith("→ ") or "？" in line or line == "你介绍一下你的 Multi-Agent 项目。") and len(line) < 150:
            table_questions.append((i+1, line.lstrip("→ "), None, heading, line.lstrip("→ ")))
    for n, (line, question, col, section, parent_question) in enumerate(table_questions, 1):
        norm = normalize(parent_question)
        matches = sorted(lookup, key=lambda item: SequenceMatcher(None, norm, item[0]).ratio(), reverse=True)
        matched = matches[0][1]
        score = SequenceMatcher(None, norm, matches[0][0]).ratio()
        key = matched["topic"]
        overrides = {
            "这个 JSON 是谁生成的？": "plan", "大模型生成 JSON 不稳定怎么办？": "plan",
            "target_agent 不存在怎么办？": "plan", "主 Agent 合并结果": "output",
            "检查是否冲突": "output", "低置信度结果标记": "output",
            "必要时二次调用或追问": "intent_error", "生成最终答案": "output",
            "主 Agent 怎么做意图识别？": "intent", "主 Agent 怎么生成任务计划？": "plan",
            "state 怎么避免互相覆盖？": "state", "Badcase 怎么快速定位到链路环节？": "monitor",
        }
        key = overrides.get(parent_question, key)
        if "GIL" in parent_question: key = "parallel"
        if parent_question == "你介绍一下你的 Multi-Agent 项目。": key = "overview"
        if "场景" in parent_question and "Agent" in parent_question: key = "plan"
        if "上下文太长" in parent_question: key = "compression"
        current, design, refs = CARDS[key]
        position = question_source_position(lines[line - 1], question, col + 1 if col is not None else None)
        locator = (f"第 {line} 行、第 {position['column']} 个表格单元" if col is not None else
                   f"第 {line} 行（连问模板文本行，字符 {position['source_start_column']}—{position['source_end_column']}）")
        body += [f'## q-{n:03d}', "", f"### {question}", "", f"来源：{section}；仓库根目录下的本地原件 `{original_reference(table_path)}` {locator}。当前同名 .md 是适配稿。", "",
                 "**项目口述：** " + current, "", "**方案补全：** " + design, "",
                 f"**对应详解：** [{matched['question']}](<{matched['output']}#{matched['anchor']}>)。", ""]
        manifest.append({"source":table_name,"line":line,**position,"question":question,"output":table_name,
                         "anchor":f"q-{n:03d}","topic":key,"kind":"table_or_sequence","match_score":round(score,3)})
    examples.extend(append_examples(body, table_path, [q for q in manifest if q['source'] == table_name]))
    (DEST/table_name).write_text(annotate_markdown("\n".join(body).rstrip()+"\n"), encoding="utf-8", newline="\n")
    index += [f"- [{table_path.stem}]({table_name})：{len(table_questions)} 个题表单元与连问。", "",
              "## 使用建议", "", "以03章的意图、槽位和任务切换为主线，接04章执行编排，再练05/06章故障与状态，最后按07/08/13章解释工具、依据和安全。00章用于架构辨析；10/11/12章按训练理论、开发方法或院内扩展设计作答。所有章节均已纳入覆盖清单。", "",
              "[覆盖清单](coverage.json)逐项记录来源文件、行号、题目和适配稿位置；文件哈希用于确认同步的是这次读取的版本。"]
    index += ["", "全部答案也已直接合入[单一话术正文](../MedAgent_AI_多Agent项目话术.md#全文档逐题正文)，分册保留用于对照。"]
    index += ["", "英文术语、工具名及参数名在正文后附中文译注；代码围栏内保持标识符与语法原样，译注放在示例之后。覆盖清单的 source 是原资料名称及适配稿映射键，original_local_path 才是本机原件路径；question 为原文，display_question 为带译注的显示题目。"]
    (DEST/"README.md").write_text(annotate_markdown("\n".join(index)+"\n"), encoding="utf-8", newline="\n")
    for row in manifest:
        row["original_local_path"] = original_reference(Path(row["source"]))
        row['display_question'] = annotate_text(row['question'])
    result = {"date":"2026-10-13", "source_files": [{"name":p.with_suffix('.md').name,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
                "original_local_path": original_reference(p), "distributed_with_git": False} for p in paths],
              "question_count":len(manifest),"questions":manifest,
              "example_count":len(examples), "examples":examples,
              "example_adaptation_counts": {kind: sum(row['adaptation'] == kind for row in examples)
                  for kind in ('unchanged', 'tob_rewrite')},
              "example_comparison_basis": "UTF-8/LF 内容比较，去除内容末尾空白；source_sha256 含原围栏，source_content_sha256 与 adapted_sha256 仅含内容，分别保存。",
              "source_meaning": "source 为原资料名/适配稿映射键；original_local_path 为本机保留原件；question 保留原文，display_question 仅添加中文译注。"}
    (DEST/"coverage.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps({"files":len(paths),"questions":len(manifest),"output":str(DEST)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    preserved = PRESERVED
    parser.add_argument("--source", type=Path, default=preserved if len(list(preserved.glob('*.txt'))) == 14 else Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "MedAgent_AI_多Agent项目")
    build(parser.parse_args().source)
