"""Agrégats quotidiens par application, équipe, environnement et modèle (§8.2, EF-ATT-05).

Le parcours d'attribution quotidienne de la spécification :

1. les journaux du jour sont lus (modèle, jetons, cache, niveau de service,
   étiquettes) — c'est `logs.py` ;
2. chaque requête reçoit un coût calculé par le socle — `calculer_cout_requete`,
   jamais une formule parallèle (ENF-IA-01) ;
3. les coûts sont agrégés par application, équipe, environnement, modèle et jour
   — `agreger_requetes`, jamais une seconde implémentation de l'agrégation ;
4. le total recalculé est rapproché de la facture — `reconcile.py`.

**Flux, pas chargement intégral** (ENF-PERF-01 : un million de requêtes par jour
en moins de 30 minutes). L'agrégateur ne garde qu'un accumulateur par clé
d'agrégat ; les valeurs individuelles vivent dans des tableaux de types
primitifs (une vingtaine d'octets par appel) et sont rejouées à la demande vers
`agreger_requetes`, qui reste seul à décider du contenu d'un agrégat.

Les étiquettes manquantes sont complétées par l'application par défaut du schéma
d'attribution du client (EF-ATT-03) et signalées dans
`ResultatAgregation.part_non_attribuee` (EF-ATT-07) : la dépense reste visible,
mais elle est marquée comme non attribuée.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

from jauge.attribution.logs import EnregistrementInvocation, RapportLecture
from jauge.attribution.tags import (
    VALEUR_INCONNUE,
    CumulNonAttribution,
    Etiquettes,
    LigneAttribution,
    PartNonAttribuee,
    appliquer_schema,
    completer,
    valeurs_hors_schema,
)
from jauge.engine.attribution import agreger_requetes
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele
from jauge.models.tenant import Application, SchemaAttribution, Tenant

#: Dimensions sur lesquelles un agrégat peut être ventilé (EF-ATT-05, EF-RAP-01).
DIMENSIONS: dict[str, str] = {
    "application": "application_id",
    "equipe": "equipe",
    "environnement": "environnement",
    "modele": "modele_id",
}


class VueMetriques(NamedTuple):
    """Vue légère des jetons d'un appel, lue attribut par attribut.

    `agreger_requetes` et `calculer_cout_requete` ne lisent que des attributs :
    cette vue évite d'instancier un modèle pydantic par appel sur un million de
    requêtes (ENF-PERF-01). Ses champs sont exactement ceux de `MetriquesRequete`.
    """

    jetons_entree: int
    jetons_sortie: int
    jetons_cache_ecriture: int
    jetons_cache_lecture: int
    latence_ms: int


class VueCout(NamedTuple):
    """Vue légère du coût d'un appel ; `cout_total` est celui calculé par le socle."""

    cout_total: float


class Colonnes(NamedTuple):
    """Colonnes d'un accumulateur : une entrée par appel du même groupe."""

    entree: array
    sortie: array
    cache_ecriture: array
    cache_lecture: array
    latence: array
    cout: array

    @classmethod
    def vides(cls) -> Colonnes:
        """Accumulateur vide, prêt à recevoir des appels."""
        return cls(
            entree=array("q"),
            sortie=array("q"),
            cache_ecriture=array("q"),
            cache_lecture=array("q"),
            latence=array("q"),
            cout=array("d"),
        )

    def ajouter(self, metriques: MetriquesRequete, cout_total: float) -> None:
        """Ajoute un appel au groupe."""
        self.entree.append(metriques.jetons_entree)
        self.sortie.append(metriques.jetons_sortie)
        self.cache_ecriture.append(metriques.jetons_cache_ecriture)
        self.cache_lecture.append(metriques.jetons_cache_lecture)
        self.latence.append(metriques.latence_ms)
        self.cout.append(cout_total)

    def __len__(self) -> int:
        return len(self.entree)


class LignesRejouees(Sequence):
    """Séquence paresseuse `(métriques, coût)` consommée par `agreger_requetes`.

    Rien n'est matérialisé : chaque accès fabrique la vue d'un appel à partir des
    colonnes, si bien que `agreger_requetes` peut itérer plusieurs fois sans coût
    mémoire.
    """

    def __init__(self, colonnes: Colonnes) -> None:
        self._colonnes = colonnes

    def __len__(self) -> int:
        return len(self._colonnes)

    def __getitem__(self, index: int) -> tuple[VueMetriques, VueCout]:
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        colonnes = self._colonnes
        return (
            VueMetriques(
                colonnes.entree[index],
                colonnes.sortie[index],
                colonnes.cache_ecriture[index],
                colonnes.cache_lecture[index],
                colonnes.latence[index],
            ),
            VueCout(colonnes.cout[index]),
        )


@dataclass(frozen=True, slots=True)
class CleAgregat:
    """Dimensions d'un agrégat quotidien (EF-ATT-05)."""

    application_id: str
    equipe: str
    environnement: str
    modele_id: str
    jour: date


@dataclass(frozen=True, slots=True)
class ResultatAgregation:
    """Agrégats d'une lecture, plus ce qu'il faut pour expliquer le total."""

    agregats: list[AgregatQuotidien]
    part_non_attribuee: PartNonAttribuee
    part_non_attribuee_par_jour: dict[date, PartNonAttribuee] = field(default_factory=dict)
    nb_enregistrements: int = 0
    nb_sans_etiquette: int = 0
    nb_sensibles_lus: int = 0
    nb_sensibles_exclus: int = 0
    nb_inexploitables: int = 0

    @property
    def cout_recalcule_usd(self) -> float:
        """Somme des coûts recalculés, base du rapprochement (EF-ATT-06)."""
        return round(sum(agregat.cout_recalcule_usd for agregat in self.agregats), 4)

    @property
    def cout_non_attribue_usd(self) -> float:
        """Dépense non attribuée à une application (EF-ATT-07)."""
        return self.part_non_attribuee.depense_non_attribuee_usd

    def par_jour(self) -> dict[date, float]:
        """Coût recalculé par jour, entrée de `reconcile.py`."""
        cumuls: dict[date, float] = {}
        for agregat in self.agregats:
            cumuls[agregat.jour] = cumuls.get(agregat.jour, 0.0) + agregat.cout_recalcule_usd
        return {jour: round(total, 4) for jour, total in cumuls.items()}

    def pour_jour(self, jour: date) -> ResultatAgregation | None:
        """Vue restreinte à un jour, avec sa propre part non attribuée.

        Le rapprochement est quotidien (EF-ATT-06) : il lui faut la part non
        attribuée du jour, pas celle de toute la période lue.
        """
        du_jour = [agregat for agregat in self.agregats if agregat.jour == jour]
        if not du_jour:
            return None
        part = self.part_non_attribuee_par_jour.get(jour, PartNonAttribuee())
        return ResultatAgregation(
            agregats=du_jour,
            part_non_attribuee=part,
            part_non_attribuee_par_jour={jour: part},
            nb_enregistrements=sum(agregat.nombre_requetes for agregat in du_jour),
            nb_sans_etiquette=self.nb_sans_etiquette,
            nb_sensibles_lus=self.nb_sensibles_lus,
            nb_sensibles_exclus=self.nb_sensibles_exclus,
            nb_inexploitables=self.nb_inexploitables,
        )


@dataclass
class _Groupe:
    """Un accumulateur et la fiche d'application qui portera l'agrégat."""

    application: Application
    colonnes: Colonnes = field(default_factory=Colonnes.vides)


class AgregateurQuotidien:
    """Construit les agrégats quotidiens d'un client à partir de ses journaux.

    `applications` est la liste des applications connues du plan de contrôle :
    elle sert à retrouver l'équipe et l'environnement d'une étiquette
    d'application, et à reconnaître une application hors schéma. Les fiches d'un
    autre client sont écartées (ENF-SEC-02).
    """

    def __init__(
        self,
        tenant: Tenant,
        tarifs: TableTarifs,
        schema: SchemaAttribution | None = None,
        applications: Iterable[Application] = (),
        region: str | None = None,
    ) -> None:
        self._tenant = tenant
        self._tarifs = tarifs
        self._schema = schema or tenant.schema_attribution
        connues = [app for app in applications if app.tenant_id == tenant.id]
        self._applications: dict[str, Application] = {app.id: app for app in connues}
        self._applications.update({app.nom: app for app in connues})
        self._region = region or tenant.region_principale
        self._groupes: dict[CleAgregat, _Groupe] = {}
        self._tarif_cache: dict[tuple[str, ServiceTier, date], TarifModele] = {}
        self._non_attribution: dict[date, CumulNonAttribution] = {}
        self._nb_sans_etiquette = 0
        self._nb_sensibles = 0

    def agreger(
        self,
        enregistrements: Iterable[EnregistrementInvocation],
        rapport: RapportLecture | None = None,
        exclure_sensibles: bool = False,
    ) -> ResultatAgregation:
        """Consomme un flux d'enregistrements et rend les agrégats consolidés.

        `exclure_sensibles=False` par défaut : une requête sensible reste une
        requête **facturée**, l'écarter fausserait le rapprochement (EF-ATT-06).
        Elle est en revanche écartée du rejeu (`logs.iterer_rejouables`). Le
        drapeau permet au client qui le souhaite de tenir ses requêtes sensibles
        hors de ses agrégats ; le nombre écarté est alors signalé.
        """
        self._groupes = {}
        self._tarif_cache = {}
        self._non_attribution = {}
        self._nb_sans_etiquette = 0
        self._nb_sensibles = 0
        total = 0
        for enregistrement in enregistrements:
            if exclure_sensibles and enregistrement.sensible:
                self._nb_sensibles += 1
                continue
            self._integrer(enregistrement)
            total += 1
        return self._resultat(total, rapport)

    def _integrer(self, enregistrement: EnregistrementInvocation) -> None:
        """Calcule le coût d'un appel, l'accumule et note son attribution."""
        jour = enregistrement.jour
        metriques = enregistrement.metriques
        tarif = self._tarif(enregistrement.model_id, enregistrement.niveau_service, jour)
        cout = calculer_cout_requete(tarif, metriques)
        etiquettes, etiquete, hors_schema = self._resoudre(enregistrement.etiquettes)
        application = self._application_pour(etiquettes, enregistrement.model_id)
        cle = CleAgregat(
            application_id=application.id,
            equipe=application.equipe,
            environnement=application.environnement,
            modele_id=enregistrement.model_id,
            jour=jour,
        )
        groupe = self._groupes.get(cle)
        if groupe is None:
            groupe = self._groupes[cle] = _Groupe(application=application)
        groupe.colonnes.ajouter(metriques, cout.cout_total)
        if not etiquete:
            self._nb_sans_etiquette += 1
        self._non_attribution.setdefault(jour, CumulNonAttribution()).ajouter(
            LigneAttribution(
                etiquettes=etiquettes,
                cout_usd=cout.cout_total,
                etiquete=etiquete,
                point_acces=enregistrement.point_acces,
                hors_schema=hors_schema,
            )
        )

    def _tarif(self, modele_id: str, niveau: ServiceTier, jour: date) -> TarifModele:
        """Tarif applicable, mis en cache par (modèle, niveau, jour)."""
        cle = (modele_id, niveau, jour)
        tarif = self._tarif_cache.get(cle)
        if tarif is None:
            tarif = self._tarifs.trouver(modele_id, self._region, niveau, jour)
            self._tarif_cache[cle] = tarif
        return tarif

    def _resoudre(self, etiquettes: Etiquettes) -> tuple[Etiquettes, bool, bool]:
        """Complète les étiquettes ; dit si l'appel est attribué et s'il est conforme."""
        etiquete = etiquettes.est_attribuee
        completees = appliquer_schema(etiquettes, self._schema)
        return completees, etiquete, bool(valeurs_hors_schema(completees, self._schema))

    def _application_pour(self, etiquettes: Etiquettes, modele_id: str) -> Application:
        """Fiche de l'application visée, réelle ou de repli.

        Une étiquette inconnue du plan de contrôle n'est pas perdue : elle produit
        une fiche locale (identifiant = étiquette) pour que la dépense apparaisse
        dans les agrégats et soit signalée comme hors schéma.
        """
        connue = self._applications.get(etiquettes.application or "")
        if connue is not None:
            return connue.model_copy(
                update={
                    "equipe": etiquettes.equipe or connue.equipe,
                    "environnement": etiquettes.environnement or connue.environnement,
                    "modele_actuel": modele_id,
                }
            )
        identifiant = etiquettes.application or self._schema.application_par_defaut
        completees = completer(etiquettes, None)
        return Application(
            id=identifiant,
            tenant_id=self._tenant.id,
            nom=identifiant,
            equipe=completees.equipe or VALEUR_INCONNUE,
            environnement=completees.environnement or VALEUR_INCONNUE,
            modele_actuel=modele_id,
        )

    def _bilan_global(self) -> PartNonAttribuee:
        """Part non attribuée de toute la période lue (somme des jours)."""
        total = CumulNonAttribution()
        for cumul in self._non_attribution.values():
            total.depense_totale_usd += cumul.depense_totale_usd
            total.depense_non_attribuee_usd += cumul.depense_non_attribuee_usd
            for cause, montant in cumul.causes.items():
                total.causes[cause] = total.causes.get(cause, 0.0) + montant
        return total.bilan()

    def _resultat(self, total: int, rapport: RapportLecture | None) -> ResultatAgregation:
        """Fige les accumulateurs en agrégats quotidiens triés."""
        agregats = [self._agregat(cle, groupe) for cle, groupe in self._groupes.items()]
        agregats.sort(key=lambda a: (a.jour, a.application_id, a.modele_id))
        return ResultatAgregation(
            agregats=agregats,
            part_non_attribuee=self._bilan_global(),
            part_non_attribuee_par_jour={
                jour: cumul.bilan() for jour, cumul in sorted(self._non_attribution.items())
            },
            nb_enregistrements=total,
            nb_sans_etiquette=self._nb_sans_etiquette,
            nb_sensibles_lus=rapport.sensibles if rapport else 0,
            nb_sensibles_exclus=self._nb_sensibles,
            nb_inexploitables=rapport.nb_rejetes if rapport else 0,
        )

    def _agregat(self, cle: CleAgregat, groupe: _Groupe) -> AgregatQuotidien:
        """Délègue le calcul de l'agrégat au socle (`agreger_requetes`)."""
        return agreger_requetes(
            self._tenant,
            groupe.application,
            cle.jour,
            LignesRejouees(groupe.colonnes),  # type: ignore[arg-type]
            list(groupe.colonnes.latence),
        )


def agregats_par_dimension(
    agregats: Iterable[AgregatQuotidien], dimension: str
) -> dict[str, float]:
    """Somme les coûts recalculés par dimension (application, équipe, modèle…).

    Sert au tableau de bord client (EF-RAP-01) et aux règles de leviers (M3), qui
    raisonnent par application, équipe, environnement ou modèle.
    """
    attribut = DIMENSIONS.get(dimension)
    if attribut is None:
        raise ValueError(f"dimension inconnue : {dimension} (attendu : {sorted(DIMENSIONS)})")
    cumuls: dict[str, float] = {}
    for agregat in agregats:
        valeur = getattr(agregat, attribut)
        cumuls[valeur] = cumuls.get(valeur, 0.0) + agregat.cout_recalcule_usd
    return {valeur: round(total, 4) for valeur, total in cumuls.items()}


def iterer_par_jour(
    agregats: Iterable[AgregatQuotidien],
) -> Iterator[tuple[date, list[AgregatQuotidien]]]:
    """Regroupe des agrégats par jour, dans l'ordre chronologique."""
    par_jour: dict[date, list[AgregatQuotidien]] = {}
    for agregat in agregats:
        par_jour.setdefault(agregat.jour, []).append(agregat)
    for jour in sorted(par_jour):
        yield jour, par_jour[jour]
