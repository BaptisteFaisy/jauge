"""Chaîne d'évaluation d'une campagne de rejeu (M5 : juge, contrôles, décision).

Relie `jauge.replay.executor.RejeuComplet` à `jauge.evaluation` :

- les paires rejouées restent dans le plan de données (`PaireRejoueeDataPlane`) ;
- chaque paire reçoit le verdict du juge (`JugeClient`) et les contrôles
  déterministes de la tâche (`executer_controles`, EF-EVA-02) ;
- les résultats sont consolidés (`consolider_resultat`, EF-EVA-03/04) puis
  tranchés par la règle de décision du §13.4 (`decider_lot`).

Aucun chiffre n'est inventé : tout vient de `jauge.evaluation` et du socle
(ENF-IA-01).

Exigences visées : EF-EVA-01..05, §13.3..13.5, ENF-CONF-01, ENF-IA-01.
"""

from __future__ import annotations

from collections import defaultdict

from jauge.agents.ports import ClusterEchec
from jauge.common.errors import CloisonnementError, IntrouvableError
from jauge.evaluation.checks import executer_controles
from jauge.evaluation.decision import SeuilsDecision, decider_lot
from jauge.evaluation.judge import (
    DemandeNotation,
    FakeJugeClient,
    JugeClient,
    RegistreCapacite,
    VerdictJuge,
    choisir_juge,
)
from jauge.evaluation.scoring import MesuresCoutLatence, PaireEvaluee, consolider_resultat
from jauge.integration.socle import charger_tenant, famille_de, rang_capacite
from jauge.models.campaign import Campagne, PaireRejoueeDataPlane, ResultatCandidat
from jauge.models.tenant import Application
from jauge.replay.executor import RejeuComplet, ResumeRejeu
from jauge.store.protocol import Store


def charger_campagne(store: Store, tenant_id: str, campagne_id: str) -> Campagne:
    """Rend une campagne, en refusant l'accès inter-clients (ENF-SEC-02)."""
    charger_tenant(store, tenant_id)
    campagne = store.obtenir_campagne(tenant_id, campagne_id)
    if campagne is not None:
        return campagne
    if _campagne_autre_client(store, tenant_id, campagne_id):
        raise CloisonnementError(
            "Campagne d'un autre client", tenant_id=tenant_id, campagne_id=campagne_id
        )
    raise IntrouvableError(f"Campagne inconnue : {campagne_id}", campagne_id=campagne_id)


def _campagne_autre_client(store: Store, tenant_id: str, campagne_id: str) -> bool:
    """Vrai si la campagne existe chez un autre client."""
    return any(
        autre.id != tenant_id and store.obtenir_campagne(autre.id, campagne_id) is not None
        for autre in store.lister_tenants()
    )


def choisir_juge_campagne(autorises: list[str], modele_actuel: str, candidats: list[str]) -> str:
    """Sélectionne un modèle juge conforme au §13.5."""
    registre = RegistreCapacite(
        {modele: rang_capacite(famille_de(modele)) for modele in {*autorises, modele_actuel}}
    )
    return choisir_juge(modele_actuel, candidats, registre)


def cout_reel_rejeu(rejeu: RejeuComplet) -> float:
    """Somme des coûts réels mesurés lors du rejeu (ENF-IA-01)."""
    return round(sum(mesure.cout_total_usd for mesure in rejeu.resume.mesures), 6)


def evaluer_rejeu(
    application: Application,
    rejeu: RejeuComplet,
    types_par_requete: dict[str, str],
    juge_client: JugeClient | None,
    juge_id: str,
) -> tuple[list[ResultatCandidat], list[ClusterEchec], list[PaireRejoueeDataPlane]]:
    """Note les paires, exécute les contrôles déterministes et applique §13.4."""
    juge = juge_client or FakeJugeClient(modele_id=juge_id)
    evaluees: dict[str, list[PaireEvaluee]] = {}
    paires_dp: list[PaireRejoueeDataPlane] = []
    for modele_id, lot in _grouper_paires(rejeu.paires).items():
        verdicts = _noter_lot(juge, lot, types_par_requete)
        evaluees[modele_id] = [
            _paire_evaluee(paire, verdict, types_par_requete.get(paire.requete_id, "general"))
            for paire, verdict in zip(lot, verdicts, strict=True)
        ]
        paires_dp.extend(_annoter_paires_dp(lot, verdicts, evaluees[modele_id]))
    resultats = _consolider(application, rejeu, evaluees)
    clusters = _clusters_echec(evaluees)
    return resultats, clusters, paires_dp


def _grouper_paires(
    paires: tuple[PaireRejoueeDataPlane, ...],
) -> dict[str, list[PaireRejoueeDataPlane]]:
    """Regroupe les paires rejouées par modèle candidat."""
    groupes: dict[str, list[PaireRejoueeDataPlane]] = defaultdict(list)
    for paire in paires:
        groupes[paire.modele_candidat_id].append(paire)
    return dict(groupes)


def _noter_lot(
    juge: JugeClient,
    lot: list[PaireRejoueeDataPlane],
    types: dict[str, str],
) -> list[VerdictJuge]:
    """Soumet le lot au modèle juge (plan de données seulement)."""
    demandes = [
        DemandeNotation(
            requete_id=paire.requete_id,
            requete=paire.texte_requete,
            reponse_reference=paire.reponse_reference,
            reponse_candidate=paire.reponse_candidate,
            type_tache=types.get(paire.requete_id, "general"),
        )
        for paire in lot
    ]
    return juge.noter_lot(demandes)


def _paire_evaluee(
    paire: PaireRejoueeDataPlane, verdict: VerdictJuge, type_tache: str
) -> PaireEvaluee:
    """Construit la paire évaluée (verdict + contrôles déterministes)."""
    controles = tuple(
        executer_controles(type_tache, paire.reponse_reference, paire.reponse_candidate)
    )
    return PaireEvaluee(
        requete_id=paire.requete_id,
        verdict=verdict,
        famille=type_tache,
        controles=controles,
    )


def _annoter_paires_dp(
    lot: list[PaireRejoueeDataPlane],
    verdicts: list[VerdictJuge],
    evaluees: list[PaireEvaluee],
) -> list[PaireRejoueeDataPlane]:
    """Inscrit les scores du juge et des contrôles dans la trace data-plane."""
    return [
        paire.model_copy(
            update={
                "score_equivalence": 1.0 if verdict.equivalent else 0.0,
                "explication_juge": verdict.explication,
                "controles_deterministes_ok": not any(
                    not ctrl.ok and ctrl.critique for ctrl in ev.controles
                ),
            }
        )
        for paire, verdict, ev in zip(lot, verdicts, evaluees, strict=True)
    ]


def _consolider(
    application: Application,
    rejeu: RejeuComplet,
    evaluees: dict[str, list[PaireEvaluee]],
) -> list[ResultatCandidat]:
    """Consolide référence + candidats et tranche selon les seuils (§13.4)."""
    resume = rejeu.resume
    auto_accord = resume.auto_accord_modele_actuel
    resultats: list[ResultatCandidat] = []
    mesure_ref = resume.pour_modele(application.modele_actuel)
    if mesure_ref is not None:
        resultats.append(
            ResultatCandidat(
                modele_id=application.modele_actuel,
                est_reference=True,
                nombre_requetes_rejouees=mesure_ref.nombre_requetes_rejouees,
                taux_equivalence=auto_accord,
                intervalle_confiance_bas=auto_accord,
                intervalle_confiance_haut=auto_accord,
                auto_accord_modele_actuel=auto_accord,
                cout_par_requete_usd=mesure_ref.cout_par_requete_usd,
                latence_p50_ms=mesure_ref.latence_p50_ms,
                latence_p95_ms=mesure_ref.latence_p95_ms,
                conforme_seuils=True,
            )
        )
    bruts = _consolider_candidats(resume, evaluees, auto_accord)
    if bruts:
        decisions = {
            d.modele_id: d.propose
            for d in decider_lot(bruts, SeuilsDecision.depuis_application(application))
        }
        resultats.extend(
            r.model_copy(update={"conforme_seuils": decisions.get(r.modele_id, False)})
            for r in bruts
        )
    return resultats


def _consolider_candidats(
    resume: ResumeRejeu,
    evaluees: dict[str, list[PaireEvaluee]],
    auto_accord: float,
) -> list[ResultatCandidat]:
    """Consolide chaque candidat via `jauge.evaluation.scoring`."""
    bruts: list[ResultatCandidat] = []
    for modele_id, paires in evaluees.items():
        mesure = resume.pour_modele(modele_id)
        if mesure is None or not paires:
            continue
        cout_lat = MesuresCoutLatence(
            cout_par_requete_usd=mesure.cout_par_requete_usd,
            latence_p50_ms=mesure.latence_p50_ms,
            latence_p95_ms=mesure.latence_p95_ms,
        )
        bruts.append(consolider_resultat(modele_id, paires, cout_lat, auto_accord))
    return bruts


def _clusters_echec(evaluees: dict[str, list[PaireEvaluee]]) -> list[ClusterEchec]:
    """Familles de requêtes où un candidat échoue (étiquettes seulement)."""
    clusters: list[ClusterEchec] = []
    for modele_id, paires in evaluees.items():
        par_famille: dict[str, list[PaireEvaluee]] = defaultdict(list)
        for paire in paires:
            par_famille[paire.famille].append(paire)
        for famille, groupe in sorted(par_famille.items()):
            echecs = sum(1 for paire in groupe if not paire.verdict.equivalent)
            if echecs > 0:
                clusters.append(
                    ClusterEchec(
                        modele_id=modele_id,
                        famille=famille,
                        nombre_requetes=len(groupe),
                        nombre_echecs=echecs,
                        taux_echec=round(echecs / len(groupe), 4),
                    )
                )
    return clusters
