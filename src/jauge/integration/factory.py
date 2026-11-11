"""Fabrique de services et d'application : le câblage du plan de contrôle (M17).

`creer_services` assemble les **douze ports** des agents (`jauge.agents.ports`)
sur les modules réels, à partir d'un simple `Store` :

| port | adaptateur | module réel |
|---|---|---|
| `referentiel` | `AdaptateurReferentiel` | `jauge.store` |
| `agregats` | `AdaptateurAgregats` | `jauge.store` + M1 |
| `rapprochement` | `AdaptateurRapprochement` | M1 (`jauge.attribution.reconcile`) |
| `leviers` | `AdaptateurLeviers` | M3 (`jauge.levers`) |
| `profils` | `AdaptateurProfilTache` | M1 + M5 |
| `candidats` | `AdaptateurCandidats` | `jauge.store` + M2 |
| `estimation` | `AdaptateurEstimation` | M4 (`jauge.replay.cost`) |
| `rejeu` | `AdaptateurRejeu` | M4 (`jauge.replay.executor`) |
| `resultats` | `AdaptateurResultats` | M5 (`jauge.evaluation`) |
| `entrees` | `AdaptateurEntrees` | M5 + M6 |
| `enregistrement` | `AdaptateurEnregistrement` | `jauge.store` + audit |
| `validation` | `AdaptateurValidation` | §12.3 (interruption humaine) |

`creer_app` renvoie l'application FastAPI **avec ces services** : les ports de
l'API (campagnes, rapports, chat) sont branchés sur les mêmes adaptateurs. En
mode démonstration, aucun appel réseau n'est possible : le client de modèle et
le juge sont les doubles hors ligne du paquet.

Exigences visées : §12, §15, ENF-SEC-02, ENF-CONF-01, ENF-IA-01.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from jauge.agents.ports import Services
from jauge.api.app import Ports
from jauge.api.app import creer_app as creer_app_api
from jauge.api.audit import AuditSink, MemoryAuditStore
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.judge import JugeClient
from jauge.integration.api_ports import AdaptateurCampagnes, AdaptateurRapports
from jauge.integration.bascules import AdaptateurBascules
from jauge.integration.candidats import AdaptateurCandidats
from jauge.integration.chat import AdaptateurChat
from jauge.integration.client_demo import ClientModeleDemo, juge_demonstration
from jauge.integration.couts import AdaptateurAgregats, AdaptateurRapprochement
from jauge.integration.estimation import AdaptateurEstimation
from jauge.integration.leviers import AdaptateurLeviers
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.integration.profil import AdaptateurProfilTache
from jauge.integration.recommandation import (
    AdaptateurEnregistrement,
    AdaptateurEntrees,
    AdaptateurValidation,
)
from jauge.integration.referentiel import AdaptateurReferentiel
from jauge.integration.rejeu import AdaptateurRejeu
from jauge.integration.resultats import AdaptateurResultats
from jauge.pricing_data.seed import table_demo
from jauge.replay.client import ModeleClient
from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store


@dataclass
class Clients:
    """Clients externes injectés ; en démo, les doubles hors ligne sont pris."""

    modele: ModeleClient | None = None
    juge: JugeClient | None = None
    modele_agent: Any | None = None
    audit: AuditSink | None = None
    passerelle: Any | None = None


def table_tarifs_demo() -> TableTarifs:
    """Table des tarifs de démonstration (module M2)."""
    return table_demo()


def creer_services(
    store: Store,
    *,
    mode_demo: bool = True,
    clients: Clients | None = None,
    tarifs: TableTarifs | None = None,
    plan: PlanDeDonnees | None = None,
) -> Services:
    """Assemble les douze ports des agents sur les modules réels."""
    clients = clients or Clients()
    table = tarifs or table_tarifs_demo()
    plan_donnees = plan or PlanDeDonnees()
    profils = AdaptateurProfilTache(store, table, plan_donnees)
    estimation = AdaptateurEstimation(store, table, profils)
    rejeu = AdaptateurRejeu(
        store,
        table,
        plan_donnees,
        modele_client=clients.modele or (ClientModeleDemo(plan_donnees) if mode_demo else None),
        juge_client=clients.juge or (juge_demonstration() if mode_demo else None),
        estimation=estimation,
        mode_demo=mode_demo,
    )
    return Services(
        referentiel=AdaptateurReferentiel(store),
        agregats=AdaptateurAgregats(store),
        leviers=AdaptateurLeviers(store),
        rapprochement=AdaptateurRapprochement(store),
        profils=profils,
        candidats=AdaptateurCandidats(store, table),
        estimation=estimation,
        rejeu=rejeu,
        resultats=AdaptateurResultats(store, plan_donnees),
        entrees=AdaptateurEntrees(store, table, plan_donnees, profils),
        enregistrement=AdaptateurEnregistrement(store, clients.audit),
        validation=AdaptateurValidation(store),
    )


def creer_app(
    mode_demo: bool = True,
    store: Store | None = None,
    *,
    clients: Clients | None = None,
    audit: AuditSink | None = None,
    tarifs: TableTarifs | None = None,
    plan: PlanDeDonnees | None = None,
) -> FastAPI:
    """Construit l'application du plan de contrôle avec les services réels.

    `plan` est le plan de données du client (textes et requêtes rejouables) : il
    doit être fourni pour que l'API puisse lancer une campagne de rejeu. Sans
    lui, un plan vide est créé et le lancement échoue explicitement (« aucune
    requête tirable »), jamais en silence.
    """
    clients = clients or Clients()
    magasin = store or MemoryStore()
    journal = audit or clients.audit or MemoryAuditStore()
    clients.audit = journal
    table = tarifs or table_tarifs_demo()
    plan_donnees = plan or PlanDeDonnees()
    services = creer_services(
        magasin, mode_demo=mode_demo, clients=clients, tarifs=table, plan=plan_donnees
    )
    ports = Ports(
        campagnes=AdaptateurCampagnes(magasin, services.rejeu),  # type: ignore[arg-type]
        rapports=AdaptateurRapports(magasin, table),
        chat=AdaptateurChat(services, magasin, modele=clients.modele_agent, mode_demo=mode_demo),
        bascules=AdaptateurBascules(magasin, passerelle=clients.passerelle),
    )
    app = creer_app_api(store=magasin, mode_demo=mode_demo, audit=journal, ports=ports)
    app.state.services = services
    app.state.plan_de_donnees = plan_donnees
    return app
