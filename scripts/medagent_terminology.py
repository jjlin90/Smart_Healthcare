"""Chinese reading annotations for interview materials; never rewrite code."""
from pathlib import Path
import re

# Long phrases win over individual words. These are reading aids, not renamed APIs.
_PAIRS = '''
MedAgent=医疗智能体项目
Agent=智能体
Agents=智能体
Multi-Agent=多智能体
Deep Agents=深度智能体
ToB=面向机构业务
API=应用程序接口
Application Programming Interface=应用程序接口
FastAPI=Python接口服务框架
MCP=模型上下文协议
Model Context Protocol=模型上下文协议
MCPAdapter=MCP工具适配器
FastMCP=MCP服务框架
A2A=智能体间通信协议
Agent-to-Agent=智能体间通信
Function Calling=函数调用
Tool Calling=工具调用
LangChain=大模型应用开发框架
LangGraph=有状态智能体编排框架
LLM=大语言模型
Large Language Model=大语言模型
BERT=双向编码语言模型
Bidirectional Encoder Representations from Transformers=基于Transformer的双向编码表示
BGE-M3=多语言多粒度多功能嵌入模型
BGE=文本嵌入模型
RAG=检索增强生成
Retrieval-Augmented Generation=检索增强生成
RAG+Agent=检索增强生成与智能体结合
JWT=身份认证令牌
JSON Web Token=JSON格式身份令牌
JSON=结构化数据格式
JSON Schema=JSON结构约束
HIS=医院信息系统
NER=命名实体识别
Named Entity Recognition=命名实体识别
SSE=服务器推送事件
WebSocket=双向实时通信协议
HTTP=超文本传输协议
HTTPS=加密超文本传输协议
REST=表述性状态转移接口风格
Streamable HTTP=可流式传输的HTTP
RPC=远程过程调用
SDK=软件开发工具包
CLI=命令行接口
UI=用户界面
SQL=结构化查询语言
MySQL=关系型数据库
Redis=内存键值数据库
SQLite=轻量关系型数据库
PostgreSQL=关系型数据库
Milvus=向量数据库
FAISS=向量检索库
pgvector=PostgreSQL向量检索扩展
ES=Elasticsearch搜索引擎
Elasticsearch=搜索引擎
BM25=关键词相关性排序算法
ColBERT=基于词元交互的检索模型
CrossEncoder=交叉编码排序模型
WeightedRanker=加权融合排序器
RRF=倒数排名融合
Ragas=检索增强生成评测框架
HyDE=假设文档嵌入检索
HNSW=分层可导航小世界索引
IVF_FLAT=倒排分桶精确向量索引
FLAT=精确向量索引
IVF=倒排分桶向量索引
IP=向量内积
Matryoshka=嵌套维度表示学习
Late Chunking=延后切块嵌入
Parent-Child=父子分块
Chunkless=不预先切块的检索
Chunk=分块
Embedding=向量嵌入
Rerank=重排序
Retriever=检索器
TopK=排名前K项
topK=排名前K项
QK=查询与键向量
Sigmoid=逐标签概率映射函数
Softmax=归一化概率函数
BCEWithLogitsLoss=含逻辑值的二元交叉熵损失
Self-Attention=自注意力
Attention=注意力
FlashAttention=高效注意力计算
Transformer=注意力网络架构
Gradient Starvation=梯度饥饿
continuous batching=连续批处理
SFT=监督微调
RL=强化学习
RLHF=基于人类反馈的强化学习
GRPO=组相对策略优化
PPO=近端策略优化
DPO=直接偏好优化
KL=相对熵
MDP=马尔可夫决策过程
GVF=广义价值函数
GVFs=广义价值函数
OaK=知识积累式智能体框架
CPU=中央处理器
GPU=图形处理器
CUDA=英伟达并行计算平台
GIL=全局解释器锁
CPython=Python的C语言实现
Python=编程语言
PyTorch=深度学习框架
Pydantic=数据校验库
TypedDict=带字段类型的字典
AsyncSession=异步数据库会话
asyncio=Python异步并发库
gather=汇集并发任务结果
to_thread=将同步函数放到线程执行
Lock=互斥锁
RLock=可重入锁
CancelledError=任务取消异常
IO=输入输出
I/O=输入输出
vLLM=大模型推理服务框架
fp16=半精度浮点数
fp32=单精度浮点数
OCR=光学字符识别
ASR=自动语音识别
TTS=语音合成
VAD=语音活动检测
Barge-in=用户打断语音播报
ReAct=推理与行动交替
Plan-and-Execute=先规划再执行
Plan-and-Solve=先规划再求解
CoT=思维链
Structured Output=结构化输出
OutputParser=输出解析器
Prompt=提示词
Prompt Injection=提示词注入
Injection=注入攻击
Harness=智能体运行约束环境
Skill=技能
Skills=技能
Workflow=工作流
Chain=链式流程
Graph=图式流程
DAG=有向无环图
Planner=规划器
Planning=任务规划
Executor=执行器
Supervisor=主协调器
Verifier=校验器
Reducer=状态归并器
Builder=构建器
Guard=守卫
SafetyGuard=安全守卫
DomainGuard=业务范围守卫
TaskReadinessGuard=任务参数澄清守卫
IntentClassifier=意图分类器
SymptomAgent=症状智能体
DrugAgent=药品智能体
GuideAgent=指南智能体
Symptom=症状
Drug=药品
Guide=指南
Observation=工具观察结果
Context=上下文
State=状态
Memory=记忆
Checkpoint=检查点
Resources=资源
Prompts=提示模板
Sampling=模型采样请求
Elicitation=用户信息征询
Server=服务端
Client=客户端
Tool=工具
Tools=工具
Acting=执行行动
Final Answer=最终回答
Protocol=协议
Engineering=工程实现
Coding=编程
Vibe Coding=自然语言驱动编程
Vibe=自然语言驱动开发
AI Coding=人工智能辅助编程
AI=人工智能
OpenAI=人工智能服务提供商
OpenClaw=开源智能体平台
Gateway=网关
Streamlit=Python交互页面框架
Vite=前端构建工具
Vue=前端开发框架
Docker=容器平台
Compose=多容器编排工具
Kubernetes=容器编排平台
K8s=容器编排平台
Deployment=应用部署对象
Pod=容器运行单元
Canary=灰度发布
Rollback=回滚
Quantization=量化
HITL=人工参与闭环
Human-in-the-Loop=人工参与闭环
PII=个人身份信息
ACL=访问控制列表
Mutual TLS=双向传输层认证
TLS=传输层安全协议
HMAC=基于密钥的消息认证码
SHA-256=安全哈希摘要算法
SHA=安全哈希摘要
MD5=消息摘要算法
UUID=通用唯一标识
ID=标识
TTL=生存时间
KV=键值
QPS=每秒请求数
P95=第95百分位延迟
p95=第95百分位延迟
RPO=恢复点目标
RTO=恢复时间目标
SLI=服务水平指标
SLO=服务水平目标
SLA=服务水平协议
Error Budget=错误预算
Outbox=待发送事件表
Transactional Outbox=事务消息表
Backpressure=背压
Backoff=退避重试
Jitter=随机抖动
Deadline=截止时间
Timeout=超时
Probe=探针
Cache Aside=旁路缓存
SingleFlight=相同请求合并执行
F1=精确率与召回率调和平均值
Micro-F1=微平均F1
Macro-F1=宏平均F1
Exact Match=完全匹配率
Recall=召回率
Precision=精确率
Accuracy=准确率
Coverage=覆盖率
Confidence=置信度
Margin=分类分数间隔
Threshold=阈值
OOD=分布外输入
Out-of-Distribution=分布外输入
Evaluation=评估
Leakage=数据泄漏
Ablation=消融实验
Regression=回归验证
Smoke=冒烟验证
Badcase=失败案例
Fallback=兜底
Fail=失败
Clarification=澄清
Mock=模拟对象
MVP=最小可用版本
FAQ=常见问题问答
KiB=1024字节单位
Token=词元
HTML=网页标记语言
Markdown=轻量文档标记格式
mermaid=图表描述语言
flowchart=流程图
PDF=便携文档格式
DOCX=Word文档格式
PPTX=PowerPoint演示文稿格式
TXT=纯文本格式
CSV=逗号分隔表格格式
MD=Markdown文档格式
URL=网址
GET=读取请求
POST=提交请求
SET=写入缓存命令
CORS=跨域资源共享
LF=换行符
Git=版本控制系统
HEAD=当前提交指针
beta=测试阶段
WSGI=Web服务器网关接口
pytest=Python测试框架
unittest=Python单元测试库
PASS=通过
FAIL=失败
DRIFT=发生漂移
analyze_symptoms=分析症状
suggest_department=建议院内科室
get_disease_info=查询疾病信息
query_drug_info=查询药品信息
check_drug_interaction=检查药物相互作用
check_contraindications=检查用药禁忌
query_drug_alternatives=查询替代药品
search_guidelines=检索临床指南
interpret_lab_results=辅助解读检验结果
get_treatment_protocol=查询治疗方案
save_patient_history=保存患者病史
load_patient_history=读取患者病史
save_medical_record=保存医疗记录
generate_referral=生成院内转诊单
read_patient_record=读取患者记录
query_patient_record=查询患者记录
patient_id=患者标识
request_id=请求标识
conversation_id=会话标识
session_id=会话标识
conversation_key=会话关联键
task_id=任务标识
step_id=步骤标识
thread_id=执行线程标识
trace_id=链路追踪标识
turn_id=对话轮次标识
operation_id=写操作标识
attempt_id=执行尝试标识
plan_version=计划版本
schema_version=结构约束版本
deadline_at=绝对截止时间
expected_revision=预期状态修订号
approved_actions=本轮批准的写动作
target_agent=目标智能体
depends_on=依赖步骤
input_refs=输入依据引用
raw_value=原始值
normalized_value=规范化值
evidence_span=原文证据片段
validation_status=校验状态
readOnlyHint=只读工具提示
side_effect_unknown=写入结果未知
task_deadline=任务截止时间
token_budget=词元预算
max_steps=最大执行步数
max_tokens=最大生成词元数
top_p=累积概率采样阈值
top_k=前K项采样数量
max_length=最大输入长度
result_schema=结果结构约束
validate_schema=校验数据结构
StepResult=步骤结果
add_messages=追加并合并消息
get_settings=读取服务配置
app_env=应用运行环境
SILICONFLOW_MODEL=硅基流动模型配置
LLM_MODEL_NAME=大语言模型名称配置
symptoms=症状列表
duration=持续时间
severity=严重程度
disease_name=疾病名称
drug_name=药品名称
drug_list=药品列表
patient_condition=患者临床情况
disease=疾病
keyword=检索关键词
test_results=检验结果
history_data=病史数据
record_data=医疗记录数据
department=院内科室
reason=原因
allergy_history=过敏史
past_medical_history=既往病史
current_medications=当前用药
source_sha256=原始内容哈希
source_content_sha256=原始示例内容哈希
adaptation=适配方式
tob_rewrite=院内业务场景改写
unchanged=内容未改写
validation_scope=验证范围
recorded_inputs_match_current=验证时输入与当前文件一致
adapted_sha256=适配内容哈希
original_local_path=本地原件路径
question_anchor=对应问答锚点
book_anchor=话术正文锚点
after_source_lead=紧随原文引导语
beside_question=置于对应问答之后
chapter_overview=章节概览
chapter_appendix=章节附录
direct_overwrite=直接覆盖原稿
worktree_dirty=工作区存在未提交改动
inputs_unchanged=检查期间输入未变化
started_at_utc=检查开始的UTC时间
finished_at_utc=检查结束的UTC时间
run_id=执行记录标识
document_files=文档文件清单
report_sha256=报告哈希
checker_source_sha256=检查器代码哈希
exit_code=退出码
source_meaning=来源字段含义
source_position_kind=原文位置类型
source_start_column=原文字符起始列
source_end_column=原文字符结束列
table_cell=表格单元
text_line=文本行
display_question=带中文译注的题目
external_urls=外部网址清单
question_count=问题数量
example_count=示例数量
'''

# Simple English nouns also occur as JSON keys / tool parameters in design examples.
_WORDS = '''
new=新任务 continue=继续当前任务 modify=修改当前任务 unclear=待澄清
task=任务 step=步骤 steps=步骤列表 schema=结构约束 state=状态 session=会话
source=来源 sources=来源列表 value=值 raw=原始 normalized=规范化
checkpoint=检查点 checkpointer=检查点存储器 observation=工具观察结果 observations=工具观察结果列表
prompt=提示词 token=词元 trace=链路追踪 context=上下文 memory=记忆
query=查询 rerank=重排序 reranker=重排序模型 tokenizer=分词器
embedding=向量嵌入 dense=稠密向量 sparse=稀疏向量 softmax=归一化概率 sigmoid=逐标签概率
argmax=取最大值位置 pooling=池化 grounding=证据支撑 backtracking=回溯
baseline=基线 badcase=失败案例 batch=批次 ratio=比例 drop=丢弃 nprobe=检索分桶数量
metadata=元数据 chunk=分块 parent=父块 children=子块 child=子块
symptom=症状 drug=药品 guide=指南 patient=患者 medical=医疗 referral=院内转诊
history=历史记录 name=名称 age=年龄 gender=性别
insert=插入记录 upsert=插入或更新记录 delete=删除记录 read=读取 save=保存 load=读取
strict=严格模式 general=通用模式 direct=直接回答 health=健康状态 faq=常见问答
true=真 false=假 null=空值 None=无值 pending=待处理 started=已开始 completed=已完成
failed=失败 cancelled=已取消 blocked=已阻断 ready=就绪 unknown=未知 partial=部分完成
success=成功 error=错误 status=状态 done=完成事件 fallback=兜底 draft=草案
intent=意图 slots=业务参数槽位 slot=业务参数槽位 confidence=置信度 threshold=阈值
plan=计划 version=版本 revision=修订号 dependency=依赖 dependencies=依赖列表
constraints=约束 dependency=依赖 acceptance=验收条件 goal=目标 target=目标
results=结果列表 result=结果 message=消息 messages=消息列表 event=事件
input=输入 output=输出 args=参数 arguments=参数列表 payload=载荷 data=数据
key=键 id=标识 type=类型 code=代码 string=字符串 text=文本
agent=智能体 agents=智能体列表 tool=工具 tools=工具列表 tool_call=工具调用
request=请求 response=响应 worker=工作单元 runtime=运行环境 model=模型
principal=认证主体 policy=策略 budgets=预算集合 budget=预算 reservation=预算预留
deadline=截止时间 timeout=超时 timeout_ms=超时毫秒数 attempt=执行尝试 attempts=执行尝试列表
lease=执行租约 breakers=熔断器 registry=注册表 manifest=清单 snapshot=状态快照
candidate=候选项 candidates=候选项列表 option=选项 group=分组 priority=优先级
store=存储器 reducer=状态归并器 verifier=校验器 replan=重新规划
metrics=评测指标 reward=奖励 reference=参考依据 entropy=熵 gradient=梯度
observed=已观察 failures=失败列表 failure=失败 drift=漂移 proposal=提议
channel=通道 thread=线程 config=配置 settings=配置 sources=来源列表
cache=缓存 Cache=缓存 gold=人工标准答案 mock=模拟对象 demo=演示样例
previous=前序 current=当前 original=原始 best=最佳 required=必填 optional=可选
size=大小 count=数量 sequence=顺序 batch=批次 format=格式 validation=校验
scope=范围 report=报告 backup=备份 placement=放置位置 association=归属
command=执行命令 head=当前提交指针 head_after=检查后的提交指针 files=文件数量 issues=问题清单
sha256=安全哈希摘要 local=本地 path=路径 line=行号 lines=行列表 output=输出
record=记录 history=历史记录 parameter=参数 parameters=参数列表
readOnly=只读 noncommittal=未给出明确结论 precision=精确率 document=文档
response_time=响应耗时 used_cache=是否使用缓存 degraded=是否降级 degrade_level=降级级别
degrade_reason=降级原因 llm_unavailable=大语言模型不可用 miss=未命中
Closed=熔断关闭 Open=熔断开启 Half-Open=半开探测 Future=异步结果对象
future=异步结果对象 temperature=采样温度 compute_score=计算相关性分数
retries=重试次数 limit=上限 limits=上限集合 bytes=字节 length=长度
fallbacks=兜底列表 hash=哈希 summary=摘要 action=动作 actions=动作列表
hacking=钻规则漏洞 continuous=连续 batching=批处理 starvation=饥饿
step_results=步骤结果集合 memory=记忆 samples=样本 labels=标签
async=异步 await=等待异步结果 dict=字典 list=列表 bool=布尔值 int=整数 float=浮点数
str=字符串 set=集合 tuple=元组
'''

TERMS = dict(line.split('=', 1) for line in _PAIRS.strip().splitlines())
TERMS.update(item.split('=', 1) for item in _WORDS.split())
_EXTRA = '''
med_rag=医疗检索增强生成项目
python-a2a=Python智能体通信库
create_agent=创建智能体
MedicalCoordinator=医疗主协调器
MCPToolAgent=MCP工具智能体
MCPToolAgent=模型上下文工具智能体
MCPTool=模型上下文工具
MCPToolAgent=调用MCP工具的智能体
Server-Sent Events=服务器推送事件
BCE=二元交叉熵损失
observability=可观测性
Train=训练
Sent=已发送
Events=事件
Decide=作出决策
order_id=订单标识，原通用示例字段
chunk_type=分块类型
Smart_Healthcare=本医疗智能体代码仓库
dense1024=1024维稠密向量
batch64=64条样本一批
max_length2048=输入长度上限2048词元
max_tokens1024=生成上限1024词元
nlist256=256个索引分桶
nprobe16=检索16个索引分桶
ratio0=比例为零
dense1=稠密向量权重为一
sparse0=稀疏向量权重为零
cos=余弦相似度
k+rank=平滑常数加排名
x-y=向量差
Sutton=萨顿
Richard=理查德
Silver=西尔弗
Karpathy=卡帕西
bug=缺陷
diff=差异
case=案例
vs=对比
running=运行中
MCPTool=协议工具
MedicalCoordinator=医疗主协调器
MCPToolAgent=调用MCP工具的智能体
React=前端组件框架
Dify=大模型应用开发平台
Coze=智能体开发平台
Anthropic=大模型服务提供商
Checkpointers=检查点存储器
PromptTemplate=提示词模板
AgentExecutor=智能体执行器
Query=查询
Action=动作
Metric=指标
Log=日志
Loop=执行循环
Value=价值
Key=键
Schema=结构约束
Runtime=运行环境
StepRequest=步骤请求
StateSnapshot=状态快照
IntentDecision=意图识别决策
IntentReviewError=意图复核失败异常
ContextView=受限上下文视图
ToolExecution=工具执行
ClinicalEntry=临床入口
Intent=意图
Slot=业务参数槽位
Model=模型
Host=宿主程序
Versioning=版本管理
Registry=能力注册表
Queue=队列
Auth=认证授权
Router=路由器
Aggregator=结果汇总器
Scheduler=调度器
Middleware=中间件
Persistence=持久化
Stateless=无状态
Delegation=受限委托
Audience=令牌接收对象
Stable=稳定状态
Budget=预算
Async=异步
Concurrency=并发
Dry Run=模拟执行
Service Mesh=服务网格
Circuit Breaker=熔断器
Rate Limit=限流
Argument Hash=参数哈希
Backward Compatibility=向后兼容
Multi Round-Trip Requests=多次往返请求
Time Travel=按检查点回溯状态
Vector Similarity=向量相似度
Top-1=排名第一项
top-k=排名前K项
Bad Case=失败案例
General Function=通用函数
Feature Construction=特征构建
Train Group Size=训练分组大小
Gradient Starvation=梯度饥饿
few-shot=少样本示例
held-out=独立留出集
test-time=测试阶段
spectral decoupling=谱解耦
deep learning=深度学习
constrained decoding=受约束解码
preference optimization=偏好优化
credit assignment=贡献归因
lost-in-the-middle=长上下文中间信息丢失
indirect injection=间接提示词注入
exponential backoff=指数退避
append-only=仅追加写入
fail-fast=快速失败
transactional outbox=事务消息表
General Value Function=广义价值函数
Welcome to the Era of Experience=欢迎来到经验时代
Speaker Diarization=说话人区分
TTFT=首个词元延迟
Streaming=流式传输
DLP=数据泄漏防护
RPS=每秒请求数
P99=第99百分位延迟
p99=第99百分位延迟
P50=第50百分位延迟
WER=词错误率
CER=字错误率
NDCG=归一化折损累计增益
MRR=平均倒数排名
TopN=排名前N项
AR=回答相关性
CR=上下文召回率
CP=上下文精确率
L0=第零层
L1=第一层
L2=第二层
TP=真正例
FP=假正例
FN=假负例
FP16=半精度浮点数
INT8=八位整数精度
int8=八位整数精度
int4=四位整数精度
PagedAttention=分页注意力
RoPE=旋转位置编码
MoE=混合专家模型
SOP=标准操作流程
AST=抽象语法树
LSP=语言服务器协议
CT=计算机断层扫描
MRI=磁共振成像
DICOM=医学影像通信标准
LIS=检验信息系统
EMR=电子病历
mTLS=双向传输层认证
HPA=水平自动扩容
Liveness=存活探针
Readiness=就绪探针
Startup=启动探针
Test=测试
Audit=审计
Idempotency=幂等性
Retry=重试
HTTPRoute=HTTP路由对象
SQLAlchemy=数据库访问框架
Alembic=数据库迁移工具
MongoDB=文档数据库
Prometheus=监控指标系统
Grafana=监控展示平台
LangSmith=大模型链路观测平台
NumPy=数值计算库
Hugging Face=模型与数据集平台
NeurIPS=神经信息处理系统会议
DeepSeekMath=深度求索数学模型
SiliconFlow=硅基流动模型服务
Horde=多预测器学习架构
FC-STOMP=特征构建与子任务选项学习方法
Option=强化学习中的时间扩展动作
SubTask=子任务
Sinks=不可信数据的执行落点
STDIO=标准输入输出
SSRF=服务器端请求伪造
Browser=浏览器
Shell=命令行解释器
DB=数据库
HR=人力资源
PPT=演示文稿
GitHub=代码托管平台
Google=谷歌
Java=编程语言
Mockito=Java模拟测试框架
Stub=桩对象
Fake=简化替代实现
SaaS=软件即服务
Offline=离线
NIST=美国国家标准与技术研究院
Hijacking=劫持
Slack=团队协作通信平台
Thought=推理思考
UTC=协调世界时
URI=统一资源标识符
W3C=万维网联盟
Mcp-Session-Id=MCP会话标识请求头
Mcp-Method=MCP方法请求头
Mcp-Name=MCP对象名称请求头
Retry-After=建议重试等待时间请求头
SEP-414=MCP无状态通信改进提案编号
MRTR=多次往返请求
BEGIN=开始标记
END=结束标记
README=项目说明文档
MD=Markdown文档格式
md=Markdown文件扩展名
txt=纯文本文件扩展名
PUT=更新请求
POST=提交请求
GET=读取请求
CRLF=回车加换行
Open=熔断开启
Closed=熔断关闭
Half-Open=半开探测
False=假
True=真
None=无值
JWTError=身份令牌校验异常
DispatchRequest=任务派发请求
ExecutionPlan=执行计划
INTENT=意图
RegExp=正则表达式
Regex=正则表达式
agent_results=智能体结果集合
allowed_tools=允许调用的工具
execution_mode=执行模式
isError=是否为工具错误
requestState=请求状态
row_count=记录数量
return_exceptions=将异常作为结果返回
publish_if_version=版本匹配后发布
commit_if_current=任务仍有效时提交
get_state=读取状态
add_observation=追加工具观察结果
add_feedback=追加反馈
query_sql=查询SQL
query_order_v1=查询订单的第一版示例接口
query_order_v2=查询订单的第二版示例接口
doc_id=文档标识
tenant_id=机构租户标识
user_id=员工标识
tool_name=工具名称
invocation_id=工具调用标识
finish_reason=生成结束原因
raw_top1_hit_rate=原始首选意图命中率
accepted_coverage=接受候选覆盖率
multi_top1_hit=多标签首选意图命中
multi_top1_miss=多标签首选意图未命中
repository_sha256=仓库快照哈希
repository_dataset_sha256=仓库数据集哈希
dataset_sha256=数据集哈希
GUIDELINE_API_BASE_URL=指南服务基础地址配置
INTERNAL_SERVICE_SECRET=内部服务签名密钥配置
SECRET_KEY=身份令牌签名密钥配置
AGENT_TOOLS=智能体工具白名单配置
INVALID_CONCURRENT_GRAPH_UPDATE=图状态并发写入冲突异常
EXTERNAL_UNTRUSTED_CONTENT=外部不可信内容标记
outcomes=执行结果集合
review_reasons=复核原因列表
is_available=是否可用
possible_missing_intent=可能漏识别的意图
effective_at=生效时间
expires_at=到期时间
retrieved_at=检索时间
observed_at=观察时间
created_at=创建时间
updated_at=更新时间
object_version=对象版本
relevant_state_version=相关状态版本
trust_class=可信等级
source_ref=来源引用
source_id=来源标识
input_ref=输入引用
output_ref=输出引用
idempotency_key=幂等键
retry_policy=重试策略
risk_level=风险等级
risk_signals=风险信号列表
route_policy=路由策略
no_signal=没有识别信号
not_started=尚未开始
completed_nodes=已完成节点
artifact_id=产物标识
content_hash=内容哈希
media_type=媒体类型
retrieved_chunks=检索到的分块
retrieval_status=检索状态
retrieval_error=检索错误
intent_classifier_unavailable=意图分类器不可用
reranker_unavailable=重排序模型不可用
parent_id=父块标识
parent_content=父块内容
children_ids=子块标识列表
child_filter_expr=子块过滤表达式
search_child_to_parent=从子块召回父块
generate_with_context=基于上下文生成
validated_review_answer=经校验的复核回答
qwen-plus=通义千问模型
glm-4=智谱语言模型
deepseek-v4-flash=深度求索模型
jieba=中文分词库
CDC=变更数据捕获
'''
TERMS.update(line.split('=', 1) for line in _EXTRA.strip().splitlines())
TERMS.update(item.split('=', 1) for item in '''
truncated=内容已截断 meta=元数据 card=能力卡 delta=增量内容 evaluation=评估
base=基础配置 collapse=压缩合并 terminal=终止状态 caption=图像描述
search=检索 env=运行环境 final=最终结果 endpoint=服务地址 replay=重放
offset=读取偏移 scores=评分列表 writes=写操作 prefill=输入预填充 cumulant=累计信号
synthesize=合成回答 synthesize_stream=流式合成回答 patients=患者列表
profile=患者档案 profile_updated=档案已更新 progress=执行进度
workflow=工作流 backlog=待处理积压 visited_agents=已访问智能体
final_answer=最终回答 span=证据跨度 interrupt=中断 errors=错误列表
dataclass=数据类 evidence=证据 allowlist=白名单 empty=空内容
citation=来源引用 estimator=估计器 mask=遮罩 attention=注意力 decode=解码
continuation=继续执行 discount=折扣因子 function=函数 endpointing=语音端点检测
multilabel_v4=第四版多标签意图配置 guidelines=指南 example=示例 answer=回答
stop=停止 params=参数集合 issue=问题记录 permission=权限 deny=拒绝
max=上限 propose=提出候选 rubric=评价标准 provider=服务提供方 region=区域
bulkhead=资源隔离 shedding=负载丢弃 wait=等待 values=值集合 next=下一步
criteria=验收条件 top=排名靠前 node=节点 durability=持久性
artifact=产物 guardrails=安全约束 chains=调用链 retrievers=检索器集合
langchain-classic=LangChain传统链组件库 core=核心组件 traceparent=父链路请求头
tracestate=链路状态请求头 baggage=链路附带信息 hit=命中 conflicts=冲突列表
rewrite=改写 issuer=签发方 timestamp=时间戳 provisional=暂定结论
effort=推理投入 validity=有效性 entailment=语义蕴含 ensemble=集成模型
reject=拒绝 sampling=采样 exploitation=利用已知策略 hack=钻规则漏洞
n-gram=连续N个词元片段 clip=裁剪 conflict=冲突 greedy=贪心选择
level=级别 loss=损失 normalization=归一化 bound=约束边界 causal=因果关系
padding=填充 experiential=经验驱动 open-ended=开放式 continual=持续学习
respecting=遵守约束 subtasks=子任务集合 option=时间扩展动作 transition=状态转移
models=模型集合 corruption=内容损坏 rot=上下文衰退 lost=信息丢失
grammar=语法约束 calling=调用 preference=偏好 optimization=优化
verification=核验 hypothesis=假设 specification=规格约束 assisted=辅助
instrumentation=观测埋点 branch=分支 arc=控制流路径 lint=静态代码检查
finalization=任务收尾 accuracy-latency=准确率与延迟 latency-accuracy=延迟与准确率
trade-off=取舍 detector=检测器 provenance=来源追溯 authentication=身份认证
sink=执行落点 pairing=配对授权 workspace=工作区 chat-template=对话模板
cwd=当前工作目录 sandbox=沙箱 strategy=检索策略 test_query=测试查询
subquery=子查询 fast=快速路径 complex=复杂路径 generate=生成回答
split_samples=划分样本 with-search=启用检索 facts=事实字段
build=构建 dense-only=仅使用稠密向量 sparse-only=仅使用稀疏向量
Sparse=稀疏向量 Dense=稠密向量 Config=配置类 ProfileUpdate=档案更新数据
build_context=构建受限上下文 dispatch=派发任务 result_type=结果类型
resource=受控资源 resource_id=资源标识 tool_calls=工具调用列表
timeout_seconds=超时秒数 seconds=秒 retry=重试 retryable=是否可重试
normalized_input=规范化输入 signature=签名 max_retries=最大重试次数
request_hash=请求摘要 required_fields=必填字段 list_tools=发现工具 call_tool=调用工具
run=运行 execute=执行 validate=校验 select_task=选择任务
valid=有效 invalid=无效 title=标题 content=内容 output_schema=输出结构约束
type=类型 matched=匹配结果 required_tools=必需工具 retrieved=已检索
temperature=采样温度 max_concurrency=最大并发数 concurrency=并发数
''' .split())
_CASE_TERMS = {term.casefold(): gloss for term, gloss in TERMS.items()}
_TERM_RE = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True)) + r')(?![A-Za-z0-9_])', re.I)
_IDENTIFIER = re.compile(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b')


def translation(term):
    if term.casefold() in _CASE_TERMS:
        return _CASE_TERMS[term.casefold()]
    # Compositional labels are used only when every component is known.
    parts = term.split('_')
    if len(parts) > 1 and all(part.casefold() in _CASE_TERMS for part in parts):
        return ''.join(_CASE_TERMS[part.casefold()] for part in parts)
    return None


def _already_translated(tail):
    return bool(re.match(r'^[（(][^()（）\n]*[\u3400-\u9fff][^()（）\n]*[）)]', tail))


def annotate_text(text):
    """Annotate readable text, preserving paths and existing bilingual labels."""
    # Extensions are parts of filenames, not technical terms to annotate per row.
    text = re.sub(r'(\.(?:md|txt|py|json|svg|png|html|pdf|yaml|yml|toml)`?)(?:[（(][^()（）\n]*[\u3400-\u9fff][^()（）\n]*[）)])', r'\1', text, flags=re.I)
    # Earlier glosses may themselves have acquired glosses: keep one annotation.
    text = text.replace('Server(服务端)-Sent Events', 'Server-Sent Events')
    starts = list(re.finditer(r'(?:`[^`\n]+`|[A-Za-z][A-Za-z0-9_ +\-]*?)[(（]', text))
    for match in reversed(starts):
        start = match.end() - 1
        depth, end = 1, start + 1
        while end < len(text) and depth:
            if text[end] in '(（': depth += 1
            elif text[end] in ')）': depth -= 1
            end += 1
        gloss = text[start + 1:end - 1]
        if not depth and re.search(r'[\u3400-\u9fff]', gloss):
            flat = re.sub(r'([A-Za-z][A-Za-z0-9_+\- ]*)[（(][^()（）]*[\u3400-\u9fff][^()（）]*[）)]', r'\1', gloss)
            text = text[:start + 1] + flat + text[end - 1:]
    protected = []
    def stash(value):
        protected.append(value)
        return f'\ue000{len(protected)-1}\ue001'
    def protect(match):
        return stash(match[0])
    # An existing bilingual label is one unit, including English in its gloss.
    text = re.sub(r'(?:`[^`\n]+`|[A-Za-z][A-Za-z0-9_ +\-]*?)[（(][^()（）\n]*[\u3400-\u9fff][^()（）\n]*[）)]', protect, text)
    # Filenames, paths, URLs, anchors and versioned model names are exact references.
    text = re.sub(r'(?:https?://[^\s<>`)]+|(?:[A-Za-z]:[/\\]|(?:\.\./)+|(?:backend|scripts|tests|docs|artifacts|output)/)[^\s`<>，；。]+|[A-Za-z0-9_\-]+\.(?:py|json|md|svg|png|html|pdf|txt|toml|yaml|yml|log)|\.(?:md|txt|py|json|svg|png|html|pdf|yaml|yml|toml)\b|(?:full-[A-Za-z0-9-]+|q-\d+|example-\d+)|\b[a-f0-9]{16,}\b)', protect, text)
    # Inline code: add the gloss OUTSIDE backticks, so identifiers remain copyable.
    def inline(match):
        raw = match[1]
        gloss = translation(raw)
        if gloss:
            return stash(match[0] + ('' if _already_translated(text[match.end():]) else f'({gloss})'))
        # A list of names is prose, not a call or command: annotate each name.
        parts = re.split(r'([ /,，；;|、]+)', raw)
        if len(parts) > 1 and all(not part or re.fullmatch(r'[ /,，；;|、]+', part) or translation(part) for part in parts):
            return stash(''.join((f'`{part}`({translation(part)})' if translation(part) else part) for part in parts))
        names = list(dict.fromkeys(m[0] for m in _TERM_RE.finditer(raw)))
        names += [m[0] for m in _IDENTIFIER.finditer(raw) if translation(m[0]) and m[0] not in names]
        glosses = list(dict.fromkeys(translation(name) for name in names))
        if glosses:
            return stash(match[0] + '(' + '；'.join(glosses) + ')')
        return protect(match)
    text = re.sub(r'`([^`\n]+)`', inline, text)
    def identifier(match):
        gloss = translation(match[0])
        return match[0] + (f'({gloss})' if gloss and not _already_translated(text[match.end():]) else '')
    text = _IDENTIFIER.sub(identifier, text)
    # Protect annotations introduced for compound identifiers against reannotation.
    text = re.sub(r'[A-Za-z][A-Za-z0-9_]*(?:\([^()]*[\u3400-\u9fff][^()]*\))', protect, text)
    text = _TERM_RE.sub(lambda m: m[0] if _already_translated(text[m.end():]) else f'{m[0]}({translation(m[0])})', text)
    # Nested masks are restored from last to first.
    for index in reversed(range(len(protected))):
        text = text.replace(f'\ue000{index}\ue001', protected[index])
    return text


def code_legend(code):
    """Translate identifiers beside a code block, keeping its bytes intact."""
    names = set(m[0] for m in _TERM_RE.finditer(code))
    names.update(m[0] for m in _IDENTIFIER.finditer(code) if translation(m[0]))
    # Language syntax is not a professional term or a business parameter.
    names -= {'async', 'await', 'str', 'int', 'float', 'bool', 'dict', 'list', 'set', 'tuple', 'None', 'true', 'false', 'null'}
    return '；'.join(f'`{name}`({translation(name)})' for name in sorted(names))


def annotate_markdown(text):
    """Preserve executable fences / link targets, adding readable Chinese glosses."""
    pieces = re.split(r'(^```[^\n]*\n.*?^```[ \t]*$)', text, flags=re.M | re.S)
    result = []
    for piece in pieces:
        if piece.startswith('```'):
            result.append(piece)
            legend = code_legend(piece.split('\n', 1)[1].rsplit('\n```', 1)[0])
            if legend:
                result.append('\n\n**示例术语与字段译注：** ' + legend + '。')
            continue
        # Drop our previous legend before regenerating it, making reruns idempotent.
        piece = re.sub(r'\n\n\*\*示例术语与字段译注：\*\*[^\n]*', '', piece)
        protected = []
        def link(match):
            protected.append(match[0])
            return f'\ue002{len(protected)-1}\ue003'
        piece = re.sub(r'<!--.*?-->', link, piece, flags=re.S)
        # Protect targets, translate labels unless they are exact file references.
        piece = re.sub(r'(?<=\])\((?:<[^>\n]*>|[^)\n]*)\)', link, piece)
        piece = annotate_text(piece)
        for index, value in enumerate(protected):
            piece = piece.replace(f'\ue002{index}\ue003', value)
        result.append(piece)
    return ''.join(result)


def annotate_file(path: Path):
    original = path.read_text(encoding='utf-8-sig')
    result = annotate_markdown(original)
    if result != original:
        path.write_text(result, encoding='utf-8', newline='\n')
    return result
