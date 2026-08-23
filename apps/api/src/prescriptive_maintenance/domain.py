"""Transport-independent domain vocabulary shared by internal modules."""

from enum import StrEnum


class AnalysisOutcome(StrEnum):
    """Closed analysis outcome vocabulary shared with API v1."""

    NORMAL = "normal"
    DOCUMENTED_FAULT = "documented_fault"
    UNDOCUMENTED_FAULT = "undocumented_fault"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    DEGRADED = "degraded"
