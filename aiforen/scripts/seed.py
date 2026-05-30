"""Idempotent local-development seed.

NEVER wipes filled vocab packs unless the DB is empty or ALLOW_VOCAB_WIPE=1.
Run manually: ``docker compose run --rm seed`` — API no longer depends on seed.

Populates:
  * 4 plans (free/basic/pro/vip; stored as free/standard/premium/vip codes)
  * 4 writing groups
  * 12 writing tasks (mix of Task 1 / Task 2)
  * 20 vocab words
  * 10 grammar structures
  * 3 demo users (admin@aiforen.local, demo@aiforen.local, premium@aiforen.local)
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger

from aiforen.core import db as core_db
from aiforen.core import security
from aiforen.repositories.pg.grammar import GrammarRepo
from aiforen.repositories.pg.plans import PlanRepo, SubscriptionRepo
from aiforen.repositories.pg.users import UserRepo
from aiforen.repositories.pg.writing import WritingGroupRepo, WritingTaskRepo

PLANS = [
    {
        "code": "free",
        "name": "Free",
        "description": "Essential IELTS practice for everyone.",
        "price_usd": 0.0,
        "monthly_assessments": 2,
        "daily_ai_feedback": 3,
        "daily_vocab_reviews": 20,
        "can_create_personal_tasks": False,
        "sort_order": 1,
        "features": {"highlight": False, "color": "neutral"},
    },
    {
        "code": "standard",
        "name": "Basic",
        "description": "Daily IELTS practice with the standard AI model.",
        "price_usd": 1.89,
        "monthly_assessments": 60,
        "daily_ai_feedback": 30,
        "daily_vocab_reviews": 100,
        "can_create_personal_tasks": True,
        "quarterly_discount": 10.0,
        "half_yearly_discount": 10.0,
        "sort_order": 2,
        "features": {
            "highlight": False,
            "color": "primary",
            "display_name": "Basic",
            "model": "standard",
        },
    },
    {
        "code": "premium",
        "name": "Pro",
        "description": "Same focused workflow, powered by a stronger AI model.",
        "price_usd": 2.89,
        "monthly_assessments": 200,
        "daily_ai_feedback": 100,
        "daily_vocab_reviews": 200,
        "can_create_personal_tasks": True,
        "quarterly_discount": 15.0,
        "half_yearly_discount": 15.0,
        "sort_order": 3,
        "features": {
            "highlight": True,
            "color": "premium",
            "display_name": "Pro",
            "model": "advanced",
        },
    },
    {
        "code": "vip",
        "name": "VIP",
        "description": "Everything in Pro plus 1-on-1 coaching credits.",
        "price_usd": 29.99,
        "monthly_assessments": 500,
        "daily_ai_feedback": 300,
        "daily_vocab_reviews": 500,
        "can_create_personal_tasks": True,
        "quarterly_discount": 10.0,
        "half_yearly_discount": 15.0,
        "sort_order": 4,
        "features": {"highlight": False, "color": "gold"},
    },
]


GROUPS: List[Dict[str, Any]] = [
    {
        "id": 1,
        "name": "Cambridge Practice 19",
        "description": "Latest Cambridge book",
        "icon": "BookOpen",
        "sort_order": 1,
        "total_tasks": 0,
        "is_active": True,
    },
    {
        "id": 2,
        "name": "Topic-based Tasks",
        "description": "Grouped by question topic",
        "icon": "Layers",
        "sort_order": 2,
        "total_tasks": 0,
        "is_active": True,
    },
    {
        "id": 3,
        "name": "Recent Real Tests",
        "description": "Recent recall questions",
        "icon": "Calendar",
        "sort_order": 3,
        "total_tasks": 0,
        "is_active": True,
    },
    {
        "id": 999,
        "name": "Personal Tasks",
        "description": "Tasks you create",
        "icon": "User",
        "sort_order": 0,
        "total_tasks": 0,
        "is_active": True,
    },
]


def _writing_tasks() -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    base = [
        (
            "task_1",
            "Coffee consumption in four cities (1990–2010)",
            "The line graph below shows the consumption of coffee in four major cities between 1990 and 2010. Summarise the information by selecting and reporting the main features.",
        ),
        (
            "task_1",
            "Renewable energy share by country",
            "The bar chart compares the percentage of total energy produced from renewable sources in five countries in 2010 and 2020. Describe the information.",
        ),
        (
            "task_1",
            "Process: How chocolate is made",
            "The diagram illustrates the stages of chocolate production. Summarise the process in your own words.",
        ),
        (
            "task_2",
            "Working from home — boon or bane?",
            "Some people argue that working remotely improves productivity, while others say it harms team culture. Discuss both views and give your own opinion.",
        ),
        (
            "task_2",
            "University education funding",
            "Universities should be funded entirely by the government to provide free education. To what extent do you agree or disagree?",
        ),
        (
            "task_2",
            "Plastic packaging regulation",
            "Many countries are introducing strict regulations on plastic packaging. Discuss the advantages and disadvantages of such policies.",
        ),
        (
            "task_1",
            "Annual rainfall trends in Australia",
            "The chart compares average annual rainfall in five Australian states from 1980 to 2020. Summarise the information.",
        ),
        (
            "task_2",
            "Children and screen time",
            "Children today spend more hours in front of screens than ever before. What are the effects of this trend, and how can it be mitigated?",
        ),
        (
            "task_1",
            "Public transport usage in London",
            "The graph shows weekday public-transport usage in London by mode (bus, underground, rail) between 2005 and 2023. Describe the trends.",
        ),
        (
            "task_2",
            "Cashless societies",
            "Some countries are moving toward becoming cashless. Discuss the benefits and drawbacks of a cashless society.",
        ),
        (
            "task_1",
            "Tourist arrivals in Vietnam",
            "The chart shows monthly tourist arrivals to Vietnam in 2019 and 2023. Summarise the comparison.",
        ),
        (
            "task_2",
            "AI in education",
            "Artificial intelligence is increasingly being used in classrooms. Do the benefits outweigh the drawbacks?",
        ),
    ]
    for idx, (task_type, title, description) in enumerate(base, start=1):
        group_id = 1 if idx <= 6 else (2 if idx <= 9 else 3)
        group_name = next(g["name"] for g in GROUPS if g["id"] == group_id)
        samples.append(
            {
                "id": idx,
                "group_id": group_id,
                "group_name": group_name,
                "task_type": task_type,
                "title": title,
                "description": description,
                "image_url": None,
                "data_description": "",
                "time_limit": 1200 if task_type == "task_1" else 2400,
                "difficulty": "intermediate",
                "tags": ["seed"],
                "access": {
                    "free_access": True,
                    "required_plan": None,
                    "daily_limit": None,
                },
                "tests_taken": (idx * 17) % 200,
                "average_score": 6.0 + (idx % 5) * 0.2,
                "created_by": None,
                "is_personal": False,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        )
    return samples


def _vocab_words() -> List[Dict[str, Any]]:
    rows = [
        (
            "demonstrate",
            "verb",
            "to clearly show that something exists or is true",
            "Academic",
            7.0,
        ),
        ("substantial", "adj", "considerable in size or worth", "Academic", 7.0),
        (
            "plummet",
            "verb",
            "to fall or drop straight down at high speed",
            "Trends",
            7.5,
        ),
        ("alleviate", "verb", "to make less severe", "Society", 7.5),
        ("ubiquitous", "adj", "present everywhere", "Technology", 8.0),
        ("nascent", "adj", "just coming into existence", "Business", 8.0),
        ("paramount", "adj", "more important than anything else", "Society", 7.5),
        ("rampant", "adj", "spreading uncontrollably", "Society", 7.5),
        ("scrutinise", "verb", "to examine carefully", "Academic", 7.0),
        (
            "aspiration",
            "noun",
            "a hope or ambition of achieving something",
            "Education",
            6.5,
        ),
        ("dwindle", "verb", "to diminish gradually", "Trends", 7.0),
        ("proliferate", "verb", "to increase rapidly in number", "Trends", 7.5),
        ("redundant", "adj", "no longer needed", "Workplace", 7.0),
        ("salient", "adj", "most noticeable or important", "Academic", 7.5),
        ("staggering", "adj", "deeply shocking; astonishing", "Trends", 7.0),
        ("trivial", "adj", "of little value or importance", "General", 6.5),
        ("vibrant", "adj", "full of energy and life", "General", 6.0),
        ("vulnerable", "adj", "exposed to harm", "Society", 6.5),
        ("intricate", "adj", "very complicated or detailed", "Academic", 7.0),
        ("mitigate", "verb", "to make less severe", "Environment", 7.5),
    ]
    out: List[Dict[str, Any]] = []
    for idx, (word, pos, definition, category, band) in enumerate(rows, start=1):
        out.append(
            {
                "word_id": f"vocab_{idx:03d}",
                "word": word,
                "definition": definition,
                "pronunciation": "",
                "part_of_speech": pos,
                "category": category,
                "task_type": "Both",
                "band_score": band,
                "difficulty_level": "advanced" if band >= 7.5 else "intermediate",
                "examples": [
                    {
                        "correct": f"This study {word} how language develops.",
                        "context": "academic",
                        "explanation": "",
                    },
                ],
                "synonyms": [],
                "collocations": [],
                "tags": ["seed", category.lower()],
                "total_attempts": 0,
                "success_rate": 0.0,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "is_active": True,
            }
        )
    return out


VOCAB_PACKS: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_band_4",
        "title": "Band 4 Vocabulary",
        "description": "Simple high-frequency IELTS words for building basic sentence control.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 4",
        "task_type": "Both",
        "sort_order": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_band_5",
        "title": "Band 5 Vocabulary",
        "description": "Core words for clearer explanations and basic comparison.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 5",
        "task_type": "Both",
        "sort_order": 2,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_band_6",
        "title": "Band 6 Vocabulary",
        "description": "Useful IELTS words for band 6 across all skills and common topics.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 6",
        "task_type": "Both",
        "sort_order": 3,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_band_7",
        "title": "Band 7 Vocabulary",
        "description": "Academic words for precise claims, causes, and consequences.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 7",
        "task_type": "Both",
        "sort_order": 4,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_band_8",
        "title": "Band 8 Vocabulary",
        "description": "Higher-band vocabulary for nuance, evaluation, and balanced essays.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 8",
        "task_type": "Both",
        "sort_order": 5,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_band_9",
        "title": "Band 9 Vocabulary",
        "description": "Precise and flexible lexis for sophisticated IELTS expression.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "Band 9",
        "task_type": "Both",
        "sort_order": 6,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
    {
        "pack_id": "pack_gre",
        "title": "GRE Vocabulary",
        "description": "Advanced academic words for ambitious learners beyond IELTS needs.",
        "source_band_min": 0.0,
        "source_band_max": 9.0,
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "category": "GRE",
        "task_type": "Academic",
        "sort_order": 7,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    },
]


PACK_WORD_ROWS: Dict[str, List[tuple[str, str, str, float, str, str]]] = {
    "pack_band_4": [
        (
            "important",
            "adj",
            "having great value or effect",
            4.0,
            "Use before a noun to show basic importance.",
            "Hãy viết một câu nói rằng giáo dục rất quan trọng.",
        ),
        (
            "increase",
            "verb",
            "to become larger",
            4.0,
            "Use for numbers going up.",
            "Hãy viết một câu nói rằng số lượng người dùng tăng.",
        ),
        (
            "decrease",
            "verb",
            "to become smaller",
            4.0,
            "Use for numbers going down.",
            "Hãy viết một câu nói rằng giá giảm.",
        ),
        (
            "problem",
            "noun",
            "a difficult situation",
            4.0,
            "Use for simple negative issues.",
            "Hãy viết một câu nói rằng ô nhiễm là một vấn đề.",
        ),
        (
            "reason",
            "noun",
            "why something happens",
            4.0,
            "Use to explain causes.",
            "Hãy viết một câu nói rằng có nhiều lý do.",
        ),
        (
            "change",
            "noun",
            "a difference from before",
            4.0,
            "Use for general trends.",
            "Hãy viết một câu nói rằng có một thay đổi lớn.",
        ),
        (
            "people",
            "noun",
            "human beings in general",
            4.0,
            "Use for general social statements.",
            "Hãy viết một câu nói rằng mọi người cần học kỹ năng mới.",
        ),
        (
            "better",
            "adj",
            "of higher quality",
            4.0,
            "Use in simple comparisons.",
            "Hãy viết một câu nói rằng giao thông công cộng tốt hơn.",
        ),
        (
            "job",
            "noun",
            "paid work",
            4.0,
            "Use for work topics.",
            "Hãy viết một câu nói rằng công nghệ thay đổi công việc.",
        ),
        (
            "study",
            "verb",
            "to learn about a subject",
            4.0,
            "Use for education topics.",
            "Hãy viết một câu nói rằng học sinh nên học mỗi ngày.",
        ),
    ],
    "pack_band_5": [
        (
            "benefit",
            "noun",
            "a good result",
            5.0,
            "Use for advantages.",
            "Hãy viết một câu nói rằng thể thao có nhiều lợi ích.",
        ),
        (
            "effect",
            "noun",
            "a result caused by something",
            5.0,
            "Use for causes and results.",
            "Hãy viết một câu nói rằng mạng xã hội có tác động lớn.",
        ),
        (
            "improve",
            "verb",
            "to make better",
            5.0,
            "Use for progress.",
            "Hãy viết một câu nói rằng đọc sách cải thiện từ vựng.",
        ),
        (
            "reduce",
            "verb",
            "to make smaller",
            5.0,
            "Use for solutions.",
            "Hãy viết một câu nói rằng xe buýt giảm ô nhiễm.",
        ),
        (
            "compare",
            "verb",
            "to look at similarities and differences",
            5.0,
            "Use for Task 1 and discussions.",
            "Hãy viết một câu so sánh hai thành phố.",
        ),
        (
            "common",
            "adj",
            "happening often",
            5.0,
            "Use for popular trends.",
            "Hãy viết một câu nói rằng học online rất phổ biến.",
        ),
        (
            "support",
            "verb",
            "to help or agree with",
            5.0,
            "Use for opinions.",
            "Hãy viết một câu nói rằng chính phủ nên hỗ trợ học sinh.",
        ),
        (
            "create",
            "verb",
            "to make something new",
            5.0,
            "Use for results or jobs.",
            "Hãy viết một câu nói rằng công nghệ tạo ra việc làm.",
        ),
        (
            "choice",
            "noun",
            "an option",
            5.0,
            "Use for consumer and education topics.",
            "Hãy viết một câu nói rằng sinh viên có nhiều lựa chọn.",
        ),
        (
            "healthy",
            "adj",
            "good for health",
            5.0,
            "Use for lifestyle topics.",
            "Hãy viết một câu nói rằng ăn rau là lành mạnh.",
        ),
    ],
    "pack_band_6": [
        (
            "substantial",
            "adj",
            "large or important",
            6.5,
            "Use before increase/decrease/difference.",
            "Hãy viết một câu nói rằng có một sự gia tăng đáng kể.",
        ),
        (
            "whereas",
            "conj",
            "used to compare contrasting facts",
            6.5,
            "Use to compare two groups in one sentence.",
            "Hãy viết một câu so sánh hai quốc gia bằng whereas.",
        ),
        (
            "overall",
            "adv",
            "considering everything",
            6.0,
            "Use in overview sentences.",
            "Hãy viết một câu overview bắt đầu với Overall.",
        ),
        (
            "proportion",
            "noun",
            "a part or share of a whole",
            6.0,
            "Use for percentages.",
            "Hãy viết một câu nói rằng tỉ lệ người dùng xe buýt cao hơn.",
        ),
        (
            "evidence",
            "noun",
            "facts showing something is true",
            6.5,
            "Use to support claims.",
            "Hãy viết một câu nói rằng bằng chứng cho thấy tập thể dục cải thiện sức khỏe.",
        ),
        (
            "drawback",
            "noun",
            "a disadvantage",
            6.5,
            "Use as a clean synonym for disadvantage.",
            "Hãy viết một câu nói rằng làm việc từ xa có một bất lợi.",
        ),
        (
            "outcome",
            "noun",
            "a result",
            6.5,
            "Use in formal explanation.",
            "Hãy viết một câu nói rằng kết quả phụ thuộc vào chính sách.",
        ),
        (
            "stable",
            "adj",
            "not changing much",
            6.0,
            "Use for flat trends.",
            "Hãy viết một câu nói rằng con số giữ ổn định trong hai năm.",
        ),
        (
            "marginal",
            "adj",
            "very small",
            6.5,
            "Use before change/increase/decrease.",
            "Hãy viết một câu nói rằng thay đổi là rất nhỏ.",
        ),
        (
            "beneficial",
            "adj",
            "having a good effect",
            6.5,
            "Use for advantages without sounding casual.",
            "Hãy viết một câu nói rằng giáo dục miễn phí có lợi cho xã hội.",
        ),
    ],
    "pack_band_7": [
        (
            "mitigate",
            "verb",
            "to make a problem less severe",
            7.0,
            "Use for solutions.",
            "Hãy viết một câu nói rằng luật mới giảm tác động của ô nhiễm.",
        ),
        (
            "contribute",
            "verb",
            "to help cause or improve something",
            7.0,
            "Use with 'to'.",
            "Hãy viết một câu nói rằng giao thông công cộng góp phần giảm ô nhiễm.",
        ),
        (
            "emphasise",
            "verb",
            "to give special importance to something",
            7.0,
            "Use for priorities.",
            "Hãy viết một câu nói rằng trường học nên nhấn mạnh kỹ năng thực tế.",
        ),
        (
            "inequality",
            "noun",
            "an unfair difference between groups",
            7.0,
            "Use for social gaps.",
            "Hãy viết một câu nói rằng công nghệ có thể làm tăng bất bình đẳng.",
        ),
        (
            "sustainable",
            "adj",
            "able to continue without harm",
            7.0,
            "Use for environment/economy policies.",
            "Hãy viết một câu nói rằng thành phố cần giao thông bền vững.",
        ),
        (
            "arguably",
            "adv",
            "used when giving a defensible opinion",
            7.0,
            "Use carefully before a claim.",
            "Hãy viết một câu bắt đầu bằng Arguably.",
        ),
        (
            "fluctuate",
            "verb",
            "to change up and down irregularly",
            7.0,
            "Use for unstable data.",
            "Hãy viết một câu nói rằng giá dầu dao động trong suốt giai đoạn.",
        ),
        (
            "scrutinise",
            "verb",
            "to examine carefully",
            7.0,
            "Use for analysis or policy.",
            "Hãy viết một câu nói rằng chính phủ nên kiểm tra dữ liệu cẩn thận.",
        ),
        (
            "redundant",
            "adj",
            "no longer needed",
            7.0,
            "Use for work and technology topics.",
            "Hãy viết một câu nói rằng một số công việc có thể trở nên không cần thiết.",
        ),
        (
            "aspiration",
            "noun",
            "a strong hope or ambition",
            7.0,
            "Use for education and career topics.",
            "Hãy viết một câu nói rằng sinh viên có tham vọng nghề nghiệp cao.",
        ),
    ],
    "pack_band_8": [
        (
            "nuanced",
            "adj",
            "showing subtle differences",
            8.0,
            "Use for balanced arguments.",
            "Hãy viết một câu nói rằng vấn đề này cần một cách nhìn tinh tế.",
        ),
        (
            "plausible",
            "adj",
            "reasonable or believable",
            8.0,
            "Use for arguments or explanations.",
            "Hãy viết một câu nói rằng giải pháp này nghe có vẻ hợp lý.",
        ),
        (
            "undermine",
            "verb",
            "to gradually weaken something",
            8.0,
            "Use for negative long-term effects.",
            "Hãy viết một câu nói rằng tin giả làm suy yếu niềm tin công chúng.",
        ),
        (
            "robust",
            "adj",
            "strong and effective",
            8.0,
            "Use for evidence and systems.",
            "Hãy viết một câu nói rằng cần có bằng chứng vững chắc.",
        ),
        (
            "prevalent",
            "adj",
            "common in a place or time",
            8.0,
            "Use for social trends.",
            "Hãy viết một câu nói rằng học online ngày càng phổ biến.",
        ),
        (
            "constraint",
            "noun",
            "a limit or restriction",
            8.0,
            "Use for practical limitations.",
            "Hãy viết một câu nói rằng ngân sách là một hạn chế lớn.",
        ),
        (
            "compelling",
            "adj",
            "very convincing",
            8.0,
            "Use for evidence/reasons.",
            "Hãy viết một câu nói rằng có một lý do thuyết phục.",
        ),
        (
            "trade-off",
            "noun",
            "a balance between two competing things",
            8.0,
            "Use in balanced arguments.",
            "Hãy viết một câu nói rằng có sự đánh đổi giữa tốc độ và chất lượng.",
        ),
        (
            "counterproductive",
            "adj",
            "having the opposite intended effect",
            8.0,
            "Use for harmful policies.",
            "Hãy viết một câu nói rằng hình phạt quá nặng có thể phản tác dụng.",
        ),
        (
            "disproportionate",
            "adj",
            "too large or small in comparison",
            8.0,
            "Use for unequal effects.",
            "Hãy viết một câu nói rằng người nghèo chịu tác động không cân xứng.",
        ),
    ],
    "pack_band_9": [
        (
            "ubiquitous",
            "adj",
            "present everywhere",
            9.0,
            "Use for widespread trends.",
            "Hãy viết một câu nói rằng điện thoại thông minh có mặt ở khắp nơi.",
        ),
        (
            "salient",
            "adj",
            "most noticeable or important",
            9.0,
            "Use for key points.",
            "Hãy viết một câu nói rằng đặc điểm quan trọng nhất là chi phí.",
        ),
        (
            "intricate",
            "adj",
            "very detailed or complicated",
            9.0,
            "Use for complex systems.",
            "Hãy viết một câu nói rằng hệ thống giáo dục rất phức tạp.",
        ),
        (
            "paradigm",
            "noun",
            "a model or pattern of thinking",
            9.0,
            "Use with 'shift' for major change.",
            "Hãy viết một câu nói rằng công nghệ tạo ra một mô hình mới.",
        ),
        (
            "contentious",
            "adj",
            "causing disagreement",
            9.0,
            "Use for debated issues.",
            "Hãy viết một câu nói rằng kiểm duyệt là một vấn đề gây tranh cãi.",
        ),
        (
            "pragmatic",
            "adj",
            "practical rather than theoretical",
            9.0,
            "Use for realistic solutions.",
            "Hãy viết một câu nói rằng cần một giải pháp thực tế.",
        ),
        (
            "pervasive",
            "adj",
            "spreading widely through an area",
            9.0,
            "Use for broad influence.",
            "Hãy viết một câu nói rằng quảng cáo có ảnh hưởng rộng khắp.",
        ),
        (
            "inadvertently",
            "adv",
            "without intending to",
            9.0,
            "Use for unintended outcomes.",
            "Hãy viết một câu nói rằng chính sách này vô tình làm tăng chi phí.",
        ),
        (
            "detrimental",
            "adj",
            "harmful",
            9.0,
            "Use for negative effects.",
            "Hãy viết một câu nói rằng thiếu ngủ có hại cho sức khỏe.",
        ),
        (
            "exacerbate",
            "verb",
            "to make worse",
            9.0,
            "Use for worsening problems.",
            "Hãy viết một câu nói rằng biến đổi khí hậu làm vấn đề tệ hơn.",
        ),
    ],
    "pack_gre": [
        (
            "aberration",
            "noun",
            "a departure from what is normal",
            9.0,
            "Use for unusual cases.",
            "Hãy viết một câu nói rằng kết quả này là một trường hợp bất thường.",
        ),
        (
            "equivocal",
            "adj",
            "open to more than one interpretation",
            9.0,
            "Use for unclear evidence.",
            "Hãy viết một câu nói rằng bằng chứng chưa rõ ràng.",
        ),
        (
            "laconic",
            "adj",
            "using very few words",
            9.0,
            "Use for communication style.",
            "Hãy viết một câu nói rằng câu trả lời của anh ấy rất ngắn gọn.",
        ),
        (
            "magnanimous",
            "adj",
            "generous and forgiving",
            9.0,
            "Use for character.",
            "Hãy viết một câu nói rằng nhà lãnh đạo rộng lượng.",
        ),
        (
            "obdurate",
            "adj",
            "stubbornly refusing to change",
            9.0,
            "Use for rigid attitudes.",
            "Hãy viết một câu nói rằng một số người cố chấp trước bằng chứng.",
        ),
        (
            "prosaic",
            "adj",
            "ordinary and lacking imagination",
            9.0,
            "Use for dull ideas.",
            "Hãy viết một câu nói rằng giải pháp này quá tầm thường.",
        ),
        (
            "recalcitrant",
            "adj",
            "resisting authority or control",
            9.0,
            "Use for resistant groups.",
            "Hãy viết một câu nói rằng một số học sinh chống đối nội quy.",
        ),
        (
            "sagacious",
            "adj",
            "wise and showing good judgement",
            9.0,
            "Use for decisions/people.",
            "Hãy viết một câu nói rằng quyết định đó rất khôn ngoan.",
        ),
        (
            "tenuous",
            "adj",
            "weak or slight",
            9.0,
            "Use for weak links/arguments.",
            "Hãy viết một câu nói rằng mối liên hệ này rất yếu.",
        ),
        (
            "venerate",
            "verb",
            "to respect deeply",
            9.0,
            "Use for culture or history.",
            "Hãy viết một câu nói rằng nhiều người kính trọng các nhà khoa học.",
        ),
    ],
}


_PACK_TOPICS: Dict[str, str] = {
    "Band 4": "công nghệ và cuộc sống hàng ngày",
    "Band 5": "môi trường và sức khoẻ cộng đồng",
    "Band 6": "giáo dục, việc làm và đô thị",
    "Band 7": "biến đổi khí hậu và chính sách công",
    "Band 8": "xã hội, công việc và truyền thông",
    "Band 9": "công nghệ, văn hoá và toàn cầu hoá",
    "GRE": "nghiên cứu, phân tích và lập luận học thuật",
}


def _derive_translate_prompt(vi_prompt: str) -> str:
    """Strip the directive prefix to get a direct Vietnamese sentence to translate."""

    cleaned = re.sub(
        r"^Hãy viết một câu (?:nói rằng|so sánh|bắt đầu(?: bằng| với)?|overview(?: bắt đầu(?: với| bằng)?)?)\s*",
        "",
        vi_prompt,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip().strip(".").strip()
    if not cleaned:
        return vi_prompt
    return cleaned[:1].upper() + cleaned[1:] + "."


def _pack_word(
    *,
    pack_id: str,
    idx: int,
    word: str,
    pos: str,
    definition: str,
    band: float,
    category: str,
    task_type: str,
    usage: str,
    vi_prompt: str,
) -> Dict[str, Any]:
    word_id = f"{pack_id}_{idx:02d}"
    example = f"Learners can use '{word}' accurately when the context is clear."
    translate_prompt = _derive_translate_prompt(vi_prompt)
    topic = _PACK_TOPICS.get(category, "một vấn đề học thuật quen thuộc")
    topic_prompt = f"Viết một câu tiếng Anh dùng '{word}' về chủ đề {topic}."
    return {
        "word_id": word_id,
        "pack_id": pack_id,
        "word": word,
        "definition": definition,
        "pronunciation": "",
        "part_of_speech": pos,
        "category": category,
        "task_type": task_type,
        "band_score": band,
        "difficulty_level": (
            "advanced" if band >= 7.5 else ("beginner" if band <= 5 else "intermediate")
        ),
        "examples": [
            {
                "correct": example,
                "context": "IELTS writing",
                "explanation": "Shows natural academic use.",
            }
        ],
        "synonyms": [],
        "collocations": [f"{word} issue", f"{word} effect"],
        "usage": usage,
        "tips": [
            "Use it only when the meaning is precise.",
            "Put it in a complete sentence with a clear IELTS context.",
        ],
        "mcq": {
            "question": f"Which sentence uses '{word}' most naturally?",
            "options": [
                {"id": "a", "text": example},
                {"id": "b", "text": f"The {word} is very people and many thing."},
                {"id": "c", "text": f"I {word} go to school yesterday."},
                {"id": "d", "text": f"It is {word} because it is."},
            ],
            "correct_option_id": "a",
            "explanation": "Option A uses the word in a grammatically complete sentence.",
        },
        "vi_prompt": vi_prompt,
        "vi_translate_prompt": translate_prompt,
        "topic_prompt": topic_prompt,
        "example_good_sentence": example,
        "tags": ["seed", "pack", category.lower().replace(" ", "_")],
        "total_attempts": 0,
        "success_rate": 0.0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True,
    }


def _vocab_pack_words() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    packs_by_id = {pack["pack_id"]: pack for pack in VOCAB_PACKS}
    for pack_id, pack_rows in PACK_WORD_ROWS.items():
        pack = packs_by_id[pack_id]
        for idx, (word, pos, definition, band, usage, vi_prompt) in enumerate(
            pack_rows, start=1
        ):
            out.append(
                _pack_word(
                    pack_id=pack_id,
                    idx=idx,
                    word=word,
                    pos=pos,
                    definition=definition,
                    band=band,
                    category=pack["category"],
                    task_type=pack["task_type"],
                    usage=usage,
                    vi_prompt=vi_prompt,
                )
            )
    return out


def _grammar_structures() -> List[Dict[str, Any]]:
    rows = [
        (
            "Conditional 2nd",
            "If + past simple, would + base verb",
            "Hypothetical present/future situations",
            "Conditional Structures",
            6.5,
        ),
        (
            "Conditional 3rd",
            "If + had + past participle, would have + past participle",
            "Past hypotheticals",
            "Conditional Structures",
            7.0,
        ),
        (
            "Passive voice",
            "Subject + be + past participle",
            "Used when the agent is unknown or unimportant",
            "Passive Voice",
            6.5,
        ),
        (
            "Inversion (Hardly...)",
            "Hardly had + S + V when + S + V",
            "Adds emphasis",
            "Inversions",
            7.5,
        ),
        (
            "Cleft sentence (It is/was)",
            "It is X that Y",
            "Focuses information on one constituent",
            "Emphasis",
            7.0,
        ),
        (
            "Relative clause (defining)",
            "..., who/which/that ...",
            "Adds essential information",
            "Relative Clauses",
            6.0,
        ),
        (
            "Modal of deduction (must have)",
            "must have + past participle",
            "Logical certainty about the past",
            "Modal Verbs",
            7.0,
        ),
        (
            "Future continuous",
            "will be + V-ing",
            "Action in progress at a future time",
            "Future Tenses",
            6.0,
        ),
        (
            "Reported speech (back-shift)",
            "S + said (that) + S + past tense",
            "Indirect speech",
            "Reported Speech",
            6.5,
        ),
        (
            "Concession (Despite/In spite of)",
            "Despite + noun phrase",
            "Concedes a point",
            "Connectors",
            6.5,
        ),
    ]
    out: List[Dict[str, Any]] = []
    for idx, (name, pattern, description, category, band) in enumerate(rows, start=1):
        out.append(
            {
                "structure_id": f"gram_{idx:03d}",
                "name": name,
                "structure_pattern": pattern,
                "description": description,
                "category": category,
                "task_type": "Both",
                "band_score": band,
                "difficulty_level": "advanced" if band >= 7.5 else "intermediate",
                "examples": [
                    {
                        "correct": f"Example sentence using {name}.",
                        "context": "academic",
                        "explanation": "",
                    }
                ],
                "common_errors": [],
                "tags": ["seed", category.lower().replace(" ", "_")],
                "total_attempts": 0,
                "success_rate": 0.0,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "is_active": True,
            }
        )
    return out


async def seed_pg() -> None:
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        plans = PlanRepo(session)
        users = UserRepo(session)
        subs = SubscriptionRepo(session)

        for p in PLANS:
            await plans.upsert(**p)

        admin = await users.by_email("admin@aiforen.dev")
        if not admin:
            admin = await users.create(
                email="admin@aiforen.dev",
                name="Admin",
                password_hash=security.hash_password("admin1234"),
                email_verified=True,
                is_admin=True,
            )

        free = await users.by_email("demo@aiforen.dev")
        if not free:
            free = await users.create(
                email="demo@aiforen.dev",
                name="Demo User",
                password_hash=security.hash_password("demo1234"),
                email_verified=True,
            )

        prem = await users.by_email("premium@aiforen.dev")
        if not prem:
            prem = await users.create(
                email="premium@aiforen.dev",
                name="Pro Demo",
                password_hash=security.hash_password("premium1234"),
                email_verified=True,
            )

        # Grant the premium user a Pro subscription if they don't have one
        existing_sub = await subs.active_for_user(prem.id)
        if not existing_sub:
            await subs.grant(
                user_id=prem.id,
                plan_code="premium",
                billing_cycle="monthly",
                months=12,
                price_paid=2.89 * 12,
                payment_method="seed",
            )
        await session.commit()
    logger.info("Postgres seed complete")


async def seed_vocab_pg() -> None:
    """Populate Postgres vocab lexicon (packs, lexemes, questions).

    Safe by default: skips entirely when packs already have >=100 items.
    Full wipe only when lexicon is empty OR ALLOW_VOCAB_WIPE=1 (destructive).
    """

    from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
    from aiforen.scripts.vocab.bootstrap import (
        bootstrap_lexemes_from_legacy,
        bootstrap_questions_for_all,
    )
    from aiforen.scripts.vocab.build_packs import build_thematic_packs

    allow_wipe = os.environ.get("ALLOW_VOCAB_WIPE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)

        if await repo.has_filled_packs(min_pack_items=100):
            logger.warning(
                "Vocab packs already filled (>=100 pack_items) — "
                "seed_vocab_pg SKIPPED. Set ALLOW_VOCAB_WIPE=1 to force wipe (destructive)."
            )
            return

        if allow_wipe or not await repo.has_content():
            if allow_wipe:
                logger.warning("ALLOW_VOCAB_WIPE=1 — clearing all vocab content")
            await repo.clear_all_vocab_content()
            await bootstrap_lexemes_from_legacy(repo, approve=True)
            await bootstrap_questions_for_all(repo)
            await build_thematic_packs(repo, reset_items=True)
            try:
                from aiforen.scripts.vocab.import_oxford_csv import (
                    _default_csv,
                    _load_rows,
                    import_oxford,
                )

                oxford_csv = _default_csv()
                if oxford_csv.is_file():
                    await import_oxford(repo, _load_rows(oxford_csv))
                    logger.info("Oxford CEFR packs imported from {}", oxford_csv)
            except Exception as exc:
                logger.warning("Oxford CEFR import skipped: {}", exc)
        else:
            # Lexemes exist but packs not filled — metadata only, never reset pack_items.
            await build_thematic_packs(repo, reset_items=False)
            logger.info(
                "Vocab lexicon partial — updated pack metadata only (no item wipe)"
            )

        await session.commit()
    logger.info("Postgres vocab lexicon seed complete")


async def seed_content_pg() -> None:
    async with core_db.pg_session() as session:
        groups = WritingGroupRepo(session)
        tasks = WritingTaskRepo(session)
        grammar = GrammarRepo(session)

        for g in GROUPS:
            await groups.upsert(g)

        seeded_tasks = _writing_tasks()
        for t in seeded_tasks:
            await tasks.upsert(t)

        counts: dict[int, int] = {}
        for t in seeded_tasks:
            counts[t["group_id"]] = counts.get(t["group_id"], 0) + 1
        for gid, count in counts.items():
            group = await groups.get(gid)
            if group:
                group["total_tasks"] = count
                await groups.upsert(group)

        await grammar.insert_many(_grammar_structures())
    logger.info("Postgres content seed complete (writing + grammar)")


async def main() -> None:
    core_db.init_pg()
    core_db.init_redis()
    await core_db.ping_all()
    await seed_pg()
    await seed_vocab_pg()
    await seed_content_pg()
    await core_db.shutdown_all()
    logger.info("✅ Seeding complete")


if __name__ == "__main__":
    asyncio.run(main())
