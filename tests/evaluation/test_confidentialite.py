"""Preuve qu'aucun texte de requête ne sort du module (ENF-CONF-01, §11.1).

Le plan de données (compte client) manipule les textes ; le plan de contrôle ne
reçoit que des identifiants, des verdicts et des nombres. Ce test fait tourner la
chaîne complète sur des textes sentinelles et vérifie qu'aucun d'eux n'apparaît
dans ce qui est rendu au plan de contrôle.
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from jauge.evaluation.checks import executer_controles
from jauge.evaluation.decision import decider
from jauge.evaluation.human_review import preparer_relecture
from jauge.evaluation.judge import DemandeNotation, FakeJugeClient, VerdictJuge
from jauge.evaluation.scoring import MesuresCoutLatence, PaireEvaluee, consolider_resultat

# Textes sentinelles : s'ils apparaissent dans une vue du plan de contrôle, le
# test échoue et pointe la fuite.
REQUETE_SENTINELLE = "SECRET-REQUETE-ne-doit-pas-sortir"
REFERENCE_SENTINELLE = "SECRET-REFERENCE-ne-doit-pas-sortir"
CANDIDATE_SENTINELLE = "SECRET-CANDIDATE-ne-doit-pas-sortir"
SENTINELLES = (REQUETE_SENTINELLE, REFERENCE_SENTINELLE, CANDIDATE_SENTINELLE)

MESURES = MesuresCoutLatence(cout_par_requete_usd=0.0009, latence_p50_ms=410, latence_p95_ms=900)


@pytest.fixture
def chaine():
    """Fait tourner juge, contrôles, scoring, décision et relecture sur 40 paires."""
    demandes = [
        DemandeNotation(
            requete_id=f"q-{index:03d}",
            requete=f"{REQUETE_SENTINELLE} {index}",
            reponse_reference=f"{REFERENCE_SENTINELLE} {index}",
            reponse_candidate=f"{CANDIDATE_SENTINELLE} {index}",
            type_tache="classification",
        )
        for index in range(40)
    ]
    juge = FakeJugeClient(
        verdicts={f"q-{index:03d}": VerdictJuge(True, "même service") for index in range(40)}
    )
    verdicts = juge.noter_lot(demandes)
    paires = [
        PaireEvaluee(
            requete_id=demande.requete_id,
            verdict=verdict,
            controles=tuple(
                executer_controles(
                    "redaction", demande.reponse_reference, demande.reponse_candidate
                )
            ),
        )
        for demande, verdict in zip(demandes, verdicts, strict=True)
    ]
    resultat = consolider_resultat(
        modele_id="petit-modele",
        paires=paires,
        mesures=MESURES,
        auto_accord_modele_actuel=0.96,
    )
    return {
        "demandes": demandes,
        "paires": paires,
        "resultat": resultat,
        "decision": decider(resultat),
        "registre": preparer_relecture(paires),
    }


def _verifier_absence(texte: str, quoi: str) -> None:
    """Aucune sentinelle ne doit apparaître dans `texte`."""
    for sentinelle in SENTINELLES:
        assert sentinelle not in texte, f"fuite de texte dans {quoi}"


def test_vue_du_plan_de_controle_sans_texte(chaine) -> None:
    """La vue de décision remise au plan de contrôle ne porte aucun texte."""
    vue = chaine["decision"].vers_dict()
    _verifier_absence(json.dumps(vue, ensure_ascii=False), "la vue de décision")
    assert set(vue) == {
        "modele_id",
        "propose",
        "qualification",
        "conditions",
        "motifs",
        "lecture_relative",
    }


def test_resultat_candidat_sans_texte(chaine) -> None:
    """`ResultatCandidat` est l'entité du socle : elle ne contient aucun texte de paire."""
    _verifier_absence(chaine["resultat"].model_dump_json(), "ResultatCandidat")


def test_paire_evaluee_ne_porte_aucun_texte(chaine) -> None:
    """La vue de paire du module ne déclare aucun champ de texte."""
    noms = {champ.name for champ in fields(PaireEvaluee)}
    assert noms == {"requete_id", "verdict", "famille", "controles"}


def test_sous_echantillon_ne_porte_que_des_identifiants(chaine) -> None:
    """Le sous-échantillon de relecture ne transporte que des `requete_id`."""
    sous_echantillon = chaine["registre"].sous_echantillon
    _verifier_absence(repr(sous_echantillon), "le sous-échantillon")
    assert all(identifiant.startswith("q-") for identifiant in sous_echantillon.requete_ids)


def test_motifs_de_rejet_sans_texte() -> None:
    """Un rejet motivé ne cite que des noms de contrôles et des nombres."""
    resultat = consolider_resultat(
        modele_id="petit-modele",
        paires=[
            PaireEvaluee(
                requete_id="q-001",
                verdict=VerdictJuge(False, "hors sujet"),
                controles=tuple(
                    executer_controles(
                        "classification",
                        '{"etiquette": "acces"}',
                        "SECRET-CANDIDATE-ne-doit-pas-sortir",
                    )
                ),
            )
        ],
        mesures=MESURES,
    )
    decision = decider(resultat)
    assert decision.propose is False
    _verifier_absence(json.dumps(decision.vers_dict(), ensure_ascii=False), "les motifs de rejet")


def test_le_juge_recoit_bien_les_textes_dans_le_plan_de_donnees(chaine) -> None:
    """Contre-épreuve : les textes existent côté plan de données, sinon le test ne prouve rien."""
    demande = chaine["demandes"][0]
    assert REQUETE_SENTINELLE in demande.requete
    assert REFERENCE_SENTINELLE in demande.reponse_reference
    assert CANDIDATE_SENTINELLE in demande.reponse_candidate
