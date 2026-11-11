"""Recette R4 et R5 — campagne de rejeu et candidat sous le seuil (§22, §13).

- **R4** « Campagne sur 200 requêtes et 3 candidats » : pour chaque candidat,
  la campagne produit un résultat complet — **taux d'équivalence, intervalle de
  confiance, latence et coût** (§13.3, EF-EVA-03, EF-REJ-04).
- **R5** « Candidat sous le seuil » : le candidat est **rejeté**, avec les
  **familles de requêtes en échec** (§13.4, §13.6).

La chaîne exécutée est celle des modules réels : `jauge.replay` (tirage,
rejeu), `jauge.evaluation` (juge, contrôles déterministes, règle de décision).
Les seuls adaptateurs fictifs sont ceux fournis par ces modules
(`FakeModeleClient`, `FakeJugeClient`) ; le trafic vient de `demo/`.
Aucun réseau.
"""

from __future__ import annotations

import pytest
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT
from demo.socle import charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE
from tests.recette.conftest import entetes
from tests.recette.outillage import (
    MODELE_DISTILLE,
    campagne,
    echantillon_de,
    echantillon_ids,
    famille_question,
    familles_par_strate,
    juge_avec_echecs,
    rejouer,
    reponses_imposees,
    resultat_candidat,
    resultat_reference,
    table_avec_distille,
    textes_de,
)

from jauge.evaluation.decision import SeuilsDecision, decider
from jauge.models.campaign import Campagne, ResultatCandidat
from jauge.models.tenant import Application
from jauge.replay.client import FakeModeleClient
from jauge.replay.executor import RejeuComplet
from jauge.store.memory import MemoryStore

APP_CLASSEMENT = "ticket-classifier"
APP_ASSISTANT = "support-assistant"
TAILLE = 200
CANDIDATS = (MOYEN_MODELE, PETIT_MODELE, MODELE_DISTILLE)
TYPE_TACHE_CLASSIFICATION = "classification"
TYPE_TACHE_REDACTION = "redaction"
SUJET_CIBLE = "Le contrat peut-il être transféré vers une autre entité ?"


def _records(app_id: str, jours: int) -> list[JournalInvocation]:
    """Trafic synthétique d'une application de démonstration."""
    return [
        record
        for record in generer_trafic(graine=GRAINE_DEFAUT, jours=jours, date_fin=DATE_FIN_DEFAUT)
        if record.application_id == app_id
    ]


def _echecs(identifiants: list[str], pas: int, nombre: int) -> set[str]:
    """Jeu déterministe de requêtes en échec."""
    return set(identifiants[::pas][:nombre])


def _resultats(
    rejeu: RejeuComplet,
    echantillon,
    en_echec: dict[str, set[str]],
    en_variante: dict[str, set[str]],
) -> tuple[dict[str, ResultatCandidat], ResultatCandidat]:
    """Résultat de chaque candidat et résultat de la référence."""
    resultats: dict[str, ResultatCandidat] = {}
    for modele in CANDIDATS:
        juge = juge_avec_echecs(en_echec.get(modele, set()) | en_variante.get(modele, set()))
        resultats[modele] = resultat_candidat(
            rejeu, modele, juge, familles_par_strate(echantillon), TYPE_TACHE_CLASSIFICATION
        )
    return resultats, resultat_reference(rejeu, GRAND_MODELE)


def _verifier_indicateurs(
    resultats: dict[str, ResultatCandidat], reference: ResultatCandidat
) -> None:
    """Chaque candidat expose taux, intervalle, latence et coût (EF-EVA-03, EF-REJ-04)."""
    assert reference.est_reference is True
    assert reference.nombre_requetes_rejouees == TAILLE
    for modele, resultat in resultats.items():
        assert resultat.nombre_requetes_rejouees == TAILLE, modele
        assert 0.0 <= resultat.taux_equivalence <= 1.0, modele
        assert resultat.intervalle_confiance_bas <= resultat.taux_equivalence, modele
        assert resultat.taux_equivalence <= resultat.intervalle_confiance_haut, modele
        assert resultat.latence_p50_ms > 0, modele
        assert resultat.latence_p95_ms >= resultat.latence_p50_ms, modele
        assert resultat.cout_par_requete_usd > 0.0, modele


def _verifier_decisions(resultats: dict[str, ResultatCandidat], application: Application) -> None:
    """La règle de décision du §13.4 s'applique aux résultats de la campagne."""
    seuils = SeuilsDecision.depuis_application(application)
    decisions = {modele: decider(resultat, seuils) for modele, resultat in resultats.items()}
    assert decisions[MOYEN_MODELE].propose is True
    assert decisions[PETIT_MODELE].propose is True
    assert decisions[MODELE_DISTILLE].propose is False
    # Le candidat qui dégrade la catégorie échoue aussi au contrôle déterministe.
    assert resultats[MODELE_DISTILLE].echecs_deterministes > 0


def _executer_campagne(
    records: list[JournalInvocation],
    echantillon,
    en_echec: dict[str, set[str]],
    en_variante: dict[str, set[str]],
) -> tuple[RejeuComplet, dict[str, ResultatCandidat], ResultatCandidat]:
    """Rejoue l'échantillon et consolide le résultat de chaque candidat."""
    socle = charger_socle_demo()
    table = table_avec_distille(socle.tarifs)
    reponses = reponses_imposees(records, (GRAND_MODELE, *CANDIDATS), en_echec, en_variante)
    client_modele = FakeModeleClient(jetons_sortie=17, latence_ms=180, textes=reponses)
    rejeu = rejouer(
        echantillon,
        modele_actuel=GRAND_MODELE,
        candidats=CANDIDATS,
        table=table,
        textes=textes_de(records),
        client_modele=client_modele,
        date_campagne=DATE_FIN_DEFAUT,
    )
    resultats, reference = _resultats(rejeu, echantillon, en_echec, en_variante)
    return rejeu, resultats, reference


def _verifier_taux(
    resultats: dict[str, ResultatCandidat],
    en_echec: dict[str, set[str]],
    en_variante: dict[str, set[str]],
) -> None:
    """Le taux mesuré correspond exactement aux verdicts du juge."""
    for modele, resultat in resultats.items():
        echecs_modele = len(en_echec.get(modele, set())) + len(en_variante.get(modele, set()))
        assert resultat.taux_equivalence == pytest.approx((TAILLE - echecs_modele) / TAILLE), modele


def _verifier_couts(resultats: dict[str, ResultatCandidat], reference: ResultatCandidat) -> None:
    """Le coût par requête suit les tarifs : grand > moyen > petit > distillé."""
    couts = {modele: resultat.cout_par_requete_usd for modele, resultat in resultats.items()}
    assert couts[MOYEN_MODELE] < reference.cout_par_requete_usd
    assert couts[PETIT_MODELE] < couts[MOYEN_MODELE]
    assert couts[MODELE_DISTILLE] < couts[PETIT_MODELE]


def _verifier_api(client_api: TestClient, tenant, campagne_creee: Campagne) -> None:
    """La campagne est publiée : résultats par candidat sur l'API (§15)."""
    reponse = client_api.get(
        f"/v1/campaigns/{campagne_creee.id}/results", headers=entetes(tenant.id)
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert len(corps) == 4
    assert {resultat["modele_id"] for resultat in corps} == {GRAND_MODELE, *CANDIDATS}
    for resultat in corps:
        assert resultat["nombre_requetes_rejouees"] == TAILLE
        assert resultat["cout_par_requete_usd"] > 0
        assert resultat["latence_p50_ms"] > 0


def test_r4_campagne_200_requetes_3_candidats(
    client_api: TestClient, store_recette: MemoryStore
) -> None:
    """R4 : trois candidats, 200 requêtes, un résultat complet par candidat."""
    socle = charger_socle_demo()
    tenant = socle.tenant_a
    application: Application = socle.applications[APP_CLASSEMENT]
    store_recette.enregistrer_tenant(tenant)
    store_recette.enregistrer_application(application)

    records = _records(APP_CLASSEMENT, 1)
    echantillon = echantillon_de(records, APP_CLASSEMENT, TAILLE)
    assert len(echantillon.requetes) == TAILLE, "l'échantillon doit faire 200 requêtes"

    # Le moyen et le petit modèle se trompent de formulation (le juge le voit) ;
    # le modèle distillé se trompe de catégorie (le contrôle déterministe le voit).
    identifiants = echantillon_ids(echantillon)
    en_echec = {MODELE_DISTILLE: _echecs(identifiants, 3, 60)}
    en_variante = {
        MOYEN_MODELE: _echecs(identifiants, 50, 4),
        PETIT_MODELE: _echecs(identifiants, 33, 6),
    }

    # 1. Rejeu et consolidation des résultats par candidat.
    _, resultats, reference = _executer_campagne(records, echantillon, en_echec, en_variante)
    _verifier_indicateurs(resultats, reference)
    _verifier_taux(resultats, en_echec, en_variante)

    # 2. Le coût par requête suit les tarifs, et la règle de décision tranche.
    _verifier_couts(resultats, reference)
    _verifier_decisions(resultats, application)

    # 3. La campagne est publiée : résultats par candidat sur l'API (§15).
    campagne_creee = _publier_campagne(client_api, store_recette, tenant, resultats, reference)
    _verifier_api(client_api, tenant, campagne_creee)


def _publier_campagne(
    client_api: TestClient,
    store_recette: MemoryStore,
    tenant,
    resultats: dict[str, ResultatCandidat],
    reference: ResultatCandidat,
) -> Campagne:
    """Enregistre la campagne terminée et la rend lisible sur l'API."""
    cout_total = sum(r.cout_par_requete_usd * TAILLE for r in resultats.values())
    campagne_creee = campagne(
        tenant_id=tenant.id,
        application_id=APP_CLASSEMENT,
        modele_actuel=GRAND_MODELE,
        candidats=CANDIDATS,
        resultats=[reference, *(resultats[modele] for modele in CANDIDATS)],
        taille=TAILLE,
        cout_estime=cout_total,
        cout_reel=cout_total * 0.97,
        modele_juge=GRAND_MODELE,
    )
    store_recette.enregistrer_campagne(campagne_creee)
    return campagne_creee


def _rejouer_assistant(records: list[JournalInvocation], table) -> RejeuComplet:
    """Rejeu du petit modèle sur l'assistant (candidat trop faible attendu)."""
    echantillon = echantillon_de(records, APP_ASSISTANT, TAILLE)
    assert len(echantillon.requetes) == TAILLE
    client_modele = FakeModeleClient(jetons_sortie=730, latence_ms=2500)
    return rejouer(
        echantillon,
        modele_actuel=GRAND_MODELE,
        candidats=(PETIT_MODELE,),
        table=table,
        textes=textes_de(records),
        client_modele=client_modele,
        date_campagne=DATE_FIN_DEFAUT,
        max_tokens=1024,
    )


def test_r5_candidat_sous_le_seuil_rejette_avec_familles_en_echec() -> None:
    """R5 : le petit modèle sous le seuil de 97 % est rejeté, avec ses familles en échec."""
    socle = charger_socle_demo()
    application: Application = socle.applications[APP_ASSISTANT]
    records = _records(APP_ASSISTANT, 2)
    table = table_avec_distille(socle.tarifs)
    famille_de = famille_question(records)

    # Un sujet de questions est mal traité par le candidat (famille en échec),
    # plus quelques réponses isolées.
    identifiants = echantillon_ids(echantillon_de(records, APP_ASSISTANT, TAILLE))
    echecs = {
        identifiant
        for identifiant in identifiants
        if famille_de(identifiant) == SUJET_CIBLE or identifiant in set(identifiants[::20][:5])
    }
    assert len(echecs) >= 10, "la famille en échec doit être significative"

    rejeu = _rejouer_assistant(records, table)
    resultat = resultat_candidat(
        rejeu, PETIT_MODELE, juge_avec_echecs(echecs), famille_de, TYPE_TACHE_REDACTION
    )

    # 1. Le taux est sous le seuil de l'application (97 %).
    assert resultat.taux_equivalence < application.seuil_qualite
    assert resultat.taux_equivalence == pytest.approx((TAILLE - len(echecs)) / TAILLE)

    # 2. La règle de décision refuse le candidat et motive son refus (§13.4).
    decision = decider(resultat, SeuilsDecision.depuis_application(application))
    assert decision.propose is False
    assert decision.motifs, "un refus doit être motivé"
    assert any("taux_equivalence" in motif for motif in decision.motifs)
    conditions = {condition.nom: condition.remplie for condition in decision.conditions}
    assert conditions["taux_equivalence"] is False

    # 3. Les familles de requêtes en échec sont citées (§13.6).
    assert resultat.familles_en_echec, "les familles en échec doivent être listées"
    assert SUJET_CIBLE in resultat.familles_en_echec

    # 4. Le taux du candidat est lu à côté de l'auto-accord du modèle actuel.
    assert decision.lecture_relative
    assert "auto-accord" in decision.lecture_relative
