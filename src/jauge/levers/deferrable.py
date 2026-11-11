"""Règle EF-LEV-03 — traitement différable resté au niveau Standard (Annexe A).

Indices de l'Annexe A : « Trafic nocturne ou par rafales, appels non
interactifs, niveau Standard ». Action proposée : « Niveau Flex ou traitement
par lots ». Preuve exigée : « Accord du responsable sur le délai ».

Le trafic différable est celui qui est **non interactif** et situé en heures
creuses ou en heures de rafale ; l'intersection est calculée exactement.
"""

from __future__ import annotations

from jauge.engine.stats import baisse_pourcentage
from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    formater_nombre,
    part_niveau,
    part_non_interactive,
    part_trafic_deferrable,
    part_trafic_nocturne,
    part_trafic_rafale,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import cout_requete_niveau, estimer_traitement_differable, tarif_pour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier

ACTION_PROPOSEE = "Niveau Flex ou traitement par lots"
PREUVE_EXIGEE = "Accord du responsable sur le délai"


class RegleTraitementDifferable:
    """Détecte un trafic de fond encore facturé au niveau Standard."""

    type_levier = TypeLevier.TRAITEMENT_DIFFERABLE

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si le trafic est différable et servi en Standard."""
        if not self._deja_flex(contexte):
            return None
        if part_non_interactive(contexte.profil) < contexte.parametres.seuil_part_non_interactive:
            return None
        if not self._trafic_deferrable(contexte):
            return None
        economie = estimer_traitement_differable(contexte)
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

    def _deja_flex(self, contexte: ContexteLevier) -> bool:
        """Le trafic n'est déjà pas majoritairement au niveau Flex."""
        return part_niveau(contexte.profil, ServiceTier.FLEX) < 0.5

    def _trafic_deferrable(self, contexte: ContexteLevier) -> bool:
        """Trafic nocturne ou par rafales, sans utilisateur en attente."""
        parametres = contexte.parametres
        nocturne = part_trafic_nocturne(contexte.profil)
        rafale = part_trafic_rafale(contexte.profil)
        return (
            nocturne >= parametres.seuil_part_trafic_nocturne
            or rafale >= parametres.seuil_part_trafic_rafale
        )

    def _indices(self, contexte: ContexteLevier) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        tarif_standard = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
        tarif_flex = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.FLEX)
        cout_standard = cout_requete_niveau(contexte, tarif_standard) if tarif_standard else 0.0
        cout_flex = cout_requete_niveau(contexte, tarif_flex) if tarif_flex else 0.0
        part = part_trafic_deferrable(profil)
        return [
            f"Trafic nocturne sur {pourcent(part_trafic_nocturne(profil))}, "
            f"rafale sur {pourcent(part_trafic_rafale(profil))}",
            f"Appels non interactifs : {pourcent(part_non_interactive(profil))} du trafic",
            f"Niveau de service actuel : Standard "
            f"({pourcent(part_niveau(profil, ServiceTier.STANDARD))} du trafic, "
            f"Flex {pourcent(part_niveau(profil, ServiceTier.FLEX))})",
            f"Hypothèse : {pourcent(part)} du trafic est différable "
            f"({formater_nombre(volume_mensuel(contexte))} requêtes/mois), "
            f"coût {cout_standard:.6f} $/requête en Standard contre "
            f"{cout_flex:.6f} $/requête en Flex",
            f"Remise Flex appliquée : {baisse_pourcentage(cout_standard, cout_flex):.2f} %",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part du trafic réellement différable."""
        return min(0.95, 0.6 + 0.3 * part_trafic_deferrable(contexte.profil))
