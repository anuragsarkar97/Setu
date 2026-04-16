from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class MessageRole(str, Enum):
    USER   = "user"
    SYSTEM = "system"


class MessageType(str, Enum):
    INTENT   = "intent"
    ANSWER   = "answer"
    QUESTION = "question"
    RESULT   = "result"
    RESPONSE = "response"


class Action(str, Enum):
    ASK     = "ask"
    SEARCH  = "search"
    SERVE   = "serve"
    RESPOND = "respond"


@dataclass
class Message:
    role:    MessageRole
    type:    MessageType
    content: str


@dataclass
class IntentThread:
    thread_id: str
    user_id:   str
    messages:  list[Message] = field(default_factory=list)
    status:    str = "open"

    def append(self, msg: Message):
        self.messages.append(msg)

    def user_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == MessageRole.USER]

    def questions_asked(self) -> list[str]:
        return [m.content for m in self.messages if m.type == MessageType.QUESTION]

    def full_text(self) -> str:
        return " ".join(m.content for m in self.messages if m.role == MessageRole.USER)


@dataclass
class HardFilter:
    location:   Optional[str]   = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    timeline:   Optional[str]   = None
    dietary:    Optional[str]   = None
    smoking:    Optional[str]   = None
    urgency:    bool             = False


@dataclass
class EnrichedIntent:
    intent_type:     str
    summary:         str
    hard_filters:    HardFilter
    soft_signals:    list[str]
    embedding_query: str
    missing_signals: list[str]
    completeness:    float


@dataclass
class Candidate:
    candidate_id: str
    name:         str
    description:  str
    score:        float
    tags:         list[str]


@dataclass
class SearchResult:
    candidates:         list[Candidate]
    top_score:          float
    score_distribution: str   # "clustered" | "spread"


@dataclass
class OrchestratorOutput:
    reasoning:       str
    action:          Action
    question:        Optional[str]         = None
    enriched_intent: Optional[EnrichedIntent] = None


@dataclass
class Persona:
    user_id:  str
    location: Optional[str] = None
    dietary:  Optional[str] = None
    smoking:  Optional[str] = None
