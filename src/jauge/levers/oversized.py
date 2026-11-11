"""Règle EF-LEV-01 — modèle probablement surdimensionné (Annexe A).

Indices de l'Annexe A : « Requêtes courtes, sorties courtes ou structurées,
faible diversité des sorties ». Action proposée : « Rejeu sur des modèles plus
petits ». Preuve exigée : « Campagne de rejeu ».

La règle ne voit que le profil agrégé et les agrégats : jamais le texte des
requêtes.
"""

from __future__ import annotations

from jauge.engine.stats import baisse_pourcentage
from jauge.levers.base import (
    TACHES_SIMPLES,
    ContexteLevier,
    construire_levier,
    cout_par_requete,
    formater_nombre,
    niveau_dominant,
    part_type_tache,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import (
    candidats_autorises,
    candidats_cheapers,
    cout_requete_modele,
    estimer_modele_surdimensionne,
)
from jauge.models.lever import Levier, TypeLevier

ACTION_PROPOSEE = "Rejeu sur des modèles plus petits"
PREUVE_EXIGEE = "Campagne de rejeu"


class RegleModeleSurdimensionne:
    """Détecte une application servie par un modèle trop grand pour sa tâche."""

    type_levier = TypeLevier.MODELE_SURDIMENSIONNE

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si la tâche est simple et les volumes courts."""
        if not self._tache_simple(contexte):
            return None
        if not self._volumes_courts(contexte):
            return None
        candidats = candidats_cheapers(contexte)
        if not candidats:
            return None
        economie = estimer_modele_surdimensionne(contexte)
        if economie <= 0:
            return None
        return construire_levier(
            contexte,
            self.type_levier,
            self._indices(contexte, candidats),
            ACTION_PROPOSEE,
            PREUVE_EXIGEE,
            economie,
            self._confiance(contexte),
            candidats,
        )

    def _tache_simple(self, contexte: ContexteLevier) -> bool:
        """La majorité du trafic relève d'une classification ou d'une extraction."""
        part = part_type_tache(contexte.profil, TACHES_SIMPLES)
        return part >= contexte.parametres.seuil_part_tache_simple

    def _volumes_courts(self, contexte: ContexteLevier) -> bool:
        """Requêtes courtes, sorties courtes ou structurées."""
        profil = contexte.profil
        parametres = contexte.parametres
        entree_courte = profil.longueur_entree_moyenne <= parametres.seuil_entree_courte
        sortie_courte = profil.longueur_sortie_moyenne <= parametres.seuil_sortie_courte
        structuree = profil.part_sortie_structuree >= parametres.seuil_part_sortie_structuree
        return entree_courte or sortie_courte or structuree

    def _indices(self, contexte: ContexteLevier, candidats: list[str]) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        parametres = contexte.parametres
        part = part_type_tache(profil, TACHES_SIMPLES)
        cout_actuel = cout_par_requete(contexte)
        cout_cible = cout_requete_modele(contexte, candidats[0], niveau_dominant(profil))
        return [
            f"Tâche simple sur {pourcent(part)} du trafic (classification, extraction)",
            f"Entrée moyenne {formater_nombre(profil.longueur_entree_moyenne)} jetons "
            f"(seuil {parametres.seuil_entree_courte}), sortie moyenne "
            f"{formater_nombre(profil.longueur_sortie_moyenne)} jetons "
            f"(seuil {parametres.seuil_sortie_courte})",
            f"Sorties structurées sur {pourcent(profil.part_sortie_structuree)} des requêtes, "
            f"diversité des sorties {pourcent(profil.diversite_sorties)}",
            f"Candidat d'estimation : {candidats[0]} (modèle autorisé le moins cher ; "
            f"{len(candidats_autorises(contexte))} candidats autorisés retenus)",
            f"Hypothèse : coût moyen observé {cout_actuel:.6f} $/requête → "
            f"{cout_cible:.6f} $/requête, volume {formater_nombre(volume_mensuel(contexte))} "
            f"requêtes/mois × part éligible {pourcent(part)}",
            f"Baisse potentielle du coût par requête : "
            f"{baisse_pourcentage(cout_actuel, cout_cible):.2f} %",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la force des signaux (tâche simple et structure)."""
        profil = contexte.profil
        part = part_type_tache(profil, TACHES_SIMPLES)
        return min(0.95, 0.6 + 0.2 * part + 0.15 * profil.part_sortie_structuree)
