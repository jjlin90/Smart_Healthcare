from pathlib import Path
from html import escape
import math
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "medagent_upgrade_20261013"
OUT.mkdir(parents=True, exist_ok=True)
W, H, SCALE = 1600, 2500, 2
BG = '#f6f8fc'
INK = '#15243c'
MUTED = '#50617b'
BLUE = '#2563eb'
FONT = 'C:/Windows/Fonts/msyh.ttc'
BOLD = 'C:/Windows/Fonts/msyhbd.ttc'
canvas = Image.new('RGB', (W*SCALE, H*SCALE), BG)
draw = ImageDraw.Draw(canvas)
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       f'<rect width="{W}" height="{H}" fill="{BG}"/>']

def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, round(size*SCALE))

def text(x, y, value, size=24, color=INK, bold=False, anchor='middle'):
    draw.text((x*SCALE,y*SCALE), value, font=font(size,bold), fill=color,
              anchor='mm' if anchor=='middle' else 'lm')
    svg.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" dominant-baseline="central" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(value)}</text>')

def rect(x,y,w,h,fill='#fff',stroke='#ced8e8',radius=18):
    draw.rounded_rectangle((x*SCALE,y*SCALE,(x+w)*SCALE,(y+h)*SCALE),radius=radius*SCALE,fill=fill,outline=stroke,width=2*SCALE)
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')

def node(y,h,title,lines=(),x=460,w=680,fill='#fff',stroke='#ced8e8',size=25):
    rect(x,y,w,h,fill,stroke)
    count=len(lines)
    total=36+count*33
    start=y+(h-total)/2+18
    text(x+w/2,start,title,29,INK,True)
    for i,line in enumerate(lines):
        text(x+w/2,start+39+i*33,line,size,MUTED)

def arrow(points,color=BLUE,width=3,dashed=False):
    p=[(x*SCALE,y*SCALE) for x,y in points]
    if dashed:
        for (ax,ay),(bx,by) in zip(points,points[1:]):
            length=math.hypot(bx-ax,by-ay)
            if not length: continue
            for d in range(0,int(length),18):
                e=min(d+10,length)
                draw.line(((ax+(bx-ax)*d/length)*SCALE,(ay+(by-ay)*d/length)*SCALE,
                           (ax+(bx-ax)*e/length)*SCALE,(ay+(by-ay)*e/length)*SCALE),fill=color,width=width*SCALE)
    else:
        draw.line(p,fill=color,width=width*SCALE,joint='curve')
    svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"'+(' stroke-dasharray="10 8"' if dashed else '')+'/>')
    ax,ay=points[-2]; bx,by=points[-1]
    angle=math.atan2(by-ay,bx-ax)
    tip=[(bx,by),(bx-14*math.cos(angle)+7*math.sin(angle),by-14*math.sin(angle)-7*math.cos(angle)),(bx-14*math.cos(angle)-7*math.sin(angle),by-14*math.sin(angle)+7*math.cos(angle))]
    draw.polygon([(x*SCALE,y*SCALE) for x,y in tip],fill=color)
    svg.append(f'<polygon points="{" ".join(f"{x},{y}" for x,y in tip)}" fill="{color}"/>')

text(800,72,'MedAgent 项目主流程',48,INK,True)
text(800,132,'工作台 → API → 主协调器 → 专科智能体 → MCP 工具 → 保存并确认',24,MUTED)

# Background regions keep the main route visually separate from its branches.
rect(430,700,740,430,'#eaf1ff','#b8cdf9',26)
text(470,733,'主协调器：检查、路由、规划',24,BLUE,True,'start')
rect(430,1155,740,365,'#e9f7f2','#b6ddcf',26)
text(470,1187,'专科智能体：获取工具依据',24,'#13795b',True,'start')
rect(430,1560,740,145,'#fff6e7','#eed4a4',26)

# Draw connectors first so they sit behind all boxes and labels.
for start,end in [(316,370),(500,550),(646,775),(875,895),(1005,1020),(1110,1225),(1345,1395),(1500,1580),(1685,1745),(1855,1915),(2030,2110),(2240,2310)]:
    arrow([(800,start),(800,end)])
arrow([(460,598),(390,598)],'#c34b53')
arrow([(460,850),(390,850)],'#b77915',dashed=True)
arrow([(460,945),(412,945),(412,915),(390,915)],'#b77915',dashed=True)
arrow([(225,940),(225,2175),(460,2175)],'#b77915',dashed=True)
arrow([(1140,1630),(1210,1630)],'#c34b53',dashed=True)
arrow([(1140,1800),(1180,1800),(1180,1715),(1210,1715)],'#c34b53',dashed=True)
arrow([(1140,1800),(1190,1800),(1190,1448),(1140,1448)],'#13795b',dashed=True)

node(220,96,'医生使用工作台',['登录、选择已授权患者、提交问题'])
node(370,130,'API 接收请求',['患者 / 会话 / 请求标识；任务模式与版本','本轮批准动作；绝对期限与新任务隔离'])
node(550,96,'API 检查权限',['员工身份、当前角色、患者授权'])
node(551,94,'拒绝请求',['权限不通过'],x=60,w=330,fill='#fff0f1',stroke='#e6aeb3')
text(829,675,'权限通过',22,BLUE,False,'start')
node(775,100,'安全与业务范围检查',['SafetyGuard：风险检查；DomainGuard：范围检查'],size=21)
node(785,155,'提前返回',['安全提示 / 范围拒识','或追问缺失信息'],x=60,w=330,fill='#fff7e8',stroke='#e4c48c',size=23)
node(895,110,'意图识别与信息澄清',['IntentClassifier 识别任务类型','TaskReadinessGuard 检查典型缺失信息'],size=21)
node(1020,90,'确定执行计划',['最多三步；校验专科覆盖与前序依赖'],size=23)
text(829,1140,'A2A：任务、受限委托与剩余预算',21,BLUE,False,'start')
node(1225,120,'目标专科接收任务',['SymptomAgent / DrugAgent / GuideAgent','核验签名与原文摘要、重读档案、接收前序依据'],size=21)
node(1395,105,'Function Calling',['模型提出参数；代码检查关键槽位及来源'])
node(1580,105,'MCP 工具服务',['核验权限、预算与写状态；Redis 核对活跃版本'],size=21)
text(829,1542,'LangChain + MCPAdapter + MCP 客户端',20,'#13795b',False,'start')
node(1745,110,'专科处理工具结果',['脱敏 Observation、调用状态与参数来源'])
text(829,1884,'A2A：协调器核对任务 / 版本 / 步骤',20,BLUE,False,'start')
node(1915,115,'核对目标依据并汇总',['每项意图检查工具覆盖；汇总真实 Observation'])
node(2110,130,'API 持久化',['咨询、证据与来源审计、会话及任务版本','提前返回按对应分支规则保存'])
node(2310,90,'发送 done，页面确认完成',fill='#eaf1ff',stroke='#a8c3f9')

# Supporting notes show responsibilities without crowding the route.
rect(1210,765,330,178,'#fff','#ced8e8')
text(1375,801,'主协调器的分派对象',23,INK,True)
for i,t in enumerate(['症状 / 分诊 / 转诊 → Symptom','药品查询 / 审核 → Drug','指南 / 报告等 → Guide']):
    text(1375,846+i*31,t,19,MUTED)
rect(1210,1000,330,145,'#fff','#ced8e8')
text(1375,1034,'按依赖决定串行 / 并行',22,INK,True)
text(1375,1071,'有依赖先完成前序并传递依据',18,MUTED)
text(1375,1103,'独立只读并行；批准写动作串行',18,MUTED)
rect(60,1155,330,160,'#fff','#ced8e8')
text(225,1193,'共享任务有效性检查',22,INK,True)
text(225,1232,'生产必须配置 Redis 与完整任务域',17,MUTED)
text(225,1265,'本地无 Redis 时不提供跨进程围栏',17,MUTED)
rect(1210,1395,330,135,'#fff6e7','#eed4a4')
text(1375,1429,'工具的实际执行目标',22,INK,True)
text(1375,1470,'医院业务接口 / 本地数据库',21,MUTED)
text(1375,1500,'新调用受剩余预算；写动作需批准',18,MUTED)
node(1580,180,'失败分支',['只读依据不匹配：最多一次重路由','写入未知：核对状态，不自动重跑','普通工具故障：失败，不确认成功'],x=1210,w=330,fill='#fff0f1',stroke='#e6aeb3',size=20)
text(1280,1838,'绿色虚线：有预算的工具循环',18,'#13795b')
text(800,2449,'文字开始显示 ≠ 本轮已保存完成；最终 done 才确认对应分支完成。',23,MUTED)

svg.append('</svg>')
(OUT/'MedAgent_项目流程图.svg').write_text('\n'.join(svg),encoding='utf-8',newline='\n')
# Keep a cloneable canonical diagram alongside the implementation documents.
(OUT.parents[1]/'docs'/'MedAgent_项目流程图.svg').write_text('\n'.join(svg),encoding='utf-8',newline='\n')
canvas.save(OUT/'MedAgent_项目流程图.png',dpi=(192,192))
canvas.resize((800,1250),Image.Resampling.LANCZOS).save(OUT/'flowchart_preview.png')
print('Exported SVG and PNG (3200 × 5000).')
