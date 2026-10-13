# 12-多模态与语音Agent：MedAgent 逐题口述

核查日期：2026-10-13。所有答案以院内 ToB 边界为前提。一般理论与未落地的扩展方案明确分开。

## q-001

### 主问题｜ASR 选型怎么做？

原文位置：12-多模态与语音Agent.md 第 24 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

ASR（Automatic Speech Recognition，自动语音识别）选型不能只看平均错误率。英文常用 WER（Word Error Rate，词错误率），中文通常更常用 CER（Character Error Rate，字符错误率）；二者都只反映转写差异，覆盖不了真实业务关心的维度。完整选型至少看六项：

1. WER/CER：按语言和 tokenizer 口径选择基础转写错误率，并固定归一化规则；

2. 流式支持：是否支持边听边识别（低延迟连续交互通常需要）；

3. 延迟：首字延迟、稳态延迟；

4. 领域/口音/噪声适配：对专有名词、方言、嘈杂环境的鲁棒性；

5. 成本与部署：SaaS 按量计费 vs 私有化部署；

6. 标点/数字/实体：标点恢复、数字转写、专有名词识别是否正确。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-002

### 追问｜为什么不能只看 WER/CER？

原文位置：12-多模态与语音Agent.md 第 37 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-003

### 追问｜还要看哪些指标？

原文位置：12-多模态与语音Agent.md 第 41 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-004

### 主问题｜流式 ASR 的最低可用延迟由哪些环节决定？怎么压延迟？

原文位置：12-多模态与语音Agent.md 第 45 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

流式 ASR 的端到端延迟由一串环节叠加决定：音频采集与网络传输 → VAD（Voice Activity Detection，语音活动检测）起点检测 → chunk/右上下文等待 → 特征与编码器/解码器计算 → partial 稳定 → endpoint/finalization → 下游意图、Agent 与 TTS（Text-to-Speech，语音合成）首包。只报模型推理时间会漏掉最常见的端点和网络等待。

压延迟的关键：

1. 流式解码：不等整段结束，按 chunk 产生带 revision 的 partial；

2. 区分 VAD 与 endpointing（端点检测）：VAD 尽早发现开始说话，endpointing 判断何时提交 final；二者阈值分别调优；

3. 投机执行但延迟提交：partial 可用于预取、检索和意图候选，只有稳定/final 文本才能触发不可逆工具写操作；

4. 控制 chunk 与右上下文：通过真实语料画 accuracy-latency 曲线，不只压单点延迟；

5. 流水线并行：ASR、检索、LLM prefill 和 TTS 在安全边界内重叠，同时保留取消和版本校验。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-005

### 追问｜怎么把延迟压下去？

原文位置：12-多模态与语音Agent.md 第 58 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

核心是“边听边形成可修订假设，边做可撤销的投机计算”。不能把“partial 看起来语义完整”当作最终提交条件；要记录 hypothesis version，修订后使旧推测失效，高风险动作必须等待稳定文本和授权。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-006

### 追问｜与 VAD/端点检测、TTS 首包的关系？

原文位置：12-多模态与语音Agent.md 第 62 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

VAD 主要判断是否存在语音，endpointing/endpoint detector 决定当前话轮何时结束：过早会截断，过晚会拖延迟。TTS 首包决定用户何时听到回应，barge-in（打断）则要求 VAD/声学前端在播放期间仍能检测用户发声。整条链路要看端到端分位延迟和正确性，而不是只压 ASR。[Google：低延迟流式 ASR](https://research.google/pubs/towards-fast-and-accurate-streaming-end-to-end-asr/)

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-007

### 主问题｜非流式 ASR 和流式 ASR 的根本差别？只用非流式行不行？

原文位置：12-多模态与语音Agent.md 第 66 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

根本差别是可用上下文和提交时机：非流式模型可利用整段未来音频后统一解码；流式模型受因果或有限右上下文约束，持续输出可能修订的 partial。非流式通常更容易获得高准确率，流式强调 latency-accuracy trade-off，但现代统一/两遍系统可以显著缩小差距，不能笼统断言流式一定更差。[Google：缩小 Streaming/Offline 差距](https://research.google/pubs/bridging-the-gap-between-streaming-and-non-streaming-automatic-speechrecognition-systems-through-distillation-of-an-ensemble-of-models/)

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-008

### 追问｜只用非流式行不行？

原文位置：12-多模态与语音Agent.md 第 74 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

离线转写优先非流式；实时交互通常优先流式或两遍架构。但若话轮很短且 SLA 允许，也可在端点后调用低延迟非流式模型。选择依据应是端到端延迟、准确率、打断和成本实测，而不是绝对规则。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

## q-009

### 追问｜实时交互场景为什么必须流式？

原文位置：12-多模态与语音Agent.md 第 78 行（原件保存在同步前备份）。

**项目口述：** 当前 MedAgent 是院内文字工作台，没有 ASR、TTS、电话或 OCR 服务。这类题可结合‘医生语音录入临床辅助任务’解释设计，不能扩展成患者电话挂号、预约、付费或自动问诊。

**针对本题的设计展开（原文原理，作为方案解释）：**

流式的主要收益是更早获得 partial，从而展示字幕、预取信息并降低话轮完成后的等待。打断本身由 VAD/播放控制完成；真正的业务动作仍要等稳定或 final 文本，避免 partial revision 造成误调用。

**在 MedAgent 中怎么补全：** 院内语音方案：员工登录授权后，ASR 识别、展示原文和关键药名供确认，再进入现有安全/意图/工具链，结果由工作台或 TTS 呈现。看医疗实体错误率、嘈杂环境表现、端点延迟和打断；转诊及病史写入要单独确认具体目标与参数。

**代码依据：** [streamlit_app.py](../../streamlit_app.py)、[backend/main.py](../../backend/main.py)、[backend/access.py](../../backend/access.py)

