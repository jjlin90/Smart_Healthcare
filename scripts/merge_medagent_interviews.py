"""Keep all interview questions in the main book, with readable answer layers.

The optional 72-topic guide supplies concrete examples. It never replaces source
questions, and generation never rewrites that human-edited guide.
"""
from pathlib import Path
import json
import re
import os
import sys
import hashlib
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIBRARY = ROOT / "docs/MedAgent_逐题适配"
BOOK = ROOT / "docs/MedAgent_AI_多Agent项目话术.md"
START = "<!-- BEGIN MEDAGENT FULL QUESTION LIBRARY -->"
END = "<!-- END MEDAGENT FULL QUESTION LIBRARY -->"

# Narrow topic matches choose related examples, never generate an answer from a
# broad chapter label. The original question-specific explanation remains first.
EXAMPLE_TOPICS = (
    (r'重规划|replan|动态改计划', 20), (r'计划版本|plan_version|旧版本|旧结果', 21),
    (r'幂等|重复写|重复发消息', 29), (r'结果未知|unknown|写.*超时', 30),
    (r'转诊', 28), (r'修订号|revision|档案.*编辑', 31),
    (r'Prompt Injection|注入|指令欺骗', 37), (r'Key.*泄露|敏感信息|隐私|脱敏', 38),
    (r'委托|Delegation|Audience', 35), (r'角色|患者.*权限', 34),
    (r'工具.*白名单|工具.*权限|权限.*最小', 36), (r'权限不足|越权', 33),
    (r'安全.*风险|SafetyGuard', 15),
    (r'多标签|Sigmoid|Softmax', 10), (r'三路|融合|正则|BGE', 9),
    (r'阈值|置信度|相似度', 59), (r'槽位|缺.*信息|澄清', 12),
    (r'意图.*错误|识别.*错误|Badcase|Bad Case|badcase', 16),
    (r'意图漂移|换话题|任务切换|继承.*槽位', 13),
    (r'Plan.and.Execute|ReAct|Supervisor', 17), (r'计划.*校验|DAG|依赖', 18),
    (r'串行|并行', 19), (r'死循环|反复调用|max_steps|调用.*上限', 22),
    (r'工具.*依据|验收|结果.*校验', 23), (r'子.*失败|结果.*冲突', 24),
    (r'Function Calling|MCP.*区别', 25),
    (r'Checkpoint|checkpoint|断点|断点恢复|状态快照', 44),
    (r'上下文.*过长|上下文.*压缩|历史.*压缩|token budget|工具结果太长', 43),
    (r'会话.*隔离|串数据|session_id', 42),
    (r'上下文污染|全局 state|局部状态|State.*Memory|长期记忆', 41),
    (r'流式|SSE', 45), (r'done|保存.*失败|持久化.*完成', 46),
    (r'汇总|聚合|幻觉', 47), (r'事务|回滚', 48),
    (r'超时|deadline|任务.*预算', 49), (r'重试|退避', 50),
    (r'熔断|限流|背压|过载', 51), (r'异步|GIL|阻塞|线程', 52),
    (r'锁|并发.*会话', 53), (r'首.*Token|性能|吞吐|耗时', 54),
    (r'成本|降本', 55), (r'启动|运维|部署', 56),
    (r'训练集|验证集|测试集|合成数据', 58), (r'评测|评估|准确率', 61),
    (r'SFT|微调|RAG', 64), (r'Mock|mock|单测|覆盖率|故障回归', 62),
    (r'AI Coding|Vibe Coding|生成代码', 69),
    (r'ASR|VAD|TTS|WER|CER|语音|多模态', 70),
    (r'个人.*职责|自己.*负责|贡献', 65), (r'最大.*难点|遇到.*问题', 66),
    (r'需求|客户|甲方|业务.*收益', 67),
)

# These short notes explain dense terms with an example, rather than repeating
# a generic project paragraph as the answer to an algorithm / speech question.
PLAIN_NOTES = (
    (r'reward hacking|奖励.*漏洞|reward.*共谋',
     '奖励是衡量目标的代理，得分高不一定真的完成任务。例如评分只检查“有没有引用”，模型可能堆砌无关引用得高分，却没有回答问题。需要检查真实任务结果，并用未参与训练的样本发现这种取巧。'),
    (r'Verifier|判题器',
     '判题器也会漏检，不能把“它说通过”当作真理。例如只检查转诊返回编号，仍可能漏掉科室或患者不一致。应分别验证判题器的误接受、误拒绝和异常路径，并保留人工抽查。'),
    (r'熵坍缩|entropy collapse',
     '可以把熵理解为模型保留多少种选择。过早只选一条路径，看起来输出稳定，却可能失去尝试其他解法的能力。例如所有问题都套同一回答模板，奖励暂时不错，困难题却一起失败。要同时看策略变化、样本多样性和独立评测结果。'),
    (r'梯度饥饿|gradient starvation',
     '梯度是训练时推动参数调整的信号。如果容易样本或常见模式长期主导更新，少见但重要的能力可能得不到足够学习信号。例如大多数资料查询学得很好，否定请求仍总分错。应检查数据覆盖、采样和损失贡献，不能只盲目提高学习率。'),
    (r'训推不一致|训练与推理|训练/推理',
     '训练和上线时看到的信息、允许的动作不同，会让学到的方法无法使用。例如训练时给模型完整工具答案，上线却要求它自己逐步查询；离线答得好，不代表会正确调用工具。评测要使用接近上线的输入、预算和工具条件。'),
    (r'KL 散度|KL.*约束|采样估计 KL',
     'KL 约束用于控制新策略偏离参考策略的程度。约束太强，模型难以学到新行为；太弱，又可能偏离过大。统计时还要说明是按词元平均还是按整段求和，否则长回答天然累计更多，两个数不能直接比较。'),
    (r'GRPO|group size|组大小',
     '同一道题生成一组回答，再比较组内谁的奖励更好。组更大可能提供更多比较，但生成与评分更贵；如果一组全对或全错，奖励相同，仍可能缺少有效区分。应固定总预算比较效果，不能只说组越大越好。'),
    (r'Self.Attention|Q/K/V|Query.*Key',
     '可以把 Query 看成当前位置想找什么，Key 看成其他位置提供的匹配线索，Value 是真正汇总的信息。先算匹配程度，再按权重汇总内容。三者由不同投影学习，因此“怎样匹配”和“传递什么”可以分别调整。'),
    (r'ASR|WER|CER',
     '语音识别的平均字错率低，也可能把关键药名识别错。例如一段话只有一个字错了，却改变了药名或剂量，业务影响很大。选型还要检查领域词、数字与单位、噪声、口音和交互延迟；本项目尚未接入完整语音链路。'),
    (r'VAD|端点检测|TTS|流式 ASR',
     '实时语音要经过收音、判断是否说完、识别、业务处理和播报等阶段。端点等待太长会让人觉得系统迟钝，太短又可能截断药名或数字。应测整段交互中各阶段的延迟，并单独核对关键字段；这是扩展设计。'),
    (r'插桩|分支覆盖率|覆盖率统计',
     '插桩是在执行过程中记录哪些语句或分支被走过。覆盖率说明代码运行到了哪里，不说明结果判断一定正确。例如测试执行了权限拒绝分支，却没断言请求被拒绝，覆盖率仍可能很好看。还要检查断言与业务后果。'),
    (r'Mock|mock',
     'Mock 用受控替身代替外部依赖，让失败场景可以稳定复现。例如让医院接口返回超时，再检查系统是否阻止第二次写入。它能验证本地控制逻辑，不能证明真实医院接口的行为完全一致，因此仍需联调。'),
)

# A small set of original follow-ups had only a generic project paragraph.
# Supply an actual explanation for those questions, retaining their identities.
QUESTION_EXPLANATIONS = {
    '00-q-009': '按“谁遇到什么问题—系统如何推进—怎么控制风险—验证到哪一步”讲。先说院内员工需要把分散资料查全，再说授权、路由、专科工具与汇总链路，最后用一次重复写入或保存失败的例子说明工程价值。技术栈和测试数字用于追问，不占满开场。',
    '01-q-008': '先找到拆分依据。业务目标、数据入口和工具权限明显不同时，拆分有助于隔离职责；如果只是接口多，但都属于同一目标和授权范围，可以先放在同一专科。任务链长本身不是增加智能体的充分理由，还要看是否真有独立决策和验收边界。',
    '01-q-030': '把模糊诉求拆成可验证的任务：谁提出请求、操作哪类数据、输出什么、允许哪些写动作、失败时交给谁。再列出明确不支持的范围。例如“辅助用药”先落到查说明书、相互作用和禁忌，而不是承诺自动诊疗。',
    '01-q-047': '先与业务方确定一类具体任务和当前操作基线，再定义质量、权限和效率指标。先交付能安全完成的最小范围，验证通过后再扩展。没有医院实测基线时，讲测量方法与目标，不能把假设节省比例写成成果。',
    '02-q-008': '四个机制分别解决不同问题：独立查询可并行，网络等待用异步，缓存减少相同且仍有效的数据查询，阶段结果降低等待的不透明感。不能缓存未确认写入，也不能把阶段成功展示成整轮完成。当前已有有界并行与流式事件，不代表具备任意任务的后台队列或通用缓存平台。',
    '02-q-013': '这是独立业务设计题，可按用户身份、订单查询、知识检索、处理策略和人工工单分层。先验证订单归属，再区分咨询与退款等副作用操作，自动处理只覆盖明确授权、规则可验证的范围。MedAgent 仅提供架构类比，没有实现售后订单或退款功能。',
    '02-q-014': '先确认用户身份与订单归属，再并行或按依赖查订单和知识，识别问题类型后选择查询、规则处理或人工工单。写动作要校验金额、对象、确认和去重，结果未知时先核对状态。这里解释售后系统的设计步骤，不属于 MedAgent 的现有业务。',
    '02-q-015': '遇到身份或归属不清、关键事实冲突、自动流程不支持、高影响写动作无法确认，或连续澄清仍无法形成可执行任务时，应转人工。转交时带上已核验信息、失败阶段和操作状态，不能让人工从零重问，也不能把未确认动作重新执行。',
    '03-q-014': '先判断缺的是业务目标还是参数：目标不确定就给候选让用户确认，参数缺失就只追问缺项；历史只能在同一待续任务中提供辅助。改写查询要保留药名、否定、时间等关键约束，不能把模型猜出的新值当成用户事实。',
    '03-q-021': '会决定查询对象或业务安全的字段通常必填，例如具体药名、报告内容或确认后的患者与科室。仅影响展示的选项可以有明确默认值。缺必填项时解释缺什么及为何需要，一次优先问最关键的信息；医学事实、身份和写入参数不能随意给默认值。',
    '03-q-024': '判断缺失信息是否会改变目标对象、结果含义或允许动作。如果不同补全会得到不同业务结果，就先问，不能猜。例如“解释这个报告”却没报告内容，或“这个药能用吗”却没有药名，都不能直接形成可靠查询。',
    '03-q-026': '只在任务连续、事实仍有效且来源可信时复用。明确的新问题重建任务；更正目标后使旧参数失效；上一轮模型回答不能自动升级成临床事实。例如补充药名可接续待澄清查询，但切换患者后不能沿用前一患者用药。',
    '03-q-030': '把抽取值与原输入、字段格式及可信业务数据交叉检查，冲突或歧义时确认。例如城市和时间要检查格式与范围，产品编号应核对数据库归属；在 MedAgent 中，药名要有本轮输入或可信档案依据，模型不能自行替换患者身份。',
    '03-q-035': '按风险选择处理：关键必填项追问；不改变业务含义的可选项可用明确默认值；同一待续任务中的可信信息可复用；无法澄清或超出能力范围时转人工。不要为了减少追问而默认患者、剂量或写入目标。',
    '03-q-036': '这是一组策略，需要补充适用条件。必填项缺失暂停执行；可选展示项才适合默认；历史补全要求任务和事实仍适用；反复无法确认时交人工。当前项目覆盖典型参数澄清，不是一个已配置任意槽位和人工工单的通用平台。',
    '03-q-050': '语义路由是根据问题的意思选择业务能力，而不只匹配固定关键词。常见做法是把问题与意图示例编码成向量，比较相似度并应用阈值。例如“两个药能一起吃吗”虽然没有“相互作用”四个字，也可能属于用药审核；相似度不足或多义时仍要复核。',
    '04-q-005': '优先围绕能独立解释和验收的业务职责拆分，再为职责绑定工具，而不是每个接口创建一个智能体。例如药品查询和用药审核可共享药品专科的相关工具；它不能因此获得转诊写权限。工具多只是线索，还要看目标、上下文和授权是否不同。',
    '04-q-089': '执行失败是没能完成动作，例如接口超时或参数被拒；评估不通过是动作运行了，但结果不满足目标或质量要求。例如药品接口正常返回空资料，可能通信成功却没有目标依据。前者处理可用性和状态，后者检查数据与验收，不能一律重试。',
    '04-q-093': '事实变化要保留来源、更新时间和适用范围，再判断哪些结果或确认已失效。例如患者过敏史被修正后，旧的用药审核结果不能无条件复用。原始记录保留以便追溯，新的任务使用当前可信事实；计划版本与事实版本需要分别理解。',
    '04-q-094': '在同一批任务上比较完整上下文与压缩上下文的表现，检查关键实体、否定、数值、时间和证据引用有没有丢失。例如把“无青霉素过敏”压成“青霉素过敏”，长度变短但业务含义反了。要统计任务成功与错误继承，不能只比较压缩率。',
    '04-q-103': '至少区分模型、提示词、工具结构、知识来源、计划和状态数据版本，评测集也要可追溯。目的不是给所有对象起编号，而是能解释某次运行使用了什么，以及更新后哪些缓存、旧结果或确认不再适用。当前任务计划有版本控制，完整全链路版本注册平台属于扩展。',
    '06-q-035': '信任顺序要按字段和权威来源制定，不能只设一句“数据库永远最高”。身份与授权听服务端；当前临床事实需看可靠来源、时间和人工更正；外部检索与助手摘要只能提供辅助。发生冲突时保留冲突项并核对，不把两个版本拼成一个确定结论。',
    '08-q-003': '先检查是否在比较同一患者、时间和条件，再核对来源及工具是否成功。能定位原因就按可信且适用的依据处理，不能解释的冲突要明确呈现并交人工复核。例如两个不同日期的指标不应被误当成同次检验互相矛盾；不能只让汇总模型任选一个。',
    '08-q-024': '可以先展示阶段进度或已确认的局部查询，但必须标明范围与未完成项。阶段文字不等于整轮成功，更不能表示写入已确认。当前主要在最终汇总阶段流式出字，正常分支完成要求的保存后才发完成事件，没有任意专科结果实时恢复的通用能力。',
    '12-q-002': '平均词错率或字错率把不同错误近似按数量统计，但业务影响可能完全不同。药名、剂量或单位错一个字，就可能比十个语气词错误更严重。因此还要专门统计关键实体与数字的正确率，并结合延迟、噪声和口音条件评估。',
    '12-q-003': '除字词错误率，还应看药名和数值单位准确率、首段识别与最终确认延迟、误截断率、噪声与口音表现，以及失败后能否方便重说或人工确认。不同场景权重不同，不能只给一个平均分；本项目尚未落地完整语音功能。',
    '13-q-005': '收件人必须来自可信业务状态或经用户确认的目标，不让邮件正文或检索文字直接覆盖。执行前对照实际地址、操作范围和批准内容；目标改变就重新确认。将这个原则类比到 MedAgent，就是确认后的患者、科室和原因不能在调用途中被替换；项目本身没有邮件发送功能。',
}


def guide_examples():
    path = LIBRARY / '核心题理解导读.md'
    if not path.is_file():
        return {}, ''
    text = path.read_text(encoding='utf-8-sig')
    examples = {}
    for match in re.finditer(r'^### Q(\d\d) [^\n]+\n(.*?)(?=^### Q|^## |\Z)', text, re.M | re.S):
        example = re.search(r'\*\*例子：\*\* ([^\n]+)', match[2])
        if example:
            examples[int(match[1])] = example[1]
    introduction = text.partition('## 一分钟项目介绍\n')[2].split('## 主题导航', 1)[0].strip()
    return examples, introduction


def original_reason(chunk):
    match = re.search(r'\*\*针对本题的设计展开[^\n]*\n(.*?)(?=\n\*\*(?:在 MedAgent|代码依据|本题图)|\Z)', chunk, re.S)
    if not match:
        return ''
    # Cross-table answers refer to the original explanation, not its examples.
    return match[1].split('\n### example-', 1)[0].strip()


def prose_parts(text, transform):
    """Change labels in prose while preserving every fenced example byte."""
    parts = re.split(r'(^```[^\n]*\n.*?^```[ \t]*$)', text, flags=re.M | re.S)
    return ''.join(part if part.startswith('```') else transform(part) for part in parts)


def question_sections(book, coverage):
    # Remove fences, including misleading headings / labels inside examples.
    prose = re.sub(r'^```[^\n]*\n.*?^```[ \t]*$', '', book, flags=re.M | re.S)
    expected = {row['book_anchor'] for row in coverage['questions']}
    headings = [m for m in re.finditer(r'^### ([^\n]+)\n', prose, re.M) if m[1] in expected]
    return {m[1]: prose[m.end():headings[i+1].start() if i+1 < len(headings) else len(prose)]
            for i, m in enumerate(headings)}


def reading_summary(book, coverage):
    """Persist measured layout / supplement identities, not a hand-typed total."""
    sections = question_sections(book, coverage)
    counts = {'schema_version': 1, 'question_count': len(sections)}
    for field, label in (
        ('principle_questions', '先讲原理'), ('outline_questions', '回答思路'),
        ('project_questions', '项目里怎么说'), ('extension_questions', '扩展与限制'),
        ('plain_notes', '通俗理解'), ('scenario_notes', '用例理解')):
        counts[field] = sum(len(re.findall(r'^\*\*' + label + r'：\*\*', section, re.M))
                            for section in sections.values())
    entries = [{'book_anchor': anchor, 'source_key': key,
                'content_sha256': hashlib.sha256(explanation.encode()).hexdigest()}
               for anchor, section in sections.items()
               for key, explanation in QUESTION_EXPLANATIONS.items() if explanation in section]
    counts.update(supplemented_question_count=len(entries), supplemented_questions=entries)
    return counts


def readable_question(chunk, row, detailed, examples):
    parts = re.split(r'(?m)(?=^### example-\d+)', chunk, maxsplit=1)
    question, tail = parts[0], ''.join(parts[1:])
    oral = re.search(r'^\*\*项目口述：\*\* ([^\n]+)\n', question, re.M)
    project = oral[1] if oral else ''
    if oral:
        question = question[:oral.start()] + question[oral.end():]
    # Full source positions remain in the chapters and coverage manifest. A
    # short, direct link keeps that provenance out of the answer's first line.
    question = re.sub(r'^(?:原文位置：|来源：)[^\n]*\n', '', question, flags=re.M)
    question = re.sub(r'\*\*针对本题的设计展开[^\n]*', '**先讲原理：**', question)
    has_reason = '**先讲原理：**' in question
    local_reason = detailed.get((row['output'], row['anchor']), '')
    related = re.search(r'\*\*对应详解：\*\* \[[^\n]*?\]\(<?([^#<>]+)#(q-\d+)>?\)', question)
    if not has_reason:
        reason = (detailed.get((unquote(related[1]), related[2]), '') if related else local_reason)
        if reason:
            position = re.search(r'(?m)^### [^\n]+\n', question).end()
            question = question[:position] + '\n**回答思路：**\n\n' + reason + '\n\n' + question[position:]
    def normalize_extensions(prose):
        return re.sub(r'\*\*(?:在 MedAgent[^\n]*怎么补全：|方案补全：)\*\*', '**扩展与限制：**', prose)
    question = prose_parts(question, normalize_extensions)
    tail = prose_parts(tail, normalize_extensions)
    extra = ('**项目里怎么说：** ' + project + '\n\n') if project else ''
    context = row.get('reading_context', row.get('question', ''))
    for pattern, note in PLAIN_NOTES:
        if re.search(pattern, context, re.I):
            extra += '**通俗理解：** ' + note + '\n\n'
            break
    else:
        for pattern, number in EXAMPLE_TOPICS:
            if re.search(pattern, context, re.I) and number in examples:
                extra += '**用例理解：** ' + examples[number] + '\n\n'
                break
    # Put current implementation beside (rather than inside) the explanation of
    # the general principle, before optional extensions and source-code paths.
    marker = re.search(r'(?m)^\*\*(?:扩展与限制|代码依据|对应详解|本题图)', question)
    if marker:
        question = question[:marker.start()] + extra + question[marker.start():]
    else:
        question = question.rstrip() + '\n\n' + extra
    source = f"**原题定位：** [分章原题]({row['output']}#{row['anchor']})；原件行号见[覆盖清单](coverage.json)。\n\n"
    question = question.rstrip() + '\n\n' + source
    return re.sub(r'\n{3,}', '\n\n', question) + tail


def example_summary(coverage):
    counts = {kind: sum(row['association'] == kind for row in coverage['examples'])
              for kind in ('question', 'chapter_overview', 'chapter_appendix')}
    return (f"{counts['question']} 个示例随对应问答保留，{counts['chapter_overview']} 个章首示例单列概览，"
            f"{counts['chapter_appendix']} 个独立段落示例单列章节附录")


def merge():
    coverage_path = LIBRARY / "coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    chapters = [row["name"] for row in coverage["source_files"]]
    codes = {name: name[:2] if re.match(r"\d\d-", name) else "cross" for name in chapters}
    anchors = {(q["output"], q["anchor"]):f"full-{codes[q['output']]}-{q['anchor']}" for q in coverage["questions"]}
    anchors.update({(q["output"], q["anchor"]): f"full-{codes[q['output']]}-{q['anchor']}"
                    for q in coverage.get("examples", [])})
    examples, introduction = guide_examples()
    raw = {name: (LIBRARY/name).read_text(encoding='utf-8-sig') for name in chapters}
    chunks = {(name, match[1]): match[0] for name, chapter in raw.items()
              for match in re.finditer(r'^## (q-\d+)\s*\n.*?(?=^## q-|\Z)', chapter, re.M | re.S)}
    detailed = {key: original_reason(value) or QUESTION_EXPLANATIONS.get(
        codes[key[0]] + '-' + key[1], '') for key, value in chunks.items()}
    base = ("# MedAgent：完整面试话术与逐题讲解\n\n"
            f"完整保留 {coverage['question_count']:,} 处问答（含重复问法）与 {coverage['example_count']} 个图、伪代码和结构示例。"
            + example_summary(coverage) + "。\n\n"
            "没有将完整题库缩成 72 题。[72 题理解导读](MedAgent_逐题适配/核心题理解导读.md)只是可选的预习入口，全部原题及答案仍在本文件。\n\n"
            "每题先读“先讲原理／回答思路”，理解为什么；再看“项目里怎么说”，区分当前做法；有对应场景的题补“用例理解”。“扩展与限制”及伪代码用于继续追问，不表示功能已经落地。"
            "重复问法保留，来源和原件行号集中到分章与覆盖清单，避免每题先读一大段路径。\n\n"
            "个人职责按真实分工陈述。项目已有本地工程验证，真实医院接入、临床验收和业务提效需另行测量。本地 .txt 原件不随仓库克隆分发。")
    base += ("\n\n**编辑说明：** 本文件由分章题库与理解导读生成，直接修改主文会在下次生成时被覆盖。"
             "原题答案请改对应分章；项目开场请改导读；新增的原理补充由生成器中的问答补充表维护。")
    if introduction:
        base += '\n\n## 一分钟项目介绍\n\n' + introduction
    appendix = [START, "## 全文档逐题正文", "", "原始资料编号为 00—08、10—13，加一份横向题表，共 14 份；来源本身没有 09 号文件，跳号不是遗漏。", "", "### 全文章节导航", ""]
    for name in chapters:
        count = sum(q["output"] == name for q in coverage["questions"])
        appendix.append(f"- [{Path(name).stem}：{count} 处問答](#full-{codes[name]})".replace("問", "问"))
    appendix.append("")
    def rebase(text, name):
        def link(match):
            label, raw = match.groups()
            raw = raw.strip("<>")
            if re.match(r"(?:https?://|[A-Za-z]:/)", raw): return match.group(0)
            path, marker, fragment = unquote(raw).partition("#")
            if label != '分章原题' and (path, fragment) in anchors:
                return f"[{label}](#{anchors[(path,fragment)]})"
            if not path and (name, fragment) in anchors:
                return f"[{label}](#{anchors[(name,fragment)]})"
            target = (LIBRARY / path).resolve()
            if target.is_relative_to(ROOT):
                relative = Path(os.path.relpath(target, BOOK.parent)).as_posix()
                dest = relative + marker + fragment
                return f"[{label}](<{dest}>)" if " " in dest else f"[{label}]({dest})"
            return match.group(0)
        blocks = re.split(r"(^```[^\n]*\n.*?^```[ \t]*$)", text, flags=re.M | re.S)
        return ''.join(block if block.startswith('```') else re.sub(
            r"\[([^\]\n]+)\]\((<[^>\n]+>|[^)\s]+)\)", link, block) for block in blocks)
    for name in chapters:
        chapter = raw[name]
        rows = {row['anchor']: row for row in coverage['questions'] if row['output'] == name}
        parent = ''
        for anchor, row in rows.items():
            chunk = chunks[(name, anchor)]
            if re.search(r'^### 主问题', chunk, re.M):
                parent = row.get('question', '')
            # Short follow-ups such as “早期信号是什么” need their parent topic.
            rows[anchor] = {**row, 'reading_context': row.get('question', '') + ' ' + parent}
        chapter = re.sub(r'^## (q-\d+)\s*\n.*?(?=^## q-|\Z)',
                         lambda m: readable_question(m[0], rows[m[1]], detailed, examples),
                         chapter, flags=re.M | re.S)
        # All content after the first question belongs to the complete body.
        body = chapter[chapter.index("## q-"):]
        body = re.sub(r"(?m)^### ", "#### ", body)
        body = re.sub(r"(?m)^## (q-\d+)[ \t]*$", lambda m: "### " + anchors[(name,m[1])], body)
        body = re.sub(r"(?m)^#### (example-\d+)[ \t]*$", lambda m: "#### " + anchors[(name,m[1])], body)
        body = body.replace("## 原文图与结构示例（ToB适配）", "### 原文图与结构示例（ToB适配）")
        body = body.replace("## 章节概览示例（无对应问答）", "### 章节概览示例（无对应问答）")
        body = body.replace("## 章节附录示例（无对应问答）", "### 章节附录示例（无对应问答）")
        appendix += [f"## full-{codes[name]}", "", f"**{Path(name).stem}：项目适配问答**", "", rebase(body,name).rstrip(), ""]
    appendix.append(END)
    # Chapters already contain bilingual terms and code legends. Reannotating
    # the edited introduction / examples would reintroduce repetitive glosses.
    result = base + "\n\n" + "\n".join(appendix) + "\n"
    for q in coverage["questions"]:
        anchor = anchors[(q["output"],q["anchor"])]
        if result.count("### " + anchor + "\n") != 1:
            raise ValueError("Question missing or duplicated: " + anchor)
        q["book"] = BOOK.name
        q["book_anchor"] = anchor
    for example in coverage.get("examples", []):
        anchor = anchors[(example["output"], example["anchor"])]
        if result.count("#### " + anchor + "\n") != 1:
            raise ValueError("Example missing or duplicated: " + anchor)
        example["book_anchor"] = anchor
    coverage.pop('reference_document', None)
    coverage['full_question_document'] = BOOK.relative_to(ROOT).as_posix()
    coverage['reading_layout'] = reading_summary(result, coverage)
    BOOK.write_text(result, encoding="utf-8", newline="\n")
    coverage_path.write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"chapters":len(chapters),"questions":len(coverage['questions']),"book_lines":len(result.splitlines()),"book":str(BOOK)},ensure_ascii=False))


if __name__ == "__main__":
    merge()
