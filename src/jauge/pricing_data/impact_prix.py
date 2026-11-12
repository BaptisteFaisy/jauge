"""Impact économique d'un changement de prix et preuves à rejouer (piste n° 10).

Suite de `jauge.pricing_data.surveillance` (la détection) : une fois les
variations de prix connues, ce module répond à deux questions, de façon
déterministe (ENF-IA-01) :

1. **Impact économique** — chiffrer, à volume constant et à partir des agrégats
   observés du client, ce que le changement vaut. Une baisse sur un modèle que
   le client n'utilise pas ne vaut rien : aucun impact.
2. **Preuves à rejouer** — dire quelles campagnes et quelles recommandations
   doivent être rejouées, parce que leur conclusion dépendait d'un prix qui a
   changé. En particulier, un candidat **rejeté pour motif économique** (la
   qualité était suffisante, le gain était trop faible) redevient pertinent
   après une baisse : il faut donc rejouer la preuve.

Outils du socle réutilisés (zéro duplication) :

- `jauge.engine.calculator.calculer_cout_requete` : la formule de l'Annexe D ;
- `jauge.engine.stats.economie_mensuelle` et `baisse_pourcentage` : l'économie
  mensuelle à volume constant et la baisse relative en % ;
- `jauge.levers.base.volume_mensuel` : le volume mensuel extrapolé des agrégats
  observés ;
- `jauge.evaluation.decision.SeuilsDecision` : les seuils de l'application quand
  la conclusion doit être recontrôlée.

Rien n'est réécrit dans le store : ce module **signale** ce qui est devenu caduc,
la décision de rejouer reste humaine (ENF-IA-02).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from jauge.engine.calculator import calculer_cout_requete
from jauge.engine.stats import baisse_pourcentage, economie_mensuelle
from jauge.evaluation.decision import SeuilsDecision
from jauge.levers.base import (
    ContexteLevier,
    ParametresLevier,
    ProfilRequetes,
    volume_mensuel,
)
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.pricing_data.surveillance import VariationPrix
from jauge.store.protocol import Store

#: Nombre de jours retenu pour l'extrapolation mensuelle (socle : 30 jours).
JOURS_PAR_MOIS = 30


class ImpactVariation(BaseModel):
    """Impact économique d'une variation sur la dépense observée d'un client."""

    tenant_id: str
    modele_id: str
    region: str
    niveau: ServiceTier
    date_effet: date
    volume_mensuel_requetes: int
    cout_par_requete_avant_usd: float
    cout_par_requete_apres_usd: float
    economie_mensuelle_usd: float
    baisse_pourcentage: float
    surface_mensuelle_avant_usd: float
    surface_mensuelle_apres_usd: float


class PreuveARejouer(BaseModel):
    """Preuve à rejouer : une campagne ou une recommandation dont le prix a bougé."""

    tenant_id: str
    kind: Literal["recommandation", "campagne"]
    identifiant: str
    application_id: str
    modele_id: str
    date_effet: date
    variation_pourcentage: float
    motif: str
    economie_mensuelle_usd: float | None = None


def impact_variation(
    store: Store, tenant_id: str, variation: VariationPrix
) -> ImpactVariation | None:
    """Impact d'une variation sur la dépense observée d'un client.

    `None` si le client n'utilise pas le modèle : une baisse de prix sur un
    modèle inutilisé n'intéresse personne.
    """
    agregats = [
        agregat
        for agregat in store.lister_agregats(tenant_id)
        if agregat.modele_id == variation.modele_id
    ]
    if not agregats:
        return None
    requetes = sum(agregat.nombre_requetes for agregat in agregats)
    if requetes <= 0:
        return None
    cout_avant = sum(agregat.cout_recalcule_usd for agregat in agregats) / requetes
    cout_apres = _cout_par_requete(agregats, variation, requetes)
    volume = _volume_mensuel(tenant_id, variation.modele_id, agregats)
    return ImpactVariation(
        tenant_id=tenant_id,
        modele_id=variation.modele_id,
        region=variation.region,
        niveau=variation.niveau,
        date_effet=variation.date_effet,
        volume_mensuel_requetes=volume,
        cout_par_requete_avant_usd=round(cout_avant, 8),
        cout_par_requete_apres_usd=round(cout_apres, 8),
        economie_mensuelle_usd=economie_mensuelle(cout_avant, cout_apres, volume),
        baisse_pourcentage=baisse_pourcentage(cout_avant, cout_apres),
        surface_mensuelle_avant_usd=round(cout_avant * volume, 2),
        surface_mensuelle_apres_usd=round(cout_apres * volume, 2),
    )


def _cout_par_requete(
    agregats: list[AgregatQuotidien], variation: VariationPrix, requetes: int
) -> float:
    """Coût par requête au nouveau prix, sur les volumes de jetons observés."""
    metriques = MetriquesRequete(
        jetons_entree=sum(agregat.jetons_entree_total for agregat in agregats),
        jetons_sortie=sum(agregat.jetons_sortie_total for agregat in agregats),
        jetons_cache_ecriture=sum(agregat.jetons_cache_ecriture_total for agregat in agregats),
        jetons_cache_lecture=sum(agregat.jetons_cache_lecture_total for agregat in agregats),
    )
    cout = calculer_cout_requete(variation.tarif_apres(), metriques)
    return cout.cout_total / requetes


def _volume_mensuel(tenant_id: str, modele_id: str, agregats: list[AgregatQuotidien]) -> int:
    """Volume mensuel du modèle, extrapolé des agrégats observés (socle)."""
    total = 0
    for application_id in sorted({agregat.application_id for agregat in agregats}):
        par_application = [
            agregat for agregat in agregats if agregat.application_id == application_id
        ]
        contexte = ContexteLevier(
            tenant_id=tenant_id,
            application_id=application_id,
            modele_actuel=modele_id,
            profil=ProfilRequetes(),
            parametres=ParametresLevier(
                date_reference=max(agregat.jour for agregat in par_application),
                jours_par_mois=JOURS_PAR_MOIS,
            ),
            agregats=tuple(par_application),
        )
        total += volume_mensuel(contexte)
    return total


def preuves_a_rejouer(
    store: Store,
    tenant_id: str,
    variations: list[VariationPrix],
) -> list[PreuveARejouer]:
    """Liste des campagnes et recommandations à rejouer après un changement de prix.

    Règles (déterministes, dans l'ordre d'importance) :

    1. une recommandation **refusée pour motif économique** — la qualité était
       suffisante, le gain était trop faible — dont le modèle cible baisse (ou
       dont le modèle actuel monte) : le gain peut devenir suffisant, la preuve
       est à rejouer ;
    2. une recommandation en attente d'approbation ou approuvée, dont le prix
       du modèle courant ou cible a bougé : son économie attendue est à
       recalculer ;
    3. une campagne dont un résultat porte le modèle au prix modifié : le coût
       par requête du candidat est à rejouer, donc le choix du candidat aussi.
    """
    par_modele: dict[str, list[VariationPrix]] = {}
    for variation in variations:
        par_modele.setdefault(variation.modele_id, []).append(variation)
    preuves: list[PreuveARejouer] = []
    preuves.extend(_recommandations_a_rejouer(store, tenant_id, par_modele))
    preuves.extend(_campagnes_a_rejouer(store, tenant_id, par_modele))
    return preuves


def _recommandations_a_rejouer(
    store: Store, tenant_id: str, par_modele: dict[str, list[VariationPrix]]
) -> list[PreuveARejouer]:
    """Recommandations dont la conclusion dépendait d'un prix qui a bougé."""
    preuves: list[PreuveARejouer] = []
    for reco in store.lister_recommandations(tenant_id):
        if reco.status == StatutRecommandation.REFUSEE:
            if not _qualite_suffisante(store, tenant_id, reco):
                continue
            preuves.extend(_preuves_rejet_economique(tenant_id, reco, par_modele))
        elif reco.status in (
            StatutRecommandation.EN_ATTENTE_APPROBATION,
            StatutRecommandation.APPROUVEE,
        ):
            preuves.extend(_preuves_economie_a_recalculer(tenant_id, reco, par_modele))
    return preuves


def _preuves_rejet_economique(
    tenant_id: str, reco: Recommandation, par_modele: dict[str, list[VariationPrix]]
) -> list[PreuveARejouer]:
    """Preuves à rejouer pour une recommandation refusée pour motif économique."""
    preuves: list[PreuveARejouer] = []
    for variation in par_modele.get(reco.targetModel, []):
        if variation.sens != "baisse":
            continue
        preuves.append(
            PreuveARejouer(
                tenant_id=tenant_id,
                kind="recommandation",
                identifiant=reco.id,
                application_id=reco.applicationId,
                modele_id=reco.targetModel,
                date_effet=variation.date_effet,
                variation_pourcentage=_variation_centrale(variation),
                motif=(
                    "Refus pour motif économique (qualité suffisante, gain trop faible) : "
                    "le prix du modèle cible baisse, le gain peut devenir suffisant."
                ),
            )
        )
    for variation in par_modele.get(reco.currentModel, []):
        if variation.sens != "hausse":
            continue
        preuves.append(
            PreuveARejouer(
                tenant_id=tenant_id,
                kind="recommandation",
                identifiant=reco.id,
                application_id=reco.applicationId,
                modele_id=reco.currentModel,
                date_effet=variation.date_effet,
                variation_pourcentage=_variation_centrale(variation),
                motif=(
                    "Refus pour motif économique (qualité suffisante, gain trop faible) : "
                    "le prix du modèle actuel monte, l'écart de prix se creuse."
                ),
            )
        )
    return preuves


def _preuves_economie_a_recalculer(
    tenant_id: str, reco: Recommandation, par_modele: dict[str, list[VariationPrix]]
) -> list[PreuveARejouer]:
    """Preuves à rejouer quand l'économie attendue d'une recommandation est périmée."""
    preuves: list[PreuveARejouer] = []
    for modele_id in (reco.currentModel, reco.targetModel):
        for variation in par_modele.get(modele_id, []):
            preuves.append(
                PreuveARejouer(
                    tenant_id=tenant_id,
                    kind="recommandation",
                    identifiant=reco.id,
                    application_id=reco.applicationId,
                    modele_id=modele_id,
                    date_effet=variation.date_effet,
                    variation_pourcentage=_variation_centrale(variation),
                    motif=(
                        "Économie attendue calculée sur un prix qui a changé : "
                        "à recalculer avant approbation."
                    ),
                )
            )
    return preuves


def _campagnes_a_rejouer(
    store: Store, tenant_id: str, par_modele: dict[str, list[VariationPrix]]
) -> list[PreuveARejouer]:
    """Campagnes dont le coût par requête d'un candidat est devenu caduc."""
    preuves: list[PreuveARejouer] = []
    for campagne in store.lister_campagnes(tenant_id):
        for resultat in campagne.resultats:
            variations = par_modele.get(resultat.modele_id, [])
            if not variations:
                continue
            variation = variations[0]
            preuves.append(
                PreuveARejouer(
                    tenant_id=tenant_id,
                    kind="campagne",
                    identifiant=campagne.id,
                    application_id=campagne.application_id,
                    modele_id=resultat.modele_id,
                    date_effet=variation.date_effet,
                    variation_pourcentage=_variation_centrale(variation),
                    motif=(
                        "Coût par requête du candidat mesuré sur un prix qui a changé : "
                        "campagne à rejouer."
                    ),
                )
            )
    return preuves


def _qualite_suffisante(store: Store, tenant_id: str, reco: Recommandation) -> bool:
    """Vrai si la qualité était suffisante : le refus était donc économique.

    La campagne porte le verdict du socle (`ResultatCandidat.conforme_seuils`) ;
    à défaut, on recontrôle les seuils de l'application (§13.4).
    """
    campagne = store.obtenir_campagne(tenant_id, reco.campaignId)
    if campagne is not None:
        for resultat in campagne.resultats:
            if resultat.modele_id == reco.targetModel:
                return resultat.conforme_seuils
    application = store.obtenir_application(tenant_id, reco.applicationId)
    if application is None:
        return False
    seuils = SeuilsDecision.depuis_application(application)
    return (
        reco.quality.equivalenceRate >= seuils.seuil_equivalence
        and reco.quality.confidenceIntervalLow > seuils.seuil_intervalle_bas
        and reco.quality.deterministicFailures == 0
    )


def _variation_centrale(variation: VariationPrix) -> float:
    """Variation en % la plus représentative d'une variation (entrée puis sortie)."""
    if variation.variation_entree_pourcentage:
        return variation.variation_entree_pourcentage
    return variation.variation_sortie_pourcentage
