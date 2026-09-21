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
    "症状分析": {
        "a": ["头痛并且恶心", "夜间反复咳嗽", "站起时头晕", "右下腹疼痛", "胸口发闷", "连续低烧", "皮肤突然起疹", "饭后胃胀反酸", "腰背酸痛", "总觉得疲乏无力"],
        "b": ["从昨天开始", "已经三天了", "最近一周", "这两个月反复出现", "今天突然发生"],
        "c": ["可能是什么原因", "想分析可能的方向", "这种表现常见于什么情况", "需要关注哪些可能性", "请帮我判断症状性质"],
    },
    "药品查询": {
        "a": ["阿莫西林", "布洛芬", "氯雷他定", "蒙脱石散", "二甲双胍", "奥美拉唑", "阿托伐他汀", "左氧氟沙星", "对乙酰氨基酚", "硝苯地平"],
        "b": ["主要作用", "适应症", "常见副作用", "说明书信息", "保存方式"],
        "c": ["请介绍一下", "想查清楚", "帮我查询", "用通俗的话说明", "我想了解"],
    },
    "指南检索": {
        "a": ["高血压", "2型糖尿病", "慢阻肺", "哮喘", "冠心病", "脂肪肝", "骨质疏松", "慢性肾病", "脑卒中", "肺结节"],
        "b": ["诊断标准", "治疗推荐", "随访管理", "运动管理", "危险分层"],
        "c": ["最新临床指南怎么规定", "请检索权威共识", "现行诊疗规范怎么说", "帮我查指南依据", "指南有哪些推荐"],
    },
    "检验解读": {
        "a": ["白细胞", "空腹血糖", "谷丙转氨酶", "肌酐", "低密度脂蛋白", "血小板", "血红蛋白", "尿酸", "促甲状腺激素", "C反应蛋白"],
        "b": ["高于参考范围", "低于正常值", "轻度异常", "连续两次升高", "结果标了箭头"],
        "c": ["这个指标代表什么", "请解释数值含义", "应该怎样理解", "异常可能说明什么", "帮我解读化验结果"],
    },
    "分诊建议": {
        "a": ["发烧咳嗽", "膝盖活动时疼", "耳朵疼", "持续头晕", "胃部反复不适", "皮肤瘙痒起疹", "胸口闷", "腰痛", "眼睛红肿", "手指关节疼"],
        "b": ["成年人", "老人", "儿童", "孕妇", "第一次就诊的人"],
        "c": ["应该看哪个科", "先去什么科室", "如何分诊比较合适", "该挂哪一科", "请推荐就诊科室"],
    },
    "健康咨询": {
        "a": ["睡眠时间", "日常饮食", "办公室锻炼", "控制体重", "戒烟", "久坐改善", "饮水安排", "作息调整", "有氧运动", "健康体检"],
        "b": ["普通成年人", "经常加班的人", "中老年人", "久坐上班族", "大学生"],
        "c": ["怎样安排更健康", "有哪些日常建议", "如何形成好习惯", "需要注意什么", "怎么做比较合适"],
    },
    "用药指导": {
        "a": ["华法林和阿司匹林", "抗生素漏服一次", "青霉素过敏时用药", "布洛芬和感冒药", "降压药忘记服用", "二甲双胍饭前还是饭后", "多种保健品与处方药", "儿童退烧药", "孕期服用止痛药", "药片能否掰开"],
        "b": ["结合我的情况", "已经服用其他药", "有过敏史", "肝肾功能异常", "医生暂时联系不上"],
        "c": ["应该怎样处理", "能不能一起使用", "需要核对哪些禁忌", "服用时要注意什么", "请给个体化用药提醒"],
    },
    "疾病科普": {
        "a": ["脂肪肝", "甲状腺功能减退", "带状疱疹", "高血压", "糖尿病", "哮喘", "痛风", "骨质疏松", "肺结节", "胃食管反流"],
        "b": ["基本概念", "常见表现", "可能诱因", "一般发展过程", "日常认识误区"],
        "c": ["请通俗介绍", "想系统了解", "请做基础科普", "这种病是怎么回事", "帮我解释相关知识"],
    },
    "挂号指引": {
        "a": ["心内科", "消化内科", "皮肤科", "儿科", "骨科", "呼吸科", "内分泌科", "眼科", "耳鼻喉科", "神经内科"],
        "b": ["预约明天下午门诊", "查询专家号源", "取消已有预约", "修改就诊时间", "第一次线上挂号"],
        "c": ["手机上怎么操作", "请说明办理流程", "需要准备什么信息", "在哪里可以完成", "帮我提供挂号步骤"],
    },
    "报告解读": {
        "a": ["胸部CT报告", "年度体检报告", "腹部超声报告", "肝功能检查报告", "心电图报告", "肺功能报告", "胃镜报告", "甲状腺超声报告", "骨密度报告", "病理检查报告"],
        "b": ["出现多项异常", "提示需要随访", "有一处不理解", "结论写得很专业", "前后指标有变化"],
        "c": ["请整体解读", "帮我综合说明结论", "想了解整份报告意味着什么", "请按重要程度解释", "帮我梳理报告重点"],
    },
}

TEMPLATES = {
    "train": [
        "{b}，我有{a}，{c}。",
        "{c}：{b}出现{a}。",
        "关于{a}，{b}，{c}？",
        "麻烦{c}，情况是{b}{a}。",
        "我想咨询{a}，{b}，{c}。",
    ],
    "validation": [
        "能否针对{b}的{a}{c}？",
        "有个问题想请教：{b}{a}，{c}。",
        "请从专业角度{c}，涉及{a}，时间情况是{b}。",
    ],
    "test": [
        "家里人问我{a}这件事，{b}，想请你{c}。",
        "不太确定该怎么理解：{b}，涉及{a}，{c}？",
        "请直接回答，{a}在{b}这种情况下，{c}。",
        "我主要想弄清{a}，补充情况为{b}，{c}。",
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
                "complex_task": label in {"用药指导", "报告解读"},
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
        "药品查询": "问我个人该怎么吃药",
        "分诊建议": "办理线上预约",
        "挂号指引": "判断应该看哪个科",
        "疾病科普": "分析我现在的症状",
        "报告解读": "只解释一个化验数值",
    }
    target_labels = list(exclusions)
    seen_negation: set[str] = set()
    for index in range(50):
        target = target_labels[index % len(target_labels)]
        candidates = by_label[target]
        base = candidates[(index // len(target_labels)) % len(candidates)]["text"]
        text = f"我不是想{exclusions[target]}，真正想问的是：{base}"
        if text in seen_negation:
            raise RuntimeError("否定样本生成了重复文本")
        seen_negation.add(text)
        rows.append({"id": f"challenge-negation-{index + 1:03d}", "text": text, "expected_intents": [target], "complex_task": target in {"用药指导", "报告解读"}, "challenge_type": "negation", "source": "synthetic_bootstrap"})

    ambiguous = [
        ("症状分析", "这几天身体表现有些异常，想判断可能的原因"),
        ("健康咨询", "没有具体疾病，只想改善平时的生活习惯"),
        ("疾病科普", "不是问我本人诊断，想了解这种疾病的基础知识"),
        ("药品查询", "不涉及个人服法，只查这个药的通用说明"),
        ("分诊建议", "不办理预约，只想知道这种情况该去哪个科"),
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
