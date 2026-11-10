"""Utilitaires communs : erreurs métier, identifiants."""

from __future__ import annotations

from jauge.common.errors import (
    ApprobationRequiseError,
    CloisonnementError,
    EtatIncompatibleError,
    IntrouvableError,
    JaugeError,
    PlafondDepasseError,
    ServiceIndisponibleError,
    TarifInconnuError,
    ValidationError,
)
from jauge.common.ids import aleatoire, deterministic, graine_de, horodatage_utc, jour_compact

__all__ = [
    "ApprobationRequiseError",
    "CloisonnementError",
    "EtatIncompatibleError",
    "IntrouvableError",
    "JaugeError",
    "PlafondDepasseError",
    "ServiceIndisponibleError",
    "TarifInconnuError",
    "ValidationError",
    "aleatoire",
    "deterministic",
    "graine_de",
    "horodatage_utc",
    "jour_compact",
]
