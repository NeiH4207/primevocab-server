"""Upsert level-based coaching reading catalog units.

Run:
  python -m aiforen.scripts.vocab.seed_coaching_reading_catalog
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import delete, update

from aiforen.core import db as core_db
from aiforen.domain.sql_models import CoachingReadingUnit, CoachingReadingUnitQuestion
from aiforen.repositories.pg.coaching_content import CoachingContentRepo

# Retired A2 units — archived on seed so only one published A2 Day 1 remains.
RETIRED_A2_UNIT_IDS = (
    "a2-day01-city-park",
    "a2-day02-vocab-app",
    "a2-day01-vocab-app",
)

A1_DAY1_UNIT_ID = "a1-day01-small-bag"

A1_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "bag", "pos": "noun", "vi_gloss": "cái túi / cặp"},
    {"lemma": "book", "pos": "noun", "vi_gloss": "quyển sách"},
    {"lemma": "pen", "pos": "noun", "vi_gloss": "cái bút"},
    {"lemma": "water", "pos": "noun", "vi_gloss": "nước"},
    {"lemma": "phone", "pos": "noun", "vi_gloss": "điện thoại"},
    {"lemma": "school", "pos": "noun", "vi_gloss": "trường học"},
    {"lemma": "morning", "pos": "noun", "vi_gloss": "buổi sáng"},
    {"lemma": "small", "pos": "adj", "vi_gloss": "nhỏ"},
]

A1_DAY1_PARAGRAPHS = [
    "Anna has a small bag. Every morning, she puts a book, a pen, a bottle of water, "
    "and her phone in the bag.",
    "Anna goes to school by bus. On the bus, she opens her book and reads. At school, "
    "she uses her pen in class. At lunch, she drinks her water.",
    "After school, Anna looks in her bag. At first, she cannot see her pen. Then she "
    "moves her book and finds it. Her book, pen, water, and phone are all in the bag.",
    "Anna smiles. Her small bag is very useful.",
]

A1_DAY1_QUESTIONS = [
    {
        "id": "a1-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "What does Anna have?",
        "options": [
            "A small bag",
            "A big car",
            "A new bike",
            "A red hat",
        ],
        "correct_option": "A small bag",
        "explanation": "The first sentence says Anna has a small bag.",
    },
    {
        "id": "a1-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "When does Anna put things in her bag?",
        "options": [
            "Every morning",
            "Every night",
            "Every Sunday",
            "Every month",
        ],
        "correct_option": "Every morning",
        "explanation": "The passage says every morning, Anna puts things in her bag.",
    },
    {
        "id": "a1-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "How does Anna go to school?",
        "options": [
            "By bus",
            "By train",
            "By car",
            "By boat",
        ],
        "correct_option": "By bus",
        "explanation": "Paragraph 2 says Anna goes to school by bus.",
    },
    {
        "id": "a1-d01-q04",
        "sort_order": 4,
        "question_type": "comprehension",
        "prompt": "What does Anna read on the bus?",
        "options": [
            "Her book",
            "Her phone",
            "Her pen",
            "Her water",
        ],
        "correct_option": "Her book",
        "explanation": "Paragraph 2 says she opens her book and reads on the bus.",
    },
    {
        "id": "a1-d01-q05",
        "sort_order": 5,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with ONE word from the passage.\n\n"
        "At school, Anna uses her ______ in class.",
        "options": [],
        "correct_option": "pen",
        "acceptable_answers": ["pen"],
        "explanation": "Paragraph 2 says Anna uses her pen in class.",
    },
    {
        "id": "a1-d01-q06",
        "sort_order": 6,
        "question_type": "comprehension",
        "prompt": "At first, what can Anna not see after school?",
        "options": [
            "Her pen",
            "Her book",
            "Her phone",
            "Her water",
        ],
        "correct_option": "Her pen",
        "explanation": "Paragraph 3 says at first, Anna cannot see her pen.",
    },
    {
        "id": "a1-d01-q07",
        "sort_order": 7,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "small" means:',
        "options": [
            "not big",
            "not clean",
            "not happy",
            "not new",
        ],
        "correct_option": "not big",
        "source_word": "small",
        "explanation": "Small means not big.",
    },
    {
        "id": "a1-d01-q08",
        "sort_order": 8,
        "question_type": "comprehension",
        "prompt": "Why does Anna smile at the end?",
        "options": [
            "Her things are all in her bag.",
            "Her bag is empty.",
            "Her phone is lost.",
            "Her school is closed.",
        ],
        "correct_option": "Her things are all in her bag.",
        "explanation": "Anna smiles because her book, pen, water, and phone are all in the bag.",
    },
]

A2_DAY1_UNIT_ID = "a2-day01-quiet-park"

A2_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "park", "pos": "noun", "vi_gloss": "công viên"},
    {"lemma": "street", "pos": "noun", "vi_gloss": "đường phố"},
    {"lemma": "tree", "pos": "noun", "vi_gloss": "cây"},
    {"lemma": "child", "pos": "noun", "vi_gloss": "trẻ em"},
    {"lemma": "clean", "pos": "adj", "vi_gloss": "sạch / làm sạch"},
    {"lemma": "evening", "pos": "noun", "vi_gloss": "buổi tối"},
    {"lemma": "crowded", "pos": "adj", "vi_gloss": "đông đúc"},
    {"lemma": "relax", "pos": "verb", "vi_gloss": "thư giãn"},
]

A2_DAY1_PARAGRAPHS = [
    "Linh lives on a busy street in the city. In the morning, many cars and motorbikes "
    "go past her house. The street is often noisy and crowded.",
    "Near Linh's home, there is a small park. It has trees, flowers, and two long "
    "benches. After school, Linh sometimes goes there with her younger brother. They "
    "sit under a tree and relax because the park is quieter than the street.",
    "Many local people use the park. Children play on the grass. Some students read "
    "books on the benches. In the evening, older people walk slowly around the park.",
    "The park is useful, but it needs care. People should not leave rubbish on the "
    "grass. Linh thinks everyone should help keep the park clean, so children and "
    "families can enjoy it.",
]

A2_DAY1_QUESTIONS = [
    {
        "id": "a2-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "Where does Linh live?",
        "options": [
            "On a busy street",
            "Near a beach",
            "In a quiet village",
            "Next to a farm",
        ],
        "correct_option": "On a busy street",
        "explanation": "Paragraph 1 says Linh lives on a busy street in the city.",
    },
    {
        "id": "a2-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "What is near Linh's home?",
        "options": [
            "A small park",
            "A big airport",
            "A new hotel",
            "A train station",
        ],
        "correct_option": "A small park",
        "explanation": "Paragraph 2 says there is a small park near Linh's home.",
    },
    {
        "id": "a2-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "Why does Linh relax in the park?",
        "options": [
            "It is quieter than the street.",
            "It has many cars.",
            "It is far from her house.",
            "It is full of shops.",
        ],
        "correct_option": "It is quieter than the street.",
        "explanation": "Paragraph 2 says Linh and her brother relax because the park is quieter than the street.",
    },
    {
        "id": "a2-d01-q04",
        "sort_order": 4,
        "question_type": "comprehension",
        "prompt": "What do children do in the park?",
        "options": [
            "Play on the grass",
            "Drive motorbikes",
            "Sell flowers",
            "Wash cars",
        ],
        "correct_option": "Play on the grass",
        "explanation": "Paragraph 3 says children play on the grass.",
    },
    {
        "id": "a2-d01-q05",
        "sort_order": 5,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with ONE word from the passage.\n\n"
        "In the evening, older people walk slowly around the ______.",
        "options": [],
        "correct_option": "park",
        "acceptable_answers": ["park"],
        "explanation": "Paragraph 3 says older people walk slowly around the park in the evening.",
    },
    {
        "id": "a2-d01-q06",
        "sort_order": 6,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "crowded" means:',
        "options": [
            "full of many people or vehicles",
            "very clean",
            "very quiet",
            "far away",
        ],
        "correct_option": "full of many people or vehicles",
        "source_word": "crowded",
        "explanation": "Crowded means full of many people or vehicles. The street has many cars and motorbikes.",
    },
    {
        "id": "a2-d01-q07",
        "sort_order": 7,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "relax" means:',
        "options": [
            "rest and feel calm",
            "run very fast",
            "make a loud noise",
            "buy something",
        ],
        "correct_option": "rest and feel calm",
        "source_word": "relax",
        "explanation": "Relax means rest and feel calm.",
    },
    {
        "id": "a2-d01-q08",
        "sort_order": 8,
        "question_type": "comprehension",
        "prompt": "Why should people not leave rubbish on the grass?",
        "options": [
            "The park needs to stay clean.",
            "The park is too far away.",
            "The trees need more cars.",
            "The benches are too long.",
        ],
        "correct_option": "The park needs to stay clean.",
        "explanation": "Paragraph 4 says the park needs care and people should help keep it clean.",
    },
]

B1_DAY1_UNIT_ID = "b1-day01-study-habit"

B1_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "forget", "pos": "verb", "vi_gloss": "quên"},
    {"lemma": "mistake", "pos": "noun", "vi_gloss": "lỗi"},
    {"lemma": "enough", "pos": "adj", "vi_gloss": "đủ"},
    {"lemma": "advice", "pos": "noun", "vi_gloss": "lời khuyên"},
    {"lemma": "meaning", "pos": "noun", "vi_gloss": "nghĩa"},
    {"lemma": "improve", "pos": "verb", "vi_gloss": "cải thiện"},
    {"lemma": "habit", "pos": "noun", "vi_gloss": "thói quen"},
    {"lemma": "review", "pos": "verb", "vi_gloss": "ôn lại"},
    {"lemma": "regularly", "pos": "adv", "vi_gloss": "đều đặn"},
]

B1_DAY1_PARAGRAPHS = [
    "Mai wants to improve her English vocabulary. She learns many new words, but she "
    "often forgets them after a few days. At first, she studies for two hours every "
    "Sunday. However, this plan does not work well. She feels tired, and by Monday she "
    "cannot remember enough words.",
    "One day, her teacher gives her simple advice: study a little every day. Mai starts "
    "with ten minutes each evening. She chooses five words, reads their meanings, and "
    "writes one short sentence for each word.",
    "The next day, Mai reviews the same words before learning new ones. When she makes "
    "a mistake in a quiz, she writes the word in a small notebook. Later, she reviews "
    "that word again.",
    "After two weeks, Mai notices a change. She remembers more words because she sees "
    "them regularly. She also feels less stressed because each lesson is short. Mai "
    "understands that a good study habit is not about studying for many hours at one "
    "time. It is about studying often and not giving up.",
]

B1_DAY1_QUESTIONS = [
    {
        "id": "b1-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "What does Mai want to improve?",
        "options": [
            "Her English vocabulary",
            "Her cooking skills",
            "Her phone camera",
            "Her drawing ability",
        ],
        "correct_option": "Her English vocabulary",
        "explanation": "The first sentence says Mai wants to improve her English vocabulary.",
    },
    {
        "id": "b1-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "What problem does Mai have at first?",
        "options": [
            "She forgets new words after a few days.",
            "She does not have a teacher.",
            "She cannot read short sentences.",
            "She studies every evening.",
        ],
        "correct_option": "She forgets new words after a few days.",
        "explanation": "Paragraph 1 says Mai often forgets new words after a few days.",
    },
    {
        "id": "b1-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "Why does studying for two hours every Sunday not work well?",
        "options": [
            "Mai feels tired and cannot remember enough words.",
            "Mai only learns one word.",
            "Mai loses her notebook.",
            "Mai studies in the morning.",
        ],
        "correct_option": "Mai feels tired and cannot remember enough words.",
        "explanation": "Paragraph 1 says studying for two hours makes her tired, and she cannot remember enough words on Monday.",
    },
    {
        "id": "b1-d01-q04",
        "sort_order": 4,
        "question_type": "comprehension",
        "prompt": "What advice does Mai's teacher give her?",
        "options": [
            "Study a little every day.",
            "Stop learning vocabulary.",
            "Study only on Sunday.",
            "Read one difficult book every night.",
        ],
        "correct_option": "Study a little every day.",
        "explanation": "Paragraph 2 says her teacher advises her to study a little every day.",
    },
    {
        "id": "b1-d01-q05",
        "sort_order": 5,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with ONE word from the passage.\n\n"
        "When Mai makes a ______ in a quiz, she writes the word in a notebook.",
        "options": [],
        "correct_option": "mistake",
        "acceptable_answers": ["mistake"],
        "explanation": "Paragraph 3 says when Mai makes a mistake in a quiz, she writes the word in a notebook.",
    },
    {
        "id": "b1-d01-q06",
        "sort_order": 6,
        "question_type": "comprehension",
        "prompt": "What does Mai do before learning new words the next day?",
        "options": [
            "She reviews the same words.",
            "She watches a film.",
            "She buys a new notebook.",
            "She calls her teacher.",
        ],
        "correct_option": "She reviews the same words.",
        "explanation": "Paragraph 3 says Mai reviews the same words before learning new ones.",
    },
    {
        "id": "b1-d01-q07",
        "sort_order": 7,
        "question_type": "comprehension",
        "prompt": "Why does Mai remember more words after two weeks?",
        "options": [
            "She sees the words regularly.",
            "She studies only once a month.",
            "She stops taking quizzes.",
            "She learns no new words.",
        ],
        "correct_option": "She sees the words regularly.",
        "explanation": "Paragraph 4 says she remembers more words because she sees them regularly.",
    },
    {
        "id": "b1-d01-q08",
        "sort_order": 8,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "habit" means:',
        "options": [
            "something you do regularly",
            "a difficult word",
            "a short quiz",
            "a school subject",
        ],
        "correct_option": "something you do regularly",
        "source_word": "habit",
        "explanation": "A habit is something you do regularly. Mai's habit is studying for ten minutes each evening.",
    },
    {
        "id": "b1-d01-q09",
        "sort_order": 9,
        "question_type": "comprehension",
        "prompt": "What is the main message of the passage?",
        "options": [
            "Studying regularly can help people remember vocabulary better.",
            "Students should study for many hours only once a week.",
            "Mistakes are always bad and should be avoided.",
            "Vocabulary is less important than grammar.",
        ],
        "correct_option": "Studying regularly can help people remember vocabulary better.",
        "explanation": "The passage shows that short, regular study helps Mai remember vocabulary better.",
    },
]

B2_DAY1_UNIT_ID = "b2-day01-urban-heat"

# Curated focus vocab for B2 Day 1 — resolved from DB at runtime; missing lemmas are skipped.
B2_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "reduce", "pos": "verb", "vi_gloss": "giảm"},
    {"lemma": "provide", "pos": "verb", "vi_gloss": "cung cấp"},
    {"lemma": "risk", "pos": "noun", "vi_gloss": "nguy cơ"},
    {"lemma": "suitable", "pos": "adj", "vi_gloss": "phù hợp"},
    {"lemma": "absorb", "pos": "verb", "vi_gloss": "hấp thụ"},
    {"lemma": "resident", "pos": "noun", "vi_gloss": "cư dân"},
    {"lemma": "concrete", "pos": "noun", "vi_gloss": "bê tông"},
    {"lemma": "drainage", "pos": "noun", "vi_gloss": "hệ thống thoát nước"},
    {"lemma": "maintain", "pos": "verb", "vi_gloss": "bảo trì / duy trì"},
    {"lemma": "infrastructure", "pos": "noun", "vi_gloss": "cơ sở hạ tầng"},
]

B2_DAY1_PARAGRAPHS = [
    "In many large cities, summer heat is becoming harder to manage. Roads, buildings, "
    "and car parks absorb heat during the day and release it slowly at night. As a "
    "result, some city centres remain warm even after the sun has gone down. This "
    'problem is often called the "urban heat island" effect.',
    "One practical way to reduce this effect is to plant more trees. Trees provide "
    "shade, so streets and buildings do not become as hot in direct sunlight. They "
    "also release small amounts of water into the air, which can help cool the area "
    "around them. For residents who walk to school, work, or public transport, a "
    "tree-lined street can feel much more comfortable than an open road with no shade.",
    "Trees can also help cities deal with heavy rain. In places covered mainly by "
    "concrete and asphalt, rainwater quickly runs into drains. During storms, this can "
    "put pressure on the drainage system and increase the risk of flooding. Soil, grass, "
    "and tree roots can absorb some of this water before it reaches the drains.",
    "However, tree planting is not a quick solution by itself. Young trees need regular "
    "water, suitable soil, and protection from traffic. If they are not maintained, many "
    "die within a few years. City planners also need to think about fairness. Wealthier "
    "neighbourhoods often have more trees and cooler streets, while poorer areas may have "
    "fewer green spaces and hotter roads.",
    "For this reason, urban trees should be seen as more than decoration. They are part "
    "of city infrastructure, like roads, drains, and public transport. When trees are "
    "planted in the right places and cared for over time, they can reduce heat, manage "
    "rainwater, and make city life healthier for more people.",
]

B2_DAY1_QUESTIONS = [
    {
        "id": "b2-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "What is the main idea of the passage?",
        "options": [
            "City trees can reduce heat and flooding, but they need careful planning and maintenance.",
            "Rural areas are becoming hotter than city centres.",
            "Trees are useful mainly because they make streets look beautiful.",
            "City planners should replace all roads with forests.",
        ],
        "correct_option": "City trees can reduce heat and flooding, but they need careful planning and maintenance.",
        "explanation": "The passage explains that trees help reduce heat and flooding, but they need planning, suitable conditions, and maintenance.",
    },
    {
        "id": "b2-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "Why do some city centres remain warm after the sun has gone down?",
        "options": [
            "Roads and buildings release heat slowly at night.",
            "Trees release too much water into the air.",
            "Rural areas send warm air into cities.",
            "Public transport produces shade.",
        ],
        "correct_option": "Roads and buildings release heat slowly at night.",
        "explanation": "Paragraph 1 says roads, buildings, and car parks absorb heat during the day and release it slowly at night.",
    },
    {
        "id": "b2-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "The writer mentions tree-lined streets to show that trees can:",
        "options": [
            "make daily walking more comfortable for residents",
            "stop people from using public transport",
            "make roads wider and faster",
            "remove the need for pavements",
        ],
        "correct_option": "make daily walking more comfortable for residents",
        "explanation": "Paragraph 2 explains that tree-lined streets feel more comfortable for residents who walk to school, work, or public transport.",
    },
    {
        "id": "b2-d01-q04",
        "sort_order": 4,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with NO MORE THAN TWO WORDS from the passage.\n\n"
        "During storms, too much rainwater can put pressure on __________.",
        "options": [],
        "correct_option": "drainage system",
        "acceptable_answers": ["drainage system"],
        "explanation": "Paragraph 3 says rainwater can put pressure on the drainage system during storms.",
    },
    {
        "id": "b2-d01-q05",
        "sort_order": 5,
        "question_type": "comprehension",
        "prompt": "According to the passage, why may some young trees die?",
        "options": [
            "They grow too quickly.",
            "They are planted only in rural areas.",
            "They are not maintained properly.",
            "They absorb too much sunlight.",
        ],
        "correct_option": "They are not maintained properly.",
        "explanation": "Paragraph 4 says many young trees die if they are not maintained.",
    },
    {
        "id": "b2-d01-q06",
        "sort_order": 6,
        "question_type": "comprehension",
        "prompt": "What fairness problem is mentioned in the passage?",
        "options": [
            "Some neighbourhoods have more trees and cooler streets than others.",
            "All neighbourhoods have the same number of green spaces.",
            "Wealthier areas are usually hotter than poorer areas.",
            "Public transport always damages trees.",
        ],
        "correct_option": "Some neighbourhoods have more trees and cooler streets than others.",
        "explanation": "Paragraph 4 says wealthier neighbourhoods often have more trees and cooler streets, while poorer areas may have fewer green spaces and hotter roads.",
    },
    {
        "id": "b2-d01-q07",
        "sort_order": 7,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "absorb" means:',
        "options": ["take in", "throw away", "make louder", "move quickly"],
        "correct_option": "take in",
        "source_word": "absorb",
        "explanation": "Absorb means take in. In the passage, roads absorb heat, and soil or roots can absorb water.",
    },
    {
        "id": "b2-d01-q08",
        "sort_order": 8,
        "question_type": "vocabulary",
        "prompt": 'In the phrase "increase the risk of flooding", "risk" means:',
        "options": [
            "the possibility of something bad happening",
            "a safe and comfortable situation",
            "a plan for building new roads",
            "a type of public transport",
        ],
        "correct_option": "the possibility of something bad happening",
        "source_word": "risk",
        "explanation": "Risk means the possibility of something bad happening. Here, the bad event is flooding.",
    },
    {
        "id": "b2-d01-q09",
        "sort_order": 9,
        "question_type": "comprehension",
        "prompt": "Why are trees compared to roads, drains, and public transport in the final paragraph?",
        "options": [
            "To show that trees should be treated as important city infrastructure",
            "To explain why trees are more expensive than buses",
            "To argue that roads should be replaced by forests",
            "To show that trees are only decorative",
        ],
        "correct_option": "To show that trees should be treated as important city infrastructure",
        "explanation": "The comparison shows that trees are not just decoration; they are part of important city infrastructure.",
    },
    {
        "id": "b2-d01-q10",
        "sort_order": 10,
        "question_type": "comprehension",
        "prompt": "Which statement would the writer most likely agree with?",
        "options": [
            "Tree planting is useful only when cities also plan and care for trees properly.",
            "Trees solve urban heat problems immediately after they are planted.",
            "Young trees do not need water or protection.",
            "Green spaces are less important than car parks.",
        ],
        "correct_option": "Tree planting is useful only when cities also plan and care for trees properly.",
        "explanation": "The writer supports tree planting but argues it must be planned and maintained carefully.",
    },
]

C1_DAY1_UNIT_ID = "c1-day01-green-gentrification"

C1_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "resident", "pos": "noun", "vi_gloss": "cư dân"},
    {"lemma": "restore", "pos": "verb", "vi_gloss": "khôi phục"},
    {"lemma": "desirable", "pos": "adj", "vi_gloss": "đáng mong muốn"},
    {"lemma": "policy", "pos": "noun", "vi_gloss": "chính sách"},
    {"lemma": "neglected", "pos": "adj", "vi_gloss": "bị bỏ bê"},
    {"lemma": "renovate", "pos": "verb", "vi_gloss": "cải tạo"},
    {
        "lemma": "unaffordable",
        "pos": "adj",
        "vi_gloss": "quá đắt, không đủ khả năng chi trả",
    },
    {"lemma": "distribute", "pos": "verb", "vi_gloss": "phân bổ / phân phối"},
    {"lemma": "vulnerable", "pos": "adj", "vi_gloss": "dễ bị tổn thương"},
    {"lemma": "equity", "pos": "noun", "vi_gloss": "công bằng xã hội"},
]

C1_DAY1_PARAGRAPHS = [
    "Many cities are trying to become greener. They plant trees, build parks, and "
    "restore old riverside paths. These projects can reduce heat, improve air quality, "
    "and give each resident a better place to walk, rest, or meet others. In city "
    "reports, urban greening is often presented as a public good that benefits everyone.",
    "However, the benefits are not always distributed equally. When a neglected area "
    "receives a new park, it may become more attractive to visitors, businesses, and "
    "property developers. Cafés open nearby, older buildings are renovated, and the "
    "neighbourhood starts to appear in lifestyle magazines. What begins as an "
    "environmental improvement can also change the local housing market.",
    "For homeowners, this change may be positive because their properties become more "
    "desirable. For renters, however, the situation can be harder. As the area becomes "
    "popular, rents may rise. Some long-term residents may find the neighbourhood "
    "unaffordable and feel pressure to move away. This process is often called green "
    "gentrification.",
    "The problem is not that parks are harmful in themselves. Green spaces can improve "
    "physical and mental health, especially in crowded districts where people do not "
    "have private gardens. The real question is whether environmental policy is "
    "connected to housing protection, community participation, and fair access. A park "
    "may look successful in a planning report but still fail local people if they "
    "cannot afford to remain nearby.",
    "Some cities have tried to address this tension by combining greening projects "
    "with rent controls, affordable housing rules, or community land trusts. These "
    "tools aim to distribute the benefits of urban nature more fairly. They also "
    "recognise that some groups are more vulnerable to rent increases than others.",
    "Urban greening should therefore not be judged only by the number of trees planted "
    "or the size of new parks. A greener city is not simply a city with more plants. "
    "It is a city where environmental improvement supports social equity and helps "
    "existing communities stay healthy, secure, and connected.",
]

C1_DAY1_QUESTIONS = [
    {
        "id": "c1-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "What is the main idea of the passage?",
        "options": [
            "Urban greening can be valuable, but it must be linked to housing protection and fair access.",
            "Cities should stop building parks because parks always increase rent.",
            "Property developers are the only people who benefit from green spaces.",
            "Urban greening is mainly useful because it makes cities look more modern.",
        ],
        "correct_option": "Urban greening can be valuable, but it must be linked to housing protection and fair access.",
        "explanation": "The passage supports urban greening but argues that it should be linked to housing protection, participation, and fair access.",
    },
    {
        "id": "c1-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "Why does the writer mention cafés, renovated buildings, and lifestyle magazines?",
        "options": [
            "To show how a greener area can become more commercially attractive",
            "To explain why cafés should be built inside every park",
            "To prove that magazines are responsible for rising rents",
            "To argue that old buildings should never be repaired",
        ],
        "correct_option": "To show how a greener area can become more commercially attractive",
        "explanation": "These examples show that a neglected area can become more attractive to businesses, visitors, and developers after greening.",
    },
    {
        "id": "c1-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "According to the passage, why may renters suffer from green gentrification?",
        "options": [
            "They may face higher rents and feel pressure to leave.",
            "They usually own the most valuable homes.",
            "They are not allowed to use new parks.",
            "They prefer areas without trees or riverside paths.",
        ],
        "correct_option": "They may face higher rents and feel pressure to leave.",
        "explanation": "Paragraph 3 says rents may rise and long-term residents may feel pressure to move away.",
    },
    {
        "id": "c1-d01-q04",
        "sort_order": 4,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with NO MORE THAN TWO WORDS from the passage.\n\n"
        "Some cities combine greening projects with rent controls and __________ rules.",
        "options": [],
        "correct_option": "affordable housing",
        "acceptable_answers": ["affordable housing"],
        "explanation": "Paragraph 5 mentions rent controls, affordable housing rules, and community land trusts.",
    },
    {
        "id": "c1-d01-q05",
        "sort_order": 5,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "neglected" is closest in meaning to:',
        "options": [
            "not given enough care or attention",
            "very expensive and popular",
            "newly built and modern",
            "protected by strict rules",
        ],
        "correct_option": "not given enough care or attention",
        "source_word": "neglected",
        "explanation": "Neglected means not given enough care or attention.",
    },
    {
        "id": "c1-d01-q06",
        "sort_order": 6,
        "question_type": "comprehension",
        "prompt": 'The word "unaffordable" is used to emphasise that some residents may:',
        "options": [
            "be unable to pay the cost of living nearby",
            "dislike the design of the new park",
            "prefer private gardens to public spaces",
            "be uninterested in environmental policy",
        ],
        "correct_option": "be unable to pay the cost of living nearby",
        "explanation": "Unaffordable means too expensive to pay for; here it shows that some residents may not be able to remain nearby.",
    },
    {
        "id": "c1-d01-q07",
        "sort_order": 7,
        "question_type": "comprehension",
        "prompt": "What does the writer suggest about environmental policy?",
        "options": [
            "It should be connected to housing protection, participation, and access.",
            "It should focus only on planting as many trees as possible.",
            "It should ignore renters and focus on homeowners.",
            "It should be controlled only by property developers.",
        ],
        "correct_option": "It should be connected to housing protection, participation, and access.",
        "explanation": "Paragraph 4 says environmental policy should be connected to housing protection, community participation, and fair access.",
    },
    {
        "id": "c1-d01-q08",
        "sort_order": 8,
        "question_type": "vocabulary",
        "prompt": 'In the passage, "vulnerable" means:',
        "options": [
            "easily harmed or affected",
            "extremely wealthy",
            "unwilling to use public spaces",
            "fully protected from change",
        ],
        "correct_option": "easily harmed or affected",
        "source_word": "vulnerable",
        "explanation": "Vulnerable means easily harmed or affected. In this context, some groups are more affected by rent increases.",
    },
    {
        "id": "c1-d01-q09",
        "sort_order": 9,
        "question_type": "comprehension",
        "prompt": "What is the function of the final paragraph?",
        "options": [
            "To redefine a greener city as one that combines environmental benefits with social fairness",
            "To introduce a new topic about private gardens",
            "To argue that tree numbers are the only important measure",
            "To explain how to build a riverside path",
        ],
        "correct_option": "To redefine a greener city as one that combines environmental benefits with social fairness",
        "explanation": "The final paragraph explains that a greener city should not be judged only by plants, but also by social equity.",
    },
    {
        "id": "c1-d01-q10",
        "sort_order": 10,
        "question_type": "comprehension",
        "prompt": "Which statement would the writer most likely agree with?",
        "options": [
            "A truly greener city should improve the environment without pushing existing residents aside.",
            "A park is successful if it increases property prices as quickly as possible.",
            "Green spaces are harmful and should not be built in crowded districts.",
            "Housing costs are unrelated to environmental improvement.",
        ],
        "correct_option": "A truly greener city should improve the environment without pushing existing residents aside.",
        "explanation": "The writer argues that greening should support existing communities instead of pushing vulnerable people aside.",
    },
]

C2_DAY1_UNIT_ID = "c2-day01-convenience-surveillance"

C2_DAY1_VOCAB_KEYWORDS = [
    {"lemma": "behavioural", "pos": "adj", "vi_gloss": "thuộc về hành vi"},
    {"lemma": "voluntary", "pos": "adj", "vi_gloss": "tự nguyện"},
    {"lemma": "preference", "pos": "noun", "vi_gloss": "sở thích / sự ưu tiên"},
    {"lemma": "normalise", "pos": "verb", "vi_gloss": "bình thường hóa"},
    {"lemma": "monitoring", "pos": "noun", "vi_gloss": "sự theo dõi / giám sát"},
    {"lemma": "manipulation", "pos": "noun", "vi_gloss": "sự thao túng"},
    {"lemma": "surveillance", "pos": "noun", "vi_gloss": "sự giám sát"},
    {"lemma": "asymmetry", "pos": "noun", "vi_gloss": "sự bất cân xứng"},
    {"lemma": "autonomy", "pos": "noun", "vi_gloss": "quyền tự chủ"},
    {"lemma": "costly", "pos": "adj", "vi_gloss": "khó/tốn kém trong thực tế"},
]

C2_DAY1_PARAGRAPHS = [
    "Modern technology is often praised for making everyday life more convenient. A phone "
    "can suggest the fastest route home, a streaming platform can recommend a film, and an "
    "online store can remember what a customer bought last month. These services save time "
    "and reduce effort. For many people, this convenience feels harmless, even natural.",
    "However, convenience usually depends on data. To offer personalised services, digital "
    "platforms collect information about what users click, watch, search for, buy, and "
    "ignore. This kind of behavioural data helps companies predict future choices. A "
    "platform does not need to understand a person deeply; it only needs enough "
    "information to guess what the person may do next.",
    "The exchange is often described as voluntary. Users can accept cookies, turn on "
    "location services, or choose whether to create an account. Yet this idea of free "
    "choice becomes less convincing when digital tools are built into work, education, "
    "banking, transport, and social life. Refusing them may be possible in theory, but "
    "practically costly in reality.",
    "This creates an asymmetry between users and platforms. Users may know that data is "
    "being collected, but they rarely know exactly how it is analysed, combined, or used. "
    "Companies, by contrast, can study millions of users at once and adjust their systems "
    "to influence what people notice, prefer, and choose. A recommendation system does "
    "not simply respond to user preference; over time, it may help shape it.",
    "The problem is not that digital convenience has no value. Online maps, learning "
    "platforms, and communication tools can make life easier and more accessible. The "
    "danger is that convenience can normalise constant monitoring. When people become used "
    "to being tracked in exchange for smoother services, they may slowly surrender part "
    "of their autonomy without noticing.",
    "A better approach would not require people to reject technology completely. Instead, "
    "it would place clearer limits on data collection, reduce hidden manipulation, and "
    "give users real alternatives when they do not want to be tracked. The central "
    "question is not whether convenience is useful. It is whether society can keep its "
    "benefits without allowing convenience to become a softer name for surveillance.",
]

C2_DAY1_QUESTIONS = [
    {
        "id": "c2-d01-q01",
        "sort_order": 1,
        "question_type": "comprehension",
        "prompt": "What is the main idea of the passage?",
        "options": [
            "Digital convenience is useful, but it can hide surveillance and reduce user autonomy.",
            "Modern technology is always harmful and should be avoided.",
            "Online platforms collect data only to improve user happiness.",
            "Convenience is more important than privacy in modern life.",
        ],
        "correct_option": "Digital convenience is useful, but it can hide surveillance and reduce user autonomy.",
        "explanation": "The passage does not reject technology, but argues that convenience can hide surveillance and reduce autonomy.",
    },
    {
        "id": "c2-d01-q02",
        "sort_order": 2,
        "question_type": "comprehension",
        "prompt": "Why does the writer mention phones, streaming platforms, and online stores in paragraph 1?",
        "options": [
            "To introduce everyday examples of digital convenience",
            "To argue that entertainment is more important than work",
            "To prove that online shopping is the main form of surveillance",
            "To compare old technology with new technology",
        ],
        "correct_option": "To introduce everyday examples of digital convenience",
        "explanation": "These examples introduce familiar ways digital services make daily life easier.",
    },
    {
        "id": "c2-d01-q03",
        "sort_order": 3,
        "question_type": "comprehension",
        "prompt": "According to paragraph 2, why do platforms collect behavioural data?",
        "options": [
            "To predict what users may do next",
            "To understand users emotionally",
            "To stop users from buying products",
            "To remove the need for online accounts",
        ],
        "correct_option": "To predict what users may do next",
        "explanation": "Paragraph 2 says behavioural data helps companies predict future choices.",
    },
    {
        "id": "c2-d01-q04",
        "sort_order": 4,
        "question_type": "gap_fill",
        "prompt": "Complete the sentence with NO MORE THAN TWO WORDS from the passage.\n\n"
        "Refusing digital tools may be possible in theory, but __________ in reality.",
        "options": [],
        "correct_option": "practically costly",
        "acceptable_answers": ["practically costly"],
        "explanation": "Paragraph 3 says refusing digital tools may be possible in theory, but practically costly in reality.",
    },
    {
        "id": "c2-d01-q05",
        "sort_order": 5,
        "question_type": "comprehension",
        "prompt": "In paragraph 3, the writer questions the idea that digital participation is truly voluntary because:",
        "options": [
            "avoiding digital tools can create real practical disadvantages",
            "users never accept cookies or create accounts",
            "all digital tools are illegal to refuse",
            "technology is used only for entertainment",
        ],
        "correct_option": "avoiding digital tools can create real practical disadvantages",
        "explanation": "Paragraph 3 explains that digital tools are built into work, education, banking, transport, and social life, so refusing them can be costly in practice.",
    },
    {
        "id": "c2-d01-q06",
        "sort_order": 6,
        "question_type": "comprehension",
        "prompt": 'The term "asymmetry" is used to highlight that:',
        "options": [
            "platforms possess greater knowledge and influence than individual users",
            "users and platforms exchange equal forms of value",
            "digital systems are becoming less capable of predicting behaviour",
            "companies are unable to influence choices at scale",
        ],
        "correct_option": "platforms possess greater knowledge and influence than individual users",
        "explanation": "Asymmetry highlights an unequal relationship: platforms can analyse and influence users more than users can understand or control platforms.",
    },
    {
        "id": "c2-d01-q07",
        "sort_order": 7,
        "question_type": "comprehension",
        "prompt": "What does the writer suggest about recommendation systems?",
        "options": [
            "They may shape what users come to prefer.",
            "They only show users random content.",
            "They make users completely independent.",
            "They prevent companies from collecting data.",
        ],
        "correct_option": "They may shape what users come to prefer.",
        "explanation": "Paragraph 4 says recommendation systems may help shape user preference over time.",
    },
    {
        "id": "c2-d01-q08",
        "sort_order": 8,
        "question_type": "comprehension",
        "prompt": 'In paragraph 5, "surrender part of their autonomy" means users may:',
        "options": [
            "give up some control over their own choices",
            "lose access to all digital services immediately",
            "become better at making independent decisions",
            "refuse to use convenient technology",
        ],
        "correct_option": "give up some control over their own choices",
        "explanation": "Autonomy means control over one's own choices, so surrendering autonomy means giving up some of that control.",
    },
    {
        "id": "c2-d01-q09",
        "sort_order": 9,
        "question_type": "comprehension",
        "prompt": 'Why does the writer say the solution is not to "reject technology completely"?',
        "options": [
            "Because the writer recognises that digital tools can have real benefits.",
            "Because the writer believes all tracking is harmless.",
            "Because the writer thinks privacy is no longer important.",
            "Because the writer wants companies to collect more data.",
        ],
        "correct_option": "Because the writer recognises that digital tools can have real benefits.",
        "explanation": "Paragraph 5 says digital convenience has value and gives examples such as maps, learning platforms, and communication tools.",
    },
    {
        "id": "c2-d01-q10",
        "sort_order": 10,
        "question_type": "comprehension",
        "prompt": "Which statement would the writer most likely agree with?",
        "options": [
            "Technology should be designed so people can benefit from it without unnecessary tracking.",
            "Users should accept all monitoring because convenience is always worth the cost.",
            "Companies should be free to collect any data they want.",
            "People who care about privacy should stop using all modern technology.",
        ],
        "correct_option": "Technology should be designed so people can benefit from it without unnecessary tracking.",
        "explanation": "The final paragraph supports limits, less manipulation, and real alternatives, not a complete rejection of technology.",
    },
]


async def seed_a1_day1(repo: CoachingContentRepo) -> None:
    await repo.upsert_unit(
        unit_id=A1_DAY1_UNIT_ID,
        cefr_level="A1",
        day_number=1,
        topic_slug="small-bag",
        topic_title="Everyday objects & school",
        title="A Small Bag",
        paragraphs=A1_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · A1",
        estimated_minutes=6,
        question_limit=8,
        content_version=2,
        status="published",
        questions=A1_DAY1_QUESTIONS,
        vocab_keywords=A1_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", A1_DAY1_UNIT_ID)


async def retire_a2_legacy_units(repo: CoachingContentRepo) -> None:
    await repo.s.execute(
        delete(CoachingReadingUnitQuestion).where(
            CoachingReadingUnitQuestion.unit_id.in_(RETIRED_A2_UNIT_IDS)
        )
    )
    await repo.s.execute(
        update(CoachingReadingUnit)
        .where(CoachingReadingUnit.id.in_(RETIRED_A2_UNIT_IDS))
        .values(status="archived")
    )
    logger.info("Archived retired A2 units: {}", ", ".join(RETIRED_A2_UNIT_IDS))


async def seed_a2_day1(repo: CoachingContentRepo) -> None:
    await retire_a2_legacy_units(repo)
    await repo.upsert_unit(
        unit_id=A2_DAY1_UNIT_ID,
        cefr_level="A2",
        day_number=1,
        topic_slug="quiet-park",
        topic_title="City life & green spaces",
        title="A Quiet Park",
        paragraphs=A2_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · A2",
        estimated_minutes=8,
        question_limit=8,
        content_version=2,
        status="published",
        questions=A2_DAY1_QUESTIONS,
        vocab_keywords=A2_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", A2_DAY1_UNIT_ID)


async def seed_b1_day1(repo: CoachingContentRepo) -> None:
    await repo.upsert_unit(
        unit_id=B1_DAY1_UNIT_ID,
        cefr_level="B1",
        day_number=1,
        topic_slug="study-habit",
        topic_title="Learning habits & vocabulary",
        title="Building a Study Habit",
        paragraphs=B1_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · B1",
        estimated_minutes=8,
        question_limit=9,
        content_version=3,
        status="published",
        questions=B1_DAY1_QUESTIONS,
        vocab_keywords=B1_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", B1_DAY1_UNIT_ID)


async def seed_b2_day1(repo: CoachingContentRepo) -> None:
    await repo.upsert_unit(
        unit_id=B2_DAY1_UNIT_ID,
        cefr_level="B2",
        day_number=1,
        topic_slug="urban-heat",
        topic_title="Urban climate & green infrastructure",
        title="City Trees and Urban Heat",
        paragraphs=B2_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · B2",
        estimated_minutes=10,
        question_limit=10,
        content_version=5,
        status="published",
        questions=B2_DAY1_QUESTIONS,
        vocab_keywords=B2_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", B2_DAY1_UNIT_ID)


async def seed_c1_day1(repo: CoachingContentRepo) -> None:
    await repo.upsert_unit(
        unit_id=C1_DAY1_UNIT_ID,
        cefr_level="C1",
        day_number=1,
        topic_slug="green-gentrification",
        topic_title="Urban greening & housing",
        title="Green Parks and Rising Rents",
        paragraphs=C1_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · C1",
        estimated_minutes=10,
        question_limit=10,
        content_version=2,
        status="published",
        questions=C1_DAY1_QUESTIONS,
        vocab_keywords=C1_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", C1_DAY1_UNIT_ID)


async def seed_c2_day1(repo: CoachingContentRepo) -> None:
    await repo.upsert_unit(
        unit_id=C2_DAY1_UNIT_ID,
        cefr_level="C2",
        day_number=1,
        topic_slug="convenience-surveillance",
        topic_title="Digital convenience & privacy",
        title="Convenience and Surveillance",
        paragraphs=C2_DAY1_PARAGRAPHS,
        source_label="PrimeVocab Original · C2",
        estimated_minutes=10,
        question_limit=10,
        content_version=3,
        status="published",
        questions=C2_DAY1_QUESTIONS,
        vocab_keywords=C2_DAY1_VOCAB_KEYWORDS,
    )
    logger.info("Seeded coaching reading unit {}", C2_DAY1_UNIT_ID)


async def main() -> None:
    core_db.init_pg()
    async with core_db.pg_session() as session:
        repo = CoachingContentRepo(session)
        await seed_a1_day1(repo)
        await seed_a2_day1(repo)
        await seed_b1_day1(repo)
        await seed_b2_day1(repo)
        await seed_c1_day1(repo)
        await seed_c2_day1(repo)
    logger.info("Coaching reading catalog seed complete")


if __name__ == "__main__":
    asyncio.run(main())
