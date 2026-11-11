"""Règle EF-LEV-04 — sorties anormalement longues au regard de la tâche (Annexe A).

Indices de l'Annexe A : « Sorties proches de la limite, bien au-delà de la
médiane de la tâche ». Action proposée : « Limite de jetons, consigne de
concision ». Preuve exigée : « Rejeu avec la nouvelle limite ».
"""

from __future__ import annotations

from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    formater_nombre,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import estimer_sorties_trop_longues, tarif_pour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier

ACTION_PROPOSEE = "Limite de jetons, consigne de concision"
PREUVE_EXIGEE = "Rejeu avec la nouvelle limite"


class RegleSortiesTropLongues:
    """Détecte des sorties collées à la limite et loin de la médiane de la tâche."""

    type_levier = TypeLevier.SORTIES_TROP_LONGUES

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si les sorties sont proches de la limite et aberrantes."""
        if not self._sorties_proches_de_la_limite(contexte):
            return None
        if not self._sorties_au_dessus_de_la_mediane(contexte):
            return None
        if contexte.profil.part_requetes_sortie_longue < (
            contexte.parametres.seuil_part_sortie_longue
        ):
            return None
        economie = estimer_sorties_trop_longues(contexte)
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

    def _sorties_proches_de_la_limite(self, contexte: ContexteLevier) -> bool:
        """La sortie p95 frôle la limite de jetons du modèle."""
        profil = contexte.profil
        if profil.limite_sortie_modele <= 0:
            return False
        seuil = contexte.parametres.ratio_limite_sortie * profil.limite_sortie_modele
        return profil.longueur_sortie_p95 >= seuil

    def _sorties_au_dessus_de_la_mediane(self, contexte: ContexteLevier) -> bool:
        """La sortie p95 est bien au-delà de la médiane de la tâche."""
        profil = contexte.profil
        mediane = max(1, profil.longueur_sortie_mediane)
        return profil.longueur_sortie_p95 >= contexte.parametres.ratio_sortie_mediane * mediane

    def _indices(self, contexte: ContexteLevier) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
        excedent = profil.longueur_sortie_p95 - profil.longueur_sortie_mediane
        prix = f"{tarif.prix_sortie_1k} $/1k" if tarif else "tarif inconnu"
        return [
            f"Sortie p95 à {formater_nombre(profil.longueur_sortie_p95)} jetons pour une limite "
            f"de {formater_nombre(profil.limite_sortie_modele)} jetons "
            f"(ratio {contexte.parametres.ratio_limite_sortie:.2f})",
            f"Médiane de la tâche : {formater_nombre(profil.longueur_sortie_mediane)} jetons, "
            f"soit {formater_nombre(excedent)} jetons excédentaires au p95",
            f"Requêtes à sortie longue : {pourcent(profil.part_requetes_sortie_longue)}",
            f"Hypothèse : {formater_nombre(volume_mensuel(contexte))} requêtes/mois, "
            f"prix de sortie {prix}, gain sur les seuls jetons excédentaires",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part des requêtes à sortie longue."""
        return min(0.95, 0.6 + 0.25 * contexte.profil.part_requetes_sortie_longue)
