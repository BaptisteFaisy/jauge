"""Tests du scellement : canonicité, chaîne de hachage, signature, constat détaché.

Aucun réseau, aucun service AWS : `hashlib`, `hmac` et `json` de la bibliothèque
standard suffisent. Ces tests sont la démonstration exigée par la piste 6 — un
tiers peut localiser **exactement** où un dossier de preuve a été modifié.
"""

from __future__ import annotations

import json
import math

import pytest

from jauge.reports.scellement import (
    ALGORITHME,
    ALGORITHME_SIGNATURE,
    GENESE,
    MaillonChaine,
    SignataireHMAC,
    constat_detache,
    empreinte,
    empreinte_liee,
    extraire_sections_et_sceau,
    sceller,
    sceller_et_signer,
    serialiser_canonique,
    verifier_chaine,
    verifier_constat,
    verifier_signature,
)

SECRET = b"secret-de-demonstration"

# Trois sections, dans l'ordre de la chaîne (contenu volontairement minimal).
SECTIONS_EXEMPLE: tuple[tuple[str, dict], ...] = (
    ("identite", {"campagne_id": "c-1", "client": "Client A"}),
    ("resultats", {"taux": 0.97, "effectif": 200}),
    ("economie", {"montant": 992.0, "devise": "USD"}),
)


def _pack(sections=SECTIONS_EXEMPLE, signataire=None) -> dict:
    """Document de pack minimal : sections + sceau scellé."""
    return {"sections": dict(sections), "scellement": sceller_et_signer(sections, signataire)}


# --- Sérialisation canonique -------------------------------------------------


def test_canonique_independant_de_l_ordre_des_cles() -> None:
    """Même contenu, deux ordres d'insertion différents → mêmes octets."""
    premier = {"b": 1, "a": {"y": 2, "x": 3}, "c": [1, 2]}
    second = {"c": [1, 2], "a": {"x": 3, "y": 2}, "b": 1}
    assert serialiser_canonique(premier) == serialiser_canonique(second)
    assert empreinte(premier) == empreinte(second)


def test_canonique_cles_triees_et_separateurs_fixes() -> None:
    """Clés triées à tous les niveaux, séparateurs sans espace, UTF-8 conservé."""
    rendu = serialiser_canonique({"z": 1, "a": {"b": "café"}, "m": [2, 1]})
    assert rendu == '{"a":{"b":"café"},"m":[2,1],"z":1}'
    assert " " not in rendu


def test_canonique_refuse_les_valeurs_non_finies() -> None:
    """Un NaN rendrait l'empreinte dépendante de la représentation flottante."""
    with pytest.raises(ValueError):
        serialiser_canonique({"taux": math.nan})
    with pytest.raises(ValueError):
        serialiser_canonique({"taux": math.inf})


def test_canonique_stable_sur_deux_machines() -> None:
    """L'empreinte ne dépend ni du hash aléatoire de Python ni de l'objet source."""
    charge = {"modele": "demo", "prix": 0.0004, "date": "2026-01-01"}
    premiere = empreinte(charge)
    relue = json.loads(serialiser_canonique(charge))
    assert empreinte(relue) == premiere


def test_dates_et_enumerations_serialisees() -> None:
    """Les types usuels non-JSON sont convertis de façon déterministe."""
    from datetime import date
    from enum import StrEnum

    class Niveau(StrEnum):
        FLEX = "flex"

    assert json.loads(serialiser_canonique({"d": date(2026, 1, 1)})) == {"d": "2026-01-01"}
    assert json.loads(serialiser_canonique({"n": Niveau.FLEX})) == {"n": "flex"}
    assert json.loads(serialiser_canonique({"t": (1, 2)})) == {"t": [1, 2]}
    with pytest.raises(TypeError):
        serialiser_canonique({"objet": object()})


# --- Chaîne de hachage -------------------------------------------------------


def test_chaine_valide_sur_un_pack_intact() -> None:
    """Un pack non modifié vérifie toute sa chaîne."""
    resultat = verifier_chaine(_pack())
    assert resultat.conforme
    assert resultat.position is None
    assert resultat.raison == ""


def test_chaine_un_maillon_par_section_dans_l_ordre() -> None:
    """Le sceau porte un maillon par section, dans l'ordre déclaré."""
    sceau = sceller(SECTIONS_EXEMPLE)
    assert sceau["algorithme"] == ALGORITHME
    assert sceau["genese"] == GENESE
    assert [maillon["nom"] for maillon in sceau["sections"]] == [nom for nom, _ in SECTIONS_EXEMPLE]
    assert sceau["empreinte_pack"] == sceau["sections"][-1]["empreinte_cumulee"]


def test_chaque_maillon_depend_du_precedent() -> None:
    """Le maillon n inclut le maillon n-1 : l'ordre fait partie du scellement."""
    sceau = sceller(SECTIONS_EXEMPLE)
    maillons = [MaillonChaine.depuis_dict(item) for item in sceau["sections"]]
    assert maillons[0].empreinte_cumulee == empreinte_liee(GENESE, maillons[0].empreinte)
    assert maillons[1].empreinte_cumulee == empreinte_liee(
        maillons[0].empreinte_cumulee, maillons[1].empreinte
    )
    permute = sceller(tuple(reversed(SECTIONS_EXEMPLE)))
    assert permute["empreinte_pack"] != sceau["empreinte_pack"]


@pytest.mark.parametrize("rang", [0, 1, 2])
def test_modification_de_section_localisee_exactement(rang: int) -> None:
    """Modifier une section casse la chaîne, à la position de cette section."""
    pack = _pack()
    nom = SECTIONS_EXEMPLE[rang][0]
    pack["sections"][nom] = {**pack["sections"][nom], "falsifie": True}
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.position == rang + 1
    assert resultat.section == nom
    assert "modifié" in resultat.raison
    assert resultat.empreinte_calculee != resultat.empreinte_attendue


def test_suppression_de_valeur_dans_une_section_detectee() -> None:
    """Retirer un seul champ d'une section suffit à casser la chaîne."""
    pack = _pack()
    pack["sections"]["economie"].pop("montant")
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.section == "economie"


def test_maillon_recolle_detecte() -> None:
    """Recoller une empreinte de section sans refaire la cumulative est détecté."""
    pack = _pack()
    nouveau = {"autre": "contenu"}
    pack["sections"]["resultats"] = nouveau
    pack["scellement"]["sections"][1]["empreinte"] = empreinte(nouveau)
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.position == 2
    assert "recollé" in resultat.raison


def test_rupture_propagee_vers_l_aval() -> None:
    """Un maillon refait à la main casse le suivant : la chaîne ne se répare pas."""
    pack = _pack()
    nouveau = {"autre": "contenu"}
    pack["sections"]["resultats"] = nouveau
    maillon = pack["scellement"]["sections"][1]
    maillon["empreinte"] = empreinte(nouveau)
    maillon["empreinte_cumulee"] = empreinte_liee(
        pack["scellement"]["sections"][0]["empreinte_cumulee"], maillon["empreinte"]
    )
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.position == 3  # la section suivante n'a pas été rescellée
    assert resultat.section == "economie"


def test_section_ajoutee_hors_sceau_detectee() -> None:
    """Une section ajoutée après coup n'est pas couverte par le sceau."""
    pack = _pack()
    pack["sections"]["surprise"] = {"ajoute": True}
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.section == "surprise"
    assert "absente du sceau" in resultat.raison


def test_section_supprimee_du_pack_detectee() -> None:
    """Une section scellée mais disparue du pack casse la chaîne."""
    pack = _pack()
    del pack["sections"]["resultats"]
    resultat = verifier_chaine(pack)
    assert not resultat.conforme
    assert resultat.section == "resultats"


def test_reecriture_complete_change_l_empreinte_finale() -> None:
    """Un faussaire qui rescelle tout obtient une empreinte finale différente.

    C'est la limite assumée de la chaîne seule : elle ne prouve pas l'origine.
    Le constat détaché (ancre externe) est ce qui rend la réécriture détectable.
    """
    falsifiees = (
        ("identite", {"campagne_id": "c-1", "client": "Client A"}),
        ("resultats", {"taux": 0.99, "effectif": 200}),
        ("economie", {"montant": 5_000.0, "devise": "USD"}),
    )
    original = _pack()
    falsifie = _pack(falsifiees)
    assert verifier_chaine(falsifie).conforme  # la chaîne seule ne suffit pas
    assert falsifie["scellement"]["empreinte_pack"] != original["scellement"]["empreinte_pack"]
    constat = constat_detache(
        "pp-1", original["scellement"], horodatage="2026-11-12T10:00:00+00:00", auteur="alice"
    )
    assert verifier_constat(constat, original)
    assert not verifier_constat(constat, falsifie)


def test_sceau_vide_et_pack_illisible_refuses() -> None:
    """Sans sceau exploitable, la vérification échoue au lieu de passer."""
    assert not verifier_chaine({"sections": {}, "scellement": {"sections": []}}).conforme
    with pytest.raises(ValueError):
        verifier_chaine({"sections": {}})
    with pytest.raises(ValueError):
        extraire_sections_et_sceau(object())
    assert not verifier_chaine(
        {"sections": dict(SECTIONS_EXEMPLE), "scellement": {"sections": [{"nom": "x"}]}}
    ).conforme


# --- Signature et constat détaché -------------------------------------------


def test_signature_hmac_verifiee_par_le_detenteur_de_la_cle() -> None:
    """La signature prouve l'origine ; une autre clé ne la vérifie pas."""
    signataire = SignataireHMAC(secret=SECRET, cle_id="msp-2026")
    pack = _pack(signataire=signataire)
    signature = pack["scellement"]["signature"]
    assert signature["algorithme"] == ALGORITHME_SIGNATURE
    assert signature["cle_id"] == "msp-2026"
    assert verifier_signature(pack["scellement"], signataire)
    assert not verifier_signature(pack["scellement"], SignataireHMAC(SECRET, "autre-cle"))
    assert not verifier_signature(pack["scellement"], SignataireHMAC(b"autre", "msp-2026"))


def test_signature_cassee_par_une_modification_du_pack() -> None:
    """Signer l'empreinte finale couvre indirectement toutes les sections."""
    signataire = SignataireHMAC(secret=SECRET)
    pack = _pack(signataire=signataire)
    pack["scellement"]["empreinte_pack"] = empreinte({"pack": "falsifie"})
    assert not verifier_signature(pack["scellement"], signataire)


def test_signature_absente_non_verifiee() -> None:
    """Un pack non signé ne peut pas être dit signé."""
    assert not verifier_signature(_pack()["scellement"], SignataireHMAC(SECRET))


def test_constat_detache_et_verifie() -> None:
    """Le constat publié séparément ancre l'empreinte du pack."""
    pack = _pack()
    constat = constat_detache(
        "pp-1", pack["scellement"], horodatage="2026-11-12T10:00:00+00:00", auteur="alice"
    )
    assert verifier_constat(constat, pack)
    falsifie = dict(constat, auteur="mallory")
    assert not verifier_constat(falsifie, pack)


def test_objet_pack_accepte_aussi_bien_que_le_document() -> None:
    """`verifier_chaine` accepte un objet pack ou son document JSON."""

    class PackObjet:
        def __init__(self) -> None:
            document = _pack()
            self.sections = document["sections"]
            self.scellement = document["scellement"]

    assert verifier_chaine(PackObjet()).conforme
