"""Authentification et gestion des rôles pour le plan de contrôle (EF-POR-01).

Définit les rôles du §6, l'entité `Utilisateur`, le vérificateur JWT Cognito
(réellement implémenté mais jamais appelé par les tests) et le mode démo qui
lit les en-têtes `X-Jauge-*` (EF-POR-02 : compte de démonstration pour les juges).
"""

from __future__ import annotations

import base64
import json
import os
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.errors import JaugeError, ValidationError


class AuthentificationRequiseError(JaugeError):
    """Aucun jeton d'authentification exploitable (401)."""

    code = "unauthenticated"
    statut_http = 401


class RoleUtilisateur(StrEnum):
    """Rôles définis au §6 du cahier des charges."""

    ADMINISTRATEUR_PLATEFORME = "administrateur_plateforme"
    ANALYSTE_FINOPS = "analyste_finops"
    RESPONSABLE_COMPTE = "responsable_compte"
    RESPONSABLE_APPLICATION = "responsable_application"
    RELECTEUR = "relecteur"
    DIRECTEUR = "directeur"


#: Rôles appartenant au MSP (visibilité transverse, sous conditions).
ROLES_MSP = frozenset(
    {
        RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
        RoleUtilisateur.ANALYSTE_FINOPS,
        RoleUtilisateur.RESPONSABLE_COMPTE,
    }
)


class Utilisateur(BaseModel):
    """Contexte d'authentification d'un appelant de l'API."""

    id: str
    email: str = ""
    role: RoleUtilisateur = RoleUtilisateur.DIRECTEUR
    tenant_id: str | None = None
    tenants_autorises: list[str] = Field(default_factory=list)
    nom: str = ""
    scopes: list[str] = Field(default_factory=list)

    @property
    def est_msp(self) -> bool:
        """Indique si l'utilisateur appartient à l'organisation MSP."""
        return self.role in ROLES_MSP

    @property
    def est_admin(self) -> bool:
        """Indique si l'utilisateur est administrateur de plateforme."""
        return self.role == RoleUtilisateur.ADMINISTRATEUR_PLATEFORME


class VerificateurAuth(Protocol):
    """Port de vérification d'un jeton d'authentification."""

    def verifier_jeton(self, jeton: str) -> Utilisateur:
        """Valide le jeton et rend l'utilisateur associé."""
        ...


class CognitoJwksVerifier:
    """Vérificateur de jetons Cognito.

    Avec un `user_pool_id`, la vérification est complète : signature RS256 contre
    le JWKS du groupe d'utilisateurs, puis contrôle `iss`/`aud`/`exp` (voir
    `jauge.api.jwks`). Sans `user_pool_id` (mode démo, tests), seuls les claims
    sont lus : aucun appel réseau n'est effectué.

    Les tests n'appellent jamais le chemin JWKS (règle : aucun réseau en test) ;
    les briques pures (claims, en-tête, signature sur clé locale) sont testées
    séparément dans `tests/api/test_jwks.py`.
    """

    def __init__(
        self, user_pool_id: str = "", region: str = "eu-west-1", client_id: str = ""
    ) -> None:
        self.user_pool_id = user_pool_id
        self.region = region
        self.client_id = client_id

    def verifier_jeton(self, jeton: str) -> Utilisateur:
        """Décode et, si un groupe d'utilisateurs est configuré, valide le jeton."""
        if self.user_pool_id:
            from jauge.api.jwks import VerificateurCognito

            claims = VerificateurCognito(self.user_pool_id, self.region, self.client_id).verifier(
                jeton
            )
            return _utilisateur_depuis_claims(claims)
        return _utilisateur_depuis_claims(_decoder_claims(jeton))


def _decoder_claims(jeton: str) -> dict[str, object]:
    """Décode la partie « payload » d'un JWT sans vérifier la signature."""
    parties = jeton.split(".")
    if len(parties) != 3:
        raise AuthentificationRequiseError("Jeton JWT malformé")
    payload_b64 = parties[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthentificationRequiseError("Jeton JWT illisible") from exc
    if not isinstance(claims, dict):
        raise AuthentificationRequiseError("Jeton JWT sans claims exploitables")
    return claims


def _utilisateur_depuis_claims(claims: dict[str, object]) -> Utilisateur:
    """Traduit les claims Cognito en `Utilisateur` typé."""
    role_brut = str(claims.get("custom:role") or RoleUtilisateur.DIRECTEUR.value)
    try:
        role = RoleUtilisateur(role_brut)
    except ValueError as exc:
        raise AuthentificationRequiseError(f"Rôle inconnu : {role_brut}") from exc
    tenants = claims.get("custom:tenants") or []
    return Utilisateur(
        id=str(claims.get("sub") or "inconnu"),
        email=str(claims.get("email") or ""),
        role=role,
        tenant_id=(str(claims["custom:tenant_id"]) if claims.get("custom:tenant_id") else None),
        tenants_autorises=[str(t) for t in tenants] if isinstance(tenants, list) else [],
        nom=str(claims.get("name") or ""),
    )


#: Compte de démonstration exposé aux juges (EF-POR-02).
COMPTE_DEMO_JUGES = Utilisateur(
    id="juge-hackathon",
    email="juge@demo.jauge.local",
    role=RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    tenant_id="t-demo-a",
    tenants_autorises=["t-demo-a", "t-demo-b"],
    nom="Juge du hackathon (compte de démonstration)",
)


def compte_demonstration_juges() -> Utilisateur:
    """Rend une copie du compte de démonstration pour les juges (EF-POR-02)."""
    return COMPTE_DEMO_JUGES.model_copy(deep=True)


def extraire_utilisateur_demo(
    tenant_header: str | None = None,
    role_header: str | None = None,
    user_header: str | None = None,
) -> Utilisateur:
    """Résout l'utilisateur en mode démo depuis les en-têtes `X-Jauge-*`.

    Sans en-tête, le compte de démonstration des juges est utilisé : le portail
    fonctionne donc sans configuration (EF-POR-02).
    """
    defaut = COMPTE_DEMO_JUGES
    role = defaut.role
    if role_header:
        try:
            role = RoleUtilisateur(role_header)
        except ValueError as erreur:
            # Fail-closed : un rôle inconnu ne retombe PAS sur le compte de
            # démonstration (administrateur de plateforme, tous clients). Un
            # repli silencieux transformerait une faute de frappe en escalade
            # de privilèges (ENF-SEC-02).
            raise ValidationError(
                "Rôle inconnu en mode démonstration",
                role_fourni=role_header,
                roles_valides=[r.value for r in RoleUtilisateur],
            ) from erreur
    tenant_final = tenant_header if tenant_header is not None else defaut.tenant_id
    return Utilisateur(
        id=user_header or defaut.id,
        email=f"{user_header or defaut.id}@demo.jauge.local",
        role=role,
        tenant_id=tenant_final,
        # Le portefeuille du compte de démo est réduit au client qu'il incarne :
        # un utilisateur du client A ne peut pas atteindre les données du client B
        # (ENF-SEC-02). L'administrateur de plateforme, lui, voit tout.
        tenants_autorises=[tenant_final] if tenant_final else [],
        nom="Utilisateur de démonstration",
    )


def mode_demo_actif() -> bool:
    """Indique si l'environnement active le mode démo (`JAUGE_MODE_DEMO`)."""
    return os.getenv("JAUGE_MODE_DEMO", "1").strip().lower() in {"1", "true", "yes", "oui"}
