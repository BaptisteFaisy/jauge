"""Table des tarifs datée de Jauge (EF-ATT-04, §14, §23 Q3).

Le socle fournit le modèle (`jauge.models.pricing.TarifModele`), la recherche du
tarif applicable et la formule de coût (`jauge.engine.calculator`). Ce paquet
apporte ce qui manquait : le **fichier** de tarifs daté, un **chargeur**
validant, et un accès à la table de démonstration.
"""

from __future__ import annotations

from jauge.pricing_data.loader import charger_table, charger_tarifs, lignes
from jauge.pricing_data.seed import (
    NOM_FICHIER_DEMO,
    VARIABLE_ENV,
    chemin_table_demo,
    table_demo,
)

__all__ = [
    "NOM_FICHIER_DEMO",
    "VARIABLE_ENV",
    "charger_table",
    "charger_tarifs",
    "chemin_table_demo",
    "lignes",
    "table_demo",
]
