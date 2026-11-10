"""Persistence : contrat `Store` et implémentations (mémoire, DynamoDB)."""

from __future__ import annotations

from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store

__all__ = ["MemoryStore", "Store"]
