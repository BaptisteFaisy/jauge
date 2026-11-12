"""Preuve que la suite de calibration **détecte** un juge biaisé (Top 10, n° 2).

C'est le test le plus important du lot : une suite de calibration qui ne
détecte rien ne vaut pas mieux qu'une hypothèse. Chaque double de
`tests.evaluation.juges_fictifs` injecte un biais précis — ancrage sur la
position, préférence pour la réponse longue, hostilité au formatage, laxisme —
et la suite doit le voir, le chiffrer, et refuser de déclarer le juge apte.

Aucun appel réseau, aucun AWS : les juges sont fictifs et le jeu est
synthétique.
"""

from __future__ import annotations

import pytest
from tests.evaluation.juges_fictifs import (
    juge_ancre_position,
    juge_hostile_format,
    juge_laxiste,
    juge_parfait,
    juge_severe,
    juge_verbeux,
)

from jauge.evaluation.aptitude_juge import (
    Aptitude,
    HistoriqueCalibration,
    SeuilsCalibration,
    comparer_derive,
)
from jauge.evaluation.calibration import GRAINE_DEFAUT, calibrer
from jauge.evaluation.jeux_calibration import CATALOGUE_MODELES, jeu_calibration_defaut

SEUILS = SeuilsCalibration()


@pytest.fixture
def jeu():
    """Le jeu de calibration par défaut."""
    return jeu_calibration_defaut()


def _calibrer(juge, jeu):
    """Calibration à graine fixée : les mesures sont comparables d'un test à l'autre."""
    return calibrer(juge, jeu, graine=GRAINE_DEFAUT)


def _condition(rapport, nom: str):
    """Condition nommée du verdict d'aptitude."""
    return next(condition for condition in rapport.aptitude.conditions if condition.nom == nom)


# --- référence : un juge sain passe -------------------------------------------


def test_un_juge_sain_est_apte(jeu):
    """Le juge qui rend la vérité passe toutes les conditions d'aptitude."""
    rapport = _calibrer(juge_parfait(jeu), jeu)
    assert rapport.aptitude.aptitude is Aptitude.APTE
    assert rapport.aptitude.motifs == ()
    assert all(condition.remplie for condition in rapport.aptitude.conditions)


def test_les_pieges_dores_sont_reconnus_par_un_juge_sain(jeu):
    """Les items-pièges dorés sont réussis à 100 % par un juge sain."""
    rapport = _calibrer(juge_parfait(jeu), jeu)
    assert rapport.mesures.accord_pieges_dores.taux == 1.0
    assert rapport.mesures.accord_pieges_dores.n == 6


# --- biais de position --------------------------------------------------------


def test_le_biais_de_position_est_detecte(jeu):
    """Un juge ancré sur la position diverge sur toutes les paires à deux ordres."""
    rapport = _calibrer(juge_ancre_position(), jeu)
    mesures = rapport.mesures
    assert mesures.paires_position == 4
    assert mesures.divergences_position == 4
    assert mesures.biais_position == 1.0
    assert mesures.biais_position > SEUILS.biais_position_max


def test_le_biais_de_position_empeche_l_aptitude(jeu):
    """Le biais de position suffit à refuser l'aptitude du juge."""
    rapport = _calibrer(juge_ancre_position(), jeu)
    assert rapport.aptitude.aptitude is not Aptitude.APTE
    assert _condition(rapport, "biais_position").remplie is False
    assert any("biais de position" in motif for motif in rapport.aptitude.motifs)


def test_un_juge_invariant_ne_diverge_pas(jeu):
    """Un juge qui ne dépend que du contenu ne diverge d'un ordre à l'autre."""
    rapport = _calibrer(juge_parfait(jeu), jeu)
    assert rapport.mesures.divergences_position == 0
    assert rapport.mesures.biais_position == 0.0


# --- biais de verbosité -------------------------------------------------------


def test_le_biais_de_verbosite_est_detecte(jeu):
    """Un juge convaincu par la longueur accepte les réponses longues et fausses."""
    rapport = _calibrer(juge_verbeux(jeu), jeu)
    mesures = rapport.mesures
    assert mesures.biais_verbosite > SEUILS.biais_verbosite_max
    assert mesures.faux_accept.taux > SEUILS.faux_accept_max


def test_le_biais_de_verbosite_empeche_l_aptitude(jeu):
    """Le biais de verbosité suffit à refuser l'aptitude du juge."""
    rapport = _calibrer(juge_verbeux(jeu), jeu)
    assert rapport.aptitude.aptitude is not Aptitude.APTE
    assert _condition(rapport, "biais_verbosite").remplie is False
    assert any("verbosité" in motif for motif in rapport.aptitude.motifs)


def test_le_biais_de_verbosite_n_est_pas_confondu_avec_la_position(jeu):
    """Les mesures sont spécifiques : un biais n'en déclenche pas un autre."""
    rapport = _calibrer(juge_verbeux(jeu), jeu)
    assert rapport.mesures.biais_position == 0.0
    rapport_position = _calibrer(juge_ancre_position(), jeu)
    assert rapport_position.mesures.biais_verbosite == 0.0


# --- biais de format ----------------------------------------------------------


def test_le_biais_de_format_est_detecte(jeu):
    """Un juge hostile au formatage rejette des paires au contenu identique."""
    rapport = _calibrer(juge_hostile_format(jeu), jeu)
    assert rapport.mesures.biais_format > SEUILS.biais_format_max
    assert rapport.mesures.faux_rejet.taux > 0.0


def test_le_biais_de_format_empeche_l_aptitude(jeu):
    """Le biais de format suffit à refuser l'aptitude du juge."""
    rapport = _calibrer(juge_hostile_format(jeu), jeu)
    assert rapport.aptitude.aptitude is not Aptitude.APTE
    assert _condition(rapport, "biais_format").remplie is False
    assert any("format" in motif for motif in rapport.aptitude.motifs)


# --- juge laxiste et juge sévère ----------------------------------------------


def test_un_juge_qui_valide_tout_a_un_faux_accept_eleve(jeu):
    """Le juge laxiste valide aussi les réponses fausses : faux-accept à 100 %."""
    rapport = _calibrer(juge_laxiste(), jeu)
    assert rapport.mesures.faux_accept.taux == 1.0
    assert rapport.mesures.faux_accept.taux > SEUILS.faux_accept_rejet


def test_un_juge_qui_valide_tout_est_rejete(jeu):
    """Un juge qui valide n'importe quoi ne peut pas servir de preuve."""
    rapport = _calibrer(juge_laxiste(), jeu)
    assert rapport.aptitude.aptitude is Aptitude.REJETE
    assert any("faux-accept" in motif for motif in rapport.aptitude.motifs)


def test_les_pieges_dores_demasquent_le_juge_laxiste(jeu):
    """Le juge laxiste ne réussit que la moitié des pièges dorés."""
    rapport = _calibrer(juge_laxiste(), jeu)
    assert rapport.mesures.accord_pieges_dores.taux == 0.5
    assert _condition(rapport, "accord_pieges_dores_rejet").remplie is False


def test_un_juge_qui_refuse_tout_est_rejete(jeu):
    """Le miroir du laxisme : tout rejeter casse le faux-rejet et l'accord."""
    rapport = _calibrer(juge_severe(), jeu)
    assert rapport.mesures.faux_rejet.taux == 1.0
    assert rapport.aptitude.aptitude is Aptitude.REJETE


# --- aveuglement --------------------------------------------------------------


def test_le_juge_ne_recoit_aucun_identifiant_de_modele(jeu):
    """Aucune trace du modèle producteur n'atteint le juge (ENF-CONF-01)."""
    juge = juge_parfait(jeu)
    _calibrer(juge, jeu)
    assert juge.appels, "le juge n'a pas été appelé"
    for demande in juge.appels:
        champs = (
            demande.requete_id,
            demande.requete,
            demande.reponse_reference,
            demande.reponse_candidate,
            demande.type_tache,
        )
        for champ in champs:
            for modele in CATALOGUE_MODELES:
                assert modele not in champ


def test_le_juge_recoit_tout_le_jeu(jeu):
    """Chaque item du jeu est soumis au juge, une fois."""
    juge = juge_parfait(jeu)
    _calibrer(juge, jeu)
    assert len(juge.appels) == len(jeu)


# --- dérive entre deux versions ----------------------------------------------


def test_la_derive_entre_deux_versions_est_signalee(jeu):
    """Un prompt qui dégrade le juge invalide les preuves de la version précédente."""
    historique = HistoriqueCalibration("juge-test")
    version_1 = calibrer(
        juge_parfait(jeu, "juge-test"),
        jeu,
        graine=GRAINE_DEFAUT,
        version_prompt="prompt-v1",
        jour="2026-11-01",
    )
    version_2 = calibrer(
        juge_verbeux(jeu, "juge-test"),
        jeu,
        graine=GRAINE_DEFAUT,
        version_prompt="prompt-v2",
        jour="2026-11-12",
    )
    historique.ajouter(version_1.observation())
    historique.ajouter(version_2.observation())
    rapport = comparer_derive(historique)
    assert rapport.derive is True
    assert rapport.prompt_modifie is True
    assert any(ecart.nom == "faux_accept" and ecart.depasse for ecart in rapport.ecarts)
    assert rapport.observations_concernees == ("prompt-v1@2026-11-01",)


def test_aucune_derive_entre_deux_versions_identiques(jeu):
    """Le même juge, le même prompt, le même jeu : aucune dérive."""
    historique = HistoriqueCalibration("juge-test")
    for jour in ("2026-11-01", "2026-11-12"):
        rapport = calibrer(
            juge_parfait(jeu, "juge-test"),
            jeu,
            graine=GRAINE_DEFAUT,
            version_prompt="prompt-v1",
            jour=jour,
        )
        historique.ajouter(rapport.observation())
    assert comparer_derive(historique).derive is False


# --- reproductibilité ---------------------------------------------------------


def test_la_detection_est_reproductible_a_graine_fixee(jeu):
    """À graine fixée, le biais mesuré est identique d'une exécution à l'autre."""
    premier = _calibrer(juge_ancre_position(), jeu).mesures
    second = _calibrer(juge_ancre_position(), jeu).mesures
    assert premier == second


def test_la_detection_survit_au_changement_de_graine(jeu):
    """Le biais est une propriété du juge, pas du tirage : il reste visible."""
    for graine in (1, 7, 42):
        rapport = calibrer(juge_ancre_position(), jeu, graine=graine)
        assert rapport.mesures.biais_position > SEUILS.biais_position_max
