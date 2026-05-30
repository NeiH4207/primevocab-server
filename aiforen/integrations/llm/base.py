"""LLM provider protocol + shared event shape.

Workers consume providers as an async iterator of `EvaluationStreamEvent`.
The wire shape sent to the FE is a JSON line per event, mirroring the
existing streaming contract that `Writing.tsx` already parses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol


@dataclass
class EvaluationStreamEvent:
    status: str  # 'processing' | 'completed' | 'error'
    step: Optional[str] = None  # 'task_achievement', 'final', ...
    message: Optional[str] = None
    content: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"status": self.status}
        if self.step is not None:
            body["step"] = self.step
        if self.message is not None:
            body["message"] = self.message
        if self.content is not None:
            body["content"] = self.content
        if self.data is not None:
            body["data"] = self.data
        body.update(self.extra)
        return body


class LLMProvider(Protocol):
    async def evaluate_writing(
        self,
        *,
        task: Dict[str, Any],
        answer: str,
    ) -> AsyncIterator[EvaluationStreamEvent]: ...

    async def evaluate_vocab_sentence(
        self,
        *,
        word: str,
        translate_prompt: str,
        topic_prompt: str,
        translate_sentence: str,
        topic_sentence: str,
        current_band: float,
        target_band: float,
        weakness_context: Optional[List[str]] = None,
    ) -> Dict[str, Any]: ...

    async def generate_vocab_daily_mission(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    async def generate_vocab_calibration_review(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]: ...
