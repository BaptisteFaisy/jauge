"""Analyse du trafic synthétique : statistiques et leviers détectés.

Ce module ne touche jamais au store : il produit des objets du socle
(`Levier`) à partir du trafic synthétique et de la table des tarifs.
`demo.seed` se contente ensuite de les enregistrer.

Toutes les valeurs chiffrées sont calculées par le code à partir des journaux
(ENF-IA-01) : aucun chiffre n'est inventé par un modèle de langage.

Les campagnes, recommandations et bascules qui découlent de ces leviers sont
construites par `demo.scenario`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from demo.profils import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION, ProfilApplication
from demo.traffic import JournalInvocation
from jauge.common.ids import deterministic
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import economie_mensuelle, percentile
from jauge.models.lever import Levier, StatutLevier, TypeLevier
from jauge.models.pricing import MetriquesRequete, ServiceTier

JOURS_MOIS = 30


@dataclass(frozen=True)
class StatistiquesApplication:
    """Statistiques calculées sur le trafic d'une application."""

    application_id: str
    tenant_id: str
    nombre_requetes: int
    erreurs: int
    jetons_entree_moyen: float
    jetons_entree_mediane: float
    jetons_sortie_moyen: float
    jetons_sortie_mediane: float
    jetons_sortie_p95: float
    part_identiques: float
    heures: tuple[int, ...]
    latence_p50: int
    latence_p95: int
    cout_par_requete: float
    cout_total: float
    volume_mensuel: int
    prefixe_jetons: int
    max_tokens: int
    niveau: ServiceTier


def analyser_trafic(
    records: list[JournalInvocation],
    table_tarifs: TableTarifs,
    profils: dict[str, ProfilApplication],
    jours: int,
    date_ref: date,
) -> dict[str, StatistiquesApplication]:
    """Calcule les statistiques par application à partir du trafic."""
    stats: dict[str, StatistiquesApplication] = {}
    for application_id, profil in profils.items():
        lignes = [r for r in records if r.application_id == application_id]
        if not lignes:
            continue
        stats[application_id] = _statistiques(lignes, profil, table_tarifs, jours, date_ref)
    return stats


def _statistiques(
    lignes: list[JournalInvocation],
    profil: ProfilApplication,
    table_tarifs: TableTarifs,
    jours: int,
    date_ref: date,
) -> StatistiquesApplication:
    """Statistiques d'une application (jetons, latences, coût, doublons)."""
    succes = [r for r in lignes if r.code_erreur is None]
    entrees = [r.jetons_entree for r in succes]
    sorties = [r.jetons_sortie for r in succes]
    latences = [r.latence_invocation_ms for r in succes]
    tarif = table_tarifs.trouver(profil.modele_id, REGION, profil.niveau, date_ref)
    cout_total = sum(calculer_cout_requete(tarif, r.metriques()).cout_total for r in succes)
    return StatistiquesApplication(
        application_id=profil.application_id,
        tenant_id=profil.tenant_id,
        nombre_requetes=len(succes),
        erreurs=len(lignes) - len(succes),
        jetons_entree_moyen=sum(entrees) / len(entrees),
        jetons_entree_mediane=percentile(entrees, 50),
        jetons_sortie_moyen=sum(sorties) / len(sorties),
        jetons_sortie_mediane=percentile(sorties, 50),
        jetons_sortie_p95=percentile(sorties, 95),
        part_identiques=_part_identiques(succes),
        heures=tuple(sorted({r.event_time.hour for r in succes})),
        latence_p50=int(percentile(latences, 50)),
        latence_p95=int(percentile(latences, 95)),
        cout_par_requete=cout_total / len(succes) if succes else 0.0,
        cout_total=cout_total,
        volume_mensuel=max(1, round(len(succes) * JOURS_MOIS / max(1, jours))),
        prefixe_jetons=_prefixe_long(succes),
        max_tokens=profil.max_tokens,
        niveau=profil.niveau,
    )


def _prefixe_long(succes: list[JournalInvocation]) -> int:
    """Taille du préfixe système répété sur la majorité des requêtes.

    Indice du levier « cache de prompts absent » (Annexe A) : on ne retient un
    préfixe que s'il est identique sur au moins 90 % des appels et dépasse
    200 jetons, seuil à partir duquel le cache de prompts a un intérêt.
    """
    if not succes:
        return 0
    comptes: dict[str, int] = {}
    for record in succes:
        systeme = json.loads(record.corps_entree).get("system") or []
        prefixe = " ".join(bloc.get("text", "") for bloc in systeme)
        if prefixe:
            comptes[prefixe] = comptes.get(prefixe, 0) + 1
    if not comptes:
        return 0
    prefixe, occurrences = max(comptes.items(), key=lambda item: item[1])
    if occurrences < 0.9 * len(succes):
        return 0
    jetons = len(prefixe) // 4
    return jetons if jetons >= 200 else 0


def _part_identiques(succes: list[JournalInvocation]) -> float:
    """Part des requêtes identiques à une requête déjà vue."""
    if not succes:
        return 0.0
    vues: set[str] = set()
    doublons = 0
    for record in succes:
        signature = record.signature_requete()
        if signature in vues:
            doublons += 1
        vues.add(signature)
    return doublons / len(succes)


# --- Leviers ------------------------------------------------------------------


def construire_leviers(
    stats: dict[str, StatistiquesApplication],
    table_tarifs: TableTarifs,
    date_ref: date,
) -> list[Levier]:
    """Construit les leviers détectés sur le trafic (Annexe A, §8.3)."""
    leviers: list[Levier] = []
    for application_id, st in stats.items():
        if application_id == "ticket-classifier":
            leviers.append(_levier_surdimensionne(st, table_tarifs, date_ref))
            leviers.append(_levier_erreurs(st))
        if application_id == "support-assistant":
            leviers.append(_levier_cache(st, table_tarifs, date_ref))
            leviers.append(_levier_repetitions(st))
        if application_id == "doc-summarizer":
            leviers.append(_levier_differable(st, table_tarifs, date_ref))
            leviers.append(_levier_sorties_longues(st, table_tarifs, date_ref))
    return leviers


def identifiant_demo(prefixe: str, *parties: object) -> str:
    """Identifiant déterministe (`lv-…`, `c-…`, `r-…`)."""
    return f"{prefixe}-{deterministic(*parties)[:12]}"


def _levier_surdimensionne(
    st: StatistiquesApplication, table_tarifs: TableTarifs, date_ref: date
) -> Levier:
    """Levier « modèle surdimensionné » : requêtes courtes, sorties structurées."""
    cout_avant = st.cout_par_requete
    cout_apres = cout_par_requete_pour(
        st, table_tarifs, PETIT_MODELE, ServiceTier.STANDARD, date_ref
    )
    economie = economie_mensuelle(cout_avant, cout_apres, st.volume_mensuel)
    return Levier(
        id=identifiant_demo(
            "lv", st.tenant_id, st.application_id, TypeLevier.MODELE_SURDIMENSIONNE
        ),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
        statut=StatutLevier.EN_COURS_DE_PREUVE,
        indices=[
            f"Requêtes courtes : médiane {st.jetons_entree_mediane:.0f} jetons d'entrée",
            f"Sorties JSON courtes : médiane {st.jetons_sortie_mediane:.0f} jetons de sortie",
            f"Faible diversité des sorties : {st.part_identiques:.0%} de requêtes identiques",
        ],
        action_proposee="Rejouer l'application sur un modèle plus petit",
        preuve_exigee="Campagne de rejeu avec modèle juge",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.9,
        candidats_suggeres=[PETIT_MODELE, MOYEN_MODELE],
    )


def _levier_cache(st: StatistiquesApplication, table_tarifs: TableTarifs, date_ref: date) -> Levier:
    """Levier « cache de prompts absent » : préfixe long répété, aucun jeton lu."""
    tarif = table_tarifs.trouver(GRAND_MODELE, REGION, st.niveau, date_ref)
    gain_unitaire = (
        st.prefixe_jetons * (tarif.prix_entree_1k - tarif.prix_cache_lecture_1k) / 1000.0
    )
    economie = gain_unitaire * st.volume_mensuel
    return Levier(
        id=identifiant_demo("lv", st.tenant_id, st.application_id, TypeLevier.CACHE_PROMPTS_ABSENT),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.CACHE_PROMPTS_ABSENT,
        indices=[
            f"Préfixe système de {st.prefixe_jetons} jetons identique sur chaque requête",
            "Aucun jeton lu en cache (cacheReadInputTokenCount = 0)",
        ],
        action_proposee="Activer le cache de prompts sur le préfixe système",
        preuve_exigee="Calcul sur les jetons",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.85,
    )


def _levier_differable(
    st: StatistiquesApplication, table_tarifs: TableTarifs, date_ref: date
) -> Levier:
    """Levier « traitement différable » : trafic nocturne, niveau Standard."""
    cout_standard = st.cout_par_requete
    cout_flex = cout_par_requete_pour(st, table_tarifs, GRAND_MODELE, ServiceTier.FLEX, date_ref)
    economie = economie_mensuelle(cout_standard, cout_flex, st.volume_mensuel)
    heures = ", ".join(f"{h:02d} h" for h in st.heures)
    return Levier(
        id=identifiant_demo(
            "lv", st.tenant_id, st.application_id, TypeLevier.TRAITEMENT_DIFFERABLE
        ),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.TRAITEMENT_DIFFERABLE,
        indices=[
            f"Trafic nocturne : {heures}",
            "Appels non interactifs, niveau de service Standard",
        ],
        action_proposee="Passer les appels non interactifs au niveau Flex",
        preuve_exigee="Accord du responsable sur le délai",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.8,
    )


def _levier_sorties_longues(
    st: StatistiquesApplication, table_tarifs: TableTarifs, date_ref: date
) -> Levier:
    """Levier « sorties trop longues » : sorties proches de la limite de jetons."""
    tarif = table_tarifs.trouver(GRAND_MODELE, REGION, st.niveau, date_ref)
    jetons_economises = max(0.0, st.jetons_sortie_p95 - st.jetons_sortie_mediane)
    economie = jetons_economises * tarif.prix_sortie_1k / 1000.0 * st.volume_mensuel
    return Levier(
        id=identifiant_demo("lv", st.tenant_id, st.application_id, TypeLevier.SORTIES_TROP_LONGUES),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.SORTIES_TROP_LONGUES,
        indices=[
            f"Sorties proches de la limite : p95 {st.jetons_sortie_p95:.0f} jetons "
            f"pour une limite de {st.max_tokens}",
            f"Bien au-delà de la médiane de la tâche : {st.jetons_sortie_mediane:.0f} jetons",
        ],
        action_proposee="Limiter le nombre de jetons de sortie et demander un résumé",
        preuve_exigee="Rejeu avec la nouvelle limite",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.75,
    )


def _levier_repetitions(st: StatistiquesApplication) -> Levier:
    """Levier « requêtes répétées » : mêmes requêtes à l'identique."""
    economie = st.part_identiques * st.cout_par_requete * st.volume_mensuel
    return Levier(
        id=identifiant_demo("lv", st.tenant_id, st.application_id, TypeLevier.REQUETES_REPETEES),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.REQUETES_REPETEES,
        indices=[f"{st.part_identiques:.0%} des requêtes sont identiques à une requête déjà vue"],
        action_proposee="Mettre en place un cache applicatif sur les demandes fréquentes",
        preuve_exigee="Comptage",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.8,
    )


def _levier_erreurs(st: StatistiquesApplication) -> Levier:
    """Levier « erreurs et relances » : erreurs suivies d'appels identiques."""
    part = st.erreurs / max(1, st.nombre_requetes + st.erreurs)
    economie = part * st.cout_par_requete * st.volume_mensuel
    return Levier(
        id=identifiant_demo("lv", st.tenant_id, st.application_id, TypeLevier.ERREURS_ET_RELANCES),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        type_levier=TypeLevier.ERREURS_ET_RELANCES,
        indices=[
            f"{st.erreurs} erreurs suivies d'une relance identique",
            f"{part:.1%} des appels échouent",
        ],
        action_proposee="Corriger la cause des limitations de débit",
        preuve_exigee="Comptage",
        economie_mensuelle_estimee_usd=round(economie, 2),
        confiance=0.7,
    )


def cout_par_requete_pour(
    st: StatistiquesApplication,
    table_tarifs: TableTarifs,
    modele_id: str,
    niveau: ServiceTier,
    date_ref: date,
) -> float:
    """Coût moyen d'une requête si l'application passait sur un modèle donné."""
    tarif = table_tarifs.trouver(modele_id, REGION, niveau, date_ref)
    metriques = MetriquesRequete(
        jetons_entree=int(st.jetons_entree_moyen),
        jetons_sortie=int(st.jetons_sortie_moyen),
    )
    return calculer_cout_requete(tarif, metriques).cout_total
