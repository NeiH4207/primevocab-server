"""Plain-Python enums shared by API + workers + repositories."""

from __future__ import annotations

from enum import Enum


class PlanCode(str, Enum):
    free = "free"
    standard = "standard"
    premium = "premium"
    vip = "vip"


class SubscriptionStatus(str, Enum):
    active = "active"
    past_due = "past_due"
    cancelled = "cancelled"
    expired = "expired"


class PaymentStatus(str, Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"


class TaskType(str, Enum):
    task_1 = "task_1"
    task_2 = "task_2"


class AssessmentStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ContentType(str, Enum):
    grammar = "grammar"
    vocabulary = "vocabulary"


class MasteryLevel(str, Enum):
    new = "new"
    learning = "learning"
    reviewing = "reviewing"
    mastered = "mastered"


class QuotaKind(str, Enum):
    assessment = "assessment"
    ai_feedback = "ai_feedback"
    vocab_ai_eval = "vocab_ai_eval"
    vocab_word = "vocab_word"
    personal_task = "personal_task"
