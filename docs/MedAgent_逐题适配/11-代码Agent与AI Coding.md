# 11-代码Agent与AI Coding：MedAgent 逐题口述

核查日期：2026-10-13。所有答案以院内 ToB 边界为前提。一般理论与未落地的扩展方案明确分开。

## q-001

### 主问题｜Vibe Coding 指的是什么能力？

原文位置：11-代码Agent与AI Coding.md 第 25 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Vibe Coding 的原始语义比“AI 辅助编程”更窄：开发者主要观察程序行为、用自然语言继续调整，甚至不再逐行理解和审查生成代码。它适合低风险原型和一次性项目，但这种“先接受、跑起来再说”的工作方式不能直接等同生产级 AI Coding。[Karpathy 原始帖存档](https://threadreaderapp.com/thread/1886192184808149383.html)

面试里应先说清原义，再给出工程纠偏：生产开发仍需需求拆解、架构与安全边界、代码审查、测试和发布控制。更严谨的名称是 AI-assisted / agentic software engineering（AI 辅助／智能体软件工程），而不是把所有“自然语言写代码”都叫 Vibe Coding。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-002

### 追问｜面试里为什么被高频问？

原文位置：11-代码Agent与AI Coding.md 第 33 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

因为它触及 AI coding 的核心命题：AI 能写到什么程度、人还要不要懂代码、质量谁负责。面试官借这个题判断你对"人机分工"的理解，而不是考察工具熟练度。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-003

### 追问｜与 Agent 自主性的关系？

原文位置：11-代码Agent与AI Coding.md 第 37 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Vibe Coding 描述的是一种使用方式，不是一种 Agent 架构。代码 Agent 可以被用于 Vibe Coding，也可以在严格规范、测试、权限和审批下运行；二者不能按“自主性高低”直接画等号。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-004

### 主问题｜用 AI Coding 开发复杂前后端模块，人参与哪些环节？

原文位置：11-代码Agent与AI Coding.md 第 41 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

复杂模块里 AI 擅长"在清晰约束下快速实现"，但不擅长"定义清晰约束"和"判断对错"。所以人重点参与四个环节：

1. 需求与架构拆解：把模糊需求拆成明确的、AI 能实现的子任务和接口边界；

2. 关键决策：技术选型、状态设计、安全边界、性能要求；

3. 代码审查与验收：AI 生成的代码是否真符合要求、是否有隐藏 bug；

4. 测试与兜底：设计测试用例、验收关键路径、处理 AI 搞不定的边界。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-005

### 追问｜人机分工边界？

原文位置：11-代码Agent与AI Coding.md 第 52 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

边界应按风险和可验证性划分，而不是简单说“AI 负责实现、人负责目标”。低风险、可回滚且有强测试覆盖的改动可提高自主度；涉及架构迁移、权限、数据、资金、生产发布或验收标准不清时，人必须参与决策和批准。AI 也可以提出方案，人也可能亲自实现关键代码。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-006

### 追问｜如何评估生成结果是否可靠？

原文位置：11-代码Agent与AI Coding.md 第 56 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

不能只看"能跑"，要看：① 是否真满足需求（功能正确）；② 是否有测试覆盖关键路径；③ 是否引入安全/性能隐患；④ 边界和异常是否处理。核心是用可执行验证（测试/静态检查）替代"看着像对了"。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-007

### 主问题｜Loop Engineering 的五个要素是什么？

原文位置：11-代码Agent与AI Coding.md 第 60 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Loop Engineering（循环工程）强调让 AI 开发形成"生成 → 执行验证 → 反馈 → 修正 → 再生成"的闭环，而不是一次性生成就结束。它的核心是反馈驱动：没有反馈的生成会一直犯同样的错。

常见的闭环要素可以概括为五个方向：① 明确的验收标准（先定义"什么算对"）；② 生成；③ 可执行的验证（测试/运行/静态检查）；④ 自动化的反馈（把错误信息喂回给模型）；⑤ 迭代修正（根据反馈改变实现，直到满足验收或触发停止条件）。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-008

### 追问｜与 Harness Engineering 的区别？

原文位置：11-代码Agent与AI Coding.md 第 70 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Loop 关注反馈驱动的迭代控制：如何根据观测判断继续、修正、重规划或停止。Harness（驾驭工程）的范围更广，指包围 Agent 的可执行环境与工程控制，包括上下文入口、工具、权限与架构约束、测试、日志/指标、进度产物和恢复机制。“验证夹具”只是 Harness 的一部分。

两者不是互斥组件：Loop 通过 Harness 执行动作并获得真实观测，再把结构化反馈交给下一轮。编排侧的 Harness 调用边界见 对应主题。OpenAI 的工程实践将环境、工具、可观测性、约束和反馈循环统一放在 Harness Engineering 中理解。[OpenAI：Harness engineering](https://openai.com/index/harness-engineering/)

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-009

### 追问｜各自解决什么问题？

原文位置：11-代码Agent与AI Coding.md 第 76 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Harness 解决“Agent 在什么环境和边界内工作，如何看见真实状态并可重复验证”；Loop 解决“拿到反馈后如何推进，以及什么时候停止”。没有 Harness，Loop 容易在文本自证中空转；没有有界 Loop，Harness 提供的观测和验证也不会自动转化为修正。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-010

### 主问题｜代码单测分支覆盖率怎么统计？插桩的底层原理？

原文位置：11-代码Agent与AI Coding.md 第 80 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

分支覆盖率 = 被测代码中被执行到的分支数 / 总分支数（如 `if` 的 true/false 两条路径算两个分支）。它衡量测试对分支路径的覆盖程度。

插桩（instrumentation）的底层原理：工具先建立控制流中的候选 branch/arc，再在源码、IR（Intermediate Representation，中间表示）或字节码层插入计数器/探针；测试运行时记录实际走过的边，最后汇总覆盖率。`if` 的 true/false 通常算两个分支，但循环、异常边和短路表达式是否计入、怎样计入取决于语言与覆盖率工具，不能给出跨工具统一口径。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-011

### 追问｜插桩的底层原理？

原文位置：11-代码Agent与AI Coding.md 第 88 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

三步：① 解析源码/AST（Abstract Syntax Tree，抽象语法树）找到分支位置；② 注入探针（通常是给分支分配 ID，插入"该 ID 执行了"的标记）；③ 运行时收集 + 事后汇总成覆盖率报告。核心是"在分支处埋点"。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-012

### 追问｜仅靠 AST 或 LSP 难以自动生成单测时怎么办？

原文位置：11-代码Agent与AI Coding.md 第 92 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

AST 提供语法结构，LSP 暴露符号、引用、类型和诊断等语言服务结果；二者都不能直接给出业务意图、真实运行状态和外部副作用。遇到重度依赖外部服务、动态生成或反射等代码时，先做依赖隔离，把难测边界抽出来替换为合适的 test double（测试替身）；再由人定义测试意图并让 AI 辅助生成用例，必要时用契约测试或行为测试替代对内部实现的机械覆盖。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-013

### 主问题｜单元测试 Mock 怎么实现？

原文位置：11-代码Agent与AI Coding.md 第 96 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

Mock 是 test double（测试替身）的一种，通常既提供预设行为，也记录调用以验证交互。Stub 主要返回预设数据，Fake 提供简化但可工作的实现，不能把所有测试替身都统称为 Mock。常见实现方式包括：

1. 依赖注入 + 替身：把依赖通过构造器/参数注入，测试时传入 mock 对象；

2. 动态代理/猴子补丁：运行时替换方法实现（如 Python 的 `unittest.mock`、Java 的 Mockito）；

3. 框架生成：用测试框架在运行时拦截方法调用并返回预设值。

核心不是"造假"，而是让测试只关注被测逻辑本身，排除外部依赖的不确定性。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-014

### 追问｜与覆盖率统计的关系？

原文位置：11-代码Agent与AI Coding.md 第 108 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

两者配合：覆盖率告诉你"哪些分支没测到"，mock 帮你"把测不到的分支变得可测"（隔离掉阻碍测试的外部依赖）。mock 让难测的分支也能被覆盖，但 mock 太多会让测试脱离真实环境。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-015

### 追问｜复杂依赖怎么 mock？

原文位置：11-代码Agent与AI Coding.md 第 112 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

分层 mock：先 mock 最外部的边界（网络、DB、文件），再对复杂内部依赖做接口抽象。原则是mock 边界而非实现细节——mock 掉不稳定、慢、有副作用的依赖，保留被测的真实逻辑。过度 mock 会让测试失真，需在"隔离"和"真实"间平衡。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-016

### 主问题｜你有哪些可复用的 AI Coding 技巧？

原文位置：11-代码Agent与AI Coding.md 第 116 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

技巧不是“多写几个神奇 Prompt（提示词）”，而是把任务改造成 Agent 能持续验证的工程循环：

1. 先建立事实基线：让 Agent 读取仓库约束、关键入口、测试命令和当前失败，再提出改动。

2. 缩小任务面：一次只交付一个可验收目标，明确不能改的接口、文件和行为。

3. 先写验收再写实现：复现 bug 或补测试，约定功能、边界、性能与安全检查。

4. 让反馈可执行：把编译、测试、lint、类型检查、日志或截图交回循环，不用“看起来应该对”。

5. 控制 diff 与上下文：要求最小改动，分阶段审查；长任务把计划、决策和未完成项写入持久化文件。

6. 高风险动作留给人：依赖升级、数据库迁移、权限、密钥、生产发布和破坏性操作需要审批。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-017

### 追问｜怎样防止 Agent 修一个问题又破坏另一个？

原文位置：11-代码Agent与AI Coding.md 第 129 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

建立保留回归集并先跑基线；每次只改变一个主要变量，检查 diff、相关测试和全量回归。若测试无法覆盖关键风险，就补契约测试、集成测试或人工验收，不能用更长 Prompt 代替验证。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

## q-018

### 追问｜什么时候应该停止让 Agent 继续改？

原文位置：11-代码Agent与AI Coding.md 第 133 行（原件保存在同步前备份）。

**项目口述：** 我用现有仓库的工具失败、授权撤销、重复写和断流案例说明工程验证。AI 可以帮助生成改动、定位日志和补回归，人仍负责需求、医疗边界、权限设计和最终检查；新代码要跑对应行为测试，而不是只检查格式。

**针对本题的设计展开（原文原理，作为方案解释）：**

连续修改没有新增证据、同一失败重复出现、改动范围开始越界、预算耗尽，或剩余问题需要产品/架构决策时停止。输出当前事实、已尝试路径和最小待决问题，交给人判断或重新规划。

**在 MedAgent 中怎么补全：** Mock 外部医院、模型和传输可以稳定验证失败分支，仍要有独立契约及真实组件联调。覆盖率只说明执行到分支，不证明断言充分；把修改范围、回归条件和终止标准写清楚，不能靠不断让模型改代码保证可靠。

**代码依据：** [tests/test_task_contracts.py](../../tests/test_task_contracts.py)、[tests/test_enterprise_boundaries.py](../../tests/test_enterprise_boundaries.py)、[tests/test_audit_regressions.py](../../tests/test_audit_regressions.py)

