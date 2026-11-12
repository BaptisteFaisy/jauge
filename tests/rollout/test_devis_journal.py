"""Tests du devis de shadow et du journal des étapes (piste n° 7).

Le shadow a un prix : ce prix se connaît **avant** le lancement, et un devis au
delà du plafond du client est refusé (ENF-COUT-01). Chaque étape laisse une
trace exploitable par l'audit (EF-ADM-02), sans aucun texte de requête ni de
réponse (ENF-CONF-01).

Aucun réseau, aucun AWS : la table de tarifs est celle des fixtures, et les
mesures d'observation sont fournies par le contrôleur.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import PlafondDepasseError, ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import TarifModele
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application
from jauge.rollout.controller import FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.devis import BornesEchantillon, estimer_cout_shadow, verifier_plafond
from jauge.rollout.fantome import (
    ControleurFantome,
    FakePasserelleFantome,
    ObservationOmbre,
    PhaseBascule,
)
from jauge.rollout.journal import JournalEtape, JournalEtapes

DATE_TABLE = date(2026, 6, 1)
PLAFOND = 10.0


def devis(tarifs: list[TarifModele], hauteur: float = 1.0, plafond: float = PLAFOND):
    """Devis d'un shadow : 200 requêtes du candidat, plus le jugement des paires."""
    return estimer_cout_shadow(
        tarifs=TableTarifs(list(tarifs)),
        region=REGION,
        a_la_date=DATE_TABLE,
        modele_candidat=PETIT_MODELE,
        modele_judge=GRAND_MODELE,
        nombre_requetes=200,
        jetons_entree_moyens=400 * hauteur,
        jetons_sortie_moyens=100 * hauteur,
        jetons_entree_juge=500,
        jetons_sortie_juge=200,
        plafond_usd=plafond,
    )


def qualite_fictive() -> FakeQualiteEnLigne:
    """Juge en ligne fictif : une mesure disponible."""
    return FakeQualiteEnLigne(
        [
            MesureEnLigne(
                taux_equivalence=0.97,
                intervalle_bas=0.95,
                taux_erreur=0.005,
                nombre_requetes=500,
            )
        ]
    )


# --- devis ----------------------------------------------------------------------


def test_le_devis_est_chiffre_avant_le_lancement(tarifs: list[TarifModele]) -> None:
    """Le devis porte le coût du candidat, celui du juge et le total."""
    estimation = devis(tarifs)

    assert estimation.nombre_requetes == 200
    assert estimation.cout_candidat_usd > 0
    assert estimation.cout_judge_usd > 0
    assert estimation.cout_total_usd == pytest.approx(
        estimation.cout_candidat_usd + estimation.cout_judge_usd, rel=1e-6
    )
    assert estimation.sous_plafond is True
    assert estimation.marge_usd() == pytest.approx(PLAFOND - estimation.cout_total_usd, rel=1e-6)


def test_un_devis_au_dela_du_plafond_est_refuse(tarifs: list[TarifModele]) -> None:
    """Le plafond du client fait foi : le devis est refusé, pas rogné."""
    estimation = devis(tarifs, plafond=PLAFOND)

    assert estimation.sous_plafond is True
    # Un plafond plus bas que le devis : le shadow est refusé, pas rogné.
    with pytest.raises(PlafondDepasseError):
        verifier_plafond(estimation, 0.0001)


def test_bornes_de_l_echantillon() -> None:
    """Deux bornes s'appliquent ensemble : taille maximale et part du trafic."""
    bornes = BornesEchantillon(taille_max=100, taux=0.1)

    assert bornes.taille_pour(1_000) == 100
    assert bornes.taille_pour(50) == 5
    assert bornes.taille_pour(0) == 0
    with pytest.raises(ValidationError):
        BornesEchantillon(taille_max=0)
    with pytest.raises(ValidationError):
        BornesEchantillon(taille_max=10, taux=1.5)


# --- journal --------------------------------------------------------------------


def test_le_journal_trace_les_etapes() -> None:
    """Chaque étape laisse une trace horodatée, consultable par étape."""
    journal = JournalEtapes()
    journal.consigner(
        JournalEtape(
            etape="ombre",
            tenant_id="t-demo-a",
            application_id="app",
            qui="responsable",
            quand="2026-11-12T10:00:00+00:00",
            decision="ombre_demarree",
        )
    )

    assert len(journal.entrees()) == 1
    assert journal.entrees("ombre")[0].decision == "ombre_demarree"
    assert journal.entrees("canari") == []
    assert journal.derniere("ombre") is not None
    assert journal.derniere("canari") is None


def test_les_entrees_du_journal_ne_portent_que_des_nombres() -> None:
    """Une entrée sérialisable ne contient ni texte de requête ni de réponse."""
    entree = JournalEtape(
        etape="ombre",
        tenant_id="t-demo-a",
        application_id="app",
        qui="responsable",
        quand="2026-11-12T10:00:00+00:00",
        decision="ombre_demarree",
        seuils=(("volume_minimum_requetes", 200.0),),
        mesures=(("nombre_requetes", 500.0),),
    )

    vue = entree.vers_dict()

    assert vue["seuils"] == (("volume_minimum_requetes", 200.0),)
    assert vue["mesures"] == (("nombre_requetes", 500.0),)
    assert all(
        isinstance(valeur, str) for cle, valeur in vue.items() if cle not in ("seuils", "mesures")
    )


# --- branchement dans la phase fantôme -----------------------------------------


def test_un_devis_au_dela_du_plafond_empêche_l_ombre(
    application: Application, recommandation: Recommandation, tarifs: list[TarifModele]
) -> None:
    """Le devis est vérifié avant toute copie : rien n'est copié si c'est trop cher."""
    passerelle = FakePasserelleFantome()
    controleur = ControleurFantome(passerelle, qualite_fictive())

    with pytest.raises(PlafondDepasseError):
        controleur.demarrer_ombre(
            "t-demo-a",
            application,
            recommandation,
            "responsable",
            devis=devis(tarifs),
            plafond_usd=0.0001,
        )

    assert passerelle.regles_ombre == {}


def test_le_journal_trace_l_ombre_puis_le_canari(
    application: Application, recommandation: Recommandation
) -> None:
    """Les étapes sont journalisées : l'audit voit ce qui s'est passé."""
    journal = JournalEtapes()
    controleur = ControleurFantome(FakePasserelleFantome(), qualite_fictive(), journal=journal)

    etat = controleur.demarrer_ombre("t-demo-a", application, recommandation, "responsable")
    observation = ObservationOmbre(
        nombre_requetes=500,
        duree_heures=30.0,
        taux_equivalence=0.97,
        intervalle_bas=0.95,
        taux_erreur=0.005,
    )
    controleur.passer_canari(etat, observation)

    decisions = [entree.decision for entree in journal.entrees()]
    assert "ombre_demarree" in decisions
    assert "canari_ouvert" in decisions
    assert etat.phase is PhaseBascule.CANARI
