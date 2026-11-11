"""Outillage commun de la recette : campagne de rejeu complète, hors réseau.

La recette doit exercer le **code réel** du rejeu (M4), de l'évaluation (M5) et
de la règle de décision (§13.4). Ce module assemble la chaîne :

    tirage de l'échantillon (§13.1)
      → rejeu sur la référence et les candidats, au niveau Flex (§13.2, EF-REJ-02)
      → notation par le modèle juge fictif (§13.3)
      → contrôles déterministes de la tâche (§13.3, EF-EVA-02)
      → consolidation en `ResultatCandidat` (§14)

Les adaptateurs (`FakeModeleClient`, `FakeJugeClient`) viennent des modules
eux-mêmes ; le trafic vient de `demo/`. Aucun appel réseau, aucun AWS.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date

from demo.apps.support_assistant import QUESTIONS_EXEMPLES
from demo.traffic import JournalInvocation

from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import intervalle_wilson
from jauge.evaluation.checks import executer_controles
from jauge.evaluation.judge import DemandeNotation, FakeJugeClient, VerdictJuge
from jauge.evaluation.scoring import (
    MesuresCoutLatence,
    PaireEvaluee,
    consolider_resultat,
)
from jauge.models.campaign import (
    Campagne,
    ParametresRejeu,
    ResultatCandidat,
    StatutCampagne,
)
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.replay.client import FakeModeleClient
from jauge.replay.executor import ExecuteurRejeu, RejeuComplet, TextesEnMemoire
from jauge.replay.sampling import Echantillon, RequeteSource, tirer_echantillon

#: Modèle candidat supplémentaire de la recette (distillation) : il n'existe pas
#: dans les fixtures du socle, sa ligne de tarif est construite ici.
MODELE_DISTILLE = "demo.modele-distille"

#: Identifiants et réglages de la campagne de recette.
CAMPAGNE_ID = "c-recette"
GRAINE = 20261111
REGION = "eu-west-1"
DATE_EFFET = date(2026, 1, 1)


def tarif_distille() -> TarifModele:
    """Ligne de tarif du modèle distillé, au niveau Standard (repli Flex)."""
    return TarifModele(
        modele_id=MODELE_DISTILLE,
        region=REGION,
        niveau=ServiceTier.STANDARD,
        date_effet=DATE_EFFET,
        prix_entree_1k=0.0002,
        prix_sortie_1k=0.001,
    )


def table_avec_distille(tarifs: Sequence[TarifModele]) -> TableTarifs:
    """Table des tarifs de la recette : socle + modèle distillé."""
    return TableTarifs([*tarifs, tarif_distille()])


def sources_de(records: Sequence[JournalInvocation]) -> list[RequeteSource]:
    """Métadonnées de tirage issues des journaux (longueur, jetons, type)."""
    return [
        RequeteSource(
            requete_id=record.request_id,
            application_id=record.application_id,
            type_requete=record.type_requete,
            longueur_caracteres=len(record.corps_entree),
            jetons_entree_origine=record.jetons_entree,
        )
        for record in records
    ]


def textes_de(records: Sequence[JournalInvocation]) -> TextesEnMemoire:
    """Textes des requêtes, côté plan de données (ENF-CONF-01)."""
    return TextesEnMemoire({record.request_id: record.corps_entree for record in records})


def echantillon_de(
    records: Sequence[JournalInvocation], application_id: str, taille: int
) -> Echantillon:
    """Tirage stratifié et reproductible de l'échantillon (§13.1, EF-REJ-01)."""
    return tirer_echantillon(
        sources_de(records), application_id=application_id, taille=taille, graine=GRAINE
    )


def rejouer(
    echantillon: Echantillon,
    modele_actuel: str,
    candidats: Sequence[str],
    table: TableTarifs,
    textes: TextesEnMemoire,
    client_modele: FakeModeleClient,
    date_campagne: date,
    max_tokens: int = 256,
) -> RejeuComplet:
    """Rejoue l'échantillon sur la référence et les candidats (§13.2, EF-REJ-02)."""
    executeur = ExecuteurRejeu(client_modele, table, region=REGION, a_la_date=date_campagne)
    return executeur.rejouer(
        campagne_id=CAMPAGNE_ID,
        application_id=echantillon.application_id,
        modele_actuel=modele_actuel,
        candidats=list(candidats),
        echantillon=echantillon.requetes,
        parametres=ParametresRejeu(temperature=0.0, max_tokens=max_tokens),
        textes=textes,
    )


def paires_evaluees(
    rejeu: RejeuComplet,
    modele: str,
    juge: FakeJugeClient,
    famille_de: Callable[[str], str],
    type_tache: str,
) -> list[PaireEvaluee]:
    """Note et contrôle les paires d'un candidat (verdict du juge + contrôles)."""
    paires = []
    for paire in rejeu.paires:
        if paire.modele_candidat_id != modele:
            continue
        verdict = juge.noter(
            DemandeNotation(
                requete_id=paire.requete_id,
                requete=paire.texte_requete,
                reponse_reference=paire.reponse_reference,
                reponse_candidate=paire.reponse_candidate,
                type_tache=type_tache,
            )
        )
        controles = executer_controles(type_tache, paire.reponse_reference, paire.reponse_candidate)
        paires.append(
            PaireEvaluee(
                requete_id=paire.requete_id,
                verdict=verdict,
                famille=famille_de(paire.requete_id),
                controles=tuple(controles),
            )
        )
    return paires


def resultat_candidat(
    rejeu: RejeuComplet,
    modele: str,
    juge: FakeJugeClient,
    famille_de: Callable[[str], str],
    type_tache: str,
) -> ResultatCandidat:
    """Consolide le résultat d'un candidat : taux, intervalle, latence, coût."""
    mesure = rejeu.resume.pour_modele(modele)
    assert mesure is not None, f"aucune mesure de rejeu pour {modele}"
    paires = paires_evaluees(rejeu, modele, juge, famille_de, type_tache)
    return consolider_resultat(
        modele_id=modele,
        paires=paires,
        mesures=MesuresCoutLatence(
            cout_par_requete_usd=mesure.cout_par_requete_usd,
            latence_p50_ms=mesure.latence_p50_ms,
            latence_p95_ms=mesure.latence_p95_ms,
        ),
        auto_accord_modele_actuel=rejeu.resume.auto_accord_modele_actuel,
    )


def resultat_reference(rejeu: RejeuComplet, modele_actuel: str) -> ResultatCandidat:
    """Résultat de la référence : le modèle actuel comparé à lui-même (§13.4)."""
    mesure = rejeu.resume.pour_modele(modele_actuel)
    assert mesure is not None, f"aucune mesure de rejeu pour {modele_actuel}"
    auto = rejeu.resume.auto_accord_modele_actuel
    intervalle = intervalle_wilson(
        round(auto * mesure.nombre_requetes_rejouees), mesure.nombre_requetes_rejouees
    )
    return ResultatCandidat(
        modele_id=modele_actuel,
        est_reference=True,
        nombre_requetes_rejouees=mesure.nombre_requetes_rejouees,
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        auto_accord_modele_actuel=auto,
        cout_par_requete_usd=mesure.cout_par_requete_usd,
        latence_p50_ms=mesure.latence_p50_ms,
        latence_p95_ms=mesure.latence_p95_ms,
        familles_en_echec=[],
    )


def campagne(
    tenant_id: str,
    application_id: str,
    modele_actuel: str,
    candidats: Sequence[str],
    resultats: Sequence[ResultatCandidat],
    taille: int,
    cout_estime: float,
    cout_reel: float,
    modele_juge: str,
) -> Campagne:
    """Assemble la campagne terminée et ses résultats par candidat (§14)."""
    return Campagne(
        id=CAMPAGNE_ID,
        tenant_id=tenant_id,
        application_id=application_id,
        modeles_candidats=list(candidats),
        taille_echantillon=taille,
        graine_aleatoire=GRAINE,
        modele_juge_id=modele_juge,
        statut=StatutCampagne.TERMINEE,
        cout_estime_usd=round(cout_estime, 4),
        cout_reel_usd=round(cout_reel, 4),
        resultats=list(resultats),
    )


# --- Familles de requêtes ----------------------------------------------------


def familles_par_strate(echantillon: Echantillon) -> Callable[[str], str]:
    """Famille d'une requête = sa strate de longueur (§13.1)."""
    strates = {requete.requete_id: requete.stratum for requete in echantillon.requetes}
    return lambda requete_id: strates.get(requete_id, "general")


def famille_question(records: Sequence[JournalInvocation]) -> Callable[[str], str]:
    """Famille d'une requête = le sujet de la question posée à l'assistant."""
    sujets: dict[str, str] = {}
    for record in records:
        sujet = "autre"
        for question in QUESTIONS_EXEMPLES:
            if question in record.corps_entree:
                sujet = question
                break
        sujets[record.request_id] = sujet
    return lambda requete_id: sujets.get(requete_id, "general")


# --- Réponses de rejeu déterministes -----------------------------------------


def texte_sortie(record: JournalInvocation) -> str:
    """Texte de la réponse d'origine du modèle (sortie de l'enveloppe Converse)."""
    if not record.corps_sortie:
        return ""
    try:
        enveloppe = json.loads(record.corps_sortie)
        contenu = enveloppe["output"]["message"]["content"]
        return contenu[0].get("text", "") if contenu else ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


def reponse_alteree(texte: str) -> str:
    """Altere la catégorie d'une réponse JSON : le contrôle déterministe échoue."""
    try:
        document = json.loads(texte)
    except json.JSONDecodeError:
        return texte
    if not isinstance(document, dict):
        return texte
    document["categorie"] = "technique" if document.get("categorie") != "technique" else "incident"
    return json.dumps(document, ensure_ascii=False)


def reponse_variante(texte: str) -> str:
    """Réponse équivalente mais formulée autrement : le juge la refuse, les contrôles passent."""
    try:
        document = json.loads(texte)
    except json.JSONDecodeError:
        return f"{texte} (variante)"
    if not isinstance(document, dict):
        return f"{texte} (variante)"
    document["confiance"] = round(float(document.get("confiance", 0.5)) * 0.9, 3)
    return json.dumps(document, ensure_ascii=False)


def reponses_imposees(
    records: Sequence[JournalInvocation],
    modeles: Sequence[str],
    en_echec: Mapping[str, set[str]],
    en_variante: Mapping[str, set[str]] | None = None,
) -> dict[tuple[str, str], str]:
    """Réponses de rejeu imposées : la référence répond juste, les candidats parfois mal.

    `en_echec[candidat]` : le candidat répond avec une **autre catégorie** — le
    contrôle déterministe « étiquette identique » échoue (EF-EVA-02).
    `en_variante[candidat]` : le candidat répond différemment mais avec la même
    catégorie — seul le juge peut le voir.
    """
    variantes = en_variante or {}
    reponses: dict[tuple[str, str], str] = {}
    for record in records:
        reference = texte_sortie(record)
        for modele in modeles:
            if record.request_id in en_echec.get(modele, ()):
                reponses[(modele, record.corps_entree)] = reponse_alteree(reference)
            elif record.request_id in variantes.get(modele, ()):
                reponses[(modele, record.corps_entree)] = reponse_variante(reference)
            else:
                reponses[(modele, record.corps_entree)] = reference
    return reponses


def juge_avec_echecs(en_echec: set[str], modele: str = "juge-fictif") -> FakeJugeClient:
    """Juge fictif : équivalent par défaut, non équivalent sur les requêtes en échec."""
    verdicts = {
        requete_id: VerdictJuge(equivalent=False, explication="réponse non équivalente")
        for requete_id in en_echec
    }
    return FakeJugeClient(
        modele_id=modele,
        verdicts=verdicts,
        verdict_defaut=VerdictJuge(equivalent=True, explication="réponse équivalente"),
    )


def requetes_du_journal(
    records: Sequence[JournalInvocation], taille: int, graine: int = GRAINE
) -> list[str]:
    """Identifiants des `taille` premières requêtes, triés (choix déterministe)."""
    return sorted(record.request_id for record in records)[:taille]


def echantillon_ids(echantillon: Echantillon) -> list[str]:
    """Identifiants des requêtes retenues par le tirage, dans l'ordre du tirage."""
    return [requete.requete_id for requete in echantillon.requetes]


def type_requetes(records: Sequence[JournalInvocation]) -> dict[str, str]:
    """Type de requête (étiquette du journal) par identifiant de requête."""
    return {record.request_id: record.type_requete for record in records}


def repartition_familles(familles: Iterable[str]) -> dict[str, int]:
    """Nombre de paires par famille de requêtes (audit de l'échantillon)."""
    compte: dict[str, int] = {}
    for famille in familles:
        compte[famille] = compte.get(famille, 0) + 1
    return compte
