"""Read-only analysis and diagnostic utilities for trading results."""

from .behavior import analyze_behavior, summarize_behavior, summarize_offer_quality
from .coordination import analyze_coordination_exploitation

__all__ = [
    "analyze_behavior",
    "analyze_coordination_exploitation",
    "summarize_behavior",
    "summarize_offer_quality",
]
