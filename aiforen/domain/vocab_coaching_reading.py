"""Real Cambridge-style reading content for vocab coaching.

The Day 1 passage is the Cambridge IELTS 10 "stepwells of India" reading. It is
stored pre-split into paragraphs so the frontend can render a clean, readable
column, and it ships with authored comprehension questions plus a curated
over-band difficult-word list used as a fallback when DB lexeme lookups are thin.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Ordered paragraphs reconstructed from the source passage.
STEPWELLS_PARAGRAPHS: List[str] = [
    "A millennium ago, stepwells were fundamental to life in the driest parts of "
    "India. Although many have been neglected, recent restoration has returned them "
    "to their former glory. Richard Cox travelled to north-western India to document "
    "these spectacular monuments from a bygone era.",
    "During the sixth and seventh centuries, the inhabitants of the modern-day states "
    "of Gujarat and Rajasthan in north-western India developed a method of gaining "
    "access to clean, fresh groundwater during the dry season for drinking, bathing, "
    "watering animals and irrigation. However, the significance of this invention — "
    "the stepwell — goes beyond its utilitarian application.",
    "Unique to the region, stepwells are often architecturally complex and vary widely "
    "in size and shape. During their heyday, they were places of gathering, of leisure, "
    "of relaxation and of worship for villagers of all but the lowest castes. Most "
    "stepwells are found dotted around the desert areas of Gujarat (where they are "
    "called vav) and Rajasthan (where they are known as baori), while a few also "
    "survive in Delhi. Some were located in or near villages as public spaces for the "
    "community; others were positioned beside roads as resting places for travellers.",
    "As their name suggests, stepwells comprise a series of stone steps descending from "
    "ground level to the water source (normally an underground aquifer) as it recedes "
    "following the rains. When the water level was high, the user needed only to descend "
    "a few steps to reach it; when it was low, several levels would have to be negotiated.",
    "Some wells are vast, open craters with hundreds of steps paving each sloping side, "
    "often in tiers. Others are more elaborate, with long stepped passages leading to "
    "the water via several storeys built from stone and supported by pillars; they also "
    "included pavilions that sheltered visitors from the relentless heat. But perhaps "
    "the most impressive features are the intricate decorative sculptures that embellish "
    "many stepwells, showing activities from fighting and dancing to everyday acts such "
    "as women combing their hair and churning butter.",
    "Down the centuries, thousands of wells were constructed throughout north-western "
    "India, but the majority have now fallen into disuse; many are derelict and dry, as "
    "groundwater has been diverted for industrial use and the wells no longer reach the "
    "water table. Their condition hasn't been helped by recent dry spells: southern "
    "Rajasthan suffered an eight-year drought between 1996 and 2004.",
    "However, some important sites in Gujarat have recently undergone major restoration, "
    "and the state government announced in June last year that it plans to restore the "
    "stepwells throughout the state.",
    "In Patan, the state's ancient capital, the stepwell of Rani Ki Vav (Queen's "
    "Stepwell) is perhaps the finest current example. It was built by Queen Udayamati "
    "during the late 11th century, but became silted up following a flood during the "
    "13th century. But the Archaeological Survey of India began restoring it in the "
    "1960s, and today it's in pristine condition. At 65 metres long, 20 metres wide and "
    "27 metres deep, Rani Ki Vav features 500 distinct sculptures carved into niches "
    "throughout the monument, depicting gods such as Vishnu and Parvati in various "
    "incarnations. Incredibly, in January 2001, this ancient structure survived a "
    "devastating earthquake that measured 7.6 on the Richter scale.",
    "Another example is the Surya Kund in Modhera, northern Gujarat, next to the Sun "
    "Temple, built by King Bhima I in 1026 to honour the sun god Surya. It's actually a "
    "tank (kund means reservoir or pond) rather than a well, but displays the hallmarks "
    "of stepwell architecture, including four sides of steps that descend to the bottom "
    "in a stunning geometrical formation. The terraces house 108 small, intricately "
    "carved shrines between the sets of steps.",
    "Rajasthan also has a wealth of wells. The ancient city of Bundi, 200 kilometres "
    "south of Jaipur, is renowned for its architecture, including its stepwells. One of "
    "the larger examples is Raniji Ki Baori, which was built by the queen of the region, "
    "Nathavatji, in 1699. At 46 metres deep, 20 metres wide and 40 metres long, the "
    "intricately carved monument is one of 21 baoris commissioned in the Bundi area by "
    "Nathavatji.",
    "In the old ruined town of Abhaneri, about 95 kilometres east of Jaipur, is Chand "
    "Baori, one of India's oldest and deepest wells; aesthetically, it's perhaps one of "
    "the most dramatic. Built in around 850 AD next to the temple of Harshat Mata, the "
    "baori comprises hundreds of zigzagging steps that run along three of its sides, "
    "steeply descending 11 storeys, resulting in a striking geometric pattern when seen "
    "from afar. On the fourth side, covered verandas supported by ornate pillars "
    "overlook the steps.",
    "Still in public use is Neemrana Ki Baori, located just off the Jaipur–Delhi "
    "highway. Constructed in around 1700, it's nine storeys deep, with the last two "
    "levels underwater. At ground level, there are 86 colonnaded openings from where the "
    "visitor descends 170 steps to the deepest water source.",
    "Today, following years of neglect, many of these monuments to medieval engineering "
    "have been saved by the Archaeological Survey of India, which has recognised the "
    "importance of preserving them as part of the country's rich history. Tourists flock "
    "to wells in far-flung corners of north-western India to gaze in wonder at these "
    "architectural marvels from 1,000 years ago, which serve as a reminder of both the "
    "ingenuity and artistry of ancient civilisations and of the value of water to human "
    "existence.",
]

# Curated over-band vocabulary for this passage with a CEFR/IELTS hint. Used as a
# fallback / union with DB lexeme lookups so flagged words are always meaningful.
CURATED_DIFFICULT_WORDS: Dict[str, Dict[str, Any]] = {
    "millennium": {"cefr": "C1", "band": 7.0},
    "neglected": {"cefr": "B2", "band": 6.5},
    "utilitarian": {"cefr": "C2", "band": 8.0},
    "heyday": {"cefr": "C2", "band": 8.0},
    "castes": {"cefr": "C1", "band": 7.0},
    "aquifer": {"cefr": "C1", "band": 7.5},
    "recedes": {"cefr": "C1", "band": 7.0},
    "negotiated": {"cefr": "B2", "band": 6.5},
    "craters": {"cefr": "B2", "band": 6.5},
    "tiers": {"cefr": "C1", "band": 7.0},
    "elaborate": {"cefr": "B2", "band": 6.5},
    "storeys": {"cefr": "B2", "band": 6.0},
    "pavilions": {"cefr": "C1", "band": 7.0},
    "relentless": {"cefr": "C1", "band": 7.5},
    "intricate": {"cefr": "C1", "band": 7.0},
    "embellish": {"cefr": "C2", "band": 8.0},
    "churning": {"cefr": "C1", "band": 7.0},
    "derelict": {"cefr": "C2", "band": 8.0},
    "diverted": {"cefr": "B2", "band": 6.5},
    "silted": {"cefr": "C2", "band": 8.0},
    "pristine": {"cefr": "C1", "band": 7.5},
    "niches": {"cefr": "C1", "band": 7.5},
    "incarnations": {"cefr": "C1", "band": 7.5},
    "devastating": {"cefr": "B2", "band": 6.5},
    "hallmarks": {"cefr": "C1", "band": 7.0},
    "terraces": {"cefr": "B2", "band": 6.5},
    "shrines": {"cefr": "B2", "band": 6.5},
    "renowned": {"cefr": "C1", "band": 7.0},
    "commissioned": {"cefr": "C1", "band": 7.0},
    "zigzagging": {"cefr": "C1", "band": 7.0},
    "verandas": {"cefr": "C1", "band": 7.0},
    "ornate": {"cefr": "C1", "band": 7.5},
    "colonnaded": {"cefr": "C2", "band": 8.0},
    "ingenuity": {"cefr": "C1", "band": 7.5},
    "artistry": {"cefr": "C1", "band": 7.0},
}

# Authored comprehension / vocabulary / context questions for the passage.
STEPWELLS_QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "rq-purpose",
        "type": "comprehension",
        "prompt": "What is the main purpose of the passage?",
        "options": [
            "To explain how stepwells were built, used and later restored",
            "To argue that stepwells should replace modern water systems",
            "To compare Indian stepwells with wells in other countries",
            "To describe a single stepwell in complete detail",
        ],
        "correct_option": "To explain how stepwells were built, used and later restored",
        "explanation": "The passage traces the function, design, decline and restoration of stepwells across several sites.",
    },
    {
        "id": "rq-utilitarian",
        "type": "vocabulary",
        "source_word": "utilitarian",
        "prompt": 'In paragraph 2, "utilitarian" is closest in meaning to:',
        "options": [
            "practical and functional",
            "religious and sacred",
            "expensive and rare",
            "decorative and beautiful",
        ],
        "correct_option": "practical and functional",
        "explanation": "The text says the stepwell's significance goes beyond its utilitarian (practical) use of supplying water.",
    },
    {
        "id": "rq-disuse",
        "type": "context",
        "prompt": "Why have most stepwells fallen into disuse?",
        "options": [
            "Groundwater was diverted for industry and the wells no longer reach the water table",
            "Local people preferred to travel to rivers instead",
            "The government banned their use for safety reasons",
            "They were destroyed by the 2001 earthquake",
        ],
        "correct_option": "Groundwater was diverted for industry and the wells no longer reach the water table",
        "explanation": "Paragraph 6 links disuse to diverted groundwater and falling water tables, worsened by drought.",
    },
    {
        "id": "rq-rani",
        "type": "comprehension",
        "prompt": "What is notable about Rani Ki Vav?",
        "options": [
            "It survived a major earthquake and holds 500 sculptures in pristine condition",
            "It is the only stepwell still supplying drinking water",
            "It was never affected by flooding or silt",
            "It is the deepest stepwell in Rajasthan",
        ],
        "correct_option": "It survived a major earthquake and holds 500 sculptures in pristine condition",
        "explanation": "Paragraph 8 describes its 500 sculptures, restoration to pristine condition, and surviving a 7.6 earthquake.",
    },
    {
        "id": "rq-intricate",
        "type": "vocabulary",
        "source_word": "intricate",
        "prompt": 'In the passage, "intricate" most nearly means:',
        "options": [
            "highly detailed and complex",
            "old and damaged",
            "large and heavy",
            "plain and simple",
        ],
        "correct_option": "highly detailed and complex",
        "explanation": "Intricate sculptures are finely detailed, contrasting with plain or simple ones.",
    },
]

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
_SENT_RE = re.compile(r"[^.!?]+[.!?]+")


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z'-]", "", (value or "").strip().lower())


def passage_text() -> str:
    return "\n\n".join(STEPWELLS_PARAGRAPHS)


def passage_tokens() -> List[str]:
    """Unique lowercase word tokens in passage order."""
    seen: set[str] = set()
    out: List[str] = []
    for match in _TOKEN_RE.finditer(passage_text()):
        token = normalize_token(match.group(0))
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def find_sentence(phrase: str) -> str:
    clean = (phrase or "").strip().lower()
    if not clean:
        return ""
    for sentence in _SENT_RE.findall(passage_text()):
        if clean in sentence.lower():
            return sentence.strip()
    return (phrase or "").strip()


def build_reading_payload(difficult_words: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble the stored reading JSON for a coaching day."""
    return {
        "id": "cambridge10-stepwells",
        "title": "The stepwells of India",
        "source_label": "Cambridge IELTS 10 · Reading",
        "estimated_minutes": 8,
        "paragraphs": list(STEPWELLS_PARAGRAPHS),
        "difficult_words": difficult_words,
        "questions": list(STEPWELLS_QUESTIONS),
    }
