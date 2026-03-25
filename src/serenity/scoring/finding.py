"""Finding data model — the atomic unit of analysis results."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from serenity.constants import Severity


class Finding(BaseModel):
    """A single issue discovered during analysis."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    domain: str
    severity: Severity
    title: str
    description: str
    url: str | None = None
    element_selector: str | None = None
    screenshot_path: str | None = None
    fix_snippet: str | None = None
    estimated_fix_minutes: int = 5
    deduction_points: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:
        """Auto-calculate deduction points from severity if not set."""
        if self.deduction_points == 0.0:
            from serenity.constants import SEVERITY_DEDUCTIONS
            self.deduction_points = SEVERITY_DEDUCTIONS.get(self.severity, 5.0)
