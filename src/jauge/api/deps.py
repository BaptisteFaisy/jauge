"""Dépendances FastAPI : store, audit, réglages, ports et contexte d'authentification.

L'application est une factory (`creer_app`) : tout est rangé dans `app.state`,
ce qui permet aux tests d'injecter `MemoryStore`, un journal d'audit mémoire et
des ports fictifs sans toucher au code des routes. Les alias `Annotated`
ci-dessous sont l'écriture attendue par FastAPI pour les dépendances.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from jauge.api.audit import AuditSink
from jauge.api.auth import (
    AuthentificationRequiseError,
    Utilisateur,
    extraire_utilisateur_demo,
)
from jauge.api.ports import CampagnePort, ChatPort, RapportPort
from jauge.api.reglages import ReglagesStore
from jauge.store.protocol import Store

ENTETE_TENANT = "X-Jauge-Tenant"
ENTETE_ROLE = "X-Jauge-Role"
ENTETE_UTILISATEUR = "X-Jauge-User"


def obtenir_store(request: Request) -> Store:
    """Rend le store injecté dans l'application."""
    return request.app.state.store


def obtenir_audit(request: Request) -> AuditSink:
    """Rend le journal d'audit injecté dans l'application."""
    return request.app.state.audit


def obtenir_reglages(request: Request) -> ReglagesStore:
    """Rend le magasin de paramètres d'administration."""
    return request.app.state.reglages


def obtenir_port_campagnes(request: Request) -> CampagnePort:
    """Rend le port de lancement de campagne (M4 ou implémentation de démo)."""
    return request.app.state.port_campagnes


def obtenir_port_rapports(request: Request) -> RapportPort:
    """Rend le port de rapports (M9 ou implémentation de démo)."""
    return request.app.state.port_rapports


def obtenir_port_chat(request: Request) -> ChatPort:
    """Rend le port de questions en langage naturel (M7 ou démo)."""
    return request.app.state.port_chat


def obtenir_utilisateur(request: Request) -> Utilisateur:
    """Résout l'utilisateur appelant : en-têtes de démo ou jeton Bearer Cognito."""
    if request.app.state.mode_demo:
        return extraire_utilisateur_demo(
            request.headers.get(ENTETE_TENANT),
            request.headers.get(ENTETE_ROLE),
            request.headers.get(ENTETE_UTILISATEUR),
        )
    entete = request.headers.get("Authorization", "")
    if not entete.lower().startswith("bearer "):
        raise AuthentificationRequiseError("Jeton Bearer absent")
    return request.app.state.verificateur.verifier_jeton(entete[7:].strip())


#: Alias de dépendances utilisés en signature des routes.
DepUtilisateur = Annotated[Utilisateur, Depends(obtenir_utilisateur)]
DepStore = Annotated[Store, Depends(obtenir_store)]
DepAudit = Annotated[AuditSink, Depends(obtenir_audit)]
DepReglages = Annotated[ReglagesStore, Depends(obtenir_reglages)]
DepPortCampagnes = Annotated[CampagnePort, Depends(obtenir_port_campagnes)]
DepPortRapports = Annotated[RapportPort, Depends(obtenir_port_rapports)]
DepPortChat = Annotated[ChatPort, Depends(obtenir_port_chat)]
