"""Learner rhythm classification and coach overview copy for vocab dashboard."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal, Mapping, Optional

from aiforen.domain.vocab_daily_streak import compute_daily_streak, parse_day_key

LearnerRhythm = Literal["new", "early", "intermittent", "consistent"]
LearnerStage = Literal[
    "new_user", "activation_under_3_days", "growth_3_to_30_days", "returning_user"
]


def _active_days_in_window(
    daily_counts: Mapping[str, int],
    *,
    today: date,
    window_days: int,
) -> int:
    start = today - timedelta(days=window_days - 1)
    count = 0
    for key, value in daily_counts.items():
        if int(value or 0) <= 0:
            continue
        day = parse_day_key(str(key))
        if day and start <= day <= today:
            count += 1
    return count


def _days_since_last_active(
    daily_counts: Mapping[str, int],
    *,
    today: date,
) -> Optional[int]:
    latest: Optional[date] = None
    for key, value in daily_counts.items():
        if int(value or 0) <= 0:
            continue
        day = parse_day_key(str(key))
        if day and day <= today and (latest is None or day > latest):
            latest = day
    if latest is None:
        return None
    return (today - latest).days


def classify_learner_rhythm(
    *,
    daily_counts: Mapping[str, int],
    today: date,
    total_progress_words: int = 0,
    learned_today: int = 0,
) -> LearnerRhythm:
    active_7 = _active_days_in_window(daily_counts, today=today, window_days=7)
    active_14 = _active_days_in_window(daily_counts, today=today, window_days=14)
    active_30 = _active_days_in_window(daily_counts, today=today, window_days=30)
    streak = compute_daily_streak(daily_counts, today=today)
    gap = _days_since_last_active(daily_counts, today=today)

    if total_progress_words < 5 and active_30 <= 1 and learned_today == 0:
        return "new"

    if streak >= 5 or active_14 >= 8 or active_7 >= 4:
        return "consistent"

    if active_30 >= 3 and gap is not None and gap >= 3 and active_7 <= 1:
        return "intermittent"

    if active_30 >= 2 or total_progress_words >= 5 or learned_today > 0:
        return "early"

    return "new"


def classify_learner_stage(
    *,
    daily_counts: Mapping[str, int],
    today: date,
    total_progress_words: int = 0,
    learned_today: int = 0,
) -> LearnerStage:
    """Business-facing stage for UX personalization.

    This intentionally differs from rhythm: rhythm is about study consistency,
    while stage is about which experience strategy should be shown.
    """

    active_30 = _active_days_in_window(daily_counts, today=today, window_days=30)
    gap = _days_since_last_active(daily_counts, today=today)

    if gap is not None and gap >= 7 and active_30 >= 1:
        return "returning_user"

    if total_progress_words < 5 and active_30 <= 1 and learned_today == 0:
        return "new_user"

    if active_30 < 3:
        return "activation_under_3_days"

    return "growth_3_to_30_days"


def build_coach_overview_lines(
    *,
    rhythm: LearnerRhythm,
    locale: str,
    streak: int = 0,
    active_days_14: int = 0,
    total_progress_words: int = 0,
    learned_today: int = 0,
    due_today: int = 0,
    primary_weakness_label: Optional[str] = None,
) -> list[str]:
    vi = str(locale).lower().startswith("vi")
    weak = (primary_weakness_label or "").strip() or (
        "weak spot" if not vi else "điểm yếu"
    )

    if rhythm == "new":
        if vi:
            return [
                "Bạn đang ở ngày đầu của hành trình từ vựng — đây là lúc dễ tạo thói quen nhất.",
                "Chỉ 5–10 phút mỗi ngày đã tách bạn khỏi kiểu học dồn cuối tuần rồi quên.",
                "Bắt đầu bằng vài từ hiểu thật sâu; momentum sẽ đến sau, không cần ép số lượng.",
            ]
        return [
            "You're at the start of your vocab journey — small habits stick best here.",
            "Even 5–10 focused minutes daily beats weekend cramming that fades fast.",
            "Begin with a few words you truly understand; momentum follows depth, not volume.",
        ]

    if rhythm == "early":
        days_note = (
            f"Bạn đã học {active_days_14} ngày trong 2 tuần qua"
            if vi and active_days_14 > 0
            else (
                f"You've studied on {active_days_14} days in the last two weeks"
                if active_days_14 > 0
                else (
                    f"Bạn đã chạm {total_progress_words} từ"
                    if vi
                    else f"You've engaged with {total_progress_words} words"
                )
            )
        )
        if vi:
            return [
                f"{days_note} — phần khó nhất (bắt đầu) bạn đã vượt qua.",
                "Não đang ghi nhận pattern lỗi; sửa sớm giúp tránh “đóng băng” sai lặp lại.",
                "Giữ nhịp ngắn hôm nay — consistency tuần này quan trọng hơn thêm 20 từ mới.",
            ]
        return [
            f"{days_note} — the hardest part (starting) is already behind you.",
            "Your brain is forming mistake patterns; fixing them early prevents repeat errors.",
            "Keep today's session short — consistency this week beats adding twenty new words.",
        ]

    if rhythm == "intermittent":
        if vi:
            return [
                "Bạn học theo nhịp gián đoạn — không tệ, nhưng recall dễ rò giữa các đợt.",
                (
                    f"Hôm nay ưu tiên {weak} và vài từ due"
                    if due_today > 0
                    else f"Hôm nay ưu tiên khóa lại {weak} thay vì mở thêm từ mới"
                ),
                "Một session 8–12 phút đúng trọng tâm sẽ ổn định band hơn học dài nhưng thưa.",
            ]
        return [
            "You study in bursts — that's fine, but recall leaks between gaps.",
            (
                f"Today, focus on {weak} and a few due words"
                if due_today > 0
                else f"Today, reinforce {weak} instead of opening many new words"
            ),
            "One focused 8–12 minute session stabilizes mastery more than rare long cramming.",
        ]

    # consistent
    streak_note = (
        f"{streak} ngày streak"
        if vi and streak > 0
        else f"{streak}-day streak" if streak > 0 else ""
    )
    if vi:
        line1 = (
            f"Nhịp học đang ổn ({streak_note}) — đây là lợi thế lớn với IELTS vocab."
            if streak_note
            else "Nhịp học đang ổn — bạn đang build recall bền, không chỉ nhớ tạm."
        )
        return [
            line1,
            (
                f"Ưu tiên sửa {weak} trước khi tăng batch — chất lượng đang quan trọng hơn số từ."
                if primary_weakness_label
                else "Ưu tiên chất lượng: review due và sửa lỗi trước khi học thêm từ mới."
            ),
            (
                f"{'Đã học ' + str(learned_today) + ' từ hôm nay — ' if learned_today else ''}"
                "Giữ session vừa đủ; bạn đang tích lũy long-term memory tốt."
            ),
        ]
    line1 = (
        f"Your rhythm is strong ({streak_note}) — a real edge for IELTS vocab."
        if streak_note
        else "Your study rhythm is solid — you're building durable recall, not cramming."
    )
    return [
        line1,
        (
            f"Fix {weak} before growing batch size — quality matters more than word count now."
            if primary_weakness_label
            else "Prioritize quality: clear due words and repair mistakes before adding volume."
        ),
        (
            f"{'Already ' + str(learned_today) + ' words today — ' if learned_today else ''}"
            "Keep sessions lean; you're compounding long-term memory well."
        ),
    ]


def normalize_coach_overview_lines(
    raw: Any,
    *,
    rhythm: LearnerRhythm,
    locale: str,
    fallback_kwargs: dict[str, Any],
) -> list[str]:
    if isinstance(raw, list):
        lines = [str(item).strip() for item in raw if str(item).strip()]
        if lines:
            return [line[:95] for line in lines[:3]]
    return [
        line[:95]
        for line in build_coach_overview_lines(
            rhythm=rhythm, locale=locale, **fallback_kwargs
        )[:3]
    ]
