from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatInput(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    course_id: str | None = None
    web_enabled: bool = False
    skill: Literal["explain", "review"] = "explain"


class SearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=600)
    course_id: str | None = None


class SourceInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=120)


class WebInput(StrictModel):
    query: str = Field(min_length=1, max_length=160)


class Route(StrictModel):
    intent: Literal["explain", "compare", "example", "practice", "review", "current", "clarify"]
    query: str = Field(max_length=600)
    clarification: str = Field(default="", max_length=500)


class Question(StrictModel):
    id: str
    topic: str = Field(min_length=1, max_length=120)
    kind: Literal["short_answer", "scenario"]
    prompt: str = Field(min_length=8, max_length=1200)
    reference_answer: str = Field(min_length=8, max_length=2000)
    rubric: list[str] = Field(min_length=3, max_length=3)
    source_ids: list[str] = Field(min_length=1, max_length=6)


class QuestionSet(StrictModel):
    questions: list[Question] = Field(min_length=3, max_length=3)


class AuditResult(StrictModel):
    approved: bool
    issues: list[str] = Field(max_length=10)


class Grade(StrictModel):
    question_id: str
    correctness: int = Field(ge=0, le=2)
    completeness: int = Field(ge=0, le=2)
    reasoning: int = Field(ge=0, le=2)
    understood: str = Field(max_length=1500)
    missing: str = Field(max_length=1500)
    misconception: str = Field(max_length=1500)
    suggestion: str = Field(max_length=1500)
    source_ids: list[str] = Field(min_length=1, max_length=6)


class GradeSet(StrictModel):
    grades: list[Grade] = Field(min_length=3, max_length=3)


class PracticeInput(StrictModel):
    course_id: str | None = None
    topic: str = Field(default="", max_length=300)
    review_id: str | None = None


class Submission(StrictModel):
    answers: dict[str, str]


class Preference(StrictModel):
    key: Literal["background", "goal", "explanation_style"]
    value: str = Field(min_length=1, max_length=400)
