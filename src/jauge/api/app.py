"""Application FastAPI du plan de contrôle Jauge (§15, EF-POR-01).

`creer_app` est une **factory** : le store, le journal d'audit, les réglages et
les ports sont injectés, ce qui permet aux tests d'utiliser `MemoryStore` sans
aucun service externe. Le mode démo (`JAUGE_MODE_DEMO=1`, actif par défaut)
lit les en-têtes `X-Jauge-*` et sert le compte de démonstration des juges
(EF-POR-02) ; hors démo, les appels exigent un jeton Cognito `Bearer`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from jauge.api.audit import AuditSink, MemoryAuditStore
from jauge.api.auth import CognitoJwksVerifier, mode_demo_actif
from jauge.api.errors import enregistrer_gestionnaires_erreurs
from jauge.api.ports import (
    BasculePort,
    CampagnePort,
    ChatPort,
    PortCampagnesDemo,
    PortCampagnesIndisponible,
    PortChatDemo,
    PortChatIndisponible,
    PortRapportsDemo,
    PortRapportsIndisponible,
    RapportPort,
)
from jauge.api.reglages import MemoryReglagesStore, ParametresAdmin, ReglagesStore
from jauge.api.routes import ROUTEURS
from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store

logger = logging.getLogger(__name__)

RACINE = Path(__file__).resolve().parents[3]
DOSSIER_PORTAIL = RACINE / "portal"

DESCRIPTION = """
Plan de contrôle Jauge : attribution des coûts d'inférence Bedrock, leviers
d'économie, campagnes de rejeu, recommandations, bascule progressive et rapports.

Cloisonnement strict par client (ENF-SEC-02) : toute route qui expose des données
filtre sur le client de l'appelant et refuse (403) l'accès aux données d'un autre
client. Les refus sont journalisés dans l'audit (EF-ADM-02).
"""


class Ports:
    """Regroupe les ports métier injectés dans l'application.

    `bascules` reste optionnel : sans lui, les routes de bascule construisent
    l'état elles-mêmes (comportement historique, testé par la recette R6/R8).
    Avec lui (module M17/M18), la bascule passe par le `ControlleurBascule` de M6
    et crée une vraie règle de passerelle (EF-BAS-01..03).
    """

    def __init__(
        self,
        campagnes: CampagnePort,
        rapports: RapportPort,
        chat: ChatPort,
        bascules: BasculePort | None = None,
    ) -> None:
        self.campagnes = campagnes
        self.rapports = rapports
        self.chat = chat
        self.bascules = bascules


def ports_par_defaut(store: Store, mode_demo: bool) -> Ports:
    """Ports par défaut : implémentations de démo, ou refus explicite (502)."""
    if mode_demo:
        return Ports(PortCampagnesDemo(store), PortRapportsDemo(store), PortChatDemo(store))
    return Ports(PortCampagnesIndisponible(), PortRapportsIndisponible(), PortChatIndisponible())


def creer_app(
    store: Store | None = None,
    mode_demo: bool | None = None,
    audit: AuditSink | None = None,
    reglages: ReglagesStore | None = None,
    ports: Ports | None = None,
) -> FastAPI:
    """Construit l'application FastAPI du plan de contrôle."""
    store = store or MemoryStore()
    demo = mode_demo_actif() if mode_demo is None else mode_demo
    if demo:
        # Le mode démo fait confiance aux en-têtes X-Jauge-* : jamais en production.
        logger.warning(
            "Jauge démarre en MODE DÉMO : les en-têtes X-Jauge-Tenant / X-Jauge-Role "
            "sont acceptés sans jeton. Mettre JAUGE_MODE_DEMO=0 hors démonstration."
        )
    app = FastAPI(
        title="Jauge — plan de contrôle FinOps IA",
        version="0.1.0",
        description=DESCRIPTION,
        docs_url="/v1/docs",
        redoc_url="/v1/redoc",
        openapi_url="/v1/openapi.json",
    )
    app.state.store = store
    app.state.mode_demo = demo
    app.state.audit = audit or MemoryAuditStore()
    app.state.reglages = reglages or MemoryReglagesStore(ParametresAdmin())
    app.state.verificateur = CognitoJwksVerifier()
    ports_choisis = ports or ports_par_defaut(store, demo)
    app.state.port_campagnes = ports_choisis.campagnes
    app.state.port_rapports = ports_choisis.rapports
    app.state.port_chat = ports_choisis.chat
    app.state.port_bascules = ports_choisis.bascules

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    enregistrer_gestionnaires_erreurs(app)
    for routeur in ROUTEURS:
        app.include_router(routeur)
    _monter_portail(app)
    return app


def _monter_portail(app: FastAPI) -> None:
    """Monte le portail statique s'il existe (module M13), sans le rendre obligatoire."""
    if DOSSIER_PORTAIL.is_dir() and (DOSSIER_PORTAIL / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(DOSSIER_PORTAIL), html=True), name="portal")


def creer_app_asgi() -> FastAPI:
    """Point d'entrée ASGI documenté : `uvicorn jauge.api.app:app`.

    En mode démonstration (défaut), l'application est celle du câblage M17 :
    les ports de l'API (campagnes, rapports, chat, **bascules**) sont branchés
    sur les modules réels, et le store est amorcé avec le scénario de démo
    (`demo.seed`) pour que le portail affiche des chiffres dès l'ouverture.

    Hors démonstration (`JAUGE_MODE_DEMO=0`), l'application n'amorce aucune
    donnée de démo, exige un jeton Cognito, et prend son store dans
    l'environnement (`JAUGE_STORE=dynamodb` + `JAUGE_STATE_TABLE`, posés par le
    CDK) : c'est la configuration déployée sur AWS.
    Les imports sont tardifs : `jauge.integration` et `jauge.store` importent ce
    module.
    """
    if not mode_demo_actif():
        from jauge.store import creer_store

        return creer_app(store=creer_store(), mode_demo=False)
    try:
        from demo.seed import peupler_store

        from jauge.integration.factory import creer_app as creer_app_integree
    except ImportError as erreur:  # paquet installé sans le dépôt (demo absent)
        logger.warning("Amorçage de démonstration indisponible (%s) : application nue.", erreur)
        return creer_app(mode_demo=True)
    magasin = MemoryStore()
    plan = peupler_store(magasin)
    return creer_app_integree(mode_demo=True, store=magasin, plan=plan)


_APP_ASGI: FastAPI | None = None


def __getattr__(nom: str) -> FastAPI:
    """Expose `app` (ASGI) sans l'amorcer à l'import du module.

    L'amorçage de démonstration coûte quelques secondes : le rendre paresseux
    évite de le payer à chaque `from jauge.api.app import creer_app` (tests).
    `uvicorn jauge.api.app:app` accède à l'attribut, donc le déclenche.
    """
    global _APP_ASGI  # noqa: PLW0603 - mise en cache volontaire du point d'entrée
    if nom != "app":
        raise AttributeError(f"module {__name__!r} n'a pas d'attribut {nom!r}")
    if _APP_ASGI is None:
        _APP_ASGI = creer_app_asgi()
    return _APP_ASGI
