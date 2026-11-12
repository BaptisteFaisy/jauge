"""Socle des vérifications d'un dossier de preuve : verdicts et comparaisons.

Ce module porte les objets partagés par les deux vérificateurs —
`proof_pack_verification` (recalculs depuis le pack) et `proof_pack_coherence`
(cohérence croisée entre sections) — pour qu'aucun des deux n'importe l'autre.

Il ne calcule rien : il décrit **comment** on compare et **comment** on rend le
verdict. Les tolérances sont celles de l'arrondi du socle : les taux sont publiés
arrondis à 6 décimales (`intervalle_wilson`), les coûts à 8
(`calculer_cout_requete`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jauge.common.errors import ValidationError

#: Tolérance sur les taux (arrondi du socle : 6 décimales).
TOLERANCE_TAUX = 1e-6
#: Tolérance sur les montants (arrondi du socle : 8 décimales).
TOLERANCE_COUT = 1e-8
#: Tolérance sur les montants mensuels (arrondi du socle : 2 décimales).
TOLERANCE_MENSUELLE = 0.01


@dataclass(frozen=True)
class Verification:
    """Une vérification recalculée : ce qui est comparé, et le verdict.

    `attendu` est la valeur **recalculée** par le vérificateur, `declare` la
    valeur **déclarée** dans le pack : un écart entre les deux est une falsification.
    """

    nom: str
    section: str
    conforme: bool
    attendu: Any = None
    declare: Any = None
    detail: str = ""

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable de la vérification."""
        return {
            "nom": self.nom,
            "section": self.section,
            "conforme": self.conforme,
            "attendu": self.attendu,
            "declare": self.declare,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ResultatRejeu:
    """Ensemble des vérifications rejouées sur un pack."""

    verifications: tuple[Verification, ...] = ()

    @property
    def conforme(self) -> bool:
        """Vrai si toutes les vérifications passent."""
        return all(verification.conforme for verification in self.verifications)

    @property
    def echecs(self) -> tuple[Verification, ...]:
        """Vérifications en échec : ce que le tiers a refusé de valider."""
        return tuple(
            verification for verification in self.verifications if not verification.conforme
        )

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable du résultat (JSON et Markdown la consomment)."""
        return {
            "conforme": self.conforme,
            "nombre_verifications": len(self.verifications),
            "nombre_echecs": len(self.echecs),
            "verifications": [verification.vers_dict() for verification in self.verifications],
        }


def egal(attendu: Any, declare: Any, tolerance: float = 0.0) -> bool:
    """Égalité avec tolérance : les valeurs publiées sont arrondies."""
    if isinstance(attendu, int | float) and isinstance(declare, int | float):
        return abs(float(attendu) - float(declare)) <= tolerance
    return attendu == declare


def candidats(sections: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Candidats publiés dans la section `resultats`."""
    return list(sections.get("resultats", {}).get("candidats", []))


def candidat_par_modele(sections: Mapping[str, Any], modele: Any) -> Mapping[str, Any]:
    """Candidat d'un modèle donné (comparaison par identifiant)."""
    for candidat in candidats(sections):
        if candidat.get("modele_id") == modele:
            return candidat
    return {}


def sections_de(pack: Any) -> dict[str, Any]:
    """Sections du pack, qu'il soit un objet ou son document JSON."""
    if hasattr(pack, "sections") and isinstance(pack.sections, Mapping):
        return dict(pack.sections)
    if isinstance(pack, Mapping):
        sections = pack.get("sections")
        if isinstance(sections, Mapping):
            return dict(sections)
    raise ValidationError("dossier de preuve illisible : sections absentes")
