"""Journal des étapes d'une bascule : qui, quand, quel seuil, quelle décision.

Chaque étape de la séquence (shadow → canary → paliers) laisse une trace
immuable et horodatée, exploitable par l'audit (EF-ADM-02) et par le proof
pack. Les entrées ne portent que des identifiants, des nombres et des motifs :
aucun texte de requête ni de réponse (ENF-CONF-01).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum

from jauge.common.ids import horodatage_utc


class StatutEtape(StrEnum):
    """État d'une étape de la séquence."""

    NON_LANCEE = "non_lancee"
    LANCEE = "lancee"
    CONCLUANTE = "concluante"
    NON_CONCLUANTE = "non_concluante"
    DIVERGENTE = "divergente"
    ANNULEE = "annulee"


@dataclass(frozen=True)
class JournalEtape:
    """Trace d'une étape : qui, quand, quel seuil, quelle mesure, quelle décision.

    `vers_dict()` ne contient que des identifiants, des nombres et des motifs —
    aucun texte de requête ni de réponse (ENF-CONF-01).
    """

    etape: str
    tenant_id: str
    application_id: str
    qui: str
    quand: str
    decision: str
    seuils: tuple[tuple[str, float], ...] = ()
    mesures: tuple[tuple[str, float], ...] = ()
    motif: str = ""

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable de l'entrée (audit, proof pack, portail)."""
        return asdict(self)


class JournalEtapes:
    """Journal append-only des étapes d'une bascule (EF-ADM-02)."""

    def __init__(self) -> None:
        self._entrees: list[JournalEtape] = []

    def consigner(self, entree: JournalEtape) -> JournalEtape:
        """Ajoute une entrée au journal et la rend."""
        self._entrees.append(entree)
        return entree

    def entrees(self, etape: str | None = None) -> list[JournalEtape]:
        """Entrées du journal, filtrées par étape si demandé."""
        if etape is None:
            return list(self._entrees)
        return [entree for entree in self._entrees if entree.etape == etape]

    def derniere(self, etape: str) -> JournalEtape | None:
        """Dernière entrée d'une étape (l'audit veut l'état courant)."""
        entrees = self.entrees(etape)
        return entrees[-1] if entrees else None


def maintenant() -> str:
    """Horodatage UTC de l'instant présent, à la seconde (lisible en audit)."""
    return horodatage_utc(datetime.now(UTC))
