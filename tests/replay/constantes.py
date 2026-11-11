"""Constantes partagées des tests du rejeu (M4).

Elles vivent hors de `conftest.py` pour être importables telles quelles par les
fichiers de test, sans dépendre de la mécanique des fixtures.
"""

from __future__ import annotations

from datetime import date

from jauge.models.pricing import ServiceTier, TarifModele

# Modèles fictifs de la démonstration (aucun identifiant réel).
MODELE_GRAND = "demo-grand-modele"
MODELE_MOYEN = "demo-moyen-modele"
MODELE_PETIT = "demo-petit-modele"
MODELE_JUGE = "demo-modele-juge"
REGION_REJEU = "eu-west-1"
DATE_EFFET = date(2026, 1, 1)
APPLICATION = "app-demo"
CAMPAGNE = "c-demo"
GRAINE = 7


def tarif(modele: str, entree: float, sortie: float, niveau: ServiceTier) -> TarifModele:
    """Ligne de tarif de démonstration (prix pour 1 000 jetons)."""
    return TarifModele(
        modele_id=modele,
        region=REGION_REJEU,
        niveau=niveau,
        date_effet=DATE_EFFET,
        prix_entree_1k=entree,
        prix_sortie_1k=sortie,
    )
