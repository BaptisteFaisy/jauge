"""Backtest contrefactuel (`GET /v1/applications/{id}/backtest`) — piste n° 5.

« Ce que ça aurait coûté le mois dernier » : la route rejoue les agrégats déjà
collectés d'une application avec les **tarifs du modèle candidat**, à volume
constant, et rend l'économie contrefactuelle avec sa dispersion P10/P50/P90.

Exigences visées :
- EF-VER-01 : coût par requête avant / après, à volume constant ;
- ENF-SEC-02 : cloisonnement strict — le `tenant_id` vient du contexte
  d'authentification, jamais du corps de la requête ; une application d'un autre
  client rend 403 `tenant_isolation_violation` et l'événement est journalisé ;
- EF-ADM-02 : chaque appel est audité (modèle candidat, période, résultat) ;
- ENF-CONF-01 : la réponse ne contient aucun texte de requête ni de réponse —
  seulement des compteurs, des identifiants et des coûts ;
- ENF-IA-01 : tous les chiffres viennent du moteur (`jauge.replay.backtest`),
  lui-même bâti sur `jauge.engine.calculator` et `jauge.engine.stats`.

**Coût de calcul : aucun jeton consommé.** La route relit les agrégats et rejoue
la table des tarifs ; elle n'appelle aucun modèle. Le backtest est donc gratuit
et utilisable en démonstration, hors ligne, sur les journaux déjà produits.

Le résultat porte ses **hypothèses** (`hypotheses`) : c'est une projection, pas
une économie constatée sur la facture — la différence avec
`VerificationEconomie` (EF-VER-01..02, vérification à 30 jours après bascule)
est assumée et affichée.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import Utilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.applications import application_controlee

# Réutilise la fenêtre par défaut de la route coûts (« les trente derniers
# jours ») : une seule implémentation de cette politique, pas une copie.
from jauge.api.routes.costs import DepDateDebut, DepDateFin, _periode
from jauge.common.errors import IntrouvableError
from jauge.engine.calculator import TableTarifs
from jauge.pricing_data.seed import table_demo
from jauge.replay.backtest import ResultatBacktest, backtester, volumes_depuis_agregats

router = APIRouter(tags=["backtest"])


class HypothesesReponse(BaseModel):
    """Hypothèses portées par la réponse : le chiffre ne voyage jamais seul."""

    volume_constant: bool
    memes_jetons: bool
    qualite_non_verifiee: bool
    niveau_candidat: str
    mention: str
    liste: list[str] = Field(default_factory=list)


class VentilationReponse(BaseModel):
    """Économie contrefactuelle ventilée, en USD."""

    par_application: dict[str, float] = Field(default_factory=dict)
    par_equipe: dict[str, float] = Field(default_factory=dict)
    par_modele: dict[str, float] = Field(default_factory=dict)
    par_jour: dict[str, float] = Field(default_factory=dict)


class ReponseBacktest(BaseModel):
    """Réponse de `GET /v1/applications/{id}/backtest`."""

    tenant_id: str
    application_id: str
    modele_actuel: str | None = None
    modele_candidat: str
    periode_debut: date
    periode_fin: date
    cout_reel_usd: float
    cout_contrefactuel_usd: float
    economie_usd: float
    economie_pourcentage: float
    economie_p10_usd: float
    economie_p50_usd: float
    economie_p90_usd: float
    cout_par_requete_reel_usd: float
    cout_par_requete_contrefactuel_usd: float
    economie_projection_mensuelle_usd: float
    nombre_requetes: int
    requetes_non_evaluables: int
    jours_observes: int
    donnees_absentes: bool
    ventilation: VentilationReponse
    hypotheses: HypothesesReponse


@lru_cache(maxsize=1)
def _table_du_depot() -> TableTarifs:
    """Table datée du dépôt, lue une seule fois (`JAUGE_TABLE_TARIFS` la remplace)."""
    return table_demo()


def obtenir_tarifs(request: Request) -> TableTarifs:
    """Table des tarifs : celle injectée par l'application, sinon celle du dépôt."""
    table = getattr(request.app.state, "tarifs", None)
    if isinstance(table, TableTarifs):
        return table
    return _table_du_depot()


DepTarifs = Annotated[TableTarifs, Depends(obtenir_tarifs)]
DepModeleCandidat = Annotated[
    str, Query(min_length=1, description="Modèle candidat dont on rejoue les tarifs")
]


def _ventilation(resultat: ResultatBacktest) -> VentilationReponse:
    """Ventilation au format d'API : les jours deviennent des clés ISO."""
    par_jour = resultat.ventilation.par_jour
    return VentilationReponse(
        par_application=dict(resultat.ventilation.par_application),
        par_equipe=dict(resultat.ventilation.par_equipe),
        par_modele=dict(resultat.ventilation.par_modele),
        par_jour={jour.isoformat(): par_jour[jour] for jour in sorted(par_jour)},
    )


def _hypotheses(resultat: ResultatBacktest) -> HypothesesReponse:
    """Hypothèses du moteur, telles quelles : rien n'est réécrit côté API."""
    hypotheses = resultat.hypotheses
    return HypothesesReponse(
        volume_constant=hypotheses.volume_constant,
        memes_jetons=hypotheses.memes_jetons,
        qualite_non_verifiee=hypotheses.qualite_non_verifiee,
        niveau_candidat=hypotheses.niveau_candidat,
        mention=hypotheses.mention,
        liste=list(hypotheses.liste),
    )


def _vers_reponse(resultat: ResultatBacktest, modele_actuel: str | None) -> ReponseBacktest:
    """Traduit le résultat du moteur en contrat d'API (aucun calcul ici)."""
    return ReponseBacktest(
        tenant_id=resultat.tenant_id,
        application_id=resultat.application_id,
        modele_actuel=modele_actuel,
        modele_candidat=resultat.modele_candidat,
        periode_debut=resultat.periode_debut,
        periode_fin=resultat.periode_fin,
        cout_reel_usd=resultat.cout_reel_usd,
        cout_contrefactuel_usd=resultat.cout_contrefactuel_usd,
        economie_usd=resultat.economie_usd,
        economie_pourcentage=resultat.economie_pourcentage,
        economie_p10_usd=resultat.economie_p10_usd,
        economie_p50_usd=resultat.economie_p50_usd,
        economie_p90_usd=resultat.economie_p90_usd,
        cout_par_requete_reel_usd=resultat.cout_par_requete_reel_usd,
        cout_par_requete_contrefactuel_usd=resultat.cout_par_requete_contrefactuel_usd,
        economie_projection_mensuelle_usd=resultat.economie_projection_mensuelle_usd,
        nombre_requetes=resultat.nombre_requetes,
        requetes_non_evaluables=resultat.requetes_non_evaluables,
        jours_observes=resultat.jours_observes,
        donnees_absentes=resultat.donnees_absentes,
        ventilation=_ventilation(resultat),
        hypotheses=_hypotheses(resultat),
    )


def _journaliser_appel(
    audit: AuditSink,
    utilisateur: Utilisateur,
    app_id: str,
    modele_candidat: str,
    debut: date,
    fin: date,
    resultat: ResultatBacktest,
) -> None:
    """Consigne l'appel et son résultat chiffré (EF-ADM-02)."""
    journaliser(
        audit,
        utilisateur,
        "lecture_backtest",
        "application",
        app_id,
        {
            "modele_candidat": modele_candidat,
            "periode_debut": str(debut),
            "periode_fin": str(fin),
            "economie_usd": resultat.economie_usd,
            "economie_pourcentage": resultat.economie_pourcentage,
            "nombre_requetes": resultat.nombre_requetes,
            "donnees_absentes": resultat.donnees_absentes,
        },
        tenant_id=resultat.tenant_id,
    )


@router.get("/v1/applications/{app_id}/backtest", response_model=ReponseBacktest)
def backtest_application(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    tarifs: DepTarifs,
    modele_candidat: DepModeleCandidat,
    debut: DepDateDebut = None,
    fin: DepDateFin = None,
) -> ReponseBacktest:
    """Économie contrefactuelle d'une application, à volume constant (EF-VER-01).

    Cloisonnement : l'application est localisée via le contexte d'appel, un
    autre client rend 403. Tarif candidat absent de la table datée : 422
    `pricing_missing`, jamais un coût à zéro. Période sans données : 200 avec
    `donnees_absentes` à vrai et des totaux nuls.
    """
    application = application_controlee(app_id, utilisateur, store, audit)
    tenant = store.obtenir_tenant(application.tenant_id)
    if tenant is None:
        raise IntrouvableError("Client introuvable", tenant_id=application.tenant_id)
    debut_reel, fin_reelle = _periode(debut, fin)
    agregats = store.lister_agregats(
        application.tenant_id, app_id=app_id, debut=debut_reel, fin=fin_reelle
    )
    resultat = backtester(
        tarifs=tarifs,
        region=tenant.region_principale,
        modele_candidat=modele_candidat,
        volumes=volumes_depuis_agregats(agregats),
        tenant_id=application.tenant_id,
        application_id=app_id,
        periode_debut=debut_reel,
        periode_fin=fin_reelle,
    )
    _journaliser_appel(
        audit, utilisateur, app_id, modele_candidat, debut_reel, fin_reelle, resultat
    )
    return _vers_reponse(resultat, application.modele_actuel)
