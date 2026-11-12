"""Tests du dossier de preuve : contenu, cohérence, confidentialité, scellement.

Le pack est construit hors ligne à partir du jeu d'essai
(`tests/reports/jeu_proof_pack.py`) : aucun store, aucun réseau, aucun AWS.
"""

from __future__ import annotations

import json

import pytest
from tests.reports import jeu_proof_pack as jeu

from jauge.common.errors import ValidationError
from jauge.models.campaign import Campagne
from jauge.models.tenant import Application, Tenant
from jauge.reports.proof_pack import (
    ORDRE_SECTIONS,
    VERSION_PROOF_PACK,
    EntreesPreuve,
    ProofPack,
    construire_proof_pack,
)
from jauge.reports.proof_pack_entrees import (
    EntreeCandidat,
    EntreeEchantillon,
    normaliser_protocole,
)
from jauge.reports.scellement import (
    SignataireHMAC,
    serialiser_canonique,
    verifier_chaine,
    verifier_signature,
)

SECRET = b"secret-de-demonstration"

# Fragments interdits dans un nom de champ : ils désigneraient une donnée de requête.
FRAGMENTS_INTERDITS = ("texte", "reponse", "prompt", "message", "contenu", "explication")


@pytest.fixture
def pack() -> ProofPack:
    """Pack de démonstration, scellé et non signé."""
    return jeu.construire_pack()


def test_les_neuf_sections_sont_presentes_dans_l_ordre(pack: ProofPack) -> None:
    """Le contenu exigé par la piste 6 tient dans neuf sections ordonnées."""
    assert list(pack.sections) == list(ORDRE_SECTIONS)
    assert pack.version == VERSION_PROOF_PACK
    assert pack.id.startswith("pp-")


def test_contenu_minimal_de_chaque_section(pack: ProofPack) -> None:
    """Chaque section porte ce qu'un auditeur doit y trouver."""
    identite = pack.section("identite")
    assert identite["campagne_id"] == jeu.CAMPAGNE_ID
    assert identite["modele_actuel"] == jeu.GRAND_MODELE
    assert identite["modeles_candidats"] == [jeu.PETIT_MODELE]
    protocole = pack.section("protocole")
    assert protocole["harnais"]["paquet"] == "jauge.replay.executor"
    assert protocole["juge"]["empreinte_invite_juge"]
    assert protocole["tarifs"]["date_reference"] == jeu.DATE_REFERENCE.isoformat()
    assert protocole["tarifs"]["empreinte"]
    assert len(protocole["tarifs"]["lignes"]) == len(jeu.tarifs())
    assert pack.section("echantillon")["taille"] == jeu.TAILLE_ECHANTILLON
    assert len(pack.section("resultats")["candidats"]) == 2
    assert pack.section("controles")["total_executions"] > 0
    assert pack.section("relecture")["accord_juge_humain"] is not None
    assert pack.section("economie")["mode_calcul"]
    assert pack.section("recommandation")["targetModel"] == jeu.PETIT_MODELE
    assert pack.section("approbation")["approuve_par"] == jeu.APPROBATEUR
    assert pack.section("approbation")["horodatage_approbation"] == jeu.HORODATAGE_APPROBATION


def test_construction_reproductible_a_entrees_identiques(pack: ProofPack) -> None:
    """Même contexte → même identifiant et même empreinte (traçabilité §16)."""
    autre = jeu.construire_pack()
    assert autre.id == pack.id
    assert autre.empreinte_pack == pack.empreinte_pack
    assert serialiser_canonique(autre.vers_dict()) == serialiser_canonique(pack.vers_dict())


def test_chaine_valide_des_la_construction(pack: ProofPack) -> None:
    """Le pack livré est scellé : sa chaîne vérifie sans intervention."""
    assert verifier_chaine(pack).conforme
    assert len(pack.scellement["sections"]) == len(ORDRE_SECTIONS)


def test_lecture_depuis_le_document_json(pack: ProofPack) -> None:
    """Un tiers relit le pack depuis son document JSON, sans objet Python."""
    document = json.loads(json.dumps(pack.vers_dict()))
    relu = ProofPack.depuis_document(document)
    assert relu.empreinte_pack == pack.empreinte_pack
    assert verifier_chaine(document).conforme
    with pytest.raises(ValidationError):
        ProofPack.depuis_document({"sections": "pas-un-dict"})


def test_signature_optionnelle_mais_verifiable() -> None:
    """Avec un signataire, l'empreinte finale est signée (HMAC std)."""
    signataire = SignataireHMAC(secret=SECRET, cle_id="msp-2026")
    pack = construire_proof_pack(jeu.entrees_preuve(), signataire=signataire)
    assert verifier_signature(pack.scellement, signataire)
    assert pack.scellement["signature"]["cle_id"] == "msp-2026"
    non_signe = jeu.construire_pack()
    assert "signature" not in non_signe.scellement


# --- Confidentialité (ENF-CONF-01) ------------------------------------------


def fragments_de_texte_interdits() -> list[str]:
    """Tous les fragments des textes du plan de données, par blocs de 12 caractères."""
    echantillon = list(jeu.TEXTES_PLAN_DE_DONNEES.values())[:6]
    reponses = list(jeu.REPONSES_PLAN_DE_DONNEES.values())[:6]
    fragments: list[str] = []
    for texte in echantillon + reponses:
        fragments.append(texte)
        fragments.extend(texte[depart : depart + 12] for depart in range(0, len(texte) - 11, 5))
    return fragments


def test_aucun_texte_de_requete_ni_de_reponse_dans_le_pack(pack: ProofPack) -> None:
    """Le pack sérialisé ne contient aucun fragment du plan de données."""
    rendu = serialiser_canonique(pack.vers_dict())
    fragments = fragments_de_texte_interdits()
    assert len(fragments) > 100  # le test n'est pas vide
    fuites = [fragment for fragment in fragments if fragment in rendu]
    assert fuites == []
    assert "CONFIDENTIEL-CUSTOMER" not in rendu
    assert "REPONSE-INTERNE" not in rendu
    assert "données personnelles" not in rendu


def test_aucun_nom_de_champ_ne_designe_un_texte(pack: ProofPack) -> None:
    """La liste des clés est close : aucun champ ne peut porter un texte."""
    cles: list[str] = []

    def parcourir(valeur: object) -> None:
        if isinstance(valeur, dict):
            for cle, sous in valeur.items():
                cles.append(str(cle))
                parcourir(sous)
        elif isinstance(valeur, list):
            for sous in valeur:
                parcourir(sous)

    parcourir(pack.vers_dict())
    fautives = sorted({cle for cle in cles for motif in FRAGMENTS_INTERDITS if motif in cle})
    assert fautives == []


def test_l_echantillon_ne_porte_que_des_empreintes(pack: ProofPack) -> None:
    """Chaque requête est identifiée et empreintée, jamais reproduite."""
    requetes = pack.section("echantillon")["requetes"]
    assert len(requetes) == jeu.TAILLE_ECHANTILLON
    premiere = requetes[0]
    assert set(premiere) == {
        "requete_id",
        "empreinte",
        "type_requete",
        "longueur_caracteres",
        "jetons_entree_origine",
        "strate",
    }
    assert premiere["requete_id"] in jeu.TEXTES_PLAN_DE_DONNEES
    assert premiere["empreinte"] == jeu.empreinte_texte(
        jeu.TEXTES_PLAN_DE_DONNEES[premiere["requete_id"]]
    )
    assert len(premiere["empreinte"]) == 64


def test_echantillon_sans_empreinte_de_texte_reste_publiable() -> None:
    """Sans empreinte fournie par le plan de données, le champ reste vide."""
    entrees = [
        EntreeEchantillon(requete_id=f"req-{index:04d}", strate="court:general")
        for index in range(12)
    ]
    pack = jeu.construire_pack(echantillon=entrees)
    assert pack.section("echantillon")["requetes"][0]["empreinte"] == ""
    assert verifier_chaine(pack).conforme


# --- Refus de cohérence (ENF-SEC-02) ----------------------------------------


def test_client_du_dossier_different_refuse() -> None:
    """Un pack ne peut pas mélanger deux clients."""
    autre = Tenant(
        id="t-demo-b",
        nom="Client B",
        compte_aws="210987654321",
        region_principale=jeu.REGION,
        role_arn="arn:aws:iam::210987654321:role/jauge-dataplane",
        external_id="ext-b",
    )
    with pytest.raises(ValidationError, match="client du dossier"):
        construire_proof_pack(jeu.entrees_preuve(tenant=autre))


def test_campagne_d_une_autre_application_refusee() -> None:
    """La campagne doit porter sur l'application fournie."""
    autre = Application(
        id="autre-app",
        tenant_id=jeu.CLIENT_ID,
        nom="Autre",
        equipe="support",
        modele_actuel=jeu.GRAND_MODELE,
    )
    with pytest.raises(ValidationError, match="ne porte pas sur cette application"):
        construire_proof_pack(jeu.entrees_preuve(application=autre))


def test_recommandation_d_une_autre_campagne_refusee() -> None:
    """La recommandation scellée doit provenir de la campagne prouvée."""
    reco = jeu.recommandation().model_copy(update={"campaignId": "c-autre"})
    with pytest.raises(ValidationError, match="ne provient pas de cette campagne"):
        construire_proof_pack(jeu.entrees_preuve(recommandation=reco))


def test_campagne_d_un_autre_client_refusee() -> None:
    """Campagne et application de clients différents : refus."""
    campagne = Campagne(**{**jeu.campagne().model_dump(), "tenant_id": "t-demo-b"})
    with pytest.raises(ValidationError, match="clients différents"):
        construire_proof_pack(jeu.entrees_preuve(campagne=campagne))


def test_candidats_en_double_refuses() -> None:
    """Un modèle ne peut pas apparaître deux fois dans le pack."""
    candidats = jeu.candidats()
    with pytest.raises(ValidationError, match="en double"):
        construire_proof_pack(jeu.entrees_preuve(candidats=[*candidats, candidats[0]]))


def test_plusieurs_references_refusees() -> None:
    """Une seule comparaison de référence est admise (§13.4)."""
    premier, second = jeu.candidats()
    double = EntreeCandidat(
        modele_id=jeu.PETIT_MODELE,
        resultat=second.resultat.model_copy(update={"est_reference": True}),
    )
    with pytest.raises(ValidationError, match="plusieurs références"):
        construire_proof_pack(jeu.entrees_preuve(candidats=[premier, double]))


def test_echantillon_vide_refuse() -> None:
    """Un pack sans échantillon ne prouve rien."""
    with pytest.raises(ValidationError, match="échantillon vide"):
        construire_proof_pack(jeu.entrees_preuve(echantillon=[]))


def test_aucun_candidat_refuse() -> None:
    """Un pack sans résultat de candidat ne prouve rien."""
    with pytest.raises(ValidationError, match="aucun candidat"):
        construire_proof_pack(jeu.entrees_preuve(candidats=[]))


def test_region_introuvable_refusee() -> None:
    """Sans client ni région explicite, la table des tarifs n'est pas applicable."""
    with pytest.raises(ValidationError, match="région absente"):
        construire_proof_pack(EntreesPreuve(**{**jeu.entrees_preuve().__dict__, "tenant": None}))


def test_resultat_d_un_autre_modele_refuse() -> None:
    """`EntreeCandidat` refuse un résultat qui porte un autre modèle."""
    resultat = jeu.candidats()[1].resultat
    with pytest.raises(ValidationError, match="différent du modèle"):
        EntreeCandidat(modele_id=jeu.GRAND_MODELE, resultat=resultat)


# --- Protocole pré-enregistré : interface souple ----------------------------


def test_protocole_absent_par_defaut(pack: ProofPack) -> None:
    """Sans protocole fourni, la section le déclare explicitement."""
    assert pack.section("protocole")["pre_enregistre"] is None


def test_protocole_fourni_sous_forme_de_mapping() -> None:
    """Un dictionnaire portant une empreinte est accepté tel quel."""
    protocole = {"empreinte": "a" * 64, "metriques": ["equivalence"], "marge": 0.05}
    pack = jeu.construire_pack(protocole_pre_enregistre=protocole)
    assert pack.section("protocole")["pre_enregistre"] == protocole


class ProtocoleObjet:
    """Double d'un protocole scellé : empreinte + métadonnées + `vers_dict()`."""

    empreinte = "b" * 64

    def vers_dict(self) -> dict:
        """Forme sérialisable du protocole."""
        return {"metriques": ["equivalence"], "seuil": 0.95}


class ProtocoleMinimal:
    """Protocole réduit à son empreinte."""

    empreinte = "c" * 64


def test_protocole_fourni_sous_forme_d_objet() -> None:
    """Tout objet exposant `empreinte` est accepté, sans dépendre de son module."""
    pack = jeu.construire_pack(protocole_pre_enregistre=ProtocoleObjet())
    assert pack.section("protocole")["pre_enregistre"] == {
        "metriques": ["equivalence"],
        "seuil": 0.95,
        "empreinte": "b" * 64,
    }
    minimal = jeu.construire_pack(protocole_pre_enregistre=ProtocoleMinimal())
    assert minimal.section("protocole")["pre_enregistre"] == {
        "empreinte": "c" * 64,
        "metadata": {},
    }


def test_protocole_sans_empreinte_refuse() -> None:
    """Un protocole non scellé n'est pas un protocole pré-enregistré."""
    with pytest.raises(ValidationError, match="sans empreinte"):
        normaliser_protocole({"metriques": ["equivalence"]})
    with pytest.raises(ValidationError, match="illisible"):
        normaliser_protocole(object())
    assert normaliser_protocole(None) is None


# --- Divers ------------------------------------------------------------------


def test_tarifs_appliques_a_la_date_donnee() -> None:
    """La date de référence choisit la ligne de tarif applicable (Annexe D)."""
    pack = jeu.construire_pack(a_la_date=jeu.DATE_EFFET_TARIFS)
    assert pack.section("protocole")["tarifs"]["date_reference"] == "2026-01-01"
    assert verifier_chaine(pack).conforme


def test_economie_incoherente_refusee_a_la_construction() -> None:
    """Une recommandation dont l'économie ne suit pas ses coûts est refusée."""
    reco = jeu.recommandation()
    fausse = reco.model_copy(
        update={"cost": reco.cost.model_copy(update={"expectedMonthlySaving": 1.0})}
    )
    with pytest.raises(ValidationError, match="incohérente"):
        construire_proof_pack(jeu.entrees_preuve(recommandation=fausse))


def test_section_inconnue_refusee(pack: ProofPack) -> None:
    """Accès à une section absente : erreur métier explicite."""
    with pytest.raises(ValidationError, match="section absente"):
        pack.section("inconnue")
