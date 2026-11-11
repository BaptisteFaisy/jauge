"""Vérification des jetons Cognito contre le JWKS du groupe d'utilisateurs.

Implémentation réelle de la vérification RS256 (signature, `iss`, `aud`, `exp`)
avec `cryptography` et `httpx`, tous deux présents dans l'environnement. Aucun
test ne l'appelle : les tests tournent en mode démo, sans réseau (règle du brief).

Besoin de socle signalé dans la PR : `cryptography` n'est pas une dépendance
déclarée de `pyproject.toml` (elle arrive par transitivité). Le jour où le plan
de contrôle tournera hors mode démo, il faudra l'ajouter explicitement.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

from jauge.api.auth import AuthentificationRequiseError

#: Marge de tolérance sur l'expiration (horloges serveur légèrement décalées).
MARGE_EXPIRATION_S = 60
#: Durée de cache des clés publiques avant relecture du JWKS.
DUREE_CACHE_JWKS_S = 3600


@dataclass
class _CacheJwks:
    """Cache des clés publiques par identifiant de clé (`kid`)."""

    url: str
    cles: dict[str, Any] = field(default_factory=dict)
    charge_le: float = 0.0

    def expire(self) -> bool:
        """Indique si le cache doit être rechargé."""
        return (time.time() - self.charge_le) > DUREE_CACHE_JWKS_S


def _b64url_vers_bytes(valeur: str) -> bytes:
    """Décode une chaîne base64url non paddée (usage JWT/JWK)."""
    return base64.urlsafe_b64decode(valeur + "=" * (-len(valeur) % 4))


def _base64url_vers_entier(valeur: str) -> int:
    """Décode un entier base64url non paddé (format JWK pour `n` et `e`)."""
    return int.from_bytes(_b64url_vers_bytes(valeur), "big")


def _cle_publique(jwk: dict[str, str]) -> Any:
    """Reconstruit une clé publique RSA depuis un JWK (module, exposant)."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError as exc:  # pragma: no cover - dépendance de production
        raise AuthentificationRequiseError(
            "Vérification JWKS indisponible : module cryptography absent"
        ) from exc
    return rsa.RSAPublicNumbers(
        _base64url_vers_entier(jwk["e"]), _base64url_vers_entier(jwk["n"])
    ).public_key()


def _charger_cles(url: str) -> dict[str, Any]:
    """Télécharge le JWKS et rend les clés publiques indexées par `kid`."""
    import httpx

    reponse = httpx.get(url, timeout=5.0)
    reponse.raise_for_status()
    return {
        jwk["kid"]: _cle_publique(jwk)
        for jwk in reponse.json().get("keys", [])
        if jwk.get("kty") == "RSA" and "kid" in jwk
    }


def verifier_signature(jeton: str, cle: Any) -> None:
    """Vérifie la signature RS256 du jeton ; lève si elle est invalide."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    entete, charge, signature = jeton.split(".")
    try:
        cle.verify(
            _b64url_vers_bytes(signature),
            f"{entete}.{charge}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise AuthentificationRequiseError("Signature du jeton invalide") from exc


def verifier_claims(
    claims: dict[str, Any], issuer: str, client_id: str, maintenant: float | None = None
) -> None:
    """Contrôle `iss`, `aud`/`client_id` et `exp` d'un jeton Cognito."""
    instant = time.time() if maintenant is None else maintenant
    if issuer and claims.get("iss") != issuer:
        raise AuthentificationRequiseError("Émetteur du jeton inattendu")
    aud = claims.get("aud") or claims.get("client_id")
    if client_id and aud != client_id:
        raise AuthentificationRequiseError("Jeton destiné à une autre application")
    expiration = claims.get("exp")
    if not isinstance(expiration, (int, float)) or expiration + MARGE_EXPIRATION_S < instant:
        raise AuthentificationRequiseError("Jeton expiré")
    if claims.get("token_use") not in (None, "id", "access"):
        raise AuthentificationRequiseError("Type de jeton inattendu")


def decoder_entete(jeton: str) -> dict[str, str]:
    """Décode l'en-tête JWT (algorithme, identifiant de clé)."""
    parties = jeton.split(".")
    if len(parties) != 3:
        raise AuthentificationRequiseError("Jeton JWT malformé")
    try:
        entete = json.loads(_b64url_vers_bytes(parties[0]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthentificationRequiseError("En-tête JWT illisible") from exc
    if not isinstance(entete, dict):
        raise AuthentificationRequiseError("En-tête JWT sans champs exploitables")
    return entete


class VerificateurCognito:
    """Vérifie un jeton Cognito contre le JWKS du groupe d'utilisateurs."""

    def __init__(
        self,
        user_pool_id: str,
        region: str = "eu-west-1",
        client_id: str = "",
    ) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        self._cache = _CacheJwks(url=f"{self.issuer}/.well-known/jwks.json")

    def _cle(self, kid: str) -> Any:
        """Rend la clé publique du `kid`, en rechargeant le cache si besoin."""
        if kid not in self._cache.cles or self._cache.expire():
            self._cache.cles = _charger_cles(self._cache.url)
            self._cache.charge_le = time.time()
        if kid not in self._cache.cles:
            raise AuthentificationRequiseError(f"Clé publique inconnue : {kid}")
        return self._cache.cles[kid]

    def verifier(self, jeton: str) -> dict[str, Any]:
        """Vérifie signature et claims, puis rend les claims du jeton."""
        entete = decoder_entete(jeton)
        if entete.get("alg") != "RS256":
            raise AuthentificationRequiseError("Algorithme de signature non supporté")
        kid = entete.get("kid")
        if not kid:
            raise AuthentificationRequiseError("Jeton sans identifiant de clé")
        verifier_signature(jeton, self._cle(kid))
        claims = json.loads(_b64url_vers_bytes(jeton.split(".")[1]))
        verifier_claims(claims, self.issuer, self.client_id)
        return claims
