"""Pydantic DTOs for writing, grammar, and learner content wire shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import AssessmentStatus, ContentType, MasteryLevel, TaskType

# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class WritingGroup(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="Stable integer id used by the FE")
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    sort_order: int = 0
    total_tasks: int = 0
    is_active: bool = True


class WritingTaskAccess(BaseModel):
    free_access: bool = True
    required_plan: Optional[str] = None
    daily_limit: Optional[int] = None


class WritingTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    group_id: int
    group_name: str
    task_type: TaskType
    title: str
    description: str
    image_url: Optional[str] = None
    data_description: Optional[str] = None
    time_limit: int = 1200
    difficulty: str = "intermediate"
    tags: List[str] = Field(default_factory=list)
    access: WritingTaskAccess = Field(default_factory=WritingTaskAccess)
    tests_taken: int = 0
    average_score: float = 0.0
    created_by: Optional[str] = None
    is_personal: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class IELTSScores(BaseModel):
    task_achievement: float = 0
    coherence_cohesion: float = 0
    lexical_resource: float = 0
    grammar_accuracy: float = 0
    overall_score: float = 0


class CriterionFeedback(BaseModel):
    score: float = 0
    feedback: str = ""


class WritingAssessmentDoc(BaseModel):
    """The persisted form of an assessment.  The wire format the FE
    expects is mostly flat (see EvaluationView.tsx)."""

    task_achievement: CriterionFeedback = Field(default_factory=CriterionFeedback)
    coherence_cohesion: CriterionFeedback = Field(default_factory=CriterionFeedback)
    lexical_resource: CriterionFeedback = Field(default_factory=CriterionFeedback)
    grammar_accuracy: CriterionFeedback = Field(default_factory=CriterionFeedback)
    scores: IELTSScores = Field(default_factory=IELTSScores)
    general_comments: str = ""
    improvement_suggestions: str = ""
    improvement_explanation: str = ""
    next_level_sample: str = ""


class WritingSubmission(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    submission_id: str
    user_id: str
    task_id: int
    answer: str
    word_count: int
    status: AssessmentStatus = AssessmentStatus.queued
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    assessment: Optional[Dict[str, Any]] = None  # WritingAssessmentDoc.dict()
    error_message: Optional[str] = None
    prompt_version: Optional[str] = None


# ---------------------------------------------------------------------------
# Learning content
# ---------------------------------------------------------------------------


class ContentExample(BaseModel):
    correct: str
    context: Optional[str] = ""
    explanation: Optional[str] = ""
    highlight: Optional[str] = None


class ContentError(BaseModel):
    incorrect: str
    correct: str
    reason: str


class GrammarStructureDoc(BaseModel):
    structure_id: str
    name: str
    structure_pattern: str
    description: str
    category: str
    task_type: str = "Both"
    band_score: float = 6.0
    difficulty_level: str = "intermediate"
    examples: List[ContentExample] = Field(default_factory=list)
    common_errors: List[ContentError] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    total_attempts: int = 0
    success_rate: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class VocabularyMcqOption(BaseModel):
    id: str
    text: str


class VocabularyMcq(BaseModel):
    question: str
    options: List[VocabularyMcqOption] = Field(default_factory=list)
    correct_option_id: str
    explanation: Optional[str] = None


class VocabularyPackDoc(BaseModel):
    pack_id: str
    title: str
    description: str = ""
    source_band_min: float = 5.0
    source_band_max: float = 9.0
    target_band_min: float = 6.0
    target_band_max: float = 9.0
    category: str = "General"
    task_type: str = "Both"
    sort_order: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class VocabularyWordDoc(BaseModel):
    word_id: str
    pack_id: Optional[str] = None
    word: str
    definition: str
    pronunciation: Optional[str] = None
    part_of_speech: str
    category: str
    task_type: str = "Both"
    band_score: float = 6.0
    difficulty_level: str = "intermediate"
    examples: List[ContentExample] = Field(default_factory=list)
    synonyms: List[str] = Field(default_factory=list)
    collocations: List[str] = Field(default_factory=list)
    usage: Optional[str] = None
    tips: List[str] = Field(default_factory=list)
    mcq: Optional[VocabularyMcq] = None
    vi_prompt: Optional[str] = None
    vi_translate_prompt: Optional[str] = None
    topic_prompt: Optional[str] = None
    example_good_sentence: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    total_attempts: int = 0
    success_rate: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class SpacedRepetition(BaseModel):
    ease_factor: float = 2.5
    interval: int = 0  # days
    repetitions: int = 0
    last_reviewed: Optional[datetime] = None
    next_review: Optional[datetime] = None


class LearningProgressDoc(BaseModel):
    user_id: str
    content_id: str
    content_type: ContentType
    mastery_level: MasteryLevel = MasteryLevel.new
    correct_answers: int = 0
    total_attempts: int = 0
    current_streak: int = 0
    best_streak: int = 0
    mastery_step: int = 0
    last_seen_date: Optional[str] = None
    failed_locked_until: Optional[datetime] = None
    marked_known: bool = False
    last_mcq_result: Optional[Dict[str, Any]] = None
    last_sentence_id: Optional[str] = None
    spaced_repetition: SpacedRepetition = Field(default_factory=SpacedRepetition)
    exercise_performance: Dict[str, Any] = Field(default_factory=dict)
    first_studied: datetime = Field(default_factory=datetime.utcnow)
    last_studied: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class VocabAttemptDoc(BaseModel):
    attempt_id: str
    user_id: str
    word_id: str
    pack_id: Optional[str] = None
    attempt_type: str
    is_correct: Optional[bool] = None
    answer: Any = None
    ai_feedback: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserStatsDoc(BaseModel):
    user_id: str
    grammar_total_learned: int = 0
    grammar_mastered: int = 0
    grammar_accuracy: float = 0.0
    grammar_current_streak: int = 0
    grammar_best_streak: int = 0
    vocab_total_learned: int = 0
    vocab_mastered: int = 0
    vocab_accuracy: float = 0.0
    vocab_current_streak: int = 0
    vocab_best_streak: int = 0
    total_study_time: int = 0
    today_study_time: int = 0
    estimated_grammar_band: float = 5.0
    estimated_vocab_band: float = 5.0
    daily_activity: Dict[str, Any] = Field(default_factory=dict)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
