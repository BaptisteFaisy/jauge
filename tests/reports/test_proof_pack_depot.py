"""Tests de l'archivage scellé : port, double hors ligne, adaptateur S3 Object Lock.

Aucun appel AWS : l'adaptateur S3 reçoit un client fictif injecté, et le double
en mémoire reproduit le contrat d'écriture seule. Ces tests prouvent aussi qu'un
pack cassé ou invérifiable n'est **jamais** publié.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from tests.reports import jeu_proof_pack as jeu

from jauge.common.errors import ServiceIndisponibleError, ValidationError
from jauge.reports.proof_pack import ProofPack
from jauge.reports.proof_pack_depot import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_MARKDOWN,
    PREFIXE_DEFAUT,
    DepotMemoireScelle,
    DepotS3Scelle,
    cle_constat,
    cle_pack,
    publier_pack,
)
from jauge.reports.proof_pack_rendu import rendre_json_pack, rendre_markdown_pack
from jauge.reports.proof_pack_verification import rejouer_verifications
from jauge.reports.scellement import (
    SignataireHMAC,
    verifier_chaine,
    verifier_constat,
    verifier_signature,
)

SECRET = b"secret-de-demonstration"


class ClientS3Fictif:
    """Double hors ligne du client S3 : enregistre les appels, jamais de réseau."""

    def __init__(self, *, echoue: bool = False) -> None:
        self.appels: list[dict] = []
        self.echoue = echoue

    def put_object(self, **parametres: object) -> dict:
        """Simule l'écriture d'un objet ; peut échouer pour tester la remontée."""
        if self.echoue:
            raise RuntimeError("panne simulée du compartiment")
        self.appels.append(parametres)
        return {"ETag": '"factice"'}


@pytest.fixture
def pack() -> ProofPack:
    """Pack nominal du jeu d'essai."""
    return jeu.construire_pack()


@pytest.fixture
def depot() -> DepotMemoireScelle:
    """Double hors ligne du dépôt scellé."""
    return DepotMemoireScelle()


# --- Clés d'archivage --------------------------------------------------------


def test_cles_deterministes_et_cloisonnees(pack: ProofPack) -> None:
    """La clé d'archivage dépend du client et de la campagne, pas du hasard."""
    assert cle_pack(pack, "json") == (
        f"{PREFIXE_DEFAUT}/{jeu.CLIENT_ID}/{jeu.CAMPAGNE_ID}/proof-pack.json"
    )
    assert cle_pack(pack, "md") == (
        f"{PREFIXE_DEFAUT}/{jeu.CLIENT_ID}/{jeu.CAMPAGNE_ID}/proof-pack.md"
    )
    assert cle_constat(pack) == (
        f"{PREFIXE_DEFAUT}/{jeu.CLIENT_ID}/{jeu.CAMPAGNE_ID}/attestation.json"
    )
    assert cle_pack(pack, "json", "autre-prefixe").startswith("autre-prefixe/")
    with pytest.raises(ValidationError, match="extension inconnue"):
        cle_pack(pack, "txt")


# --- Double hors ligne -------------------------------------------------------


def test_depot_memoire_ecrit_une_seule_fois(depot: DepotMemoireScelle) -> None:
    """Écriture seule : une clé déjà scellée ne peut plus être remplacée."""
    cle = depot.deposer_scelle("preuve/x.json", "{}", CONTENT_TYPE_JSON)
    assert cle == "preuve/x.json"
    assert depot.lire(cle) == "{}"
    assert depot.content_type(cle) == CONTENT_TYPE_JSON
    with pytest.raises(ValidationError, match="écriture seule"):
        depot.deposer_scelle(cle, '{"falsifie": true}', CONTENT_TYPE_JSON)
    assert depot.tentatives_ecrasement == [cle]
    assert depot.lire(cle) == "{}"  # la première écriture tient


def test_depot_memoire_lecture_inconnue(depot: DepotMemoireScelle) -> None:
    """Lire une clé absente est une erreur métier explicite."""
    with pytest.raises(ValidationError, match="absent du dépôt"):
        depot.lire("preuve/inexistant.json")


# --- Publication -------------------------------------------------------------


def test_publier_ecrit_json_markdown_et_constat(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Les trois objets d'un dossier sont archivés, avec leurs types MIME."""
    cles = publier_pack(pack, depot)
    assert cles == [cle_pack(pack, "json"), cle_pack(pack, "md"), cle_constat(pack)]
    assert depot.content_type(cles[0]) == CONTENT_TYPE_JSON
    assert depot.content_type(cles[1]) == CONTENT_TYPE_MARKDOWN
    assert depot.content_type(cles[2]) == CONTENT_TYPE_JSON
    assert depot.lire(cles[0]) == rendre_json_pack(pack)
    assert "## 12. Replayed verifications" in depot.lire(cles[1])


def test_publication_sans_table_de_verifications(
    pack: ProofPack, depot: DepotMemoireScelle
) -> None:
    """Le Markdown archivé peut rester sans la section de rejeu."""
    cles = publier_pack(pack, depot, inclure_verifications=False)
    assert "## 12." not in depot.lire(cles[1])
    assert depot.lire(cles[1]) == rendre_markdown_pack(pack)


def test_objet_publie_reverifiable_par_un_tiers(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Ce qui est archivé se relit et se revérifie sans le pack d'origine."""
    cles = publier_pack(pack, depot)
    document = json.loads(depot.lire(cles[0]))
    assert verifier_chaine(document).conforme
    assert rejouer_verifications(document).conforme
    assert ProofPack.depuis_document(document).empreinte_pack == pack.empreinte_pack


def test_constat_detache_ancre_l_empreinte(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Le constat publié à côté du pack correspond bien au pack archivé."""
    cles = publier_pack(pack, depot)
    constat = json.loads(depot.lire(cles[2]))
    assert constat["pack_id"] == pack.id
    assert constat["empreinte_pack"] == pack.empreinte_pack
    assert constat["auteur"] == jeu.APPROBATEUR
    assert constat["horodatage"] == jeu.HORODATAGE_APPROBATION
    assert verifier_constat(constat, json.loads(depot.lire(cles[0])))


def test_publication_refusee_si_chaine_cassee(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Une preuve modifiée après scellement n'est jamais archivée."""
    pack.sections["economie"]["volume_mensuel"] = 1
    pack.scellement = dict(pack.scellement)  # sceau inchangé → chaîne cassée
    with pytest.raises(ValidationError, match="chaîne de hachage cassée"):
        publier_pack(pack, depot)
    assert depot.objets == {}


def test_publication_refusee_si_rejeu_en_echec(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Une preuve dont les chiffres ne se recalculent pas n'est pas archivée."""
    from jauge.reports.proof_pack import construire_proof_pack

    incoherent = construire_proof_pack(jeu.entrees_preuve())
    # On casse la cohérence puis on rescelle : la chaîne tient, le rejeu non.
    incoherent.sections["recommandation"]["sampleSize"] = 999
    from jauge.reports.scellement import sceller

    incoherent.scellement = sceller(incoherent.sections_ordonnees())
    assert verifier_chaine(incoherent).conforme
    assert not rejouer_verifications(incoherent).conforme
    with pytest.raises(ValidationError, match="rejeu des vérifications en échec"):
        publier_pack(incoherent, depot)
    assert depot.objets == {}
    assert publier_pack(incoherent, depot, verifier=False)  # publication assumée


def test_publication_signee_exige_la_bonne_cle(pack: ProofPack, depot: DepotMemoireScelle) -> None:
    """Avec un signataire, la signature est contrôlée avant publication."""
    from jauge.reports.proof_pack import construire_proof_pack

    signataire = SignataireHMAC(secret=SECRET, cle_id="msp-2026")
    signe = construire_proof_pack(jeu.entrees_preuve(), signataire=signataire)
    assert verifier_signature(signe.scellement, signataire)
    cles = publier_pack(signe, depot, signataire=signataire)
    assert len(cles) == 3
    with pytest.raises(ValidationError, match="signature du pack invalide"):
        publier_pack(signe, DepotMemoireScelle(), signataire=SignataireHMAC(b"autre", "msp-2026"))


def test_prefixe_du_depot_respecte(pack: ProofPack) -> None:
    """Le préfixe appartient au dépôt (un client = un compartiment)."""
    depot = DepotMemoireScelle(prefixe="scelle/client-a")
    cles = publier_pack(pack, depot)
    assert all(cle.startswith("scelle/client-a/") for cle in cles)


# --- Adaptateur S3 (aucun appel réseau) --------------------------------------


def test_s3_client_non_cree_a_la_construction() -> None:
    """boto3 n'est importé qu'au premier appel : la construction ne touche rien."""
    depot = DepotS3Scelle("compartiment-scelle")
    assert depot._client is None
    assert depot.bucket == "compartiment-scelle"


def test_s3_pose_le_verrou_et_le_chiffrement() -> None:
    """Object Lock : garde juridique activée et chiffrement KMS (ENF-SEC-03)."""
    client = ClientS3Fictif()
    depot = DepotS3Scelle("compartiment-scelle", client=client)
    cle = depot.deposer_scelle("preuve/pack.json", "{}", CONTENT_TYPE_JSON)
    assert cle == "preuve/pack.json"
    assert len(client.appels) == 1
    appel = client.appels[0]
    assert appel["Bucket"] == "compartiment-scelle"
    assert appel["Key"] == "preuve/pack.json"
    assert appel["Body"] == b"{}"
    assert appel["ContentType"] == CONTENT_TYPE_JSON
    assert appel["ServerSideEncryption"] == "aws:kms"
    assert appel["ObjectLockLegalHoldStatus"] == "ON"
    assert "ObjectLockMode" not in appel  # aucune rétention inventée


def test_s3_retention_datée_si_demandee() -> None:
    """La rétention en mode COMPLIANCE est un paramètre contractuel explicite."""
    client = ClientS3Fictif()
    depot = DepotS3Scelle("compartiment-scelle", client=client, duree_retention_jours=3650)
    depot.deposer_scelle("preuve/pack.json", "{}", CONTENT_TYPE_JSON)
    appel = client.appels[0]
    assert appel["ObjectLockMode"] == "COMPLIANCE"
    echeance = appel["ObjectLockRetainUntilDate"]
    assert isinstance(echeance, datetime)
    assert echeance.year >= datetime.now().year


def test_s3_panne_remontee_en_indisponibilite() -> None:
    """Une panne d'archivage devient une erreur métier, pas une exception nue."""
    depot = DepotS3Scelle("compartiment-scelle", client=ClientS3Fictif(echoue=True))
    with pytest.raises(ServiceIndisponibleError, match="Archivage scellé S3 impossible"):
        depot.deposer_scelle("preuve/pack.json", "{}", CONTENT_TYPE_JSON)


def test_s3_publication_complete_sur_client_fictif(pack: ProofPack) -> None:
    """Le parcours complet de publication fonctionne sur le double S3 fictif."""
    client = ClientS3Fictif()
    cles = publier_pack(pack, DepotS3Scelle("compartiment-scelle", client=client))
    assert len(cles) == 3
    assert len(client.appels) == 3
    assert {appel["Key"] for appel in client.appels} == set(cles)
    assert all(appel["ObjectLockLegalHoldStatus"] == "ON" for appel in client.appels)
