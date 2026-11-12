"""Tests du rejeu des vérifications : un tiers ne croit pas, il recalcule.

Chaque test de falsification montre qu'une valeur déclarée fausse est détectée
**par le rejeu** (et pas seulement par la chaîne de hachage) : le vérificateur
ne fait confiance à aucun chiffre du pack.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from tests.reports import jeu_proof_pack as jeu

from jauge.common.errors import ValidationError
from jauge.engine.calculator import calculer_cout_requete
from jauge.engine.stats import baisse_pourcentage, economie_mensuelle, intervalle_wilson
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.reports.proof_pack import ProofPack
from jauge.reports.proof_pack_entrees import EntreeCandidat
from jauge.reports.proof_pack_verification import (
    ResultatRejeu,
    rejouer_verifications,
)
from jauge.reports.scellement import verifier_chaine


@pytest.fixture
def pack() -> ProofPack:
    """Pack nominal du jeu d'essai."""
    return jeu.construire_pack()


def falsifier(pack: ProofPack, section: str, chemin: Sequence[str], valeur: Any) -> ProofPack:
    """Modifie une valeur déclarée, comme le ferait un faussaire après scellement."""
    cible: Any = pack.sections[section]
    for cle in chemin[:-1]:
        cible = cible[cle]
    cible[chemin[-1]] = valeur
    return pack


def echecs_de(resultat: ResultatRejeu) -> list[str]:
    """Noms des vérifications en échec."""
    return [verification.nom for verification in resultat.echecs]


def verification(resultat: ResultatRejeu, nom: str):
    """Une vérification par son nom (erreur de test si elle est absente)."""
    trouvees = [item for item in resultat.verifications if item.nom == nom]
    assert trouvees, f"vérification absente : {nom}"
    return trouvees[0]


# --- Cas nominal -------------------------------------------------------------


def test_rejeu_conforme_sur_un_pack_honnete(pack: ProofPack) -> None:
    """Toutes les vérifications recalculées passent sur le pack du jeu d'essai."""
    resultat = rejouer_verifications(pack)
    assert resultat.conforme, echecs_de(resultat)
    assert len(resultat.verifications) >= 40
    assert resultat.echecs == ()


def test_rejeu_couvre_toutes_les_sections_calculables(pack: ProofPack) -> None:
    """Chaque section chiffrée est confrontée à un calcul."""
    sections = {item.section for item in rejouer_verifications(pack).verifications}
    assert {
        "scellement",
        "echantillon",
        "resultats",
        "controles",
        "economie",
        "recommandation",
        "relecture",
        "protocole",
    } <= sections


def test_rejeu_depuis_le_document_json_seul(pack: ProofPack) -> None:
    """Le tiers n'a besoin que du document : aucun objet Python ni store."""
    resultat = rejouer_verifications(pack.vers_dict())
    assert resultat.conforme
    with pytest.raises(ValidationError):
        rejouer_verifications({"sections": None})
    with pytest.raises(ValidationError):
        rejouer_verifications(object())


def test_intervalles_de_wilson_retrouves(pack: ProofPack) -> None:
    """Les intervalles publiés sont exactement ceux des effectifs déclarés."""
    resultat = rejouer_verifications(pack)
    for candidat, succes in (
        (jeu.GRAND_MODELE, jeu.SUCCES_REFERENCE),
        (jeu.PETIT_MODELE, jeu.SUCCES_CANDIDAT),
    ):
        intervalle = intervalle_wilson(succes, jeu.TAILLE_ECHANTILLON)
        for cle, attendu in (
            ("taux_equivalence", intervalle.taux),
            ("intervalle_confiance_bas", intervalle.bas),
            ("intervalle_confiance_haut", intervalle.haut),
        ):
            item = verification(resultat, f"candidat.{candidat}.{cle}")
            assert item.conforme
            assert item.attendu == attendu


def test_cout_par_requete_retrouve_depuis_les_jetons_et_les_tarifs(pack: ProofPack) -> None:
    """Le coût publié est recalculé avec la formule de l'Annexe D (moteur du socle)."""
    table = jeu.tarifs()
    resultat = rejouer_verifications(pack)
    for modele, prix, attendu in (
        (jeu.GRAND_MODELE, jeu.PRIX_GRAND, jeu.cout_par_requete(jeu.PRIX_GRAND)),
        (jeu.PETIT_MODELE, jeu.PRIX_PETIT, jeu.cout_par_requete(jeu.PRIX_PETIT)),
    ):
        tarif = next(
            ligne
            for ligne in table
            if ligne.modele_id == modele and ligne.niveau == ServiceTier.FLEX
        )
        recalcul = calculer_cout_requete(
            tarif,
            MetriquesRequete(jetons_entree=jeu.JETONS_ENTREE, jetons_sortie=jeu.JETONS_SORTIE),
        ).cout_total
        assert recalcul == attendu == pytest.approx(prix[0] * 0.12 + prix[1] * 0.08)
        item = verification(resultat, f"candidat.{modele}.cout_par_requete")
        assert item.conforme
        assert item.attendu == recalcul


def test_economie_et_part_msp_retrouvees(pack: ProofPack) -> None:
    """L'économie et la part du MSP se recalculent depuis coûts et volume."""
    resultat = rejouer_verifications(pack)
    avant, apres = jeu.cout_par_requete(jeu.PRIX_GRAND), jeu.cout_par_requete(jeu.PRIX_PETIT)
    attendue = economie_mensuelle(avant, apres, jeu.VOLUME_MENSUEL)
    assert verification(resultat, "economie.attendue").attendu == attendue
    assert verification(resultat, "economie.attendue").conforme
    assert verification(resultat, "economie.baisse").attendu == baisse_pourcentage(avant, apres)
    assert verification(resultat, "economie.part_msp").attendu == round(
        attendue * jeu.PART_MSP_POURCENTAGE / 100.0, 2
    )
    assert resultat.conforme


def test_accord_juge_humain_retrouve(pack: ProofPack) -> None:
    """L'accord publié correspond aux comptes de paires relues."""
    resultat = rejouer_verifications(pack)
    item = verification(resultat, "relecture.accord")
    assert item.conforme
    assert item.attendu == round(jeu.PAIRES_CONCORDANTES / jeu.PAIRES_RELECTURE, 6)


# --- Falsifications détectées ------------------------------------------------


def test_taux_declare_faux_detecte(pack: ProofPack) -> None:
    """Gonfler le taux d'équivalence est détecté par le rejeu des effectifs."""
    falsifier(pack, "resultats", ["candidats", 1, "taux_equivalence"], 0.99)
    resultat = rejouer_verifications(pack)
    assert not resultat.conforme
    assert f"candidat.{jeu.PETIT_MODELE}.taux_equivalence" in echecs_de(resultat)
    assert not verifier_chaine(pack).conforme


def test_intervalle_declare_faux_detecte(pack: ProofPack) -> None:
    """Relever la borne basse de l'intervalle est détecté."""
    falsifier(pack, "resultats", ["candidats", 1, "intervalle_confiance_bas"], 0.95)
    resultat = rejouer_verifications(pack)
    assert f"candidat.{jeu.PETIT_MODELE}.intervalle_confiance_bas" in echecs_de(resultat)


def test_effectif_gonfle_detecte(pack: ProofPack) -> None:
    """Gonfler le nombre de paires rejouées casse le taux et l'intervalle recalculés."""
    falsifier(pack, "resultats", ["candidats", 1, "nombre_requetes_rejouees"], 400)
    resultat = rejouer_verifications(pack)
    assert not resultat.conforme
    echecs = echecs_de(resultat)
    assert f"candidat.{jeu.PETIT_MODELE}.taux_equivalence" in echecs
    assert f"candidat.{jeu.PETIT_MODELE}.intervalle_confiance_bas" in echecs


def test_succes_superieur_a_l_effectif_detecte(pack: ProofPack) -> None:
    """Plus de paires équivalentes que de paires rejouées : incohérence signalée."""
    falsifier(pack, "resultats", ["candidats", 1, "succes"], 999)
    resultat = rejouer_verifications(pack)
    assert f"candidat.{jeu.PETIT_MODELE}.effectifs" in echecs_de(resultat)


def test_cout_declare_faux_detecte(pack: ProofPack) -> None:
    """Minimiser le coût recalculé est détecté par la formule de l'Annexe D."""
    falsifier(pack, "resultats", ["candidats", 1, "cout_par_requete_recalcule_usd"], 0.000001)
    resultat = rejouer_verifications(pack)
    assert f"candidat.{jeu.PETIT_MODELE}.cout_par_requete" in echecs_de(resultat)


def test_prix_de_la_table_modifie_detecte(pack: ProofPack) -> None:
    """Changer un prix de la table scellée rend le coût déclaré incohérent."""
    falsifier(pack, "protocole", ["tarifs", "lignes", 1, "prix_entree_1k"], 0.0001)
    resultat = rejouer_verifications(pack)
    assert not resultat.conforme
    assert f"candidat.{jeu.PETIT_MODELE}.cout_par_requete" in echecs_de(resultat)
    assert "tarifs.empreinte" in echecs_de(resultat)


def test_economie_declaree_fausse_detectee(pack: ProofPack) -> None:
    """Gonfler l'économie attendue est détecté depuis les coûts et le volume."""
    falsifier(pack, "economie", ["economie_mensuelle_attendue_usd"], 5_000.0)
    resultat = rejouer_verifications(pack)
    assert "economie.attendue" in echecs_de(resultat)
    assert "recommandation.economie_attendue" in echecs_de(resultat)


def test_volume_mensuel_gonfle_detecte(pack: ProofPack) -> None:
    """Le volume mensuel déclaré doit produire l'économie annoncée."""
    falsifier(pack, "economie", ["volume_mensuel"], 10_000_000)
    resultat = rejouer_verifications(pack)
    assert "economie.attendue" in echecs_de(resultat)
    assert "recommandation.volume" in echecs_de(resultat)


def test_part_msp_fausse_detectee(pack: ProofPack) -> None:
    """La part du MSP est recalculée sur l'économie attendue."""
    falsifier(pack, "economie", ["part_msp_mensuelle_usd"], 10_000.0)
    assert "economie.part_msp" in echecs_de(rejouer_verifications(pack))


def test_accord_humain_gonfle_detecte(pack: ProofPack) -> None:
    """Prétendre un accord juge/humain parfait est détecté par les comptes."""
    falsifier(pack, "relecture", ["accord_juge_humain"], 1.0)
    assert "relecture.accord" in echecs_de(rejouer_verifications(pack))


def test_paires_concordantes_impossibles_detectees(pack: ProofPack) -> None:
    """Plus de paires concordantes que de paires relues : accord recalculé > 1."""
    falsifier(pack, "relecture", ["paires_concordantes"], 45)
    resultat = rejouer_verifications(pack)
    assert "relecture.accord" in echecs_de(resultat)


def test_recommandation_decouplee_des_resultats_detectee(pack: ProofPack) -> None:
    """Une recommandation qui ne reflète pas les résultats mesurés est rejetée."""
    falsifier(pack, "recommandation", ["quality", "equivalenceRate"], 0.999)
    resultat = rejouer_verifications(pack)
    assert "recommandation.taux_equivalence" in echecs_de(resultat)
    falsifier(pack, "recommandation", ["sampleSize"], 999)
    assert "recommandation.taille_echantillon" in echecs_de(rejouer_verifications(pack))
    falsifier(pack, "recommandation", ["targetModel"], "modele-inexistant")
    assert "recommandation.modele_cible_present" in echecs_de(rejouer_verifications(pack))


def test_controles_hors_population_detectes(pack: ProofPack) -> None:
    """Un contrôle qui n'aurait pas tourné sur tout l'échantillon est détecté."""
    falsifier(pack, "controles", ["par_candidat", 1, "controles", 0, "executions"], 10)
    resultat = rejouer_verifications(pack)
    assert f"controles.{jeu.PETIT_MODELE}.json_valide.population" in echecs_de(resultat)


def test_echecs_deterministes_incoherents_detectes(pack: ProofPack) -> None:
    """Plus de paires en échec critique que d'échecs critiques : incohérence."""
    falsifier(pack, "resultats", ["candidats", 1, "echecs_deterministes"], 12)
    resultat = rejouer_verifications(pack)
    assert f"controles.{jeu.PETIT_MODELE}.echecs_deterministes" in echecs_de(resultat)


def test_echantillon_recompose_detecte(pack: ProofPack) -> None:
    """Retirer une requête de l'échantillon casse l'empreinte de l'échantillon."""
    pack.sections["echantillon"]["requetes"].pop()
    resultat = rejouer_verifications(pack)
    assert "echantillon.taille" in echecs_de(resultat)
    assert "echantillon.empreinte" in echecs_de(resultat)


def test_resultat_sans_mesures_n_est_pas_recalculable() -> None:
    """Sans profil de jetons, le coût n'est pas vérifiable — et le pack le dit."""
    candidats = [
        EntreeCandidat(
            modele_id=entree.modele_id, resultat=entree.resultat, controles=entree.controles
        )
        for entree in jeu.candidats()
    ]
    pack = jeu.construire_pack(candidats=candidats)
    resultat = rejouer_verifications(pack)
    assert resultat.conforme, echecs_de(resultat)
    assert not any("cout_par_requete" in item.nom for item in resultat.verifications)
    for candidat in pack.section("resultats")["candidats"]:
        assert candidat["cout_recalculable"] is False
        assert candidat["cout_par_requete_recalcule_usd"] is None


def test_sans_relecture_humaine_la_verification_est_sautee() -> None:
    """Aucune relecture : aucun accord à recalculer, le pack reste conforme."""
    pack = jeu.construire_pack(relecture=None)
    resultat = rejouer_verifications(pack)
    assert resultat.conforme, echecs_de(resultat)
    assert not any(item.nom == "relecture.accord" for item in resultat.verifications)
    assert pack.section("relecture")["paires_relues"] == 0


def test_resultat_serialisable_et_explicite(pack: ProofPack) -> None:
    """Le verdict du rejeu est lui aussi un document partageable."""
    document = rejouer_verifications(pack).vers_dict()
    assert document["conforme"] is True
    assert document["nombre_echecs"] == 0
    assert document["nombre_verifications"] == len(document["verifications"])
    falsifier(pack, "economie", ["economie_mensuelle_attendue_usd"], 0.0)
    document = rejouer_verifications(pack).vers_dict()
    assert document["conforme"] is False
    assert document["nombre_echecs"] >= 1
    assert all(
        {"nom", "section", "conforme", "attendu", "declare", "detail"} <= set(item)
        for item in document["verifications"]
    )
