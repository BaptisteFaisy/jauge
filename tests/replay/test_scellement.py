"""Tests du scellement : empreinte canonique et échantillon figé (§13.1, piste n° 1).

Aucun réseau, aucun AWS : les échantillons viennent du tirage reproductible du
module `jauge.replay.sampling` sur les requêtes fictives des fixtures locales.

Trois familles de cas :
- l'empreinte canonique est **déterministe** et indépendante de l'ordre des clés ;
- un échantillon re-scellé est **identique** ; un échantillon retouché, substitué
  ou réordonné est **détecté** (anti cherry-picking) ;
- le sceau ne transporte **aucun texte** de requête (ENF-CONF-01).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

import pytest
from tests.replay.constantes import APPLICATION, GRAINE

from jauge.common.errors import EtatIncompatibleError
from jauge.models.campaign import EchantillonRequete
from jauge.models.pricing import TarifModele
from jauge.replay.sampling import Echantillon, RequeteSource, tirer_echantillon
from jauge.replay.scellement import (
    ALGORITHME,
    SCELLE_PAR_DEFAUT,
    EchantillonScelle,
    charge_utile_echantillon,
    echantillon_conforme,
    empreinte_canonique,
    empreinte_table_tarifs,
    sceller_echantillon,
    serialiser_canonique,
    verifier_echantillon,
)

MOMENT_FIXE = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
TEXTE_SENTINELLE = "Question de demonstration req-0001"


def _retoucher(echantillon: Echantillon, **champs: object) -> Echantillon:
    """Rend une copie modifiée d'un échantillon (simule une retouche après coup)."""
    return dataclasses.replace(echantillon, **champs)


# --- Sérialisation canonique ------------------------------------------------


def test_serialisation_canonique_trie_les_cles() -> None:
    """Même contenu, ordre de saisie différent → exactement les mêmes octets."""
    premier = {"z": 1, "a": {"y": 2, "b": 3}}
    second = {"a": {"b": 3, "y": 2}, "z": 1}
    assert serialiser_canonique(premier) == serialiser_canonique(second)
    assert serialiser_canonique(premier) == '{"a":{"b":3,"y":2},"z":1}'


def test_empreinte_canonique_est_deterministe() -> None:
    """Deux calculs de la même empreinte rendent la même valeur (64 hex)."""
    donnees = {"seuils": {"equivalence": 0.95}, "graines": [7, 42]}
    assert empreinte_canonique(donnees) == empreinte_canonique(
        dict(reversed(list(donnees.items())))
    )
    assert len(empreinte_canonique(donnees)) == 64


def test_empreinte_canonique_change_avec_le_contenu() -> None:
    """Une valeur différente, même d'un ulp, change l'empreinte."""
    assert empreinte_canonique({"seuil": 0.95}) != empreinte_canonique({"seuil": 0.94})
    assert empreinte_canonique({"seuil": 0.95}) != empreinte_canonique({"seuils": 0.95})


def test_type_non_serialisable_refuse_explicitement() -> None:
    """Un objet inconnu dans la charge utile lève l'erreur métier, pas un TypeError brut."""
    with pytest.raises(EtatIncompatibleError):
        serialiser_canonique({"objet": object()})


# --- Scellement de l'échantillon -------------------------------------------


def test_sceau_porte_empreinte_horodatage_et_auteur(echantillon: Echantillon) -> None:
    """Le sceau dit quoi, quand et par qui (traçabilité §16)."""
    sceau = sceller_echantillon(echantillon, scelle_par="msp-audit", scelle_le=MOMENT_FIXE)
    assert sceau.empreinte == empreinte_canonique(charge_utile_echantillon(echantillon))
    assert sceau.scelle_le == MOMENT_FIXE
    assert sceau.scelle_par == "msp-audit"
    assert sceau.application_id == APPLICATION
    assert sceau.graine == GRAINE
    assert sceau.taille == echantillon.taille == 60


def test_sceau_par_defaut_horodate_et_auteur_implicite(echantillon: Echantillon) -> None:
    """Sans auteur ni moment, le sceau reste complet et horodaté en UTC."""
    sceau = sceller_echantillon(echantillon)
    assert sceau.scelle_par == SCELLE_PAR_DEFAUT
    assert sceau.scelle_le.tzinfo is not None
    vue = sceau.vers_dict()
    assert vue["algorithme"] == ALGORITHME
    assert isinstance(vue["scelle_le"], str) and "T" in str(vue["scelle_le"])


def test_echantillon_re_scelle_donne_la_meme_empreinte(echantillon: Echantillon) -> None:
    """Sceller deux fois le même tirage rend la même empreinte (l'heure n'y entre pas)."""
    premier = sceller_echantillon(echantillon, scelle_le=MOMENT_FIXE)
    second = sceller_echantillon(echantillon, scelle_le=datetime(2027, 1, 1, tzinfo=UTC))
    assert premier.empreinte == second.empreinte
    verifier_echantillon(echantillon, premier)
    assert echantillon_conforme(echantillon, second) is True


def test_un_nouveau_tirage_identique_donne_la_meme_empreinte(
    requetes_source: list[RequeteSource], echantillon: Echantillon
) -> None:
    """Même graine, même requêtes → même empreinte : le scellement est reproductible."""
    autre = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=60, graine=GRAINE)
    sceau = sceller_echantillon(echantillon)
    verifier_echantillon(autre, sceau)
    assert sceller_echantillon(autre).empreinte == sceau.empreinte


def test_une_autre_graine_donne_une_autre_empreinte(
    requetes_source: list[RequeteSource], echantillon: Echantillon
) -> None:
    """Un tirage sur une autre graine n'est pas l'échantillon engagé."""
    autre = tirer_echantillon(
        requetes_source, application_id=APPLICATION, taille=60, graine=GRAINE + 1
    )
    sceau = sceller_echantillon(echantillon)
    assert sceller_echantillon(autre).empreinte != sceau.empreinte
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(autre, sceau)


def test_echantillon_ampute_est_detecte(echantillon: Echantillon) -> None:
    """Retirer une requête après le scellement casse l'empreinte."""
    sceau = sceller_echantillon(echantillon)
    ampute = _retoucher(echantillon, requetes=echantillon.requetes[:-1])
    with pytest.raises(EtatIncompatibleError) as erreur:
        verifier_echantillon(ampute, sceau)
    assert erreur.value.details["empreinte_attendue"] == sceau.empreinte
    assert erreur.value.details["taille_attendue"] == 60
    assert erreur.value.details["taille_presentee"] == 59
    assert echantillon_conforme(ampute, sceau) is False


def test_requete_substituee_est_detectee(echantillon: Echantillon) -> None:
    """Le cherry-picking : remplacer une requête par une autre favorable est détecté."""
    sceau = sceller_echantillon(echantillon)
    favorable = EchantillonRequete(
        requete_id="req-favorable",
        application_id=APPLICATION,
        type_requete="classement",
        longueur_caracteres=100,
        jetons_entree_origine=100,
        stratum="court:classement",
    )
    substitue = _retoucher(
        echantillon, requetes=(*echantillon.requetes[:-1], favorable), strates=echantillon.strates
    )
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(substitue, sceau)


def test_reordonnancement_est_detecte(echantillon: Echantillon) -> None:
    """L'ordre du tirage fait partie de l'engagement : le renverser est détecté."""
    sceau = sceller_echantillon(echantillon)
    inverse = _retoucher(echantillon, requetes=tuple(reversed(echantillon.requetes)))
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(inverse, sceau)


def test_metadonnee_retouchee_est_detectee(echantillon: Echantillon) -> None:
    """Gonfler la longueur d'une requête (pour la changer de strate) est détecté."""
    sceau = sceller_echantillon(echantillon)
    premiere = echantillon.requetes[0]
    gonflee = premiere.model_copy(update={"longueur_caracteres": premiere.longueur_caracteres + 1})
    retouche = _retoucher(echantillon, requetes=(gonflee, *echantillon.requetes[1:]))
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(retouche, sceau)


def test_repartition_des_strates_retouchee_est_detectee(echantillon: Echantillon) -> None:
    """La composition déclarée fait partie du sceau : la falsifier est détecté."""
    sceau = sceller_echantillon(echantillon)
    falsifie = _retoucher(echantillon, strates={"court:redaction": echantillon.taille})
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(falsifie, sceau)


def test_changement_d_application_est_detecte(echantillon: Echantillon) -> None:
    """Un échantillon d'une autre application n'est pas celui qui a été scellé."""
    sceau = sceller_echantillon(echantillon)
    ailleurs = _retoucher(echantillon, application_id="autre-application")
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(ailleurs, sceau)


def test_sceau_altere_ne_valide_pas_un_echantillon_sain(echantillon: Echantillon) -> None:
    """Un sceau dont l'empreinte a été recopiée ailleurs ne passe pas."""
    sceau_altere = EchantillonScelle(
        empreinte="0" * 64,
        scelle_le=MOMENT_FIXE,
        scelle_par="faussaire",
        application_id=APPLICATION,
        graine=GRAINE,
        taille=echantillon.taille,
    )
    with pytest.raises(EtatIncompatibleError):
        verifier_echantillon(echantillon, sceau_altere)


# --- Table des tarifs -------------------------------------------------------


def test_empreinte_tarifs_independante_de_l_ordre(tarifs: list[TarifModele]) -> None:
    """La même table chargée dans un autre ordre donne la même empreinte (§16)."""
    assert len(tarifs) >= 2
    assert empreinte_table_tarifs(tarifs) == empreinte_table_tarifs(list(reversed(tarifs)))


def test_empreinte_tarifs_sensible_au_prix(tarifs: list[TarifModele]) -> None:
    """Un prix modifié change l'empreinte : la table engagée n'est plus la même."""
    modifiee = [
        tarif.model_copy(update={"prix_sortie_1k": tarif.prix_sortie_1k + 0.001})
        for tarif in tarifs
    ]
    assert empreinte_table_tarifs(modifiee) != empreinte_table_tarifs(tarifs)


# --- Confidentialité --------------------------------------------------------


def test_le_sceau_ne_contient_aucun_texte(echantillon: Echantillon) -> None:
    """ENF-CONF-01 : ni le sceau ni sa charge utile ne portent de texte de requête."""
    sceau = sceller_echantillon(echantillon)
    brut = json.dumps(sceau.vers_dict(), ensure_ascii=False)
    brut += serialiser_canonique(charge_utile_echantillon(echantillon))
    assert TEXTE_SENTINELLE not in brut
    assert "texte" not in charge_utile_echantillon(echantillon)["requetes"][0]
