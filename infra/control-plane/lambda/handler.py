"""Adaptateur Lambda → ASGI du plan de contrôle (paquetage, aucun code métier).

L'application FastAPI vit dans `src/jauge/api` (module M8) et expose une
application ASGI. AWS Lambda n'exécute pas d'ASGI nativement : ce module traduit
un événement proxy d'API Gateway (REST, `{proxy+}`) en requête ASGI, exécute
l'application et renvoie la réponse au format attendu par API Gateway.

Rien n'est dupliqué ici : pas de routage, pas de règle métier, pas de calcul.
Si le paquet `jauge` n'est pas présent dans la couche Lambda (déploiement
partiel), la fonction répond `501` avec un message explicite plutôt que de
planter — la panne est ainsi visible dans les journaux et dans les alarmes.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

# Types MIME pour lesquels le corps est renvoyé tel quel (pas de base64).
TEXTUEL = ("application/json", "application/javascript", "text/", "application/xml")


def handler(event: dict, context: Any) -> dict:
    """Point d'entrée Lambda : délègue à l'application ASGI de `jauge.api`."""
    try:
        application = _application()
    except Exception as erreur:  # paquet absent ou import cassé : on le dit
        return _erreur_501(f"jauge.api.app is not available in this deployment: {erreur}")
    return asyncio.run(_executer(application, event))


def _application() -> Any:
    """Import paresseux : l'application ASGI du module M8."""
    from jauge.api.app import app  # import local : la couche fournit `jauge`

    return app


async def _executer(application: Any, event: dict) -> dict:
    """Exécute l'application ASGI et collecte la réponse HTTP."""
    portee, corps = _portee(event)
    etat: dict[str, Any] = {"status": 500, "headers": [], "body": b""}

    async def receive() -> dict:
        return {"type": "http.request", "body": corps, "more_body": False}

    async def send(message: dict) -> None:
        _collecter(etat, message)

    await application(portee, receive, send)
    return _reponse(etat)


def _collecter(etat: dict, message: dict) -> None:
    """Accumule les messages ASGI (`http.response.start`, `http.response.body`)."""
    if message["type"] == "http.response.start":
        etat["status"] = int(message["status"])
        etat["headers"] = [
            (cle.decode("latin-1"), valeur.decode("latin-1"))
            for cle, valeur in message.get("headers", [])
        ]
    elif message["type"] == "http.response.body":
        etat["body"] += message.get("body", b"")


def _portee(event: dict) -> tuple[dict, bytes]:
    """Construit la portée ASGI à partir de l'événement proxy d'API Gateway."""
    entetes = {
        cle.lower(): valeur
        for cle, valeur in (event.get("headers") or {}).items()
        if valeur is not None
    }
    chemin = event.get("path") or "/"
    parametres = event.get("queryStringParameters") or {}
    portee = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method", "GET"),
        "scheme": "https",
        "path": chemin,
        "raw_path": chemin.encode("utf-8"),
        "root_path": "",
        "query_string": _requete_brute(parametres),
        "headers": [
            (cle.encode("latin-1"), valeur.encode("latin-1")) for cle, valeur in entetes.items()
        ],
        "client": (
            event.get("requestContext", {}).get("identity", {}).get("sourceIp") or "0.0.0.0",
            0,
        ),
        "server": (entetes.get("host", "api.jauge.local"), 443),
        "aws.event": event,
    }
    return portee, _corps_entrant(event)


def _requete_brute(parametres: dict) -> bytes:
    """Chaîne de requête encodée, sans dépendance à `urllib.parse` sur les types."""
    from urllib.parse import urlencode

    return urlencode(sorted(parametres.items())).encode("latin-1")


def _corps_entrant(event: dict) -> bytes:
    """Corps de la requête, décodé s'il est arrivé en base64."""
    corps = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(corps)
    return corps.encode("utf-8")


def _reponse(etat: dict) -> dict:
    """Réponse au format proxy d'API Gateway REST (en-têtes multi-valeurs)."""
    entetes: dict[str, str] = {}
    set_cookie: list[str] = []
    for cle, valeur in etat["headers"]:
        if cle.lower() == "set-cookie":
            set_cookie.append(valeur)
        else:
            entetes[cle] = f"{entetes[cle]},{valeur}" if cle in entetes else valeur
    texte, binaire = _corps_sortant(etat["body"], entetes.get("content-type", ""))
    reponse = {"statusCode": etat["status"], "headers": entetes, "body": texte}
    if set_cookie:
        reponse["multiValueHeaders"] = {"Set-Cookie": set_cookie}
    if binaire:
        reponse["isBase64Encoded"] = True
    return reponse


def _corps_sortant(corps: bytes, content_type: str) -> tuple[str, bool]:
    """Encode le corps : texte si le type MIME est textuel, base64 sinon."""
    type_mime = content_type.split(";")[0].strip().lower()
    if any(type_mime.startswith(prefixe) for prefixe in TEXTUEL):
        return corps.decode("utf-8", errors="replace"), False
    return base64.b64encode(corps).decode("ascii"), True


def _erreur_501(message: str) -> dict:
    """Réponse d'erreur explicite quand l'application n'est pas empaquetée."""
    return {
        "statusCode": 501,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"error": "not_implemented", "detail": message}),
        "isBase64Encoded": False,
    }
