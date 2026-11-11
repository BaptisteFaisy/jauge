"""Règle EF-LEV-05 (S) — erreurs suivies de relances (Annexe A).

Indices de l'Annexe A : « Erreurs suivies d'appels identiques ». Action
proposée : « Corriger la cause ». Preuve exigée : « Comptage ».

Le signal est un **compte** d'erreurs et de relances : la détection ne lit que
des nombres agrégés.
"""

from __future__ import annotations

from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    cout_par_requete,
    formater_nombre,
    jours_observes,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import estimer_erreurs_et_relances
from jauge.models.lever import Levier, TypeLevier

ACTION_PROPOSEE = "Corriger la cause"
PREUVE_EXIGEE = "Comptage"


class RegleErreursEtRelances:
    """Détecte des erreurs qui provoquent des relances d'appels identiques."""

    type_levier = TypeLevier.ERREURS_ET_RELANCES

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si les relances suivent réellement les erreurs."""
        ratio = self._ratio_relance(contexte)
        if ratio < contexte.parametres.seuil_ratio_relance:
            return None
        economie = estimer_erreurs_et_relances(contexte)
        if economie <= 0:
            return None
        return construire_levier(
            contexte,
            self.type_levier,
            self._indices(contexte, ratio),
            ACTION_PROPOSEE,
            PREUVE_EXIGEE,
            economie,
            self._confiance(contexte, ratio),
        )

    def _ratio_relance(self, contexte: ContexteLevier) -> float:
        """Part des erreurs qui ont déclenché une relance."""
        erreurs = contexte.profil.nombre_erreurs
        if erreurs <= 0:
            return 0.0
        return contexte.profil.nombre_relances_apres_erreur / erreurs

    def _indices(self, contexte: ContexteLevier, ratio: float) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        jours = jours_observes(contexte)
        relances_mensuelles = (
            profil.nombre_relances_apres_erreur / jours * contexte.parametres.jours_par_mois
            if jours
            else 0.0
        )
        return [
            f"{formater_nombre(profil.nombre_erreurs)} erreurs observées sur "
            f"{formater_nombre(jours)} jours, dont "
            f"{formater_nombre(profil.nombre_relances_apres_erreur)} relances "
            f"({pourcent(ratio)} des erreurs)",
            f"Hypothèse : {formater_nombre(relances_mensuelles, 1)} relances/mois à "
            f"{cout_par_requete(contexte):.6f} $/requête, "
            f"{formater_nombre(volume_mensuel(contexte))} requêtes/mois",
        ]

    def _confiance(self, contexte: ContexteLevier, ratio: float) -> float:
        """Confiance d'après la part des erreurs qui ont été relancées."""
        return min(0.9, 0.5 + 0.3 * ratio)
