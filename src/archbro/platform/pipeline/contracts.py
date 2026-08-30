from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from archbro.backend.core.contracts import (
    AgentRunResult,
    ProjectEventSource,
    ProjectEventType,
)


@dataclass(frozen=True, slots=True)
class NormalizedSignal:
    """One observation extracted from a raw external result.

    ``source_event_id`` is the pipeline's replay-protection key. A pull-based
    source carries no delivery header, so the adapter derives it, under two
    requirements:

    1. **Deterministic.** The same raw result must always produce the same
       identity *and* the same payload. The backend refuses a redelivery whose
       identity matches but whose data differs, rather than overwriting evidence.
    2. **Provider-scoped.** The backend replay key is
       ``project_id | source | source_event_id`` and does not include repository
       or connector. A bare number that is only unique inside one repository —
       a pull-request number, an issue number — collides as soon as one project
       observes two repositories. Qualify it:
       ``github:{repository}:pr:{number}``, ``github:{repository}:commit:{sha}``.

    A content hash such as a commit sha is already globally unique, but keeping
    the qualified form everywhere makes the identity self-describing.
    """

    source_event_id: str
    source: ProjectEventSource
    event_type: ProjectEventType
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized = self.source_event_id.strip()
        if not normalized:
            raise ValueError("source_event_id must not be empty")
        object.__setattr__(self, "source_event_id", normalized)
        # The identity is only stable if the data behind it is too. Copy so a
        # caller mutating the original dict cannot turn a later replay into a
        # rejection under the same source_event_id.
        object.__setattr__(self, "payload", deepcopy(self.payload))


@dataclass(frozen=True, slots=True)
class AdapterResult:
    signals: list[NormalizedSignal] = field(default_factory=list)
    #: Opaque resume point for the next sync, or ``None`` to keep the current one.
    next_position: str | None = None


class SignalAdapter(Protocol):
    """Provider-specific normalization, owned by ``integrations/``.

    Every method here needs provider knowledge, which is exactly why none of it
    belongs in the pipeline: which tool to call, how a stored position turns into
    tool arguments, and how a raw result becomes normalized signals.
    """

    #: MCP tool this adapter reads from, e.g. ``list_commits``.
    tool_name: str

    def build_arguments(self, position: str | None) -> dict[str, Any]: ...

    def normalize(self, raw: dict[str, Any], position: str | None) -> AdapterResult: ...


class DeliveryOutcome(StrEnum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"
    #: Another worker holds this observation; the same signal can be retried later.
    CONFLICT = "CONFLICT"
    #: The Agent recorded a durable failed run; project state was not changed.
    FAILED = "FAILED"
    #: The observation contract was violated (colliding identity, invalid payload).
    #: Permanent: redelivering the same signal cannot succeed, so it does not hold
    #: the connector back. Recovery means fixing the adapter and rewinding the cursor.
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    signal: NormalizedSignal
    run: AgentRunResult | None = None


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """Where a pull-based connector stopped reading.

    ``position`` is opaque to the pipeline. GitHub uses an ISO timestamp, Slack a
    message ts, Drive a change token; only the provider adapter interprets it.

    ``owner_user_id`` is **reserved metadata**. It records which trusted principal
    registered the connector, but nothing resolves credentials from it yet: the
    MCP caller still selects a server-side configured token. It becomes the
    credential-selection boundary only once per-user OAuth lands, which Slack and
    Drive will force since neither issues service-account credentials.
    """

    project_id: str
    connector_id: str
    position: str | None = None
    owner_user_id: str | None = None
    #: Consecutive syncs that ended with work still needing redelivery.
    stalled_attempts: int = 0
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "connector_id"):
            value = getattr(self, name).strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)


class SyncCursorStore(Protocol):
    def load(self, project_id: str, connector_id: str) -> SyncCursor | None: ...
    def save(self, cursor: SyncCursor) -> None: ...
    def list_cursors(self, project_id: str) -> list[SyncCursor]: ...

    def advance(
        self,
        project_id: str,
        connector_id: str,
        *,
        expected_position: str | None,
        position: str,
        owner_user_id: str | None = None,
    ) -> bool:
        """Move the position only if it still matches ``expected_position``.

        Two workers can read the same starting position concurrently. Positions
        are opaque, so an unconditional write cannot tell that it just replaced a
        newer position with an older one. Returns whether this writer won.
        """
        ...

    def record_stall(self, project_id: str, connector_id: str, attempts: int) -> None: ...
