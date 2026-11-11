"""Sonde de vie du plan de contrôle (`GET /health`).

Route **ouverte** : c'est la seule que la passerelle API du plan de contrôle
expose sans jeton Cognito (`infra/control-plane/jauge_control_plane/constructs/
api.py`). Elle ne renvoie aucune donnée client — uniquement l'état du service —
et ne dépend d'aucune authentification, pour rester utilisable par les sondes
d'alarme CloudWatch (ENF-OBS-01).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from jauge import __version__

router = APIRouter(tags=["santé"])


@router.get("/health")
def sante(request: Request) -> dict[str, object]:
    """État du service : version, mode de démonstration, clients chargés."""
    etat = request.app.state
    store = getattr(etat, "store", None)
    clients = len(store.lister_tenants()) if store is not None else 0
    return {
        "status": "ok",
        "version": __version__,
        "mode_demo": bool(getattr(etat, "mode_demo", False)),
        "clients_enregistres": clients,
        "bascules_cablees": getattr(etat, "port_bascules", None) is not None,
    }
