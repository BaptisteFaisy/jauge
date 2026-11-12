"""Tests du trafic fantôme puis canary (piste n° 7, EF-BAS-01..03, §8.6).

Les invariants vérifiés ici sont ceux qui portent la promesse du module :

- une réponse fantôme **n'est jamais servie** au client (le candidat ne sert
  que des copies, 0 % du trafic) ;
- **aucune bascule sans approbation humaine** (ENF-IA-02) ;
- le **kill-switch du client** arrête la copie du trafic ;
- le passage au canari n'a lieu que si **tous** les critères explicites sont
  satisfaits, et l'ombre est retirée sinon.

Aucun réseau, aucun AWS : la passerelle est fictive et le juge en ligne aussi.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from jauge.api.killswitch import TraitementSuspenduError
from jauge.common.errors import EtatIncompatibleError, ValidationError
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application
from jauge.rollout.controller import FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.fantome import (
    PALIER_OMBRE,
    ControleurFantome,
    FakePasserelleFantome,
    ObservationOmbre,
    PhaseBascule,
    ReglesOmbre,
    decider_passage_canari,
)

CLIENT = "t-demo-a"
DUREE_HEURES = 30.0


def horloge_fixe(valeur: float):
    """Horloge injectée : le temps d'observation est maîtrisé, jamais subi."""
    return lambda: valeur


@pytest.fixture
def passerelle() -> FakePasserelleFantome:
    """Passerelle fictive : journal des copies et des règles créées."""
    return FakePasserelleFantome()


@pytest.fixture
def controleur_fantome(passerelle: FakePasserelleFantome) -> ControleurFantome:
    """Contrôleur de phase fantôme, horloge figée et une mesure disponible."""
    qualite = FakeQualiteEnLigne(
        [
            MesureEnLigne(
                taux_equivalence=0.97,
                intervalle_bas=0.95,
                taux_erreur=0.005,
                nombre_requetes=500,
            )
        ]
    )
    return ControleurFantome(passerelle, qualite, horloge=horloge_fixe(100.0))


def observation_satisfaisante() -> ObservationOmbre:
    """Observation qui satisfait tous les critères par défaut."""
    return ObservationOmbre(
        nombre_requetes=500,
        duree_heures=DUREE_HEURES,
        taux_equivalence=0.97,
        intervalle_bas=0.95,
        taux_erreur=0.005,
    )


def controleur(
    mesures: list[MesureEnLigne] | None = None, regles: ReglesOmbre | None = None
) -> tuple[ControleurFantome, FakePasserelleFantome]:
    """Contrôleur de phase fantôme, passerelle et juge en ligne fictifs."""
    passerelle = FakePasserelleFantome()
    qualite = FakeQualiteEnLigne(
        mesures
        or [
            MesureEnLigne(
                taux_equivalence=0.97,
                intervalle_bas=0.95,
                taux_erreur=0.005,
                nombre_requetes=500,
            )
        ]
    )
    controleur_fantome = ControleurFantome(passerelle, qualite, regles, horloge=horloge_fixe(100.0))
    return controleur_fantome, passerelle


def demarrer(
    controleur_fantome: ControleurFantome,
    application: Application,
    recommandation: Recommandation,
    approuve_par: str | None = "responsable-applicatif",
    garde=None,
):
    """Démarre la phase fantôme avec les garde-fous habituels."""
    return controleur_fantome.demarrer_ombre(
        CLIENT, application, recommandation, approuve_par, garde=garde
    )


# --- l'invariant central --------------------------------------------------------


def test_le_candidat_ne_sert_rien_en_phase_fantome(
    controleur_fantome: ControleurFantome, application: Application, recommandation: Recommandation
) -> None:
    """La règle d'ombre sert 0 % au candidat : le client reçoit la production."""
    etat = demarrer(controleur_fantome, application, recommandation)

    assert etat.regle_ombre.pourcentage_servi_cible == 0
    assert etat.phase is PhaseBascule.OMBRE
    assert etat.regle_ombre.modele_cible == recommandation.targetModel
    assert etat.regle_ombre.modele_demande == application.modele_actuel


def test_la_reponse_fantome_n_est_jamais_servie(
    application: Application, recommandation: Recommandation
) -> None:
    """Ce qui est copié n'est pas servi : les deux journaux sont disjoints."""
    controleur_fantome, passerelle = controleur()
    demarrer(controleur_fantome, application, recommandation)

    passerelle.servir(application.modele_actuel)
    passerelle.copier(application.modele_actuel, recommandation.targetModel)

    assert passerelle.servis == [application.modele_actuel]
    assert passerelle.copies == [(application.modele_actuel, recommandation.targetModel)]
    assert all(cible != application.modele_actuel for _, cible in passerelle.copies)


# --- garde-fous -----------------------------------------------------------------


def test_approbation_humaine_exigee(
    controleur_fantome: ControleurFantome, application: Application, recommandation: Recommandation
) -> None:
    """Sans approbateur, pas de trafic fantôme (ENF-IA-02)."""
    with pytest.raises(EtatIncompatibleError, match="approbation humaine"):
        demarrer(controleur_fantome, application, recommandation, approuve_par=None)


def test_recommandation_non_approuvee_refusee(
    controleur_fantome: ControleurFantome,
    application: Application,
    recommandation: Recommandation,
) -> None:
    """Une recommandation non approuvée n'ouvre aucune copie du trafic."""
    from jauge.models.recommendation import StatutRecommandation

    en_attente = recommandation.model_copy(
        update={"status": StatutRecommandation.EN_ATTENTE_APPROBATION}
    )

    with pytest.raises(EtatIncompatibleError, match="approuvée"):
        demarrer(controleur_fantome, application, en_attente)


def test_kill_switch_coupe_arrete_la_copie(
    controleur_fantome: ControleurFantome,
    passerelle: FakePasserelleFantome,
    application: Application,
    recommandation: Recommandation,
) -> None:
    """Un client qui a coupé son traitement n'a pas son trafic copié."""

    def garde_coupee() -> None:
        raise TraitementSuspenduError("traitement suspendu", tenant_id=CLIENT)

    with pytest.raises(TraitementSuspenduError):
        demarrer(controleur_fantome, application, recommandation, garde=garde_coupee)

    assert passerelle.regles_ombre == {}


# --- critères du passage au canari ----------------------------------------------


def test_observation_satisfaisante_autorise_le_canari() -> None:
    """Tous les critères satisfaits : le passage est autorisé, motifs vides."""
    decision = decider_passage_canari(observation_satisfaisante(), ReglesOmbre())

    assert decision.autorise is True
    assert decision.motifs == ()
    assert all(decision.criteres.values())


@pytest.mark.parametrize(
    ("observation", "critere"),
    [
        (ObservationOmbre(10, DUREE_HEURES, 0.97, 0.95, 0.005), "volume_suffisant"),
        (ObservationOmbre(500, 1.0, 0.97, 0.95, 0.005), "duree_suffisante"),
        (ObservationOmbre(500, DUREE_HEURES, 0.80, 0.95, 0.005), "equivalence_suffisante"),
        (ObservationOmbre(500, DUREE_HEURES, 0.97, 0.80, 0.005), "intervalle_suffisant"),
        (ObservationOmbre(500, DUREE_HEURES, 0.97, 0.95, 0.50), "erreurs_maitrisees"),
    ],
)
def test_un_critere_non_satisfait_bloque_le_canari(
    observation: ObservationOmbre, critere: str
) -> None:
    """Un seul critère non satisfait suffit à bloquer, et il est nommé."""
    decision = decider_passage_canari(observation, ReglesOmbre())

    assert decision.autorise is False
    assert decision.criteres[critere] is False
    assert sum(1 for rempli in decision.criteres.values() if not rempli) == 1
    assert critere in decision.resume()


def test_canari_cree_la_regle_a_cinq_pourcents(
    application: Application, recommandation: Recommandation
) -> None:
    """Le canari sert 5 % du trafic réel, et l'ombre est retirée."""
    controleur_fantome, passerelle = controleur()
    etat = demarrer(controleur_fantome, application, recommandation)

    decision = controleur_fantome.passer_canari(etat, observation_satisfaisante())

    assert decision.autorise is True
    assert etat.phase is PhaseBascule.CANARI
    assert etat.regle_canari is not None
    assert etat.regle_canari.pourcentage_cible == 5
    assert etat.regle_canari.modele_cible == recommandation.targetModel
    # La règle d'ombre n'existe plus : le trafic n'est plus copié en double.
    assert passerelle.regles_ombre == {}
    assert len(passerelle.regles) == 1


def test_canari_refuse_retire_la_regle_d_ombre(
    application: Application, recommandation: Recommandation
) -> None:
    """Critères non satisfaits : l'ombre est retirée, aucun canari n'est créé."""
    controleur_fantome, passerelle = controleur()
    etat = demarrer(controleur_fantome, application, recommandation)
    observation = replace(observation_satisfaisante(), nombre_requetes=3)

    decision = controleur_fantome.passer_canari(etat, observation)

    assert decision.autorise is False
    assert etat.phase is PhaseBascule.ANNULEE
    assert etat.regle_canari is None
    assert passerelle.regles_ombre == {}
    assert passerelle.regles == {}


# --- mesure ---------------------------------------------------------------------


def test_mesure_de_l_ombre_agrege_sans_texte(
    application: Application, recommandation: Recommandation
) -> None:
    """La mesure ne garde que des agrégats : aucun texte de requête (ENF-CONF-01)."""
    controleur_fantome, _ = controleur()
    etat = demarrer(controleur_fantome, application, recommandation)

    observation = controleur_fantome.mesurer_ombre(CLIENT, application.id)

    assert observation.nombre_requetes == 500
    assert observation.taux_equivalence == 0.97
    assert observation.duree_heures == 0.0  # horloge figée : aucune durée écoulée
    assert etat.observations == [observation]
    assert set(ObservationOmbre.__dataclass_fields__) == {
        "nombre_requetes",
        "duree_heures",
        "taux_equivalence",
        "intervalle_bas",
        "taux_erreur",
    }


def test_la_mesure_interroge_le_juge_au_palier_ombre(
    application: Application, recommandation: Recommandation
) -> None:
    """La mesure porte le palier 0 : aucun trafic réel n'est servi au candidat."""
    qualite = FakeQualiteEnLigne(
        [
            MesureEnLigne(
                taux_equivalence=0.96,
                intervalle_bas=0.94,
                taux_erreur=0.01,
                nombre_requetes=321,
            )
        ]
    )
    controleur_fantome = ControleurFantome(
        FakePasserelleFantome(), qualite, horloge=horloge_fixe(100.0)
    )
    demarrer(controleur_fantome, application, recommandation)

    controleur_fantome.mesurer_ombre(CLIENT, application.id)

    assert qualite.appels == [(CLIENT, application.id, PALIER_OMBRE)]


def test_etat_inconnu_leve_une_erreur(controleur_fantome: ControleurFantome) -> None:
    """Demander l'état d'une application sans phase fantôme est une erreur explicite."""
    with pytest.raises(EtatIncompatibleError, match="phase fantôme"):
        controleur_fantome.etat(CLIENT, "application-inconnue")


@pytest.mark.parametrize(
    ("champ", "valeur"),
    [
        ("volume_minimum_requetes", 0),
        ("taux_equivalence_minimum", 1.5),
        ("taux_erreur_maximum", -0.1),
        ("pourcentage_canari", 0),
    ],
)
def test_seuils_hors_bornes_refuses(champ: str, valeur: float) -> None:
    """Des seuils qui ne seraient pas des seuils sont refusés à la construction."""
    with pytest.raises(ValidationError):
        ReglesOmbre(**{champ: valeur})
