"""Tests des briques de vérification JWKS — sans aucun appel réseau.

La clé RSA est générée localement : la signature est réellement produite puis
vérifiée, ce qui prouve que l'implémentation n'est pas un simple décodage.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jauge.api.auth import AuthentificationRequiseError
from jauge.api.jwks import (
    _base64url_vers_entier,
    decoder_entete,
    verifier_claims,
    verifier_signature,
)

ISSUER = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_DEMO"
CLIENT = "client-demo"


def _b64url(brut: bytes) -> str:
    """Encode en base64url sans padding (format JWT)."""
    return base64.urlsafe_b64encode(brut).decode().rstrip("=")


def _signer(cle_privee: rsa.RSAPrivateKey, claims: dict[str, object]) -> str:
    """Fabrique un JWT RS256 signé avec la clé fournie."""
    entete = _b64url(json.dumps({"alg": "RS256", "kid": "cle-1"}).encode())
    charge = _b64url(json.dumps(claims).encode())
    signature = cle_privee.sign(f"{entete}.{charge}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{entete}.{charge}.{_b64url(signature)}"


@pytest.fixture(scope="module")
def cle_privee() -> rsa.RSAPrivateKey:
    """Paire de clés RSA générée localement (aucun secret réel)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _claims_valides() -> dict[str, object]:
    """Claims Cognito d'un jeton valide."""
    return {
        "sub": "cognito-1",
        "iss": ISSUER,
        "aud": CLIENT,
        "token_use": "id",
        "exp": int(time.time()) + 600,
    }


def test_signature_valide_acceptee(cle_privee: rsa.RSAPrivateKey) -> None:
    """Une signature RS256 correcte passe la vérification."""
    verifier_signature(_signer(cle_privee, _claims_valides()), cle_privee.public_key())


def test_signature_alteree_refusee(cle_privee: rsa.RSAPrivateKey) -> None:
    """Modifier la charge invalide la signature : le jeton est refusé."""
    jeton = _signer(cle_privee, _claims_valides())
    entete, _, signature = jeton.split(".")
    charge_falsifiee = _b64url(json.dumps({"sub": "pirate"}).encode())
    with pytest.raises(AuthentificationRequiseError):
        verifier_signature(f"{entete}.{charge_falsifiee}.{signature}", cle_privee.public_key())


def test_signature_autre_cle_refusee(cle_privee: rsa.RSAPrivateKey) -> None:
    """Un jeton signé par une autre clé est refusé."""
    autre = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jeton = _signer(autre, _claims_valides())
    with pytest.raises(AuthentificationRequiseError):
        verifier_signature(jeton, cle_privee.public_key())


def test_claims_valides_acceptes() -> None:
    """`iss`, `aud` et `exp` conformes : aucun refus."""
    verifier_claims(_claims_valides(), ISSUER, CLIENT)


@pytest.mark.parametrize(
    "mutation",
    [
        {"iss": "https://cognito-idp.eu-west-1.amazonaws.com/autre"},
        {"aud": "autre-client"},
        {"exp": int(time.time()) - 3600},
        {"exp": None},
        {"token_use": "refresh"},
    ],
)
def test_claims_invalides_refuses(mutation: dict[str, object]) -> None:
    """Chaque claim hors contrat provoque un refus explicite."""
    claims = {**_claims_valides(), **mutation}
    with pytest.raises(AuthentificationRequiseError):
        verifier_claims(claims, ISSUER, CLIENT)


def test_entete_decode(cle_privee: rsa.RSAPrivateKey) -> None:
    """L'en-tête donne l'algorithme et l'identifiant de clé."""
    entete = decoder_entete(_signer(cle_privee, _claims_valides()))
    assert entete["alg"] == "RS256"
    assert entete["kid"] == "cle-1"


def test_entete_malforme_refuse() -> None:
    """Un jeton sans trois segments est refusé."""
    with pytest.raises(AuthentificationRequiseError):
        decoder_entete("pas-un-jwt")


def test_decodage_entier_jwk() -> None:
    """Le module RSA d'un JWK se décode comme un entier non signé."""
    assert _base64url_vers_entier(_b64url((65537).to_bytes(3, "big"))) == 65537
