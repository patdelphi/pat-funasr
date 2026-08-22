# -*- coding: utf-8 -*-
"""
精细转录场景预设模板模块
提供 6 种场景的专业词表、ASR 参数、LLM prompt 模板
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SceneTemplate:
    """场景模板数据结构"""
    scene_id: str            # 场景标识: meeting, interview, lecture...
    name: str                # 场景名称
    description: str         # 场景说明
    hotwords: list = field(default_factory=list)   # 专业词表
    asr_params: dict = field(default_factory=dict)  # ASR 参数覆盖
    llm_prompt: str = ""     # LLM 二次优化 prompt
    summary_prompt: str = "" # 纪要提炼 prompt
    mindmap_prompt: str = "" # 思维导图 prompt
    output_formats: list = field(default_factory=lambda: ["transcript", "summary", "mindmap"])


# ========== 通用 LLM 系统提示 ==========
_LLM_BASE = "你是一个专业转写校对助手。请根据提供的专业词表和转写文本，执行以下任务："

# ========== 场景 1: 精细会议转录 ==========
MEETING = SceneTemplate(
    scene_id="meeting",
    name="精细会议转录",
    description="识别说话人，按发言人分段，纠错专业术语，提炼会议纪要(参会人员/概要/决策/行动项等)",
    hotwords=[
        "项目", "里程碑", "迭代", "敏捷", "需求评审", "站会", "复盘",
        "OKR", "KPI", "ROI", "预算", "采购", "供应链", "交付周期",
        "总经理", "总监", "经理", "主管", "产品经理", "架构师",
        "技术方案", "风险评估", "竞品分析", "用户画像", "转化率",
    ],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
        "diarization": True,
    },
    llm_prompt=_LLM_BASE + """
1. 根据专业词表纠正同音错字
2. 按语义重新分段，合并碎片句子
3. 保留说话人标注(SPEAKER_00 等)不丢失
4. 补充/修正标点
5. 输出纯文本，不要额外说明
""",
    summary_prompt="""请从以下会议转写文本中提取结构化纪要，返回 JSON 格式：
{
  "participants": ["参会人员姓名/角色"],
  "summary": "会议概要(2-3句)",
  "deadlines": ["截止日期/里程碑"],
  "decisions": ["达成的决策"],
  "action_items": ["行动项(责任人+任务+时间)"],
  "follow_ups": ["后续跟进事项"],
  "notes": ["其他重要备注"]
}
仅输出 JSON，不要额外解释。""",
    mindmap_prompt="""请从以下会议转写文本中提取思维导图，返回 JSON 树结构：
{
  "title": "会议主题",
  "children": [
    {"title": "议题1", "children": [{"title": "要点1"}, {"title": "要点2"}]},
    {"title": "议题2", "children": [...]}
  ]
}
层级不超过 3 级。仅输出 JSON。""",
)

# ========== 场景 2: 访谈/调研 ==========
INTERVIEW = SceneTemplate(
    scene_id="interview",
    name="访谈/调研",
    description="Q&A 格式整理，区分访问者和受访者，提取关键洞察",
    hotwords=[
        "受访者", "访谈", "问卷", "样本", "定性研究", "定量研究",
        "用户满意度", "NPS", "留存率", "渗透率", "市场份额",
        "痛点", "需求", "体验", "反馈", "建议",
    ],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
        "diarization": True,
    },
    llm_prompt=_LLM_BASE + """
1. 根据专业词表纠正同音错字
2. 整理为 Q&A 格式: 问: ... 答: ...
3. 区分访问者(Q)和受访者(A)，保留说话人标注
4. 补充标点
5. 输出纯文本
""",
    summary_prompt="""请从以下访谈转写文本中提取结构化摘要，返回 JSON：
{
  "interviewee": "受访者信息",
  "topics": ["讨论主题"],
  "key_insights": ["关键洞察"],
  "pain_points": ["用户痛点"],
  "suggestions": ["建议"],
  "quotes": ["重要原话引用"],
  "conclusion": "总结"
}
仅输出 JSON。""",
    mindmap_prompt="""请从以下访谈转写文本中提取思维导图，返回 JSON 树结构：
{
  "title": "访谈主题",
  "children": [
    {"title": "主题1", "children": [{"title": "洞察1"}, {"title": "洞察2"}]}
  ]
}
仅输出 JSON。""",
)

# ========== 场景 3: 讲座/课程 ==========
LECTURE = SceneTemplate(
    scene_id="lecture",
    name="讲座/课程",
    description="按知识点分段，生成学习笔记+思维导图",
    hotwords=[
        "知识点", "公式", "定理", "推导", "例题", "练习",
        "章节", "课时", "学分", "考试", "作业", "实验",
        "概念", "定义", "原理", "方法", "应用",
    ],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
    },
    llm_prompt=_LLM_BASE + """
1. 根据专业词表纠正同音错字，特别是公式和定理名称
2. 按知识点/主题分段
3. 补充标点，修正口语化表达为书面语
4. 保留时间戳对应关系
5. 输出纯文本
""",
    summary_prompt="""请从以下课程转写文本中提取学习笔记，返回 JSON：
{
  "topic": "课程主题",
  "key_points": ["核心知识点"],
  "formulas": ["重要公式"],
  "examples": ["例题/案例"],
  "summary": "内容概要",
  "homework": ["课后作业/思考题"],
  "references": ["参考资料/延伸阅读"]
}
仅输出 JSON。""",
    mindmap_prompt="""请从以下课程转写文本中提取知识点思维导图，返回 JSON 树结构：
{
  "title": "课程主题",
  "children": [
    {"title": "知识点1", "children": [{"title": "子要点1"}, {"title": "子要点2"}]},
    {"title": "知识点2", "children": [...]}
  ]
}
层级不超过 3 级。仅输出 JSON。""",
)

# ========== 场景 4: 法庭/取证 ==========
LEGAL = SceneTemplate(
    scene_id="legal",
    name="法庭/取证",
    description="逐字稿+摘要，标注关键时间点",
    hotwords=[
        "原告", "被告", "证人", "鉴定人", "审判长", "公诉人",
        "辩护人", "代理人", "上诉", "抗诉", "再审", "执行",
        "合同", "侵权", "违约", "赔偿", "管辖权", "诉讼时效",
        "证据", "质证", "认证", "事实", "法律依据",
    ],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
        "diarization": True,
    },
    llm_prompt=_LLM_BASE + """
1. 逐字稿模式，不删减任何内容
2. 严格区分各方发言(审判长/原告/被告/证人等)
3. 法律术语必须准确，根据词表纠正
4. 补充标点
5. 输出纯文本
""",
    summary_prompt="""请从以下庭审/取证转写文本中提取摘要，返回 JSON：
{
  "case_type": "案件类型",
  "parties": {"plaintiff": "原告", "defendant": "被告"},
  "claims": ["诉讼请求"],
  "evidence": ["证据清单"],
  "key_points": ["争议焦点"],
  "rulings": ["裁定/判决要点"],
  "timeline": [{"time": "时间", "event": "事件"}],
  "conclusion": "结论"
}
仅输出 JSON。""",
    mindmap_prompt="""请从以下庭审转写文本中提取案件逻辑思维导图，返回 JSON 树结构：
{
  "title": "案件概要",
  "children": [
    {"title": "诉讼请求", "children": [...]},
    {"title": "证据链", "children": [...]},
    {"title": "争议焦点", "children": [...]},
    {"title": "裁判结果", "children": [...]}
  ]
}
仅输出 JSON。""",
)

# ========== 场景 5: 医疗/问诊 ==========
MEDICAL = SceneTemplate(
    scene_id="medical",
    name="医疗/问诊",
    description="SOAP 格式整理，主诉/诊断/处方",
    hotwords=[
        "主诉", "现病史", "既往史", "过敏史", "体格检查",
        "血压", "心率", "体温", "血糖", "血常规", "尿常规",
        "诊断", "鉴别诊断", "治疗方案", "处方", "用药",
        "mg", "ml", "静脉注射", "口服", "外用",
        "随访", "复查", "转诊", "住院", "手术",
    ],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
    },
    llm_prompt=_LLM_BASE + """
1. 医学术语、药品名必须准确，根据词表纠正
2. 区分医生和患者发言
3. 补充标点
4. 保留剂量、单位等数值信息
5. 输出纯文本
""",
    summary_prompt="""请从以下医疗问诊转写文本中按 SOAP 格式提取，返回 JSON：
{
  "subjective": "主诉/现病史(患者自述)",
  "objective": "体格检查/实验室结果",
  "assessment": "诊断/评估",
  "plan": "治疗方案/处方/用药",
  "medications": [{"name": "药名", "dose": "剂量", "route": "给药途径"}],
  "follow_up": "随访/复查建议",
  "warnings": ["注意事项"]
}
仅输出 JSON。""",
    mindmap_prompt="""请从以下医疗问诊转写文本中提取诊疗思维导图，返回 JSON 树结构：
{
  "title": "诊疗概要",
  "children": [
    {"title": "主诉", "children": [...]},
    {"title": "检查", "children": [...]},
    {"title": "诊断", "children": [...]},
    {"title": "治疗", "children": [...]}
  ]
}
仅输出 JSON。""",
)

# ========== 场景 6: 通用转录 ==========
GENERAL = SceneTemplate(
    scene_id="general",
    name="通用转录",
    description="仅转写+分段，不做摘要",
    hotwords=[],
    asr_params={
        "use_itn": True,
        "vad_preset": "default",
    },
    llm_prompt=_LLM_BASE + """
1. 纠正明显同音错字
2. 按语义分段
3. 补充标点
4. 输出纯文本
""",
    summary_prompt="",
    mindmap_prompt="",
    output_formats=["transcript"],
)

# ========== 场景注册表 ==========
SCENE_REGISTRY: dict[str, SceneTemplate] = {
    t.scene_id: t for t in [MEETING, INTERVIEW, LECTURE, LEGAL, MEDICAL, GENERAL]
}

# 场景下拉选项(供 Gradio Dropdown 使用)
SCENE_CHOICES = [(t.name, t.scene_id) for t in [MEETING, INTERVIEW, LECTURE, LEGAL, MEDICAL, GENERAL]]


def get_template(scene_id: str) -> Optional[SceneTemplate]:
    """根据场景 ID 获取模板"""
    return SCENE_REGISTRY.get(scene_id)
