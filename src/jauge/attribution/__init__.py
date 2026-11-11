"""Module M1 — Attribution et journaux (§8.2, EF-ATT-01..08).

Chaîne complète, dans l'ordre du parcours d'attribution quotidienne :

```
journaux S3/JSON ──logs.py──▶ EnregistrementInvocation (+ étiquettes)
                                   │
                     tags.py ◀─────┘  complétion des étiquettes (EF-ATT-03),
                        │              part non attribuée et causes (EF-ATT-07)
              aggregate.py             agrégats quotidiens (EF-ATT-05)
                        │
              reconcile.py             écart avec la facture CUR 2.0 (EF-ATT-06/08)
```

Points d'entrée :

- :func:`lire_journaux` / :func:`iterer_enregistrements` — lecture tolérante et
  en flux des journaux d'invocation (§23 Q2 : noms de champs non confirmés) ;
- :class:`AgregateurQuotidien` — agrégats par application, équipe,
  environnement, modèle et jour ;
- :func:`lire_cur`, :func:`facture_par_jour`, :func:`rapprocher_periode` —
  rapprochement avec la facture CUR 2.0.

Aucun coût n'est calculé ici : le socle (`jauge.engine.calculator`) reste seul
juge des prix (ENF-IA-01).
"""

from __future__ import annotations

from jauge.attribution.aggregate import (
    DIMENSIONS,
    AgregateurQuotidien,
    CleAgregat,
    ResultatAgregation,
    agregats_par_dimension,
    iterer_par_jour,
)
from jauge.attribution.champs import normaliser_cle
from jauge.attribution.logs import (
    CHAMPS_ACCEPTES,
    EnregistrementInvocation,
    EnregistrementRejete,
    RapportLecture,
    analyser_enregistrement,
    determiner_niveau_service,
    determiner_point_acces,
    iterer_enregistrements,
    iterer_rejouables,
    lire_journaux,
)
from jauge.attribution.reconcile import (
    CHAMPS_CUR,
    FactureJour,
    LigneCur,
    StatistiquesCur,
    facture_par_jour,
    iterer_lignes_cur,
    lire_cur,
    lire_fichier_cur,
    parser_ligne_cur,
    rapprocher_journee,
    rapprocher_periode,
)
from jauge.attribution.tags import (
    CHAMPS_ETIQUETTES,
    CumulNonAttribution,
    Etiquettes,
    LigneAttribution,
    PartNonAttribuee,
    PointAcces,
    appliquer_schema,
    calculer_part_non_attribuee,
    completer,
    extraire_etiquettes,
    valeurs_hors_schema,
)
from jauge.attribution.valeurs import (
    NIVEAUX,
    lire_horodatage,
)

__all__ = [
    "CHAMPS_ACCEPTES",
    "CHAMPS_CUR",
    "CHAMPS_ETIQUETTES",
    "DIMENSIONS",
    "NIVEAUX",
    "AgregateurQuotidien",
    "CleAgregat",
    "CumulNonAttribution",
    "EnregistrementInvocation",
    "EnregistrementRejete",
    "Etiquettes",
    "FactureJour",
    "LigneAttribution",
    "LigneCur",
    "PartNonAttribuee",
    "PointAcces",
    "RapportLecture",
    "ResultatAgregation",
    "StatistiquesCur",
    "agregats_par_dimension",
    "analyser_enregistrement",
    "appliquer_schema",
    "calculer_part_non_attribuee",
    "completer",
    "determiner_niveau_service",
    "determiner_point_acces",
    "extraire_etiquettes",
    "facture_par_jour",
    "iterer_enregistrements",
    "iterer_lignes_cur",
    "iterer_par_jour",
    "iterer_rejouables",
    "lire_cur",
    "lire_fichier_cur",
    "lire_horodatage",
    "lire_journaux",
    "normaliser_cle",
    "parser_ligne_cur",
    "rapprocher_journee",
    "rapprocher_periode",
    "valeurs_hors_schema",
]
