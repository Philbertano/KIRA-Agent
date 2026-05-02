"""Pseudonymisierungs-Pipeline mit strukturierten Platzhaltern.

Schützt Mandantendaten *vor* dem LLM-Call. Defense-in-Depth zusätzlich zum
EU-Hosting (Bedrock Frankfurt), nicht statt dessen.
"""

from kira.pseudonymizer.pipeline import Pseudonymizer, Party, Role, Gender, EntityKind
from kira.pseudonymizer.leakage_check import LeakageError, check_for_leaks
from kira.pseudonymizer.mapping_store import MappingStore

__all__ = [
    "Pseudonymizer",
    "Party",
    "Role",
    "Gender",
    "EntityKind",
    "LeakageError",
    "check_for_leaks",
    "MappingStore",
]
