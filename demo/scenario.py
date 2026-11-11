"""Scénario de démonstration : campagnes, recommandations, bascule (§8.4..8.6).

Ce module transforme les statistiques du trafic (`demo.analyse`) en objets du
socle prêts à être enregistrés dans le store :

- trois campagnes de rejeu terminées (`Campagne` + `ResultatCandidat`) ;
- trois recommandations (`Recommandation`, format de l'Annexe B) ;
- une bascule progressive en cours (`BasculeProgressive`) ;
- la vérification d'économie associée (`VerificationEconomie`).

Le scénario suit le script du chapitre 18.2 :

- `ticket-classifier` : le petit modèle passe le seuil → recommandation
  **en attente d'approbation** ;
- `support-assistant` : le petit modèle est trop faible → recommandation
  **refusée**, preuve à l'appui ;
- `doc-summarizer` : passage au niveau de service Flex → **bascule en cours**.

Toutes les valeurs chiffrées sont calculées par le code (ENF-IA-01) ; seuls les
champs de texte (risques, indices) sont rédigés.
"""

from __future__ import annotations

from datetime import date

from demo.analyse import StatistiquesApplication, cout_par_requete_pour, identifiant_demo
from demo.profils import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE
from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import baisse_pourcentage, economie_mensuelle, intervalle_wilson
from jauge.models.campaign import Campagne, ResultatCandidat, StatutCampagne
from jauge.models.pricing import ServiceTier
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
    VerificationEconomie,
)

TAILLE_ECHANTILLON = 200

# Résultats de rejeu synthétiques : (candidat, équivalents sur 200, échecs
# déterministes). Ils sont la trace des campagnes de rejeu de la démo.
RESULTATS_CLASSIF = ((MOYEN_MODELE, 196, 0), (PETIT_MODELE, 194, 0))
RESULTATS_ASSISTANT = ((MOYEN_MODELE, 191, 4), (PETIT_MODELE, 172, 9))
AUTO_ACCORD_REFERENCE = 0.985
APPROBATEUR = "responsable-applications@client-a.example"

# Latence relative des modèles (petit = plus rapide) et surcoût du niveau Flex.
FACTEURS_LATENCE_MODELE = {GRAND_MODELE: 1.0, MOYEN_MODELE: 0.55, PETIT_MODELE: 0.35}
FACTEUR_LATENCE_FLEX = 1.25


# --- Campagnes de rejeu -------------------------------------------------------


def construire_campagnes(
    stats: dict[str, StatistiquesApplication],
    table_tarifs: TableTarifs,
    date_ref: date,
) -> list[Campagne]:
    """Construit les trois campagnes de rejeu de la démonstration (§8.4)."""
    fabricants = {
        "ticket-classifier": (_campagne_classif, RESULTATS_CLASSIF),
        "support-assistant": (_campagne_assistant, RESULTATS_ASSISTANT),
        "doc-summarizer": (_campagne_doc, ()),
    }
    campagnes: list[Campagne] = []
    for application_id, (fabricant, resultats) in fabricants.items():
        st = stats.get(application_id)
        if st is None:
            continue
        campagnes.append(fabricant(st, resultats, table_tarifs, date_ref))
    return campagnes


def _campagne_classif(
    st: StatistiquesApplication,
    resultats: tuple[tuple[str, int, int], ...],
    table_tarifs: TableTarifs,
    date_ref: date,
) -> Campagne:
    """Campagne du classement de tickets : le petit modèle passe le seuil."""
    return _campagne_avec_candidats(st, resultats, table_tarifs, date_ref, "classif", 199)


def _campagne_assistant(
    st: StatistiquesApplication,
    resultats: tuple[tuple[str, int, int], ...],
    table_tarifs: TableTarifs,
    date_ref: date,
) -> Campagne:
    """Campagne de l'assistant : les deux candidats échouent au seuil de 97 %."""
    return _campagne_avec_candidats(st, resultats, table_tarifs, date_ref, "assistant", 199)


def _campagne_avec_candidats(
    st: StatistiquesApplication,
    candidats: tuple[tuple[str, int, int], ...],
    table_tarifs: TableTarifs,
    date_ref: date,
    suffixe: str,
    succes_reference: int,
) -> Campagne:
    """Campagne générique : une référence (modèle actuel) et ses candidats."""
    resultats = [
        _resultat_candidat(
            modele=GRAND_MODELE,
            succes=succes_reference,
            echecs=0,
            reference=True,
            st=st,
            table_tarifs=table_tarifs,
            niveau=st.niveau,
            date_ref=date_ref,
        )
    ]
    for modele, succes, echecs in candidats:
        resultats.append(
            _resultat_candidat(
                modele=modele,
                succes=succes,
                echecs=echecs,
                reference=False,
                st=st,
                table_tarifs=table_tarifs,
                niveau=st.niveau,
                date_ref=date_ref,
            )
        )
    return _campagne(st, resultats, suffixe)


def _campagne_doc(
    st: StatistiquesApplication,
    _resultats: tuple[tuple[str, int, int], ...],
    table_tarifs: TableTarifs,
    date_ref: date,
) -> Campagne:
    """Campagne du résumé : même modèle au niveau Flex, qualité identique."""
    resultats = [
        _resultat_candidat(
            modele=GRAND_MODELE,
            succes=TAILLE_ECHANTILLON,
            echecs=0,
            reference=True,
            st=st,
            table_tarifs=table_tarifs,
            niveau=ServiceTier.STANDARD,
            date_ref=date_ref,
        ),
        _resultat_candidat(
            modele=GRAND_MODELE,
            succes=TAILLE_ECHANTILLON,
            echecs=0,
            reference=False,
            st=st,
            table_tarifs=table_tarifs,
            niveau=ServiceTier.FLEX,
            date_ref=date_ref,
        ),
    ]
    return _campagne(st, resultats, "doc-flex")


def _campagne(
    st: StatistiquesApplication, resultats: list[ResultatCandidat], suffixe: str
) -> Campagne:
    """Assemble une campagne terminée à partir de ses résultats."""
    candidats = sorted({r.modele_id for r in resultats if not r.est_reference})
    reference = next(r for r in resultats if r.est_reference)
    cout_unitaire = reference.cout_par_requete_usd + sum(
        r.cout_par_requete_usd for r in resultats if not r.est_reference
    )
    cout_estime = cout_unitaire * TAILLE_ECHANTILLON * 2
    return Campagne(
        id=identifiant_demo("c", st.tenant_id, st.application_id, suffixe),
        tenant_id=st.tenant_id,
        application_id=st.application_id,
        modeles_candidats=candidats,
        taille_echantillon=TAILLE_ECHANTILLON,
        graine_aleatoire=42,
        statut=StatutCampagne.TERMINEE,
        cout_estime_usd=round(cout_estime, 2),
        cout_reel_usd=round(cout_estime * 0.97, 2),
        resultats=resultats,
    )


def _resultat_candidat(
    *,
    modele: str,
    succes: int,
    echecs: int,
    reference: bool,
    st: StatistiquesApplication,
    table_tarifs: TableTarifs,
    niveau: ServiceTier,
    date_ref: date,
) -> ResultatCandidat:
    """Résultat consolidé d'un candidat (notes du juge et contrôles)."""
    intervalle = intervalle_wilson(succes, TAILLE_ECHANTILLON)
    cout = cout_par_requete_pour(st, table_tarifs, modele, niveau, date_ref)
    seuil = 0.97 if st.application_id == "support-assistant" else 0.95
    conforme = intervalle.taux >= seuil and intervalle.bas > 0.90 and echecs == 0
    facteur_latence = FACTEURS_LATENCE_MODELE.get(modele, 1.0)
    if niveau == ServiceTier.FLEX:
        facteur_latence *= FACTEUR_LATENCE_FLEX
    return ResultatCandidat(
        modele_id=modele,
        est_reference=reference,
        nombre_requetes_rejouees=TAILLE_ECHANTILLON,
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        auto_accord_modele_actuel=AUTO_ACCORD_REFERENCE,
        echecs_deterministes=echecs,
        accord_juge_humain=None if reference else 0.93,
        cout_par_requete_usd=round(cout, 8),
        latence_p50_ms=int(st.latence_p50 * facteur_latence),
        latence_p95_ms=int(st.latence_p95 * facteur_latence),
        familles_en_echec=[] if conforme else ["tickets rédigés dans une langue rare"],
        conforme_seuils=conforme,
    )


# --- Recommandations ----------------------------------------------------------


def construire_recommandations(
    stats: dict[str, StatistiquesApplication],
    campagnes: list[Campagne],
) -> list[Recommandation]:
    """Construit les trois recommandations du scénario (§8.5, Annexe B)."""
    recos: list[Recommandation] = []
    for campagne in campagnes:
        st = stats.get(campagne.application_id)
        if st is None:
            continue
        recos.append(_recommandation(st, campagne))
    return recos


def _recommandation(st: StatistiquesApplication, campagne: Campagne) -> Recommandation:
    """Recommandation tirée d'une campagne : cible retenue et statut."""
    reference = next(r for r in campagne.resultats if r.est_reference)
    candidats = [r for r in campagne.resultats if not r.est_reference]
    retenu = _candidat_retenu(candidats)
    statut = _statut_recommandation(st.application_id, retenu)
    return Recommandation(
        id=identifiant_demo("r", st.tenant_id, st.application_id, campagne.id),
        tenantId=st.tenant_id,
        applicationId=st.application_id,
        currentModel=GRAND_MODELE,
        targetModel=retenu.modele_id,
        campaignId=campagne.id,
        sampleSize=campagne.taille_echantillon,
        quality=MetriquesQualite(
            equivalenceRate=retenu.taux_equivalence,
            confidenceIntervalLow=retenu.intervalle_confiance_bas,
            selfAgreementOfCurrentModel=AUTO_ACCORD_REFERENCE,
            deterministicFailures=retenu.echecs_deterministes,
            judgeHumanAgreement=retenu.accord_juge_humain,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=reference.cout_par_requete_usd,
            costPerRequestAfter=retenu.cout_par_requete_usd,
            monthlyRequests=st.volume_mensuel,
            expectedMonthlySaving=economie_mensuelle(
                reference.cout_par_requete_usd, retenu.cout_par_requete_usd, st.volume_mensuel
            ),
        ),
        latency=MetriquesLatence(
            p50Before=reference.latence_p50_ms,
            p50After=retenu.latence_p50_ms,
            p95Before=reference.latence_p95_ms,
            p95After=retenu.latence_p95_ms,
        ),
        risks=_risques(st.application_id, retenu),
        rolloutPlan=[10, 50, 100],
        status=statut,
        approvedBy=None if statut == StatutRecommandation.EN_ATTENTE_APPROBATION else APPROBATEUR,
    )


def _candidat_retenu(candidats: list[ResultatCandidat]) -> ResultatCandidat:
    """Candidat retenu : le moins cher qui passe les seuils, sinon le moins cher.

    Retenir le moins cher conforme est la règle métier : à qualité suffisante,
    c'est l'économie qui décide. Si aucun ne passe, on documente le refus sur le
    candidat le plus économique — c'est la preuve la plus parlante.
    """
    conformes = [c for c in candidats if c.conforme_seuils]
    return min(conformes or candidats, key=lambda c: c.cout_par_requete_usd)


def _statut_recommandation(application_id: str, retenu: ResultatCandidat) -> StatutRecommandation:
    """Statut de la recommandation : en attente, refusée ou en cours de bascule."""
    if not retenu.conforme_seuils:
        return StatutRecommandation.REFUSEE
    if application_id == "doc-summarizer":
        return StatutRecommandation.EN_COURS_DE_BASCULE
    return StatutRecommandation.EN_ATTENTE_APPROBATION


def _risques(application_id: str, retenu: ResultatCandidat) -> list[str]:
    """Risques rédigés pour la recommandation (champ `risks`, Annexe B)."""
    if application_id == "doc-summarizer":
        return [
            "Niveau Flex : latence p95 allongée d'environ 25 %, sans effet sur un "
            "traitement nocturne",
            "Vérifier l'absence d'échéance de production avant 6 h du matin",
        ]
    if not retenu.conforme_seuils:
        return [
            "Candidat sous le seuil de qualité de l'application",
            "Réponses rédigées multi-sujets : échecs du juge et contrôles "
            "déterministes non conformes",
        ]
    return [
        "Classement dans une langue rare : 6 échecs sur 200 à surveiller",
        "Surveillance en ligne à chaque palier de la bascule",
    ]


# --- Bascule et vérification --------------------------------------------------


def construire_bascules(recommandations: list[Recommandation]) -> list[BasculeProgressive]:
    """Bascule en cours : passage du résumé au niveau Flex, palier 50 %."""
    bascules: list[BasculeProgressive] = []
    for reco in recommandations:
        if reco.status != StatutRecommandation.EN_COURS_DE_BASCULE:
            continue
        bascules.append(
            BasculeProgressive(
                id=identifiant_demo("ro", reco.tenantId, reco.applicationId, reco.id),
                tenant_id=reco.tenantId,
                recommandation_id=reco.id,
                application_id=reco.applicationId,
                modele_cible=reco.targetModel,
                regle_passerelle_id="gw-rule-doc-summarizer-flex",
                palier_actuel_pourcentage=50,
                qualite_en_ligne_mesuree=0.995,
                seuil_alerte=0.90,
                actif=True,
            )
        )
    return bascules


def construire_verifications(recommandations: list[Recommandation]) -> list[VerificationEconomie]:
    """Vérification d'économie à 30 jours à volume constant (EF-VER-01)."""
    verifications: list[VerificationEconomie] = []
    for reco in recommandations:
        if reco.status not in {
            StatutRecommandation.EN_COURS_DE_BASCULE,
            StatutRecommandation.APPLIQUEE,
        }:
            continue
        attendue = reco.cost.expectedMonthlySaving
        constatee = round(attendue * 0.96, 2)
        verifications.append(
            VerificationEconomie(
                application_id=reco.applicationId,
                recommandation_id=reco.id,
                cout_par_requete_avant=reco.cost.costPerRequestBefore,
                cout_par_requete_apres=reco.cost.costPerRequestAfter,
                volume_periode=reco.cost.monthlyRequests,
                economie_constatee_usd=constatee,
                economie_attendue_usd=attendue,
                ecart_pourcentage=baisse_pourcentage(attendue, constatee),
            )
        )
    return verifications
