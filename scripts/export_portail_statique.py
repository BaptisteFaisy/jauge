#!/usr/bin/env python3
"""Exporte l'instantané statique du portail (`portal/data/demo.json`).

Le portail est une SPA sans backend quand il est publié sur GitHub Pages : il
lit alors cet instantané au lieu d'appeler l'API. Pour garantir que les formes
JSON sont **exactement** celles de l'API (§15), cet export ne recalcule rien :
il démarre la véritable application FastAPI en mode démonstration (store amorcé
par `demo.seed`) et interroge chaque point d'API avec `TestClient`, comme le
ferait le navigateur.

Usage :
    uv run python scripts/export_portail_statique.py [--sortie portal/data/demo.json]

Aucun appel réseau, aucun AWS : tout se passe en mémoire.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[1]
SORTIE_PAR_DEFAUT = RACINE / "portal" / "data" / "demo.json"

#: Graine des identifiants de l'export (campagnes, recommandations, bascules) :
#: à graine constante, l'instantané est reproductible d'un commit à l'autre.
GRAINE_IDENTIFIANTS = 20261111

#: Date de fin **figée** de la fenêtre de démonstration publiée.
#:
#: `demo.seed` utilise la date du jour quand on le lance à la main (fenêtre
#: glissante, plus parlant en démo live). L'instantané publié, lui, doit être
#: reproductible : sans date figée, le même code régénéré le lendemain décale
#: toute la fenêtre (`periode_debut`/`periode_fin`, clés d'agrégats) et le test
#: de fraîcheur échoue pour une raison purement calendaire. La date figure dans
#: `meta.genere_le` et dans l'en-tête du portail (« snapshot <date> »).
DATE_FIN_INSTANTANE = date(2026, 11, 11)

#: Points d'API consultés par le portail, résolus depuis les données de démo.
#: `{tenant}` / `{app}` / `{campagne}` / `{reco}` / `{bascule}` sont substitués.
CHEMINS_PAR_TENANT = (
    "/v1/tenants/{tenant}",
    "/v1/tenants/{tenant}/applications",
    "/v1/tenants/{tenant}/reconciliation",
    "/v1/tenants/{tenant}/savings",
)
CHEMINS_PAR_APPLICATION = (
    "/v1/applications/{app}",
    "/v1/applications/{app}/costs",
    "/v1/applications/{app}/levers",
    "/v1/applications/{app}/recommendations",
)
CHEMINS_PAR_CAMPAGNE = ("/v1/campaigns/{campagne}", "/v1/campaigns/{campagne}/results")
CHEMINS_PAR_BASCULE = ("/v1/rollouts/{bascule}",)


def _entetes(tenant: str, role: str) -> dict[str, str]:
    """En-têtes de démonstration attendus par l'API en mode démo."""
    return {"X-Jauge-Tenant": tenant, "X-Jauge-Role": role, "X-Jauge-User": "export-statique"}


def _construire_client() -> tuple[Any, Any]:
    """Démarre l'application réelle sur un store amorcé par la démo (date figée)."""
    import sys

    sys.path.insert(0, str(RACINE))
    from demo.seed import peupler_store

    from jauge.integration.factory import creer_app
    from jauge.store.memory import MemoryStore

    store = MemoryStore()
    plan = peupler_store(store, date_fin=DATE_FIN_INSTANTANE)
    return creer_app(mode_demo=True, store=store, plan=plan), store


def _collecter(client: Any, store: Any) -> dict[str, Any]:
    """Interroge chaque point d'API et range les réponses par `METHODE chemin`."""
    from fastapi.testclient import TestClient

    with TestClient(client) as http:
        instantane: dict[str, Any] = {}

        def noter(methode: str, chemin: str, tenant: str, role: str) -> Any:
            reponse = http.request(methode, chemin, headers=_entetes(tenant, role))
            cle = f"{methode} {chemin}"
            if reponse.status_code == 200:
                instantane[cle] = reponse.json()
            else:
                instantane[cle] = {"_statut": reponse.status_code, "_corps": reponse.text[:200]}
            return reponse

        noter("GET", "/v1/tenants", "t-demo-a", "analyste_finops")
        noter("GET", "/v1/demo/identity", "t-demo-a", "analyste_finops")
        noter("GET", "/v1/settings", "t-demo-a", "administrateur_plateforme")

        for tenant in store.lister_tenants():
            for modele in CHEMINS_PAR_TENANT:
                noter("GET", modele.format(tenant=tenant.id), tenant.id, "analyste_finops")
            for app in store.lister_applications(tenant.id):
                for modele in CHEMINS_PAR_APPLICATION:
                    noter("GET", modele.format(app=app.id), tenant.id, "analyste_finops")
            for campagne in store.lister_campagnes(tenant.id):
                for modele in CHEMINS_PAR_CAMPAGNE:
                    noter("GET", modele.format(campagne=campagne.id), tenant.id, "analyste_finops")
            for bascule in store.lister_bascules(tenant.id):
                for modele in CHEMINS_PAR_BASCULE:
                    noter("GET", modele.format(bascule=bascule.id), tenant.id, "analyste_finops")
        return instantane


def exporter(sortie: Path) -> dict[str, Any]:
    """Construit l'instantané et l'écrit ; rend le document écrit."""
    from jauge.common.ids import activer_ids_deterministes

    # Identifiants reproductibles : sans cela, chaque export invente de nouveaux
    # identifiants de campagne/recommandation/bascule et l'instantané versionné
    # diffère à chaque régénération (donc impossible à contrôler en CI).
    activer_ids_deterministes(GRAINE_IDENTIFIANTS)
    try:
        application, store = _construire_client()
        reponses = _collecter(application, store)
    finally:
        activer_ids_deterministes(None)
    document = {
        "meta": {
            "genere_le": datetime.now(UTC).isoformat(timespec="seconds"),
            "fenetre_demo": DATE_FIN_INSTANTANE.isoformat(),
            "source": "scripts/export_portail_statique.py",
            "avertissement": (
                "Instantané de démonstration, données synthétiques. Aucun client réel. "
                "Les nombres sont ceux calculés par le code sur le trafic de démo."
            ),
            "clients": [t.id for t in store.lister_tenants()],
            "points_api": len(reponses),
        },
        "reponses": reponses,
    }
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    return document


def main() -> int:
    """Point d'entrée : `python scripts/export_portail_statique.py`."""
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--sortie", type=Path, default=SORTIE_PAR_DEFAUT)
    options = analyseur.parse_args()
    document = exporter(options.sortie)
    meta = document["meta"]
    print(
        f"Instantané écrit : {options.sortie} "
        f"({meta['points_api']} points d'API, {meta['genere_le']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
