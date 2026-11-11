"""Rapport mensuel par client (EF-RAP-03, EF-RAP-01, §4, §7.2, §8.5).

Ce module ne calcule **aucun** chiffre nouveau : il assemble des valeurs déjà
produites par le socle. Les dépenses, la couverture d'attribution et l'écart de
rapprochement viennent de `Store.synthese_cout` ; l'écart mensuel est formé par
`jauge.engine.attribution.rapprocher_facture` ; la variation de période par
`jauge.engine.stats.baisse_pourcentage` ; les économies et les leviers sont lus
tels quels dans le store. Conséquence directe : le rapport ne peut pas
contredire le tableau de bord ni la facture (ENF-IA-01).

Contenu (EF-RAP-03) : dépense par application, par équipe et par modèle,
tendance sur la période, écart de rapprochement avec la facture, leviers
détectés et économie estimée, économies attendues **et** constatées,
recommandations en attente d'approbation, état des bascules, et appendice
méthodologique (seuil, taille d'échantillon, intervalle de confiance, date de
la table des tarifs).

Le rapport ne transporte aucune donnée de requête ou de réponse : il ne lit que
des agrégats, des identifiants et des scores (ENF-CONF-01). Toute lecture passe
par `tenant_id` (ENF-SEC-02).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from jauge.common.errors import IntrouvableError, ValidationError
from jauge.common.ids import deterministic
from jauge.engine.attribution import rapprocher_facture
from jauge.engine.stats import Z_95, baisse_pourcentage
from jauge.models.campaign import Campagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier, StatutLevier
from jauge.models.pricing import TarifModele
from jauge.models.recommendation import (
    BasculeProgressive,
    Recommandation,
    StatutRecommandation,
    VerificationEconomie,
)
from jauge.models.tenant import Application, Tenant
from jauge.reports.schema import (
    CONFIANCE_POURCENTAGE,
    SEUIL_INTERVALLE_BAS_DEFAUT,
    SEUIL_QUALITE_DEFAUT,
    AppendiceMethodologique,
    EcartFacture,
    EconomiesRapportees,
    EtatBascule,
    LevierRapporte,
    LigneCout,
    RapportMensuel,
    RecommandationEnAttente,
    TendanceMensuelle,
)
from jauge.store.protocol import Store

# En dessous de 1 % de variation, la tendance est présentée comme stable.
SEUIL_STABILITE_POURCENTAGE = 1.0
# Une recommandation « engagée » porte une économie attendue opposable au client.
STATUTS_ENGAGES = (
    StatutRecommandation.APPROUVEE,
    StatutRecommandation.EN_COURS_DE_BASCULE,
    StatutRecommandation.APPLIQUEE,
)


def periode_mensuelle(annee: int, mois: int) -> tuple[date, date]:
    """Bornes du mois demandé (premier et dernier jour), validées."""
    if not 1 <= mois <= 12:
        raise ValidationError(f"Mois invalide : {mois}", mois=mois)
    if not 2000 <= annee <= 2100:
        raise ValidationError(f"Année invalide : {annee}", annee=annee)
    return date(annee, mois, 1), date(annee, mois, calendar.monthrange(annee, mois)[1])


def mois_precedent(annee: int, mois: int) -> tuple[int, int]:
    """Année et mois du mois précédent (bascule janvier → décembre)."""
    return (annee - 1, 12) if mois == 1 else (annee, mois - 1)


def construire_rapport(
    store: Store,
    tenant_id: str,
    annee: int,
    mois: int,
    *,
    tarifs: list[TarifModele] | None = None,
) -> RapportMensuel:
    """Construit le rapport mensuel d'un client à partir du store (EF-RAP-03).

    `tarifs` ne sert qu'à dater la table des tarifs dans l'appendice
    méthodologique : aucun prix n'est recalculé ici.
    """
    debut, fin = periode_mensuelle(annee, mois)
    vue = _lire_mois(store, tenant_id, debut, fin)
    return RapportMensuel(
        id=f"rp-{deterministic('rapport', tenant_id, annee, mois)}",
        tenant_id=vue.tenant.id,
        tenant_nom=vue.tenant.nom,
        annee=annee,
        mois=mois,
        periode_debut=debut,
        periode_fin=fin,
        **_blocs(store, vue, annee, mois, tarifs),
    )


@dataclass(frozen=True)
class _VueMois:
    """Vue d'un mois lue une seule fois dans le store (client déjà validé)."""

    tenant: Tenant
    applications: list[Application]
    agregats: list[AgregatQuotidien]
    synthese: SyntheseCout
    noms: dict[str, str]
    compteurs: dict[str, dict[str, int]]


def _lire_mois(store: Store, tenant_id: str, debut: date, fin: date) -> _VueMois:
    """Lit le mois demandé : client, applications, agrégats, synthèse du socle."""
    tenant = store.obtenir_tenant(tenant_id)
    if tenant is None:
        raise IntrouvableError(f"Client inconnu : {tenant_id}", tenant_id=tenant_id)
    applications = store.lister_applications(tenant_id)
    agregats = store.lister_agregats(tenant_id, debut=debut, fin=fin)
    return _VueMois(
        tenant=tenant,
        applications=applications,
        agregats=agregats,
        synthese=store.synthese_cout(tenant_id, debut, fin),
        noms={app.id: app.nom for app in applications},
        compteurs=_compteurs_requetes(agregats),
    )


def _blocs(
    store: Store, vue: _VueMois, annee: int, mois: int, tarifs: list[TarifModele] | None
) -> dict[str, object]:
    """Assemble les blocs du rapport à partir de la vue du mois et des états."""
    synthese = vue.synthese
    total = synthese.cout_total_usd
    leviers = _leviers_du_rapport(store.lister_leviers(vue.tenant.id), vue.noms)
    recommandations = store.lister_recommandations(vue.tenant.id)
    debut, fin = periode_mensuelle(annee, mois)
    return {
        "cout_total_usd": total,
        "nombre_requetes": sum(agregat.nombre_requetes for agregat in vue.agregats),
        "couverture_attribution_pourcentage": synthese.couverture_attribution_pourcentage,
        "par_application": _lignes(
            synthese.cout_par_application, vue.noms, vue.compteurs["application"], total
        ),
        "par_equipe": _lignes(synthese.cout_par_equipe, {}, vue.compteurs["equipe"], total),
        "par_modele": _lignes(synthese.cout_par_modele, {}, vue.compteurs["modele"], total),
        "tendance": _tendance(store, vue.tenant.id, annee, mois, total),
        "ecart_facture": _ecart_facture(
            store.lister_rapprochements(vue.tenant.id), vue.tenant.id, debut, fin
        ),
        "leviers": leviers,
        "economies": _economies(
            recommandations, store.lister_verifications(vue.tenant.id), leviers
        ),
        "recommandations_en_attente": _recommandations_en_attente(recommandations, vue.noms),
        "bascules": _bascules(store.lister_bascules(vue.tenant.id), vue.noms),
        "methodologie": _methodologie(
            vue.tenant, vue.applications, store.lister_campagnes(vue.tenant.id), tarifs
        ),
    }


def _part(cout: float, total: float) -> float:
    """Part d'un poste dans le total, en pourcentage borné à 100."""
    if total <= 0:
        return 0.0
    return round(min(100.0, cout / total * 100.0), 2)


def _lignes(
    depenses: dict[str, float],
    libelles: dict[str, str],
    requetes: dict[str, int],
    total: float,
) -> list[LigneCout]:
    """Transforme un dictionnaire de dépenses en lignes triées décroissantes.

    Les postes à zéro sont écartés : `SyntheseCout` amorce à 0.0 les
    applications connues du client, et un rapport ne liste pas une application
    qui n'a rien dépensé sur la période.
    """
    lignes = [
        LigneCout(
            cle=cle,
            libelle=libelles.get(cle, cle),
            cout_usd=round(cout, 4),
            part_pourcentage=_part(cout, total),
            nombre_requetes=requetes.get(cle, 0),
        )
        for cle, cout in depenses.items()
        if cout > 0
    ]
    return sorted(lignes, key=lambda ligne: (-ligne.cout_usd, ligne.cle))


def _compteurs_requetes(agregats: list[AgregatQuotidien]) -> dict[str, dict[str, int]]:
    """Volumes de requêtes par application, par équipe et par modèle."""
    compteurs: dict[str, dict[str, int]] = {"application": {}, "equipe": {}, "modele": {}}
    for agregat in agregats:
        cles = (
            ("application", agregat.application_id),
            ("equipe", agregat.equipe),
            ("modele", agregat.modele_id),
        )
        for dimension, cle in cles:
            compteurs[dimension][cle] = compteurs[dimension].get(cle, 0) + agregat.nombre_requetes
    return compteurs


def _tendance(
    store: Store, tenant_id: str, annee: int, mois: int, courant: float
) -> TendanceMensuelle:
    """Tendance mensuelle : le mois précédent est relu dans le même store."""
    annee_prec, mois_prec = mois_precedent(annee, mois)
    debut, fin = periode_mensuelle(annee_prec, mois_prec)
    precedent = store.synthese_cout(tenant_id, debut, fin).cout_total_usd
    variation = round(courant - precedent, 2)
    # `baisse_pourcentage` donne une baisse positive : on la remet dans le sens
    # de la dépense (+ = le coût augmente) pour éviter toute lecture ambiguë.
    variation_pct = round(-baisse_pourcentage(precedent, courant), 2)
    return TendanceMensuelle(
        cout_mois_courant_usd=round(courant, 2),
        cout_mois_precedent_usd=round(precedent, 2),
        variation_usd=variation,
        variation_pourcentage=variation_pct,
        sens=_sens(variation_pct),
    )


def _sens(variation_pourcentage: float) -> str:
    """Sens de la variation : stable sous 1 %, sinon hausse ou baisse."""
    if abs(variation_pourcentage) < SEUIL_STABILITE_POURCENTAGE:
        return "stable"
    return "hausse" if variation_pourcentage > 0 else "baisse"


def _ecart_facture(
    rapprochements: list[RapprochementJour], tenant_id: str, debut: date, fin: date
) -> EcartFacture:
    """Écart mensuel recalculé/facturé, formé par `rapprocher_facture`."""
    du_mois = [r for r in rapprochements if debut <= r.jour <= fin]
    if not du_mois:
        return EcartFacture(
            disponible=False,
            cout_recalcule_usd=0.0,
            cout_facture_usd=0.0,
            ecart_usd=0.0,
            ecart_pourcentage=0.0,
            conforme=True,
            jours_rapproches=0,
        )
    recalcule = round(sum(r.cout_recalcule_usd for r in du_mois), 4)
    facture = round(sum(r.cout_facture_usd for r in du_mois), 4)
    causes = sorted({cause for r in du_mois for cause in r.causes_non_attribuees})
    ecart = rapprocher_facture(tenant_id, fin, recalcule, facture, causes=causes)
    return EcartFacture(
        disponible=True,
        cout_recalcule_usd=recalcule,
        cout_facture_usd=facture,
        ecart_usd=ecart.ecart_usd,
        ecart_pourcentage=ecart.ecart_pourcentage,
        conforme=ecart.conforme,
        jours_rapproches=len(du_mois),
        causes_non_attribuees=causes,
    )


def _leviers_du_rapport(leviers: list[Levier], noms: dict[str, str]) -> list[LevierRapporte]:
    """Leviers non rejetés, du plus gros enjeu au plus petit."""
    retenus = [
        LevierRapporte(
            id=levier.id,
            application_id=levier.application_id,
            application_nom=noms.get(levier.application_id, levier.application_id),
            type_levier=str(levier.type_levier),
            statut=str(levier.statut),
            economie_mensuelle_estimee_usd=levier.economie_mensuelle_estimee_usd,
            confiance=levier.confiance,
            applique=levier.statut == StatutLevier.APPLIQUE,
        )
        for levier in leviers
        if levier.statut != StatutLevier.REJETE
    ]
    return sorted(retenus, key=lambda item: (-item.economie_mensuelle_estimee_usd, item.id))


def _economies(
    recommandations: list[Recommandation],
    verifications: list[VerificationEconomie],
    leviers: list[LevierRapporte],
) -> EconomiesRapportees:
    """Économies attendues, constatées et restantes, sans jamais les confondre."""
    attendues = round(
        sum(r.cost.expectedMonthlySaving for r in recommandations if r.status in STATUTS_ENGAGES), 2
    )
    constatees = round(sum(v.economie_constatee_usd for v in verifications), 2)
    restantes = round(
        sum(levier.economie_mensuelle_estimee_usd for levier in leviers if not levier.applique), 2
    )
    taux = round(constatees / attendues * 100.0, 2) if attendues > 0 else 0.0
    return EconomiesRapportees(
        attendues_usd=attendues,
        constatees_usd=constatees,
        restantes_usd=restantes,
        ecart_usd=round(attendues - constatees, 2),
        taux_realisation_pourcentage=min(taux, 999.99),
        nombre_verifications=len(verifications),
    )


def _recommandations_en_attente(
    recommandations: list[Recommandation], noms: dict[str, str]
) -> list[RecommandationEnAttente]:
    """Recommandations qui attendent la décision d'un responsable (§8.5)."""
    en_attente = [
        RecommandationEnAttente(
            id=reco.id,
            application_id=reco.applicationId,
            application_nom=noms.get(reco.applicationId, reco.applicationId),
            modele_actuel=reco.currentModel,
            modele_cible=reco.targetModel,
            economie_mensuelle_attendue_usd=reco.cost.expectedMonthlySaving,
            statut=str(reco.status),
        )
        for reco in recommandations
        if reco.status == StatutRecommandation.EN_ATTENTE_APPROBATION
    ]
    return sorted(en_attente, key=lambda item: (-item.economie_mensuelle_attendue_usd, item.id))


def _bascules(bascules: list[BasculeProgressive], noms: dict[str, str]) -> list[EtatBascule]:
    """État de chaque bascule : palier courant, annulation automatique (§8.6)."""
    etats = [
        EtatBascule(
            id=bascule.id,
            application_id=bascule.application_id,
            application_nom=noms.get(bascule.application_id, bascule.application_id),
            modele_cible=bascule.modele_cible,
            palier_actuel_pourcentage=bascule.palier_actuel_pourcentage,
            actif=bascule.actif,
            annule_automatiquement=bascule.annule_automatiquement,
            qualite_en_ligne_mesuree=bascule.qualite_en_ligne_mesuree,
            seuil_alerte=bascule.seuil_alerte,
            motif_annulation=bascule.motif_annulation,
        )
        for bascule in bascules
    ]
    return sorted(etats, key=lambda item: (not item.actif, item.id))


def _methodologie(
    tenant: Tenant,
    applications: list[Application],
    campagnes: list[Campagne],
    tarifs: list[TarifModele] | None,
) -> AppendiceMethodologique:
    """Appendice méthodologique : seuils, échantillon, confiance, date des tarifs."""
    seuils = [app.seuil_qualite for app in applications] or [SEUIL_QUALITE_DEFAUT]
    bornes = [app.seuil_intervalle_bas for app in applications] or [SEUIL_INTERVALLE_BAS_DEFAUT]
    tailles = [campagne.taille_echantillon for campagne in campagnes]
    return AppendiceMethodologique(
        seuil_qualite=min(seuils),
        seuil_intervalle_bas=min(bornes),
        taille_echantillon=max(tailles, default=0),
        intervalle_confiance_pourcentage=CONFIANCE_POURCENTAGE,
        z_score=Z_95,
        date_table_tarifs=max((tarif.date_effet for tarif in tarifs), default=None)
        if tarifs
        else None,
        region=tenant.region_principale,
        sources=[
            "Bedrock invocation logs (token counts and latency)",
            "AWS invoice / Cost and Usage Report (billed amount)",
            "Dated Bedrock price table (recomputed cost)",
        ],
    )
