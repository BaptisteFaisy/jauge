"""Règle EF-LEV-05 (S) — niveau Priority sans besoin (Annexe A).

Indices de l'Annexe A : « Niveau Priority sur des appels non interactifs ».
Action proposée : « Niveau Standard ». Preuve exigée : « Accord du
responsable ».
"""

from __future__ import annotations

from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    formater_nombre,
    part_niveau,
    part_non_interactive_niveau,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import estimer_priority_sans_besoin
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier

ACTION_PROPOSEE = "Niveau Standard"
PREUVE_EXIGEE = "Accord du responsable"


class ReglePrioritySansBesoin:
    """Détecte un niveau Priority payé pour des appels qui n'attendent personne."""

    type_levier = TypeLevier.PRIORITY_SANS_BESOIN

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si le trafic Priority est non interactif et payable."""
        if part_niveau(contexte.profil, ServiceTier.PRIORITY) < (
            contexte.parametres.seuil_part_priority
        ):
            return None
        if part_non_interactive_niveau(contexte.profil, ServiceTier.PRIORITY) < (
            contexte.parametres.seuil_part_non_interactive
        ):
            return None
        economie = estimer_priority_sans_besoin(contexte)
        if economie <= 0:
            return None
        return construire_levier(
            contexte,
            self.type_levier,
            self._indices(contexte),
            ACTION_PROPOSEE,
            PREUVE_EXIGEE,
            economie,
            self._confiance(contexte),
        )

    def _indices(self, contexte: ContexteLevier) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        part_priority = part_niveau(profil, ServiceTier.PRIORITY)
        part_non_interactive = part_non_interactive_niveau(profil, ServiceTier.PRIORITY)
        return [
            f"Trafic au niveau Priority : {pourcent(part_priority)} du volume "
            f"(seuil {pourcent(contexte.parametres.seuil_part_priority)})",
            f"Appels Priority non interactifs : {pourcent(part_non_interactive)} "
            f"(seuil {pourcent(contexte.parametres.seuil_part_non_interactive)})",
            f"Hypothèse : retour au niveau Standard pour "
            f"{pourcent(part_priority * part_non_interactive)} du trafic, "
            f"{formater_nombre(volume_mensuel(contexte))} requêtes/mois",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part non interactive du trafic Priority."""
        profil = contexte.profil
        return min(
            0.9,
            0.55
            + 0.2 * part_niveau(profil, ServiceTier.PRIORITY)
            + 0.15 * part_non_interactive_niveau(profil, ServiceTier.PRIORITY),
        )
