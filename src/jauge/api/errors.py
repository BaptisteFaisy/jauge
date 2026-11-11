"""Gestion uniforme des erreurs pour l'API Jauge (EF-POR-01).

Toutes les exceptions dérivées de `JaugeError` sont converties en une réponse
JSON normalisée : `{"error": {"code": ..., "message": ..., "details": ...}}`
avec le code HTTP approprié.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from jauge.common.errors import JaugeError


async def gestionnaire_jauge_error(_request: Request, exc: JaugeError) -> JSONResponse:
    """Traduit une exception métier en réponse HTTP normalisée."""
    return JSONResponse(status_code=exc.statut_http, content=exc.vers_dict())


async def gestionnaire_validation_pydantic(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Uniformise les erreurs de validation FastAPI au format de JaugeError.

    Les erreurs pydantic peuvent porter un `ctx` non sérialisable (exception
    levée par un validateur) : on n'en garde que la position, le message et le type.
    """
    erreurs = [
        {
            "loc": list(erreur.get("loc", ())),
            "msg": erreur.get("msg", ""),
            "type": erreur.get("type", ""),
        }
        for erreur in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Erreur de validation de la requête",
                "details": {"erreurs": erreurs},
            }
        },
    )


def enregistrer_gestionnaires_erreurs(app: FastAPI) -> None:
    """Enregistre les gestionnaires d'erreurs normalisés sur l'application FastAPI."""
    app.add_exception_handler(JaugeError, gestionnaire_jauge_error)
    app.add_exception_handler(RequestValidationError, gestionnaire_validation_pydantic)
