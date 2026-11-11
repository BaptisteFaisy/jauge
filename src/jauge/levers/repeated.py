"""Règle EF-LEV-05 (S) — requêtes identiques répétées (Annexe A).

Indices de l'Annexe A : « Mêmes requêtes à l'identique ». Action proposée :
« Cache applicatif ». Preuve exigée : « Comptage ».

La détection repose sur un **compte** : la part de requêtes strictement
identiques à une requête antérieure. Aucun texte de requête n'est conservé.
"""

from __future__ import annotations

from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    cout_par_requete,
    formater_nombre,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import estimer_requetes_repetees
from jauge.models.lever import Levier, TypeLevier

ACTION_PROPOSEE = "Cache applicatif"
PREUVE_EXIGEE = "Comptage"


class RegleRequetesRepetees:
    """Détecte un volume de requêtes identiques qu'un cache applicatif absorbe."""

    type_levier = TypeLevier.REQUETES_REPETEES

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si les requêtes identiques sont une part notable."""
        part = contexte.profil.part_requetes_identiques
        if part < contexte.parametres.seuil_part_requetes_identiques:
            return None
        economie = estimer_requetes_repetees(contexte)
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
        return [
            f"Requêtes identiques répétées : {pourcent(profil.part_requetes_identiques)} "
            f"du volume (seuil {pourcent(contexte.parametres.seuil_part_requetes_identiques)})",
            f"Hypothèse : {formater_nombre(volume_mensuel(contexte))} requêtes/mois, "
            f"coût moyen {cout_par_requete(contexte):.6f} $/requête, "
            f"chaque doublon est servi sans appel au modèle",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part de requêtes identiques."""
        return min(0.95, 0.6 + 0.3 * contexte.profil.part_requetes_identiques)
