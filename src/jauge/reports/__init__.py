"""Module `reports` — rapport mensuel par client (EF-RAP-01..03).

Assemblage : `builder.construire_rapport` lit le store et fixe tous les
chiffres. Rédaction : `synthesis` orne ces chiffres d'un texte, sans les
modifier (ENF-IA-01). Sortie : `storage` rend le Markdown et le JSON et les
archive (S3 en production, mémoire en test).

Les gabarits et le texte destiné au client sont en anglais ; le code et ses
commentaires sont en français (BRIEF §2.8).
"""

from __future__ import annotations

from jauge.reports.builder import construire_rapport, mois_precedent, periode_mensuelle
from jauge.reports.schema import (
    AppendiceMethodologique,
    EcartFacture,
    EconomiesRapportees,
    EtatBascule,
    LevierRapporte,
    LigneCout,
    RapportMensuel,
    RecommandationEnAttente,
    SyntheseRapport,
    TendanceMensuelle,
)
from jauge.reports.storage import (
    DepotMemoire,
    DepotRapports,
    DepotS3,
    cle_rapport,
    publier,
    rendre_json,
    rendre_markdown,
    vers_dict,
)
from jauge.reports.synthesis import (
    ContexteSynthese,
    RedacteurBedrock,
    RedacteurDeterministe,
    RedacteurModele,
    RedacteurStrands,
    appliquer_synthese,
    contexte_depuis_rapport,
)

__all__ = [
    "AppendiceMethodologique",
    "ContexteSynthese",
    "DepotMemoire",
    "DepotRapports",
    "DepotS3",
    "EcartFacture",
    "EconomiesRapportees",
    "EtatBascule",
    "LevierRapporte",
    "LigneCout",
    "RapportMensuel",
    "RecommandationEnAttente",
    "RedacteurBedrock",
    "RedacteurDeterministe",
    "RedacteurModele",
    "RedacteurStrands",
    "SyntheseRapport",
    "TendanceMensuelle",
    "appliquer_synthese",
    "cle_rapport",
    "construire_rapport",
    "contexte_depuis_rapport",
    "mois_precedent",
    "periode_mensuelle",
    "publier",
    "rendre_json",
    "rendre_markdown",
    "vers_dict",
]
