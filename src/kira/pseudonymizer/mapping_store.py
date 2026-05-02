"""Verschlüsselte Persistenz der Mandats-Mappings.

Mappings (Platzhalter ↔ Klarname) werden pro Mandat verschlüsselt abgelegt.
Schlüssel liegt lokal beim Anwalt. Niemals in die Cloud.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet


@dataclass
class MappingStore:
    """Liest/schreibt verschlüsselte Mandats-Mappings auf der Platte."""

    base_dir: Path
    key: bytes

    @classmethod
    def open(cls, base_dir: str | Path, key_path: str | Path) -> "MappingStore":
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        key_file = Path(key_path)
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(Fernet.generate_key())
            key_file.chmod(0o600)
        return cls(base_dir=base, key=key_file.read_bytes())

    def save(self, mandat_id: str, mapping: dict[str, str]) -> None:
        cipher = Fernet(self.key)
        encrypted = cipher.encrypt(json.dumps(mapping, ensure_ascii=False).encode("utf-8"))
        path = self.base_dir / f"{mandat_id}.pseudo-mapping"
        path.write_bytes(encrypted)
        path.chmod(0o600)

    def load(self, mandat_id: str) -> dict[str, str]:
        path = self.base_dir / f"{mandat_id}.pseudo-mapping"
        if not path.exists():
            return {}
        cipher = Fernet(self.key)
        decrypted = cipher.decrypt(path.read_bytes())
        return json.loads(decrypted.decode("utf-8"))
