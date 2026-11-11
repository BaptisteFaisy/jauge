"""Adaptateur des leviers d'économie (port `PortLeviers`) et service de détection.

Deux choses :

- le **port** lit les leviers enregistrés dans le store et les classe par
  économie mensuelle décroissante (EF-LEV-06), avec la fonction de classement
  du module M3 (`jauge.levers.detector.classer_par_economie`) ;
- le **service de détection** exécute le détecteur réel — les sept règles de
  l'Annexe A (`jauge.levers.detecter_leviers`) — sur un `ContexteLevier`, puis
  enregistre les leviers trouvés dans le store.

C'est le même code qu'en production : aucun calcul n'est dupliqué (ENF-IA-01).

Exigences visées : EF-LEV-01..06, Annexe A, ENF-SEC-02.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from jauge.agents.ports import PortLeviers
from jauge.integration.socle import charger_application, charger_tenant
from jauge.levers.base import ContexteLevier, ParametresLevier
from jauge.levers.detector import classer_par_economie, detecter_leviers
from jauge.models.lever import Levier
from jauge.store.protocol import Store

#: Seuils de détection alignés sur le trafic **synthétique** de la démonstration.
#:
#: Les seuils de production du module M3 (préfixe de 1024 jetons, 20 % de
#: requêtes identiques, p95 à 90 % de la limite de sortie) ne sont pas atteints
#: par le trafic de la démo : on les aligne sur ce trafic, et on le dit.
SEUIL_PREFIXE_LONG_DEMO = 512
SEUIL_REQUETES_IDENTIQUES_DEMO = 0.10
RATIO_SORTIE_MEDIANE_DEMO = 1.4
RATIO_LIMITE_SORTIE_DEMO = 0.85


def parametres_demonstration(date_reference: date, region: str) -> ParametresLevier:
    """Seuils de détection de la démonstration (partagés par la démo et les tests)."""
    return replace(
        ParametresLevier(date_reference=date_reference, region=region),
        seuil_longueur_prefixe_long=SEUIL_PREFIXE_LONG_DEMO,
        seuil_part_requetes_identiques=SEUIL_REQUETES_IDENTIQUES_DEMO,
        ratio_sortie_mediane=RATIO_SORTIE_MEDIANE_DEMO,
        ratio_limite_sortie=RATIO_LIMITE_SORTIE_DEMO,
    )


class AdaptateurLeviers(PortLeviers):
    """Port des leviers branché sur le store du plan de contrôle."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def leviers(self, tenant_id: str, application_id: str | None) -> list[Levier]:
        """Leviers détectés du client, classés par économie décroissante."""
        charger_tenant(self._store, tenant_id)
        if application_id is not None:
            charger_application(self._store, tenant_id, application_id)
        return classer_par_economie(self._store.lister_leviers(tenant_id, application_id))


class ServiceDetectionLeviers:
    """Exécute le détecteur réel (M3) et enregistre les leviers trouvés."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def detecter(self, contexte: ContexteLevier) -> list[Levier]:
        """Applique les sept règles de l'Annexe A et enregistre le résultat."""
        leviers = detecter_leviers(contexte)
        for levier in leviers:
            self._store.enregistrer_levier(levier)
        return leviers
