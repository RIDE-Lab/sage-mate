"""Ingest paper-writing lessons and graduate-paper-writing-course materials
into the sage-mate knowledge base.

Usage:
    cd /home/shuhao/sage-mate
    python tools/ingest_paper_writing_knowledge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.source_ownership import SOURCE_OWNERSHIP

_MAX_CONTENT_LEN = 18000


def _read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[:_MAX_CONTENT_LEN]


_LECTURE_5_CONTENT = """# 第5讲：多维视角看论文写作

核心理念：写作 ≠ 单纯叙述结果；写作 = 构建可信、可重复、可传播的研究叙事。写得好的人并非语言最好的人，而是理解"读者视角"的人。

## 七种视角分析法

### 视角一 | 读者视角：目标是"快速理解"
- 读者在3分钟内判断是否值得读
- 写作策略：强标题 + 明摘要；图表讲故事；每段只表达一个意思
- 标题设计三条件：明确研究任务(what) + 暗示方法(how) + 挑战或场景边界(where/why)
- 弱标题警惕："A Study on…"、"三无标题"、滥用buzzwords
- 摘要三问：What is the problem? What is your solution? What is the result?
- 常见错误摘要：背景泛滥型、流程陈述型、留白型、空洞宣传型

### 视角二 | 同行视角：可对比与可复现性
- 是否能复现你的方法？是否能在已有框架中定位你的贡献？
- 用"差异定位 + 举例"表达差异，而不是说别人不行
- 正例："Unlike prior studies focusing on bounded latencies, we aim to handle unpredictable throughput bursts."

### 视角三 | 审稿人视角：找漏洞
- 目标：发现不合理假设、逻辑跳跃、论证不充分
- 策略：前提清晰，逻辑递进；明确假设边界与适用条件；多重实验支撑
- 正例：主动声明假设并披露局限（"We assume…This may not hold in…"）
- 反例：用泛化话术回避限制（"works under general settings"）

### 视角四 | 研究者视角：控制变量
- 确保核心假设可被验证；问题定义明确；避免confounding factors
- 正例："To isolate the impact of batching frequency, we fix the window size to 5s and disable all compression mechanisms."

### 视角五 | 未来研究者视角：是否可拓展
- 好结论不封死讨论，而是提供launchpad
- 正例：声明假设限制 + 指出具体拓展方向
- 反例："No future work is foreseen."

### 视角六 | 编辑视角：是否可发表
- 好的编辑视角表达 = 亮点突出 + 数据具体 + 命名醒目
- 正例含具体数字："retrieves top-10 in under 800ms on 100M-scale, outperforming SOTA by 2.3x"

### 视角七 | 自我视角：你是否相信这篇论文？
- 问自己：如果我不是作者会相信吗？我愿意把这句话挂homepage吗？
- 常见自欺句型："performs very well on a wide range"、"Due to space limits, details omitted"
- 高质量写作者：敢于暴露局限、准确引用数据、自信但不虚张

## 案例拆解：MapReduce (OSDI 2004)
七个视角都做得非常扎实：摘要清晰、接口定义明确、逻辑递进自然、容错策略充分、承认边界催生后续工作、创新明确、对读者诚实贡献定位克制。
"""

_LECTURE_6_CONTENT = """# 第6讲：培养写作能力的若干建议

## 一、常见的写作障碍
1. 空白恐惧：缺乏起步机制 + 担心写得不够好。解决：从中间写起或先写一句
2. 结构混乱：未事先规划结构。写作像拼图需要骨架
3. 拖延：写作压力大导致短期逃避。状态不是等来的，是写出来的
4. 完美主义：第一稿的目的不是'好'而是'完成'

## 二、基本写作周期
- 方式一：线性推进（Introduction→Method→Result→Conclusion），适合初学者
- 方式二：模块起步，从最熟的部分写起积累成就感，适合多数学生
- 方式三：结构驱动，先构思结构→写摘要→写引言→扩展，高效研究者通用

## 三、写作习惯培养
- 启动机制：写作块法(25-45min)、微目标法(每天一句)、写作仪式(固定时间地点)
- 反馈机制：版本追踪(Git/Overleaf)、自我回读(隔天大声读)、组内互评

## 四、写作资源与工具
- 表达层：Grammarly、DeepL Write、ChatGPT、Writefull
- 结构层：Zotero/Mendeley、Overleaf、Notion/Obsidian、Git+VSCode

核心信念：写作不是靠灵感，是靠习惯养成。固定节奏 + 反复训练 + 多轮修订 = 写作成长公式。
"""

_LECTURE_7_CONTENT = """# 第7讲：发表高水平论文

## 一、科研训练的常见误区
- 误区一：迷信热点与工作量（缺乏问题意识）
- 误区二：被动执行（不理解问题=无法讲清贡献）
- 误区三：为了"发"而发（高水平论文=选题+思考+实验+表达合力）

## 二、如何选题
坏选题特征：问题泛泛、没看文献、实验难落地、选题过大、没兴趣

好选题五标准：
1. 明确的问题（一句话说清）
2. 已有工作的延伸或挑战
3. 自己能持续有话说
4. 资源可行性
5. 与长期目标相关

选题三问法(3Q Check)：
- Q1: What is the problem?
- Q2: Why does it matter?
- Q3: Why is it unsolved?

## 三、如何读论文
推荐路径：摘要→引言→图表→结论→方法→实验细节
三阶段：快速通读(5-10min)→深度研读(30min+)→批判思考
上下文阅读：看引用链上下游 + 同期竞争者

## 四、科研时间管理四原则
1. 任务拆解→每周可完成的小目标
2. 固定时段→每天1-2小时免打扰深度块
3. 避免频繁切换
4. 每周回顾

## 五、心态调整
- 科研是长期非线性低反馈的探索过程
- 韧性机制：过程导向、外部反馈、自我记录
- 拒稿不是终点，是隐形合作
"""

_LECTURE_8_CONTENT = """# 第8讲：研究生毕业论文中常见的问题

核心判断：毕业论文最常见的失败是把内容、结构、格式问题混在一起改，导致越改越乱。

## 一、三层诊断法（按优先级）
1. 第一层：研究内容是否成立
2. 第二层：论文框架是否清楚
3. 第三层：格式规范是否一致

原则：内容不解决→结构调整无意义；结构不清→格式再漂亮也救不了

## 二、研究内容问题
五种表现：问题定义不清、贡献边界模糊、实验与结论脱节、研究动机不足、内容堆砌
三个失配：问题与贡献、贡献与实验、章节与主线

必须回答的四问：解决什么问题？为什么值得？核心贡献和差异？实验是否支撑？
导师五连问：一句话主问题？新在哪？证据对应？最易推翻的结论？删实验还成立否？

## 三、论文结构问题
章节任务：
- 引言：定义问题、说明难点、贡献地图
- 相关工作：比较维度与定位差异
- 方法：设计原则与关键机制
- 实验：验证贡献并解释观察
- 结论：回到问题给出边界局限

主线判断：每章能回答"在主问题上推进了哪一步"

## 四、修订流程
答辩前48小时安排：
- T-48h到T-36h：内容层修复
- T-36h到T-24h：结构层修复
- T-24h到T-12h：格式统一
- T-12h到T-0h：口头演练

答辩高频追问应答模板：
- 核心创新→一句话贡献+基线差异+证据编号
- 实验设置合理性→评价目标+场景约束+对应关系
- 方法局限→适用边界+已验证范围+后续工作
"""


_DOCUMENTS = [
    {
        "title": "论文写作经验｜英文系统论文修改准则（Paper Revision Lessons）",
        "content_source": "/home/shuhao/private-materials/论文写作/paper_revision_lessons.md",
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "revision", "writing-standard"],
        "source_name": "paper-writing:revision-lessons-english",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "language": "en+zh"},
    },
    {
        "title": "论文写作经验｜中文综述修改规范（CCCF Survey Revision Lessons）",
        "content_source": "/home/shuhao/private-materials/论文写作/cccf_chinese_survey_revision_lessons.md",
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "revision", "chinese-survey", "cccf"],
        "source_name": "paper-writing:revision-lessons-chinese-survey",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "language": "zh"},
    },
    {
        "title": "论文写作课程｜第5讲：多维视角看论文写作（七种视角分析法）",
        "content_inline": _LECTURE_5_CONTENT,
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "multi-perspective"],
        "source_name": "graduate-paper-writing-course:lecture-5",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "ordinal_type": "lecture", "ordinal": "5"},
    },
    {
        "title": "论文写作课程｜第6讲：培养写作能力的若干建议",
        "content_inline": _LECTURE_6_CONTENT,
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "writing-habit"],
        "source_name": "graduate-paper-writing-course:lecture-6",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "ordinal_type": "lecture", "ordinal": "6"},
    },
    {
        "title": "论文写作课程｜第7讲：发表高水平论文",
        "content_inline": _LECTURE_7_CONTENT,
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "publishing", "topic-selection"],
        "source_name": "graduate-paper-writing-course:lecture-7",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "ordinal_type": "lecture", "ordinal": "7"},
    },
    {
        "title": "论文写作课程｜第8讲：研究生毕业论文中常见的问题",
        "content_inline": _LECTURE_8_CONTENT,
        "tags": ["teaching", "courseware", "lecture", "course:paper-writing", "paper-writing", "thesis", "defense", "revision"],
        "source_name": "graduate-paper-writing-course:lecture-8",
        "metadata": {"domain": "teaching", "identity": "teacher", "course_id": "paper-writing", "material_type": "lecture", "ordinal_type": "lecture", "ordinal": "8"},
    },
]


def main():
    settings = AppSettings()
    store = LocalKnowledgeStore(settings)

    print(f"Knowledge backend: {store.backend_name()}")
    print(f"Existing documents: {store.count_documents()}")
    print()

    created = updated = skipped = 0
    for doc_spec in _DOCUMENTS:
        if doc_spec['source_name'] in SOURCE_OWNERSHIP:
            print(f"  SKIP (maintained by Wiki): {SOURCE_OWNERSHIP[doc_spec['source_name']]}")
            skipped += 1
            continue
        if "content_source" in doc_spec:
            content = _read_file(doc_spec["content_source"])
            if not content.strip():
                print(f"  SKIP (empty file): {doc_spec['content_source']}")
                skipped += 1
                continue
        elif "content_inline" in doc_spec:
            content = doc_spec["content_inline"].strip()
        else:
            print(f"  SKIP (no content): {doc_spec['title']}")
            skipped += 1
            continue

        payload = KnowledgeDocumentCreate(
            title=doc_spec["title"],
            content=content[:_MAX_CONTENT_LEN],
            tags=doc_spec["tags"],
            source_name=doc_spec["source_name"],
            metadata=doc_spec.get("metadata", {}),
        )

        record, is_new = store.upsert_document(payload, rebuild_indexes=False)
        if is_new:
            print(f"  CREATED: {record.title} ({record.document_id[:8]})")
            created += 1
        else:
            print(f"  UPDATED: {record.title} ({record.document_id[:8]})")
            updated += 1

    store.rebuild_indexes()
    print(f"\nDone. created={created}, updated={updated}, skipped={skipped}")
    print(f"Total documents now: {store.count_documents()}")


if __name__ == "__main__":
    main()
