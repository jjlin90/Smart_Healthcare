"""Build a reproducible synthetic bootstrap dataset for the ten intent classes.

The generated test data is useful for engineering regression, not for claiming
clinical accuracy. A human-reviewed real-world test set must remain separate.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
from collections import Counter
from pathlib import Path


SPECS = {
    "症状评估": {
        "a": ["头痛并且恶心", "夜间反复咳嗽", "站起时头晕", "右下腹疼痛", "胸口发闷", "连续低烧", "皮肤突然起疹", "饭后胃胀反酸", "腰背酸痛", "总觉得疲乏无力"],
        "b": ["从昨天开始", "已经三天了", "最近一周", "这两个月反复出现", "今天突然发生"],
        "c": ["可能是什么原因", "想分析可能的方向", "这种表现常见于什么情况", "需要关注哪些可能性", "请帮我判断症状性质"],
    },
    "药品信息查询": {
        "a": ["阿莫西林", "布洛芬", "氯雷他定", "蒙脱石散", "二甲双胍", "奥美拉唑", "阿托伐他汀", "左氧氟沙星", "对乙酰氨基酚", "硝苯地平"],
        "b": ["主要作用", "适应症", "常见副作用", "说明书信息", "保存方式"],
        "c": ["请介绍一下", "想查清楚", "帮我查询", "用通俗的话说明", "我想了解"],
    },
    "临床指南检索": {
        "a": ["高血压", "2型糖尿病", "慢阻肺", "哮喘", "冠心病", "脂肪肝", "骨质疏松", "慢性肾病", "脑卒中", "肺结节"],
        "b": ["诊断标准", "治疗推荐", "随访管理", "运动管理", "危险分层"],
        "c": ["最新临床指南怎么规定", "请检索权威共识", "现行诊疗规范怎么说", "帮我查指南依据", "指南有哪些推荐"],
    },
    "检验结果辅助解读": {
        "a": ["白细胞", "空腹血糖", "谷丙转氨酶", "肌酐", "低密度脂蛋白", "血小板", "血红蛋白", "尿酸", "促甲状腺激素", "C反应蛋白"],
        "b": ["高于参考范围", "低于正常值", "轻度异常", "连续两次升高", "结果标了箭头"],
        "c": ["这个指标代表什么", "请解释数值含义", "应该怎样理解", "异常可能说明什么", "帮我解读化验结果"],
    },
    "院内分诊建议": {
        "a": ["发烧咳嗽", "膝盖活动时疼", "耳朵疼", "持续头晕", "胃部反复不适", "皮肤瘙痒起疹", "胸口闷", "腰痛", "眼睛红肿", "手指关节疼"],
        "b": ["成人患者", "老年患者", "儿童患者", "孕期患者", "初诊患者"],
        "c": ["判断院内接诊科室", "给出院内分诊科室", "形成分诊建议", "建议首诊科室", "推荐院内接诊科室"],
    },
    "随访管理": {
        "a": ["高血压出院患者", "糖尿病复诊患者", "冠心病术后患者", "慢阻肺稳定期患者", "抗凝治疗患者", "肿瘤治疗后患者", "骨折术后患者", "妊娠期高血压患者", "脑卒中康复患者", "肺结节观察患者"],
        "b": ["复查周期", "依从性评估", "异常指标回访", "生活方式管理", "失访风险"],
        "c": ["制定随访计划", "生成复诊提醒要点", "梳理随访项目", "评估随访优先级", "明确需要回访的事项"],
    },
    "用药审核": {
        "a": ["华法林和阿司匹林", "抗生素漏服一次", "青霉素过敏时用药", "布洛芬和感冒药", "降压药忘记服用", "二甲双胍饭前还是饭后", "多种保健品与处方药", "儿童退烧药", "孕期服用止痛药", "药片能否掰开"],
        "b": ["结合患者档案", "合并使用其他药物", "病历记录有过敏史", "检验提示肝肾功能异常", "当前医嘱需要复核"],
        "c": ["审核处置风险", "核对能否合并使用", "核对禁忌事项", "形成药学审核要点", "给出用药风险提示"],
    },
    "临床知识查询": {
        "a": ["脂肪肝", "甲状腺功能减退", "带状疱疹", "高血压", "糖尿病", "哮喘", "痛风", "骨质疏松", "肺结节", "胃食管反流"],
        "b": ["基本概念", "常见表现", "可能诱因", "一般发展过程", "日常认识误区"],
        "c": ["查询临床特征", "整理基础定义", "检索鉴别要点", "查询常见表现", "梳理临床知识"],
    },
    "转诊协同": {
        "a": ["心内科", "消化内科", "皮肤科", "儿科", "骨科", "呼吸科", "内分泌科", "眼科", "耳鼻喉科", "神经内科"],
        "b": ["院内转科", "跨院转诊", "多学科会诊", "急诊转住院", "基层上转"],
        "c": ["生成转诊协同要点", "核对目标科室", "整理转诊原因", "准备转诊资料清单", "形成待确认转诊申请"],
    },
    "检查报告辅助解读": {
        "a": ["胸部CT报告", "年度体检报告", "腹部超声报告", "肝功能检查报告", "心电图报告", "肺功能报告", "胃镜报告", "甲状腺超声报告", "骨密度报告", "病理检查报告"],
        "b": ["出现多项异常", "提示需要随访", "有一处不理解", "结论写得很专业", "前后指标有变化"],
        "c": ["请整体辅助解读", "综合说明报告结论", "整理整份报告的临床关注点", "按重要程度解释", "梳理报告重点"],
    },
}

TEMPLATES = {
    "train": [
        "患者情况为{b}，涉及{a}，请{c}。",
        "请{c}：病例信息为{b}，涉及{a}。",
        "关于该患者的{a}，补充信息为{b}，请{c}。",
        "临床辅助任务：{b}{a}，请{c}。",
        "需要处理{a}，当前病例信息为{b}，请{c}。",
    ],
    "validation": [
        "请针对病例中的{b}和{a}{c}。",
        "院内辅助请求：{b}{a}，请{c}。",
        "请从临床辅助角度{c}，涉及{a}，补充信息是{b}。",
    ],
    "test": [
        "该病例涉及{a}，补充信息为{b}，请{c}。",
        "需要辅助处理：{b}，涉及{a}，请{c}。",
        "请处理院内任务，{a}在{b}情况下需要{c}。",
        "当前需要处理{a}，病例补充为{b}，请{c}。",
    ],
}

COUNTS = {"train": 200, "validation": 40, "test": 100}


def stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def build_single_split(split: str) -> list[dict]:
    rows: list[dict] = []
    for label, spec in SPECS.items():
        candidates = {
            template.format(a=a, b=b, c=c)
            for template, a, b, c in itertools.product(TEMPLATES[split], spec["a"], spec["b"], spec["c"])
        }
        ordered = sorted(candidates)
        random.Random(stable_seed(split, label)).shuffle(ordered)
        selected = ordered[: COUNTS[split]]
        if len(selected) != COUNTS[split]:
            raise RuntimeError(f"{label}/{split} 只能生成 {len(selected)} 条")
        for index, text in enumerate(selected, start=1):
            rows.append({
                "id": f"{split}-{list(SPECS).index(label) + 1:02d}-{index:03d}",
                "text": text,
                "expected_intents": [label],
                "complex_task": label in {"用药审核", "检查报告辅助解读", "转诊协同"},
                "source": "synthetic_bootstrap",
            })
    random.Random(stable_seed(split)).shuffle(rows)
    return rows


def build_challenge(test_rows: list[dict]) -> list[dict]:
    rng = random.Random(stable_seed("challenge"))
    by_label = {label: [row for row in test_rows if row["expected_intents"] == [label]] for label in SPECS}
    rows: list[dict] = []
    labels = list(SPECS)

    seen_multi: set[str] = set()
    index = 0
    attempts = 0
    while index < 250:
        attempts += 1
        if attempts > 5000:
            raise RuntimeError("无法生成足够的唯一多意图样本")
        first, second = rng.sample(labels, 2)
        left = rng.choice(by_label[first])["text"].rstrip("。？")
        right = rng.choice(by_label[second])["text"].rstrip("。？")
        text = f"{left}；另外，{right}。"
        if text in seen_multi:
            continue
        seen_multi.add(text)
        expected = [label for label in labels if label in {first, second}]
        index += 1
        rows.append({"id": f"challenge-multi-{index:03d}", "text": text, "expected_intents": expected, "complex_task": True, "challenge_type": "multi_intent", "source": "synthetic_bootstrap"})

    exclusions = {
        "药品信息查询": "审核该患者的个体用药",
        "院内分诊建议": "发起院内转诊",
        "转诊协同": "判断症状风险",
        "临床知识查询": "分析患者当前症状",
        "检查报告辅助解读": "只解释一个化验数值",
    }
    target_labels = list(exclusions)
    seen_negation: set[str] = set()
    for index in range(50):
        target = target_labels[index % len(target_labels)]
        candidates = by_label[target]
        base = candidates[(index // len(target_labels)) % len(candidates)]["text"]
        text = f"本次不是要{exclusions[target]}，实际院内任务是：{base}"
        if text in seen_negation:
            raise RuntimeError("否定样本生成了重复文本")
        seen_negation.add(text)
        rows.append({"id": f"challenge-negation-{index + 1:03d}", "text": text, "expected_intents": [target], "complex_task": target in {"用药审核", "检查报告辅助解读", "转诊协同"}, "challenge_type": "negation", "source": "synthetic_bootstrap"})

    ambiguous = [
        ("症状评估", "患者近期表现异常，需要评估可能方向"),
        ("随访管理", "需要为出院患者制定复查和回访计划"),
        ("临床知识查询", "不做患者诊断，只查询该疾病的临床特征"),
        ("药品信息查询", "不做个体用药建议，只查该药通用说明"),
        ("院内分诊建议", "不发起转诊，只判断该病例应由哪个科室处理"),
    ]
    for index in range(50):
        label, base = ambiguous[index % len(ambiguous)]
        text = f"{base}，请先明确我问的是什么。编号{index + 1:02d}。"
        rows.append({"id": f"challenge-ambiguous-{index + 1:03d}", "text": text, "expected_intents": [label], "complex_task": False, "challenge_type": "ambiguous_boundary", "source": "synthetic_bootstrap"})

    ood_texts = [
        "帮我订一张明天去上海的高铁票", "写一段公司年会主持词", "今天北京天气怎么样", "解释这段Python报错", "推荐一部周末看的电影",
        "帮我计算房贷月供", "把这段中文翻译成英文", "给我做一份旅游路线", "查询快递到了哪里", "讲一个睡前故事",
    ]
    for index in range(50):
        text = f"{ood_texts[index % len(ood_texts)]}（请求{index + 1:02d}）"
        rows.append({"id": f"challenge-ood-{index + 1:03d}", "text": text, "expected_intents": [], "complex_task": False, "expected_behavior": "clarify_or_reject", "challenge_type": "out_of_domain", "source": "synthetic_bootstrap"})

    rng.shuffle(rows)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/datasets"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    datasets = {split: build_single_split(split) for split in COUNTS}
    datasets["challenge"] = build_challenge(datasets["test"])
    all_texts = [row["text"].strip().lower() for rows in datasets.values() for row in rows]
    duplicates = len(all_texts) - len(set(all_texts))
    if duplicates:
        raise RuntimeError(f"跨数据集发现 {duplicates} 条重复文本")

    for split, rows in datasets.items():
        write_jsonl(args.output_dir / f"intent_{split}.jsonl", rows)

    manifest = {
        "total": sum(len(rows) for rows in datasets.values()),
        "splits": {split: len(rows) for split, rows in datasets.items()},
        "single_intent_distribution": {
            split: dict(Counter(row["expected_intents"][0] for row in rows if len(row["expected_intents"]) == 1))
            for split, rows in datasets.items()
        },
        "challenge_distribution": dict(Counter(row.get("challenge_type") for row in datasets["challenge"])),
        "exact_text_overlap": 0,
        "provenance": "synthetic_bootstrap",
        "warning": "合成数据用于训练、冒烟和回归；独立真实测试集必须由人工复核，不能据此声明临床准确率。",
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
