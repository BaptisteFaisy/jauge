"""Archivage scellé du dossier de preuve : port, double hors ligne, S3 Object Lock.

Le pack n'a de valeur d'audit que s'il est **inaltérable une fois publié** :
c'est le rôle du dépôt. Trois éléments, comme pour les rapports mensuels
(`jauge.reports.storage`) :

- `DepotProofPack` — le port : une seule opération, `deposer_scelle`, qui écrit
  un objet et refuse toute réécriture ;
- `DepotMemoireScelle` — le double hors ligne, utilisé par les tests et la
  démonstration : même contrat d'immuabilité, aucun appel AWS ;
- `DepotS3Scelle` — l'adaptateur réel : compartiment en **S3 Object Lock**
  (verrou juridique activé à l'écriture, rétention optionnelle en mode
  COMPLIANCE), chiffrement KMS côté serveur (ENF-SEC-03). boto3 est importé
  paresseusement : aucun réseau à l'import, aucun appel en test.

`publier_pack` écrit les trois objets d'un dossier : le JSON, le Markdown et le
**constat détaché** (l'empreinte du pack, à conserver ailleurs que le pack pour
qu'une réécriture complète soit détectable). Il refuse de publier un pack dont
la chaîne est cassée ou dont le rejeu des vérifications échoue : publier une
preuve invérifiable serait pire que ne rien publier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from jauge.common.errors import ServiceIndisponibleError, ValidationError
from jauge.reports.proof_pack import ProofPack
from jauge.reports.proof_pack_rendu import rendre_json_pack, rendre_markdown_pack
from jauge.reports.proof_pack_verification import ResultatRejeu, rejouer_verifications
from jauge.reports.scellement import (
    Signataire,
    constat_detache,
    serialiser_canonique,
    verifier_chaine,
    verifier_signature,
)

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_MARKDOWN = "text/markdown; charset=utf-8"
PREFIXE_DEFAUT = "proof-packs"
EXTENSIONS = ("json", "md")


def cle_pack(pack: ProofPack, extension: str, prefixe: str = PREFIXE_DEFAUT) -> str:
    """Clé d'archivage déterministe : `<prefixe>/<client>/<campagne>/proof-pack.<ext>`."""
    if extension not in EXTENSIONS:
        raise ValidationError("extension inconnue pour un dossier de preuve", extension=extension)
    identite = pack.section("identite")
    return (
        f"{prefixe}/{identite.get('client_id')}/{identite.get('campagne_id')}"
        f"/proof-pack.{extension}"
    )


def cle_constat(pack: ProofPack, prefixe: str = PREFIXE_DEFAUT) -> str:
    """Clé du constat détaché, publiée à côté du pack (ancre externe)."""
    identite = pack.section("identite")
    return f"{prefixe}/{identite.get('client_id')}/{identite.get('campagne_id')}/attestation.json"


class DepotProofPack(Protocol):
    """Port d'archivage en écriture seule d'un dossier de preuve.

    Le préfixe appartient au dépôt : deux clients peuvent archiver dans deux
    compartiments aux arborescences différentes.
    """

    prefixe: str

    def deposer_scelle(self, cle: str, contenu: str, content_type: str) -> str:
        """Écrit un objet de façon définitive et rend sa clé."""
        ...


class DepotMemoireScelle:
    """Double hors ligne : immuabilité réelle, aucun service AWS.

    Une clé déjà écrite ne peut plus être remplacée — c'est le comportement d'un
    compartiment en Object Lock mode conformité, reproduit en mémoire pour les
    tests et la démonstration.
    """

    def __init__(self, prefixe: str = PREFIXE_DEFAUT) -> None:
        self.prefixe = prefixe
        self.objets: dict[str, tuple[str, str]] = {}
        self.tentatives_ecrasement: list[str] = []

    def deposer_scelle(self, cle: str, contenu: str, content_type: str) -> str:
        """Range l'objet une fois pour toutes ; refuse tout écrasement."""
        if cle in self.objets:
            self.tentatives_ecrasement.append(cle)
            raise ValidationError("objet déjà scellé : le dépôt est en écriture seule", cle=cle)
        self.objets[cle] = (contenu, content_type)
        return cle

    def lire(self, cle: str) -> str:
        """Relit un objet archivé (utile aux tests et à la démonstration)."""
        try:
            return self.objets[cle][0]
        except KeyError as erreur:
            raise ValidationError("objet absent du dépôt scellé", cle=cle) from erreur

    def content_type(self, cle: str) -> str:
        """Type MIME de l'objet archivé."""
        return self.objets[cle][1]


class DepotS3Scelle:
    """Dépôt S3 réel en écriture seule (Object Lock), boto3 importé paresseusement.

    `duree_retention_jours` reste à `None` par défaut : seule la **garde
    juridique** (legal hold) est posée, ce qui interdit déjà toute suppression
    ou réécriture sans action explicite. La rétention datée, en mode
    `COMPLIANCE`, est un paramètre contractuel : c'est l'appelant qui la fixe,
    jamais ce module.
    """

    def __init__(
        self,
        bucket: str,
        prefixe: str = PREFIXE_DEFAUT,
        client: Any | None = None,
        *,
        duree_retention_jours: int | None = None,
        mode_retention: str = "COMPLIANCE",
    ) -> None:
        self.bucket = bucket
        self.prefixe = prefixe
        self.duree_retention_jours = duree_retention_jours
        self.mode_retention = mode_retention
        self._client = client

    def _client_s3(self) -> Any:
        """Client S3, créé à la première utilisation (jamais à l'import)."""
        if self._client is None:
            import boto3  # import paresseux : aucun réseau à l'import

            self._client = boto3.client("s3")
        return self._client

    def _parametres_verrou(self) -> dict[str, Any]:
        """Paramètres Object Lock : garde juridique, plus rétention si demandée."""
        parametres: dict[str, Any] = {"ObjectLockLegalHoldStatus": "ON"}
        if self.duree_retention_jours is not None:
            echeance = datetime.now(UTC) + timedelta(days=self.duree_retention_jours)
            parametres["ObjectLockMode"] = self.mode_retention
            parametres["ObjectLockRetainUntilDate"] = echeance
        return parametres

    def deposer_scelle(self, cle: str, contenu: str, content_type: str) -> str:
        """Dépose l'objet chiffré (ENF-SEC-03) et verrouillé en écriture seule."""
        try:
            self._client_s3().put_object(
                Bucket=self.bucket,
                Key=cle,
                Body=contenu.encode("utf-8"),
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
                **self._parametres_verrou(),
            )
        except Exception as erreur:  # toute panne d'archivage est remontée en 502
            raise ServiceIndisponibleError(
                f"Archivage scellé S3 impossible ({self.bucket}/{cle}) : {erreur}", cle=cle
            ) from erreur
        return cle


def _controler_avant_publication(
    pack: ProofPack, signataire: Signataire | None, verifier: bool
) -> ResultatRejeu:
    """Refuse de publier un pack cassé, mal signé ou invérifiable."""
    chaine = verifier_chaine(pack)
    if not chaine.conforme:
        raise ValidationError(
            "chaîne de hachage cassée : publication refusée",
            pack_id=pack.id,
            position=chaine.position,
            section=chaine.section,
        )
    if signataire is not None and not verifier_signature(pack.scellement, signataire):
        raise ValidationError("signature du pack invalide : publication refusée", pack_id=pack.id)
    if not verifier:
        return ResultatRejeu()
    resultat = rejouer_verifications(pack)
    if not resultat.conforme:
        raise ValidationError(
            "rejeu des vérifications en échec : publication refusée",
            pack_id=pack.id,
            echecs=[item.nom for item in resultat.echecs],
        )
    return resultat


def publier_pack(
    pack: ProofPack,
    depot: DepotProofPack,
    *,
    signataire: Signataire | None = None,
    verifier: bool = True,
    inclure_verifications: bool = True,
) -> list[str]:
    """Archive le pack (JSON + Markdown) et son constat détaché, puis rend les clés.

    Le constat détaché ne contient que l'empreinte du pack, son identifiant,
    l'horodatage et l'auteur de l'approbation : c'est l'ancre externe qui permet
    à un tiers de détecter une réécriture **complète** du dossier.
    """
    resultat = _controler_avant_publication(pack, signataire, verifier)
    approbation = pack.section("approbation")
    constat = constat_detache(
        pack.id,
        pack.scellement,
        horodatage=str(
            approbation.get("horodatage_approbation") or approbation.get("pack_genere_le")
        ),
        auteur=str(approbation.get("approuve_par") or ""),
    )
    return [
        depot.deposer_scelle(
            cle_pack(pack, "json", depot.prefixe),
            rendre_json_pack(pack),
            CONTENT_TYPE_JSON,
        ),
        depot.deposer_scelle(
            cle_pack(pack, "md", depot.prefixe),
            rendre_markdown_pack(pack, resultat if inclure_verifications else None),
            CONTENT_TYPE_MARKDOWN,
        ),
        depot.deposer_scelle(
            cle_constat(pack, depot.prefixe), serialiser_canonique(constat), CONTENT_TYPE_JSON
        ),
    ]
