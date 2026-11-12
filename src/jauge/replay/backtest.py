"""Backtest contrefactuel — « ce que ça aurait coûté le mois dernier ».

Piste n° 5 du top 10 (`docs/backlog/README.md`) : rejouer la période passée
avec les **tarifs du modèle candidat** au lieu de ceux du modèle réellement
servi, **à volume constant** (mêmes requêtes, mêmes jetons), et rendre
l'économie *contrefactuelle* en dollars — sans rien changer à la production.

Exigences visées :
- EF-VER-01 : comparer le coût par requête avant et après, **à volume constant** ;
- §4 : indicateur principal = baisse de la facture d'inférence à qualité égale,
  mesurée sur la période observée puis projetée sur un mois ;
- ENF-IA-01 : aucun chiffre n'est produit par un modèle de langage — tout vient
  de `jauge.engine.calculator` et de `jauge.engine.stats` ;
- ENF-CONF-01 : le résultat ne porte **aucun texte** de requête ni de réponse,
  seulement des compteurs, des identifiants et des coûts.

Trois propriétés que ce module garantit, et qui le séparent d'une économie
constatée (`jauge.models.recommendation.VerificationEconomie`) :

1. **Projection, pas constat.** Le candidat *n'a pas servi* : on rejoue des
   tarifs sur des journaux déjà là. L'hypothèse (« mêmes requêtes, mêmes
   jetons ») est portée dans le résultat (`HypothesesBacktest`) et rappelée dans
   le rendu.
2. **Un intervalle, pas un point.** L'économie est assortie de ses percentiles
   P10/P50/P90, obtenus par bootstrap sur les jours : une variation du trafic
   d'un jour à l'autre ne peut pas être cachée derrière un chiffre unique.
3. **Aucun coût à zéro en silence.** Un tarif absent de la table datée lève
   `TarifInconnuError` ; une période sans données rend un résultat vide
   explicite ; des requêtes dont les jetons sont illisibles sont **exclues et
   comptées**.

**Coût de calcul : aucun jeton consommé.** Le backtest relit des agrégats et
rejoue la *table des tarifs* ; il n'appelle aucun modèle, ni en démo ni en
production. C'est ce qui le rend gratuit, instantané et utilisable hors ligne
sur les journaux déjà collectés.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from jauge.common.errors import ValidationError
from jauge.common.ids import graine_de
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import baisse_pourcentage, economie_mensuelle, percentile
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import MetriquesRequete, ServiceTier

#: Nombre de tirages du bootstrap (dispersion inter-journalière du trafic).
ITERATIONS_BOOTSTRAP_DEFAUT = 1000
#: Graine par défaut du bootstrap : le résultat est reproductible.
GRAINE_DEFAUT = 42
#: Jours par mois pour la projection mensuelle (§4 : économie mensuelle).
JOURS_PAR_MOIS = 30
#: Percentiles publiés : une dispersion, jamais un point seul.
PERCENTILES_PUBLIES = (10, 50, 90)

#: Mention portée par le résultat : projection, jamais économie constatée.
MENTION_PROJECTION = (
    "Projection contrefactuelle : ce que le candidat aurait coûté sur la période observée, "
    "à volume constant. Cette économie n'est pas constatée sur la facture, et la qualité "
    "du candidat n'est pas vérifiée ici."
)

#: Hypothèses explicites, reprises telles quelles dans le résultat.
HYPOTHESES_EXPLICITES = (
    "Le candidat aurait traité les mêmes requêtes, avec les mêmes jetons (volume constant).",
    "La qualité du candidat n'est pas vérifiée par ce backtest : il mesure un coût, "
    "pas une équivalence. La preuve de qualité vient du rejeu et du juge.",
    "Le tarif du candidat est celui de la table datée, au niveau de service indiqué, "
    "à la date de chaque jour observé.",
)


@dataclass(frozen=True, slots=True)
class VolumeBacktest:
    """Volume de requêtes d'un jour et d'une dimension, rejoué au tarif du candidat.

    Les jetons sont **des totaux** pour le volume : la formule de coût
    (`calculer_cout_requete`) est linéaire en jetons, donc le coût d'un volume
    s'obtient en appliquant le tarif du candidat à ses totaux — sans jamais
    redistribuer les requêtes une à une.

    `jetons_entree` et `jetons_sortie` valent `None` quand l'information de
    consommation est illisible : le volume est alors **exclu** et compté dans
    `ResultatBacktest.requetes_non_evaluables`.
    """

    jour: date
    application_id: str
    equipe: str
    modele_id: str
    nombre_requetes: int = 0
    niveau: ServiceTier = ServiceTier.STANDARD
    jetons_entree: int | None = None
    jetons_sortie: int | None = None
    jetons_cache_ecriture: int = 0
    jetons_cache_lecture: int = 0
    cout_reel_usd: float = 0.0

    @property
    def jetons_connus(self) -> bool:
        """Vrai si la consommation de jetons est exploitable."""
        return self.jetons_entree is not None and self.jetons_sortie is not None


@dataclass(frozen=True, slots=True)
class HypothesesBacktest:
    """Hypothèses portées par le résultat : elles voyagent avec le chiffre."""

    volume_constant: bool = True
    memes_jetons: bool = True
    qualite_non_verifiee: bool = True
    niveau_candidat: str = ServiceTier.STANDARD.value
    mention: str = MENTION_PROJECTION
    liste: tuple[str, ...] = HYPOTHESES_EXPLICITES


@dataclass(frozen=True, slots=True)
class VentilationBacktest:
    """Économie contrefactuelle ventilée par dimension et par jour (USD)."""

    par_application: dict[str, float] = field(default_factory=dict)
    par_equipe: dict[str, float] = field(default_factory=dict)
    par_modele: dict[str, float] = field(default_factory=dict)
    par_jour: dict[date, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResultatBacktest:
    """Résultat d'un backtest contrefactuel (projection, pas économie constatée)."""

    tenant_id: str
    application_id: str
    modele_candidat: str
    periode_debut: date
    periode_fin: date
    cout_reel_usd: float = 0.0
    cout_contrefactuel_usd: float = 0.0
    economie_usd: float = 0.0
    economie_pourcentage: float = 0.0
    economie_p10_usd: float = 0.0
    economie_p50_usd: float = 0.0
    economie_p90_usd: float = 0.0
    cout_par_requete_reel_usd: float = 0.0
    cout_par_requete_contrefactuel_usd: float = 0.0
    economie_projection_mensuelle_usd: float = 0.0
    nombre_requetes: int = 0
    requetes_non_evaluables: int = 0
    jours_observes: int = 0
    donnees_absentes: bool = False
    ventilation: VentilationBacktest = field(default_factory=VentilationBacktest)
    hypotheses: HypothesesBacktest = field(default_factory=HypothesesBacktest)


@dataclass
class _Cumul:
    """Accumulateur interne du calcul (jamais exposé tel quel)."""

    cout_reel_usd: float = 0.0
    cout_contrefactuel_usd: float = 0.0
    nombre_requetes: int = 0
    par_application: dict[str, float] = field(default_factory=dict)
    par_equipe: dict[str, float] = field(default_factory=dict)
    par_modele: dict[str, float] = field(default_factory=dict)
    par_jour: dict[date, float] = field(default_factory=dict)

    def ajouter(self, volume: VolumeBacktest, cout_contrefactuel: float) -> None:
        """Intègre un volume évalué."""
        economie = volume.cout_reel_usd - cout_contrefactuel
        self.cout_reel_usd += volume.cout_reel_usd
        self.cout_contrefactuel_usd += cout_contrefactuel
        self.nombre_requetes += volume.nombre_requetes
        self.par_application[volume.application_id] = round(
            self.par_application.get(volume.application_id, 0.0) + economie, 6
        )
        self.par_equipe[volume.equipe] = round(
            self.par_equipe.get(volume.equipe, 0.0) + economie, 6
        )
        self.par_modele[volume.modele_id] = round(
            self.par_modele.get(volume.modele_id, 0.0) + economie, 6
        )
        self.par_jour[volume.jour] = round(self.par_jour.get(volume.jour, 0.0) + economie, 6)


def volumes_depuis_agregats(agregats: Iterable[AgregatQuotidien]) -> list[VolumeBacktest]:
    """Convertit les agrégats quotidiens du socle en volumes rejouables.

    Un agrégat qui compte des requêtes mais **aucun jeton** (information de
    consommation absente ou illisible) est marqué « jetons inconnus » : il sera
    exclu du calcul et compté comme non évaluable, jamais valorisé à zéro.
    """
    volumes: list[VolumeBacktest] = []
    for agregat in agregats:
        jetons_connus = (
            agregat.jetons_entree_total
            + agregat.jetons_sortie_total
            + agregat.jetons_cache_ecriture_total
            + agregat.jetons_cache_lecture_total
        ) > 0
        volumes.append(
            VolumeBacktest(
                jour=agregat.jour,
                application_id=agregat.application_id,
                equipe=agregat.equipe,
                modele_id=agregat.modele_id,
                nombre_requetes=agregat.nombre_requetes,
                jetons_entree=agregat.jetons_entree_total if jetons_connus else None,
                jetons_sortie=agregat.jetons_sortie_total if jetons_connus else None,
                jetons_cache_ecriture=agregat.jetons_cache_ecriture_total,
                jetons_cache_lecture=agregat.jetons_cache_lecture_total,
                cout_reel_usd=agregat.cout_recalcule_usd,
            )
        )
    return volumes


def _cout_contrefactuel(
    tarifs: TableTarifs, region: str, modele_candidat: str, volume: VolumeBacktest
) -> float:
    """Coût du volume au tarif du candidat (Annexe D, formule du socle).

    Lève `TarifInconnuError` si la table datée ne couvre pas le candidat :
    jamais un coût à zéro en silence.
    """
    tarif = tarifs.trouver(modele_candidat, region, volume.niveau, volume.jour)
    metriques = MetriquesRequete(
        jetons_entree=volume.jetons_entree or 0,
        jetons_sortie=volume.jetons_sortie or 0,
        jetons_cache_ecriture=volume.jetons_cache_ecriture,
        jetons_cache_lecture=volume.jetons_cache_lecture,
    )
    return calculer_cout_requete(tarif, metriques).cout_total


def _bootstrap(quotidien: list[float], graine: int, iterations: int) -> list[float]:
    """Sommes d'économies tirées **avec remise** sur les jours (bootstrap).

    Chaque tirage choisit `len(quotidien)` jours au hasard (avec remise) et
    additionne leur économie : la dispersion obtenue est celle du trafic
    journalier, pas une incertitude sur le tarif.
    """
    if not quotidien:
        return []
    tirage = random.Random(graine)
    taille = len(quotidien)
    return [sum(tirage.choice(quotidien) for _ in range(taille)) for _ in range(iterations)]


def _percentiles(echantillons: list[float]) -> tuple[float, float, float]:
    """P10, P50 et P90 d'un échantillon (interpolation du socle, rien de neuf)."""
    if not echantillons:
        return (0.0, 0.0, 0.0)
    p10, p50, p90 = (percentile(echantillons, p) for p in PERCENTILES_PUBLIES)
    return (p10, p50, p90)


def _couts_par_requete(cumul: _Cumul) -> tuple[float, float]:
    """Coût moyen par requête, réel et contrefactuel."""
    if cumul.nombre_requetes <= 0:
        return (0.0, 0.0)
    return (
        round(cumul.cout_reel_usd / cumul.nombre_requetes, 8),
        round(cumul.cout_contrefactuel_usd / cumul.nombre_requetes, 8),
    )


def _projection_mensuelle(
    cout_par_requete_reel: float, cout_par_requete_contrefactuel: float, cumul: _Cumul
) -> float:
    """Projection mensuelle de l'économie (volume journalier moyen × 30 jours)."""
    jours = len(cumul.par_jour)
    if jours <= 0:
        return 0.0
    volume_mensuel = round(cumul.nombre_requetes / jours * JOURS_PAR_MOIS)
    return economie_mensuelle(cout_par_requete_reel, cout_par_requete_contrefactuel, volume_mensuel)


def _valider(
    modele_candidat: str, periode_debut: date, periode_fin: date, iterations_bootstrap: int
) -> None:
    """Refuse une demande incohérente avant tout calcul (erreur explicite)."""
    if not modele_candidat.strip():
        raise ValidationError("Un modèle candidat est requis.", modele_candidat=modele_candidat)
    if periode_fin < periode_debut:
        raise ValidationError(
            "La fin de période précède son début.",
            periode_debut=str(periode_debut),
            periode_fin=str(periode_fin),
        )
    if iterations_bootstrap <= 0:
        raise ValidationError(
            "Le nombre de tirages du bootstrap doit être strictement positif.",
            iterations_bootstrap=iterations_bootstrap,
        )


def _cumuler(
    tarifs: TableTarifs,
    region: str,
    modele_candidat: str,
    volumes: Sequence[VolumeBacktest],
    periode_debut: date,
    periode_fin: date,
) -> tuple[_Cumul, int]:
    """Évalue chaque volume de la période ; compte ceux qui ne le sont pas."""
    cumul = _Cumul()
    non_evaluables = 0
    for volume in volumes:
        if not (periode_debut <= volume.jour <= periode_fin):
            continue
        if volume.nombre_requetes <= 0:
            continue
        if not volume.jetons_connus:
            non_evaluables += volume.nombre_requetes
            continue
        cumul.ajouter(volume, _cout_contrefactuel(tarifs, region, modele_candidat, volume))
    return cumul, non_evaluables


def backtester(
    *,
    tarifs: TableTarifs,
    region: str,
    modele_candidat: str,
    volumes: Sequence[VolumeBacktest],
    tenant_id: str,
    application_id: str,
    periode_debut: date,
    periode_fin: date,
    graine: int = GRAINE_DEFAUT,
    iterations_bootstrap: int = ITERATIONS_BOOTSTRAP_DEFAUT,
) -> ResultatBacktest:
    """Rejoue la période aux tarifs du candidat et rend l'économie contrefactuelle.

    Le volume est **constant** : mêmes requêtes, mêmes jetons, seul le tarif
    change. Aucun modèle n'est appelé — le backtest est gratuit (§17.1 : le
    devis d'une campagne concerne le rejeu, pas ce calcul).
    """
    _valider(modele_candidat, periode_debut, periode_fin, iterations_bootstrap)
    cumul, non_evaluables = _cumuler(
        tarifs, region, modele_candidat, volumes, periode_debut, periode_fin
    )
    return _resultat(
        cumul=cumul,
        non_evaluables=non_evaluables,
        modele_candidat=modele_candidat,
        tenant_id=tenant_id,
        application_id=application_id,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        graine=graine,
        iterations_bootstrap=iterations_bootstrap,
    )


def _resultat(
    *,
    cumul: _Cumul,
    non_evaluables: int,
    modele_candidat: str,
    tenant_id: str,
    application_id: str,
    periode_debut: date,
    periode_fin: date,
    graine: int,
    iterations_bootstrap: int,
) -> ResultatBacktest:
    """Assemble le résultat : totaux, dispersion et hypothèses."""
    cout_avant, cout_apres = _couts_par_requete(cumul)
    quotidien = [cumul.par_jour[jour] for jour in sorted(cumul.par_jour)]
    echantillons = _bootstrap(
        quotidien, graine_de(graine, "backtest", application_id), iterations_bootstrap
    )
    p10, p50, p90 = _percentiles(echantillons)
    return ResultatBacktest(
        tenant_id=tenant_id,
        application_id=application_id,
        modele_candidat=modele_candidat,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        cout_reel_usd=round(cumul.cout_reel_usd, 6),
        cout_contrefactuel_usd=round(cumul.cout_contrefactuel_usd, 6),
        economie_usd=round(cumul.cout_reel_usd - cumul.cout_contrefactuel_usd, 6),
        economie_pourcentage=baisse_pourcentage(cumul.cout_reel_usd, cumul.cout_contrefactuel_usd),
        economie_p10_usd=round(p10, 6),
        economie_p50_usd=round(p50, 6),
        economie_p90_usd=round(p90, 6),
        cout_par_requete_reel_usd=cout_avant,
        cout_par_requete_contrefactuel_usd=cout_apres,
        economie_projection_mensuelle_usd=_projection_mensuelle(cout_avant, cout_apres, cumul),
        nombre_requetes=cumul.nombre_requetes,
        requetes_non_evaluables=non_evaluables,
        jours_observes=len(quotidien),
        donnees_absentes=cumul.nombre_requetes == 0,
        ventilation=VentilationBacktest(
            par_application=dict(cumul.par_application),
            par_equipe=dict(cumul.par_equipe),
            par_modele=dict(cumul.par_modele),
            par_jour=dict(cumul.par_jour),
        ),
        hypotheses=HypothesesBacktest(),
    )
