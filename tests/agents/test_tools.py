"""Tests des onze outils (§12.1, §12.3, ENF-CONF-01, ENF-IA-01).

Ce qui est vérifié : les onze outils du §12.1 existent et sont répartis entre les
quatre agents, chaque retour est sérialisable et ne contient **aucun** texte de
requête ou de réponse, les garde-fous du §12.3 s'appliquent (plafond, liste
autorisée), et l'enregistrement d'une recommandation est interrompu jusqu'à
l'accord d'un humain.
"""

from __future__ import annotations

import json

import pytest

from jauge.agents.contexte import ContexteOutils
from jauge.agents.guardrails import CLES_INTERDITES
from jauge.agents.jeu_essai import CAMPAGNE, MODELE_HORS_LISTE, MODELE_MOYEN, MODELE_PETIT
from jauge.agents.tools import (
    OUTILS_ANALYSTE,
    OUTILS_LECTEUR,
    OUTILS_PLANIFICATEUR,
    OUTILS_REDACTEUR,
    TOUS_LES_OUTILS,
    creer_outils,
    outils_pour,
)
from jauge.common.errors import ApprobationRequiseError, PlafondDepasseError, ValidationError
from jauge.models.tenant import Application, Tenant


def _appel(nom: str, ctx: ContexteOutils, application: Application) -> object:
    """Appelle un outil avec des arguments valides (jeu d'essai)."""
    outils = creer_outils(ctx)
    arguments: dict[str, object] = {
        "query_cost_aggregates": {"application_id": application.id},
        "list_levers": {"application_id": application.id},
        "reconcile_with_bill": {"jours": 30},
        "get_task_profile": {"application_id": application.id},
        "list_candidate_models": {"application_id": application.id},
        "estimate_campaign_cost": {
            "application_id": application.id,
            "modeles_candidats": [MODELE_MOYEN],
        },
        "start_replay": {
            "application_id": application.id,
            "modeles_candidats": [MODELE_MOYEN],
        },
        "get_replay_results": {"campagne_id": CAMPAGNE},
        "get_failure_clusters": {"campagne_id": CAMPAGNE},
        "get_recommendation_inputs": {"campagne_id": CAMPAGNE},
        "save_recommendation": {
            "campagne_id": CAMPAGNE,
            "risques": ["Tickets en langue rare : 4 échecs sur 5"],
        },
    }[nom]
    return outils[nom](**arguments)


def test_onze_outils_declares() -> None:
    """Les onze outils du §12.1 sont déclarés, sans doublon et sans oubli."""
    assert len(TOUS_LES_OUTILS) == 11
    assert len(set(TOUS_LES_OUTILS)) == 11
    groupes = OUTILS_ANALYSTE + OUTILS_PLANIFICATEUR + OUTILS_LECTEUR + OUTILS_REDACTEUR
    assert set(groupes) == set(TOUS_LES_OUTILS)


def test_repartition_par_agent() -> None:
    """La répartition suit le tableau du §12.1."""
    assert OUTILS_ANALYSTE == ("query_cost_aggregates", "list_levers", "reconcile_with_bill")
    assert OUTILS_PLANIFICATEUR == (
        "get_task_profile",
        "list_candidate_models",
        "estimate_campaign_cost",
        "start_replay",
    )
    assert OUTILS_LECTEUR == ("get_replay_results", "get_failure_clusters")
    assert OUTILS_REDACTEUR == ("get_recommendation_inputs", "save_recommendation")


def test_chaque_outil_expose_nom_et_description(contexte: ContexteOutils) -> None:
    """Un outil sans description n'est pas utilisable par un modèle."""
    outils = creer_outils(contexte)
    assert set(outils) == set(TOUS_LES_OUTILS)
    for nom, outil in outils.items():
        assert outil.tool_name == nom
        assert outil.tool_spec["description"]
        assert outil.tool_spec["inputSchema"]


def test_outils_pour_refuse_un_nom_inconnu(contexte: ContexteOutils) -> None:
    """Une faute de frappe dans un nom d'outil casse à la construction, pas à l'exécution."""
    with pytest.raises(KeyError):
        outils_pour(contexte, ("outil_inexistant",))


@pytest.mark.parametrize("nom", TOUS_LES_OUTILS)
def test_aucun_texte_de_requete_dans_les_retours(
    nom: str, contexte: ContexteOutils, application: Application
) -> None:
    """§12.3 et ENF-CONF-01 : aucun outil ne renvoie de texte de requête ou de réponse."""
    resultat = _appel(nom, contexte, application)
    charge = json.dumps(resultat, ensure_ascii=False, default=str).lower()
    for cle in CLES_INTERDITES:
        assert f'"{cle}"' not in charge
    for interdit in ("texte_requete", "reponse_candidate", "reponse_reference"):
        assert interdit not in charge


def test_agregats_de_cout(contexte: ContexteOutils, application: Application) -> None:
    """`query_cost_aggregates` renvoie les totaux et la tendance calculés par le code."""
    resultat = creer_outils(contexte)["query_cost_aggregates"](application_id=application.id)
    assert resultat["application_id"] == application.id
    assert resultat["nombre_requetes"] == sum(1_000 + 100 * index for index in range(10))
    assert resultat["jours_couverts"] == 10
    # Seconde moitié plus chère que la première : la variation est positive.
    assert resultat["variation_seconde_moitie_pourcentage"] > 0


def test_leviers_tries_par_economie(contexte: ContexteOutils, application: Application) -> None:
    """`list_levers` classe par économie mensuelle décroissante."""
    resultat = creer_outils(contexte)["list_levers"](application_id=application.id)
    economies = [levier["economie_mensuelle_estimee_usd"] for levier in resultat["leviers"]]
    assert economies == sorted(economies, reverse=True)
    assert resultat["economie_totale_estimee_usd"] == pytest.approx(1_112.0)


def test_rapprochement(contexte: ContexteOutils) -> None:
    """`reconcile_with_bill` calcule l'écart et sa conformité à la cible de 3 %."""
    resultat = creer_outils(contexte)["reconcile_with_bill"](jours=30)
    assert resultat["jours_rapproches"] == 3
    assert resultat["cout_facture_usd"] == pytest.approx(61.2)
    assert resultat["ecart_pourcentage"] == pytest.approx(1.96)
    assert resultat["conforme"] is True


def test_profil_de_tache(contexte: ContexteOutils, application: Application) -> None:
    """`get_task_profile` fournit type de tâche, contrôles et seuils (§13.1)."""
    resultat = creer_outils(contexte)["get_task_profile"](application_id=application.id)
    assert resultat["controles_deterministes"] == ["format_json", "champs_obligatoires"]
    assert resultat["seuil_qualite"] == application.seuil_qualite
    assert resultat["volume_mensuel_requetes"] > 0


def test_candidats_filtres_par_la_liste_autorisee(
    contexte: ContexteOutils, application: Application
) -> None:
    """§12.3 : les candidats hors liste d'autorisation ne sont jamais renvoyés."""
    resultat = creer_outils(contexte)["list_candidate_models"](application_id=application.id)
    identifiants = [candidat["modele_id"] for candidat in resultat["candidats"]]
    assert MODELE_MOYEN in identifiants
    assert MODELE_PETIT in identifiants
    assert MODELE_HORS_LISTE not in identifiants


def test_estimation_refuse_un_candidat_non_autorise(
    contexte: ContexteOutils, application: Application
) -> None:
    """§12.3 : un candidat absent de la liste autorisée est refusé avant calcul."""
    outil = creer_outils(contexte)["estimate_campaign_cost"]
    with pytest.raises(ValidationError):
        outil(application_id=application.id, modeles_candidats=[MODELE_HORS_LISTE])


def test_lancement_refuse_au_dessus_du_plafond(
    jeu, client: Tenant, contexte: ContexteOutils, application: Application
) -> None:
    """ENF-COUT-01 : le lancement est refusé si le coût estimé dépasse le plafond."""
    jeu.tenants[client.id] = client.model_copy(update={"plafond_campagne_usd": 0.01})
    outil = creer_outils(contexte)["start_replay"]
    with pytest.raises(PlafondDepasseError):
        outil(application_id=application.id, modeles_candidats=[MODELE_MOYEN])
    assert set(jeu.campagnes) == {CAMPAGNE}


def test_lancement_reussi_trace_la_tache(
    contexte: ContexteOutils, application: Application, session
) -> None:
    """ENF-OBS-01 : chaque tâche de rejeu laisse une trace et alimente la session."""
    resultat = creer_outils(contexte)["start_replay"](
        application_id=application.id, modeles_candidats=[MODELE_MOYEN], taille_echantillon=100
    )
    assert resultat["statut"] == "planifiee"
    assert resultat["taille_echantillon"] == 100
    traces = contexte.traceur.traces()
    assert [trace.operation for trace in traces] == ["tache_rejeu"]
    assert traces[0].details["campagne_rejeu_id"] == resultat["campagne_id"]
    assert session.campagne_rejeu_id == resultat["campagne_id"]


def test_resultats_et_familles(contexte: ContexteOutils) -> None:
    """Les résultats et les familles en échec sont des agrégats, pas des extraits."""
    outils = creer_outils(contexte)
    resultats = outils["get_replay_results"](campagne_id=CAMPAGNE)["resultats"]
    assert {resultat["modele_id"] for resultat in resultats} == {
        "modele-grand-demo",
        MODELE_MOYEN,
        MODELE_PETIT,
    }
    clusters = outils["get_failure_clusters"](campagne_id=CAMPAGNE)["clusters"]
    assert clusters[0]["famille"] == "tickets en langue rare"
    assert clusters[0]["nombre_echecs"] == 4


def test_entrees_de_recommandation_exposent_les_valeurs_autorisees(
    contexte: ContexteOutils,
) -> None:
    """`get_recommendation_inputs` donne les valeurs calculées et les nombres citables."""
    resultat = creer_outils(contexte)["get_recommendation_inputs"](campagne_id=CAMPAGNE)
    assert resultat["quality"]["equivalenceRate"] == pytest.approx(0.975)
    assert resultat["cost"]["expectedMonthlySaving"] == pytest.approx(992.0)
    assert resultat["plan_bascule"] == [10, 50, 100]
    assert 0.975 in resultat["valeurs_autorisees_dans_les_textes"]


def test_enregistrement_interrompu_par_la_validation_humaine(
    contexte: ContexteOutils, jeu, session
) -> None:
    """§12.3 : le premier appel ouvre une demande de validation et n'enregistre rien."""
    resultat = creer_outils(contexte)["save_recommendation"](
        campagne_id=CAMPAGNE, risques=["Tickets en langue rare : 4 échecs sur 5"]
    )
    assert resultat["statut"] == "validation_humaine_requise"
    assert resultat["demande_id"]
    assert not jeu.recommandations
    assert session.validation_id == resultat["demande_id"]


def test_enregistrement_apres_accord_humain(contexte: ContexteOutils, jeu, session) -> None:
    """L'enregistrement n'a lieu qu'après approbation de la demande (§12.3)."""
    outils = creer_outils(contexte)
    interruption = outils["save_recommendation"](
        campagne_id=CAMPAGNE, risques=["Tickets en langue rare : 4 échecs sur 5"]
    )
    jeu.approuver(interruption["demande_id"])
    resultat = outils["save_recommendation"](
        campagne_id=CAMPAGNE,
        risques=["Tickets en langue rare : 4 échecs sur 5"],
        validation_humaine_id=interruption["demande_id"],
    )
    assert resultat["statut"] == "enregistree"
    assert len(jeu.recommandations) == 1
    assert session.terminee is True
    assert session.recommandation_id == resultat["recommandation_id"]


def test_enregistrement_refuse_un_statut_approuve(contexte: ContexteOutils, jeu) -> None:
    """Un agent ne peut pas enregistrer une recommandation « déjà approuvée » (§12.2)."""
    outil = creer_outils(contexte)["save_recommendation"]
    with pytest.raises(ApprobationRequiseError):
        outil(campagne_id=CAMPAGNE, risques=[], statut="APPROVED")
    assert not jeu.recommandations


def test_enregistrement_refuse_sans_accord(contexte: ContexteOutils, jeu) -> None:
    """Une demande non approuvée ne permet pas d'enregistrer la recommandation."""
    outils = creer_outils(contexte)
    interruption = outils["save_recommendation"](campagne_id=CAMPAGNE, risques=[])
    with pytest.raises(Exception) as capture:
        outils["save_recommendation"](
            campagne_id=CAMPAGNE, risques=[], validation_humaine_id=interruption["demande_id"]
        )
    assert getattr(capture.value, "code", "") == "approval_required"
    assert not jeu.recommandations
