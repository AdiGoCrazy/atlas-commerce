"""Deterministic recommendation scoring for Atlas Commerce."""

from .ranking import is_eligible, rank_products

__all__ = ["is_eligible", "rank_products"]
