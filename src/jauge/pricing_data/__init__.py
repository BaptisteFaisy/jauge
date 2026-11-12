"""Table des tarifs datée de Jauge (EF-ATT-04, §14, §23 Q3).

Le socle fournit le modèle (`jauge.models.pricing.TarifModele`), la recherche du
tarif applicable et la formule de coût (`jauge.engine.calculator`). Ce paquet
apporte ce qui manquait : le **fichier** de tarifs daté, un **chargeur**
validant, un accès à la table de démonstration, puis le **catalogue historisé**
(prix en vigueur, périodes de validité, empreinte) et la **surveillance** des
changements de prix (variations, impact économique, preuves à rejouer).
"""

from __future__ import annotations

from jauge.pricing_data.catalogue import (
    CatalogueTarifs,
    CleTarif,
    PrixApplicatif,
    SourceTarifs,
    SourceTarifsAws,
    SourceTarifsLocale,
)
from jauge.pricing_data.impact_prix import (
    JOURS_PAR_MOIS,
    ImpactVariation,
    PreuveARejouer,
    impact_variation,
    preuves_a_rejouer,
)
from jauge.pricing_data.loader import charger_table, charger_tarifs, lignes
from jauge.pricing_data.seed import (
    NOM_FICHIER_DEMO,
    VARIABLE_ENV,
    chemin_table_demo,
    table_demo,
)
from jauge.pricing_data.surveillance import (
    LigneApplicable,
    PrixParType,
    VariationPrix,
    detecter_variations,
    prix_par_type,
    snapshot,
    variations_entre_dates,
    variations_entre_versions,
)

__all__ = [
    "JOURS_PAR_MOIS",
    "NOM_FICHIER_DEMO",
    "VARIABLE_ENV",
    "CatalogueTarifs",
    "CleTarif",
    "ImpactVariation",
    "LigneApplicable",
    "PreuveARejouer",
    "PrixApplicatif",
    "PrixParType",
    "SourceTarifs",
    "SourceTarifsAws",
    "SourceTarifsLocale",
    "VariationPrix",
    "charger_table",
    "charger_tarifs",
    "chemin_table_demo",
    "detecter_variations",
    "impact_variation",
    "lignes",
    "preuves_a_rejouer",
    "prix_par_type",
    "snapshot",
    "table_demo",
    "variations_entre_dates",
    "variations_entre_versions",
]
