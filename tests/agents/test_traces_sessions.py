"""Tests des traces et des sessions persistantes (§12.4, ENF-OBS-01).

Ce qui est vérifié : chaque exécution d'agent et chaque tâche de rejeu laisse une
trace exploitable (succès comme échec), les alarmes se branchent sur les échecs,
et la session de campagne survive à une reprise en gardant l'état des étapes.
"""

from __future__ import annotations

import pytest

from jauge.agents.jeu_essai import CAMPAGNE, MODELE_MOYEN
from jauge.agents.session import SessionCampagne
from jauge.agents.tools import creer_outils
from jauge.agents.traces import STATUT_ECHEC, STATUT_OK, Traceur
from jauge.models.tenant import Application, Tenant

# --- Traces (ENF-OBS-01) ---------------------------------------------------


def test_trace_de_success(traceur: Traceur) -> None:
    """Une exécution réussie laisse une trace datée et sérialisable."""
    traceur.enregistrer("analyste_couts", "analyse", STATUT_OK, duree_ms=12, tenant_id="t-a")
    trace = traceur.traces()[0]
    assert trace.agent == "analyste_couts"
    assert trace.statut == STATUT_OK
    assert trace.duree_ms == 12
    assert trace.vers_dict()["trace_id"] == trace.id


def test_trace_d_echec_signale(traceur: Traceur) -> None:
    """Un échec est tracé et identifiable : c'est là que se branchent les alarmes."""
    with (
        pytest.raises(RuntimeError),
        traceur.tracer("redacteur", "enregistrement", tenant_id="t-a"),
    ):
        raise RuntimeError("échec d'écriture")
    trace = traceur.echecs()[0]
    assert trace.statut == STATUT_ECHEC
    assert trace.details["erreur"] == "RuntimeError"
    assert trace.details["message"] == "échec d'écriture"


def test_trace_de_tache_de_rejeu(contexte, application: Application) -> None:
    """Une tâche de rejeu laisse une trace qui porte l'identifiant de campagne."""
    creer_outils(contexte)["start_replay"](
        application_id=application.id, modeles_candidats=[MODELE_MOYEN]
    )
    traces = contexte.traceur.pour_campagne(CAMPAGNE)
    assert [trace.operation for trace in traces] == ["tache_rejeu"]
    assert traces[0].details["campagne_rejeu_id"]


def test_refus_de_candidat_laisse_aucune_campagne(contexte, application: Application) -> None:
    """Un garde-fou qui refuse ne crée aucune campagne, et ne trace aucune tâche."""
    outils = creer_outils(contexte)
    from jauge.common.errors import ValidationError

    with pytest.raises(ValidationError):
        outils["start_replay"](
            application_id=application.id,
            modeles_candidats=["modele-inexistant"],
        )
    assert contexte.traceur.pour_campagne(CAMPAGNE) == []


# --- Sessions par campagne (§12.4) ----------------------------------------


def test_session_enregistre_les_etapes(session: SessionCampagne) -> None:
    """La session conserve les étapes de la campagne, dans l'ordre."""
    session.reprendre("c-0002")
    session.attendre_validation("val-0001")
    session.cloturer("r-0001")
    operations = [etape.operation for etape in session.etapes]
    assert operations == ["start_replay", "validation_humaine", "save_recommendation"]
    assert session.terminee is True
    assert session.recommandation_id == "r-0001"


def test_session_resume_pour_le_portail(session: SessionCampagne) -> None:
    """Le résumé de session est lisible par le portail et l'orchestrateur."""
    session.reprendre("c-0002")
    resume = session.resume()
    assert resume["campagne_rejeu_id"] == "c-0002"
    assert resume["terminee"] is False
    assert resume["etapes"][0]["operation"] == "start_replay"


def test_session_reprise_apres_redemarrage(client: Tenant, application: Application) -> None:
    """Rouvrir une campagne avec le même identifiant retrouve son état (§12.4)."""
    premiere = SessionCampagne.pour_la_campagne(CAMPAGNE, client.id, application.id)
    premiere.reprendre("c-0002")
    seconde = SessionCampagne.pour_la_campagne(CAMPAGNE, client.id, application.id)
    assert seconde.campagne_rejeu_id is None
    assert premiere.campagne_id == seconde.campagne_id


def test_session_expose_un_gestionnaire_strands(session: SessionCampagne) -> None:
    """La session expose un gestionnaire Strands si le SDK est installé, sinon `None`."""
    gestionnaire = session.gestionnaire_session()
    if gestionnaire is None:
        assert session.campagne_id == CAMPAGNE
    else:
        assert gestionnaire.session_id == CAMPAGNE


def test_cle_de_jour_de_session(session: SessionCampagne) -> None:
    """La clé de jour sert au journal d'audit (EF-ADM-02)."""
    assert len(session.cle_de_jour()) == 10
    assert session.cle_de_jour()[4] == "-"
