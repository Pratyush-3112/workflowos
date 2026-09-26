"""Workflow discovery and repetition detection package."""

from backend.discovery.detector import RepetitionDetector
from backend.discovery.schema import WorkflowCandidate

__all__ = [
    "WorkflowCandidate",
    "RepetitionDetector",
]
