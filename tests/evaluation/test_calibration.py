"""Tests de la suite de calibration du juge (Top 10, piste n° 2 — §13.5, §21).

Aucun appel réseau, aucun AWS : le juge est fictif (`FakeJugeClient`) et le jeu
de calibration est synthétique, construit hors ligne
(`jauge.evaluation.jeux_calibration`).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.evaluation.juges_fictifs import juge_parfait

from jauge.common.errors import ValidationError
from jauge.engine.stats import intervalle_wilson
from jauge.evaluation.aptitude_juge import (
    Aptitude,
    HistoriqueCalibration,
    ObservationCalibration,
    SeuilsCalibration,
    SeuilsDerive,
    comparer_derive,
    juger_aptitude,
)
from jauge.evaluation.calibration import (
    GRAINE_DEFAUT,
    MesuresCalibration,
    aveugler_item,
    aveugler_jeu,
    bloc_calibration,
    calibrer,
    mesurer,
    version_prompt_courante,
)
from jauge.evaluation.jeux_calibration import (
    CATALOGUE_MODELES,
    MODELE_REFERENCE,
    FamilleCalibration,
    ItemCalibration,
    jeu_calibration_defaut,
    verifier_jeu,
)
from jauge.evaluation.judge import FakeJugeClient, VerdictJuge

#: Composition du jeu par défaut : 37 items, dont 25 paires équivalentes
#: (vérité « équivalent ») et 12 paires non équivalentes.
NB_ITEMS = 37
NB_GOLD_VRAI = 25
NB_GOLD_FAUX = 12
FAMILLES_ATTENDUES = {
    "equivalent",
    "non_equivalent",
    "piege_dore",
    "position",
    "verbosite",
    "format",
}


@pytest.fixture
def jeu() -> list[ItemCalibration]:
    """Le jeu de calibration par défaut, vérifié."""
    return jeu_calibration_defaut()


@pytest.fixture
def rapport_parfait(jeu):
    """Calibration d'un juge qui rend la vérité du jeu."""
    return calibrer(juge_parfait(jeu), jeu, graine=GRAINE_DEFAUT)


def _mesures(**ecarts) -> MesuresCalibration:
    """Mesures synthétiques : valeurs parfaites, sauf celles écrasées."""
    defauts = {
        "nombre_items": NB_ITEMS,
        "accord_gold": intervalle_wilson(NB_ITEMS, NB_ITEMS),
        "faux_accept": intervalle_wilson(0, NB_GOLD_FAUX),
        "faux_rejet": intervalle_wilson(0, NB_GOLD_VRAI),
        "accord_pieges_dores": intervalle_wilson(6, 6),
        "biais_position": 0.0,
        "biais_verbosite": 0.0,
        "biais_format": 0.0,
        "paires_position": 4,
        "divergences_position": 0,
    }
    defauts.update(ecarts)
    return MesuresCalibration(**defauts)


# --- jeu de calibration ----------------------------------------------------


def test_jeu_couvre_toutes_les_familles(jeu):
    """Les six familles doivent être présentes : une famille absente = mesure absente."""
    familles = {item.famille.value for item in jeu}
    assert familles == FAMILLES_ATTENDUES
    assert len(jeu) == NB_ITEMS


def test_jeu_est_reproductible():
    """Deux constructions donnent le même jeu, dans le même ordre."""
    assert jeu_calibration_defaut() == jeu_calibration_defaut()


def test_pieges_dores_sont_notes_dans_les_deux_sens(jeu):
    """Chaque piège doré existe en version correcte et en version fausse."""
    pieges = [item for item in jeu if item.famille is FamilleCalibration.PIEGE_DORE]
    assert len(pieges) == 6
    assert all(item.est_piege for item in pieges)
    assert {item.gold_equivalent for item in pieges} == {True, False}


def test_famille_verbosite_le_candidat_est_le_plus_long(jeu):
    """Définition de la famille : la réponse la plus longue est la moins bonne."""
    for item in jeu:
        if item.famille is FamilleCalibration.VERBOSITE:
            assert len(item.reponse_candidate) > len(item.reponse_reference)
            assert item.gold_equivalent is False


def test_verifier_jeu_refuse_un_jeu_vide():
    """Un jeu vide ne produit aucune mesure : il est refusé."""
    with pytest.raises(ValidationError):
        verifier_jeu([])


def test_verifier_jeu_refuse_une_famille_manquante(jeu):
    """Une famille absente rendrait une mesure non calculable."""
    amputé = [item for item in jeu if item.famille is not FamilleCalibration.FORMAT]
    with pytest.raises(ValidationError, match="familles absentes"):
        verifier_jeu(amputé)


def test_verifier_jeu_refuse_des_identifiants_dupliques(jeu):
    """Deux items ne peuvent pas partager le même identifiant."""
    doublon = [*jeu, replace(jeu[0], cle_paire="doublon")]
    with pytest.raises(ValidationError, match="dupliqués"):
        verifier_jeu(doublon)


def test_verifier_jeu_refuse_une_trace_de_modele(jeu):
    """Un texte qui cite le modèle producteur ne doit jamais être noté."""
    trahi = replace(jeu[0], reponse_candidate=f"{MODELE_REFERENCE} a produit cette réponse")
    with pytest.raises(ValidationError, match="identifiant de modèle"):
        verifier_jeu([trahi, *jeu[1:]])


def test_verifier_jeu_refuse_une_paire_non_croisee(jeu):
    """Les deux ordres d'une paire doivent être inversés l'un de l'autre."""
    index = next(
        position for position, item in enumerate(jeu) if item.famille is FamilleCalibration.POSITION
    )
    casse = replace(jeu[index], reponse_candidate="une autre réponse qui ne correspond pas")
    with pytest.raises(ValidationError, match="non croisée"):
        verifier_jeu([*jeu[:index], casse, *jeu[index + 1 :]])


# --- aveuglement ------------------------------------------------------------


def test_aveuglement_efface_toute_trace_de_modele(jeu):
    """Aucun identifiant de modèle ne doit subsister dans le texte soumis."""
    for paire in aveugler_jeu(jeu, GRAINE_DEFAUT):
        textes = (
            paire.demande.requete_id,
            paire.demande.requete,
            paire.demande.reponse_reference,
            paire.demande.reponse_candidate,
        )
        for texte in textes:
            for identifiant in CATALOGUE_MODELES:
                assert identifiant not in texte


def test_aveuglement_refuse_une_paire_trahi(jeu):
    """Une paire qui cite le modèle est refusée avant la notation."""
    trahi = replace(jeu[0], reponse_candidate=f"{MODELE_REFERENCE} a produit cette réponse")
    with pytest.raises(ValidationError, match="trace du modèle"):
        aveugler_item(trahi, GRAINE_DEFAUT)


def test_aveuglement_melange_l_ordre(jeu):
    """Une partie des paires est présentée dans l'ordre inverse."""
    paires = aveugler_jeu(jeu, GRAINE_DEFAUT)
    inversees = [paire for paire in paires if paire.ordre_inverse]
    assert 0 < len(inversees) < len(paires)


def test_aveuglement_reproductible_a_graine_fixee(jeu):
    """À graine fixée, l'ordre soumis est identique d'une exécution à l'autre."""
    premier = aveugler_jeu(jeu, GRAINE_DEFAUT)
    second = aveugler_jeu(jeu, GRAINE_DEFAUT)
    assert [paire.ordre_inverse for paire in premier] == [paire.ordre_inverse for paire in second]
    assert [paire.demande for paire in premier] == [paire.demande for paire in second]


def test_les_deux_ordres_d_une_paire_restent_distincts(jeu):
    """Mélanger l'ordre ne doit jamais remettre les deux réponses à la même place."""
    for graine in range(5):
        groupes: dict[str, list[tuple[str, str]]] = {}
        for paire in aveugler_jeu(jeu, graine):
            if paire.item.cle_paire:
                groupes.setdefault(paire.item.cle_paire, []).append(
                    (paire.demande.reponse_reference, paire.demande.reponse_candidate)
                )
        for ordres in groupes.values():
            assert len(ordres) == 2
            assert ordres[0] != ordres[1]


def test_aveuglement_change_avec_la_graine(jeu):
    """Deux graines différentes ne produisent pas le même ordre."""
    premier = [paire.ordre_inverse for paire in aveugler_jeu(jeu, 1)]
    second = [paire.ordre_inverse for paire in aveugler_jeu(jeu, 2)]
    assert premier != second


# --- mesures ----------------------------------------------------------------


def test_juge_parfait_obtient_un_accord_parfait(rapport_parfait):
    """Un juge qui rend la vérité marque 100 % d'accord et aucun biais."""
    mesures = rapport_parfait.mesures
    assert mesures.accord_gold.taux == 1.0
    assert mesures.faux_accept.taux == 0.0
    assert mesures.faux_rejet.taux == 0.0
    assert mesures.accord_pieges_dores.taux == 1.0
    assert mesures.biais_position == 0.0
    assert mesures.biais_verbosite == 0.0
    assert mesures.biais_format == 0.0
    assert mesures.nombre_items == NB_ITEMS


def test_les_effectifs_sont_comptes_par_famille(rapport_parfait):
    """Chaque famille est comptée : les effectifs servent au diagnostic."""
    effectifs = rapport_parfait.mesures.effectifs
    assert set(effectifs) == FAMILLES_ATTENDUES
    assert sum(effectifs.values()) == NB_ITEMS


def test_les_erreurs_du_juge_sont_comptees(jeu):
    """Deux verdicts inversés se voient dans les taux de faux-accept et faux-rejet."""
    verite = {item.identifiant: item.gold_equivalent for item in jeu}
    inverse = [
        next(item.identifiant for item in jeu if item.famille is FamilleCalibration.EQUIVALENT),
        next(item.identifiant for item in jeu if item.famille is FamilleCalibration.NON_EQUIVALENT),
    ]
    verdicts = {
        identifiant: VerdictJuge(gold != (identifiant in inverse), "verdict du juge")
        for identifiant, gold in verite.items()
    }
    paires = aveugler_jeu(jeu, GRAINE_DEFAUT)
    juge = FakeJugeClient(verdicts=verdicts)
    mesures = mesurer(paires, juge.noter_lot([paire.demande for paire in paires]))
    assert mesures.accord_gold.taux == round((NB_ITEMS - 2) / NB_ITEMS, 6)
    assert mesures.faux_accept.taux == round(1 / NB_GOLD_FAUX, 6)
    assert mesures.faux_rejet.taux == round(1 / NB_GOLD_VRAI, 6)


def test_mesurer_refuse_des_tailles_differentes(jeu):
    """Verdicts et paires doivent s'aligner : sinon le calcul n'a pas de sens."""
    paires = aveugler_jeu(jeu, GRAINE_DEFAUT)
    with pytest.raises(ValidationError, match="tailles différentes"):
        mesurer(paires, [])


def test_chaque_item_rend_un_resultat(rapport_parfait):
    """Le rapport conserve un résultat par item, sans texte de requête."""
    assert len(rapport_parfait.resultats) == NB_ITEMS
    for resultat in rapport_parfait.resultats:
        assert resultat.famille in FAMILLES_ATTENDUES
        assert resultat.justification


# --- aptitude ---------------------------------------------------------------


def test_aptitude_apte_quand_toutes_les_conditions_sont_remplies():
    """Un juge sans biais et sans erreur est apte."""
    verdict = juger_aptitude(_mesures())
    assert verdict.aptitude is Aptitude.APTE
    assert verdict.motifs == ()


def test_aptitude_a_recalibrer_quand_un_seuil_de_biais_est_depasse():
    """Un biais mesuré au-delà de son seuil appelle une recalibration, pas un rejet."""
    verdict = juger_aptitude(_mesures(biais_position=0.30))
    assert verdict.aptitude is Aptitude.A_RECALIBRER
    assert any("biais de position" in motif for motif in verdict.motifs)


def test_aptitude_rejete_quand_le_faux_accept_creve_le_plafond():
    """Un juge qui valide n'importe quoi est rejeté, pas recalibré."""
    verdict = juger_aptitude(
        _mesures(
            faux_accept=intervalle_wilson(6, NB_GOLD_FAUX), accord_gold=intervalle_wilson(20, 35)
        )
    )
    assert verdict.aptitude is Aptitude.REJETE
    assert any("faux-accept" in motif for motif in verdict.motifs)


def test_aptitude_rejete_quand_les_pieges_dores_sont_rates():
    """Rater les pièges dorés, c'est ne plus savoir distinguer le vrai du faux."""
    verdict = juger_aptitude(
        _mesures(accord_pieges_dores=intervalle_wilson(3, 6), accord_gold=intervalle_wilson(24, 35))
    )
    assert verdict.aptitude is Aptitude.REJETE
    assert any("pièges dorés" in motif for motif in verdict.motifs)


def test_seuils_hors_bornes_refuses():
    """Un seuil hors de [0, 1] est une erreur de configuration, pas une mesure."""
    with pytest.raises(ValidationError, match="hors de"):
        SeuilsCalibration(accord_min=1.5)
    with pytest.raises(ValidationError, match="hors de"):
        SeuilsDerive(accord_max=-0.1)


# --- dérive -----------------------------------------------------------------


def _observation(version: str, jour: str, mesures: MesuresCalibration) -> ObservationCalibration:
    """Observation de calibration datée et rattachée à une version de prompt."""
    return ObservationCalibration(
        version_prompt=version,
        jour=jour,
        juge_id="juge-test",
        mesures=mesures,
        aptitude=juger_aptitude(mesures).aptitude,
    )


def test_derive_signalee_quand_le_juge_se_degrade():
    """Une baisse d'accord entre deux versions est une dérive, pas un bruit."""
    historique = HistoriqueCalibration("juge-test")
    historique.ajouter(_observation("prompt-a", "2026-11-01", _mesures()))
    historique.ajouter(
        _observation("prompt-b", "2026-11-12", _mesures(accord_gold=intervalle_wilson(28, 35)))
    )
    rapport = comparer_derive(historique)
    assert rapport.derive is True
    assert any("accord_gold" in motif for motif in rapport.motifs)
    assert rapport.prompt_modifie is True


def test_derive_absente_quand_rien_ne_bouge():
    """Deux mesures identiques ne signalent aucune dérive."""
    historique = HistoriqueCalibration("juge-test")
    historique.ajouter(_observation("prompt-a", "2026-11-01", _mesures()))
    historique.ajouter(_observation("prompt-a", "2026-11-12", _mesures()))
    rapport = comparer_derive(historique)
    assert rapport.derive is False
    assert rapport.motifs == ()


def test_derive_liste_les_preuves_invalidees():
    """Un juge qui dérive invalide les preuves passées : elles sont listées."""
    historique = HistoriqueCalibration("juge-test")
    historique.ajouter(_observation("prompt-a", "2026-11-01", _mesures()))
    historique.ajouter(
        _observation("prompt-b", "2026-11-12", _mesures(accord_gold=intervalle_wilson(28, 35)))
    )
    rapport = comparer_derive(historique)
    assert rapport.observations_concernees == ("prompt-a@2026-11-01",)


def test_historique_refuse_un_autre_juge():
    """Un historique ne mélange pas deux juges."""
    historique = HistoriqueCalibration("juge-test")
    with pytest.raises(ValidationError, match="autre juge"):
        historique.ajouter(
            ObservationCalibration(
                version_prompt="prompt-a",
                jour="2026-11-01",
                juge_id="juge-autre",
                mesures=_mesures(),
                aptitude=Aptitude.APTE,
            )
        )


def test_historique_vide_refuse_de_comparer():
    """Sans référence, aucune dérive n'est calculable."""
    with pytest.raises(ValidationError, match="vide"):
        comparer_derive(HistoriqueCalibration("juge-test"))


# --- bloc sérialisable --------------------------------------------------------


def test_bloc_calibration_est_serialisable(rapport_parfait):
    """Le bloc se sérialise en JSON et porte les mesures et les seuils."""
    bloc = bloc_calibration(rapport_parfait)
    assert json.loads(json.dumps(bloc))["verdict"] == "apte"
    assert bloc["sampleSize"] == NB_ITEMS
    assert bloc["goldAgreement"]["rate"] == 1.0
    assert bloc["thresholds"]["accord_min"] == 0.90
    assert bloc["drift"] is None


def test_bloc_calibration_ne_contient_aucun_texte(rapport_parfait, jeu):
    """ENF-CONF-01 : ni requête ni réponse ne sortent dans le bloc."""
    rendu = json.dumps(bloc_calibration(rapport_parfait), ensure_ascii=False)
    for item in jeu:
        assert item.requete not in rendu
        assert item.reponse_reference not in rendu
        assert item.reponse_candidate not in rendu


def test_bloc_calibration_rend_le_rapport_de_derive(rapport_parfait):
    """La dérive s'ajoute au bloc quand elle est calculée."""
    historique = HistoriqueCalibration(rapport_parfait.juge_id)
    historique.ajouter(rapport_parfait.observation())
    rapport = comparer_derive(historique)
    bloc = bloc_calibration(rapport_parfait, rapport)
    assert bloc["drift"]["drifted"] is False
    assert bloc["drift"]["referenceVersion"] == rapport_parfait.version_prompt


def test_version_prompt_courante_est_stable():
    """L'empreinte du prompt identifie la version : elle ne bouge pas d'un run à l'autre."""
    assert version_prompt_courante() == version_prompt_courante()
    assert version_prompt_courante().startswith("prompt-")


def test_calibrer_est_reproductible(jeu):
    """À graine fixée, la calibration rend le même rapport."""
    premier = bloc_calibration(calibrer(juge_parfait(jeu), jeu, graine=7))
    second = bloc_calibration(calibrer(juge_parfait(jeu), jeu, graine=7))
    assert premier == second
