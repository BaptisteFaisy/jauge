"""Socle local des règles de détection des leviers d'économie (Annexe A).

Exigences visées : EF-LEV-01 à EF-LEV-06 (§9.2).

Ce module fixe le contrat commun à toutes les règles :

- `ProfilRequetes` : profil **agrégé** d'une application (longueurs, types de
  tâche, préfixes, heures, erreurs, niveaux de service). Aucun texte de
  requête ni de réponse n'y figure (ENF-CONF-01) ;
- `ParametresLevier` : volume mensuel, région et seuils de détection ;
- `ContexteLevier` : tout ce dont une règle a besoin — agrégats
  (`AgregatQuotidien`), profil, paramètres, table de tarifs et modèles
  autorisés du client ;
- `Regle` : protocole d'une règle (un type de levier, une méthode `detecter`) ;
- des helpers partagés (volumes, coûts moyens, construction d'un `Levier`).

Une règle ne lit **que** les agrégats et le profil : jamais le texte des
requêtes. Le profil est produit par le module d'attribution (M1) à partir des
journaux d'invocation ; son remplissage est un contrat local, à faire évoluer
avec M1 (cf. corps de la PR).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from jauge.common.ids import deterministic
from jauge.engine.calculator import TableTarifs
from jauge.models.cost import AgregatQuotidien
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier

# Types de tâche considérés comme « simples » (classification, extraction).
TACHES_SIMPLES: tuple[str, ...] = ("classification", "extraction")

# Heures creuses : trafic de fond, tolère un délai (Annexe A, « nuit »).
HEURES_NOCTURNES: tuple[int, ...] = (22, 23, 0, 1, 2, 3, 4, 5)

# Nombre d'heures qui, à elles seules, matérialisent une rafale de trafic.
HEURES_RAFALE: int = 3


@dataclass(frozen=True)
class ParametresLevier:
    """Paramètres de calcul et seuils de détection d'un levier.

    `volume_mensuel_requetes` est le volume de référence de l'application ;
    s'il vaut 0, il est extrapolé des agrégats observés.
    """

    date_reference: date
    region: str = "eu-west-1"
    volume_mensuel_requetes: int = 0
    jours_par_mois: int = 30
    # Seuils « modèle surdimensionné » (EF-LEV-01)
    seuil_entree_courte: int = 512
    seuil_sortie_courte: int = 256
    seuil_part_tache_simple: float = 0.5
    seuil_part_sortie_structuree: float = 0.5
    # Seuils « cache de prompts » (EF-LEV-02)
    seuil_part_prefixe_long: float = 0.5
    seuil_longueur_prefixe_long: int = 1024
    # Seuils « traitement différable » (EF-LEV-03)
    seuil_part_non_interactive: float = 0.8
    seuil_part_trafic_nocturne: float = 0.3
    seuil_part_trafic_rafale: float = 0.5
    # Seuils « sorties trop longues » (EF-LEV-04)
    seuil_part_sortie_longue: float = 0.1
    ratio_sortie_mediane: float = 2.0
    ratio_limite_sortie: float = 0.9
    # Seuils des règles S (EF-LEV-05)
    seuil_part_requetes_identiques: float = 0.2
    seuil_ratio_relance: float = 0.2
    seuil_part_priority: float = 0.2


@dataclass(frozen=True)
class ProfilRequetes:
    """Profil agrégé des requêtes d'une application (jamais de texte).

    Les compteurs (types de tâche, heures, niveaux de service) portent sur la
    période observée, la même que les agrégats.
    """

    # Longueurs, en jetons
    longueur_entree_moyenne: float = 0.0
    longueur_entree_p95: int = 0
    longueur_sortie_moyenne: float = 0.0
    longueur_sortie_mediane: int = 0
    longueur_sortie_p95: int = 0
    part_requetes_sortie_longue: float = 0.0
    limite_sortie_modele: int = 0
    # Tâches
    requetes_par_type_tache: Mapping[str, int] = field(default_factory=dict)
    part_sortie_structuree: float = 0.0
    diversite_sorties: float = 1.0
    # Préfixes communs (empreintes, jamais le texte)
    part_prefixe_long: float = 0.0
    longueur_prefixe_long: int = 0
    factor_reutilisation_prefixe: float = 1.0
    # Temporalité (24 valeurs, index 0 = minuit)
    requetes_par_heure: tuple[int, ...] = ()
    requetes_non_interactive_par_heure: tuple[int, ...] = ()
    # Erreurs et relances
    nombre_erreurs: int = 0
    nombre_relances_apres_erreur: int = 0
    # Niveaux de service
    requetes_par_niveau: Mapping[str, int] = field(default_factory=dict)
    requetes_non_interactive_par_niveau: Mapping[str, int] = field(default_factory=dict)
    # Doublons
    part_requetes_identiques: float = 0.0


@dataclass(frozen=True)
class ContexteLevier:
    """Contexte d'analyse d'une application, passé à toutes les règles."""

    tenant_id: str
    application_id: str
    modele_actuel: str
    profil: ProfilRequetes
    parametres: ParametresLevier
    agregats: tuple[AgregatQuotidien, ...] = ()
    modeles_autorises: tuple[str, ...] = ()
    table_tarifs: TableTarifs = field(default_factory=TableTarifs)


@runtime_checkable
class Regle(Protocol):
    """Une règle de détection : un type de levier et une méthode de détection.

    Une règle renvoie `None` quand le signal décrit par l'Annexe A est absent :
    l'absence de signal n'est pas un levier.
    """

    type_levier: TypeLevier

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier détecté, ou `None` si le signal est absent."""
        ...


def total_requetes(contexte: ContexteLevier) -> int:
    """Nombre de requêtes observées (agrégats, sinon profil)."""
    total = sum(a.nombre_requetes for a in contexte.agregats)
    if total > 0:
        return total
    return sum(contexte.profil.requetes_par_niveau.values())


def jours_observes(contexte: ContexteLevier) -> int:
    """Nombre de jours distincts couverts par les agrégats."""
    return len({a.jour for a in contexte.agregats})


def volume_mensuel(contexte: ContexteLevier) -> int:
    """Volume mensuel de requêtes : paramètre s'il est fourni, sinon extrapolé."""
    parametre = contexte.parametres.volume_mensuel_requetes
    if parametre > 0:
        return parametre
    jours = jours_observes(contexte)
    if jours <= 0:
        return 0
    return round(total_requetes(contexte) / jours * contexte.parametres.jours_par_mois)


def jetons_par_requete(contexte: ContexteLevier) -> tuple[float, float]:
    """Jetons d'entrée et de sortie moyens par requête (agrégats)."""
    nombre = total_requetes(contexte)
    if nombre <= 0:
        return (0.0, 0.0)
    entree = sum(a.jetons_entree_total for a in contexte.agregats) / nombre
    sortie = sum(a.jetons_sortie_total for a in contexte.agregats) / nombre
    return (entree, sortie)


def cout_par_requete(contexte: ContexteLevier) -> float:
    """Coût moyen observé par requête (agrégats recalculés)."""
    nombre = total_requetes(contexte)
    if nombre <= 0:
        return 0.0
    return sum(a.cout_recalcule_usd for a in contexte.agregats) / nombre


def jetons_cache_lecture(contexte: ContexteLevier) -> int:
    """Jetons lus en cache sur la période observée."""
    return sum(a.jetons_cache_lecture_total for a in contexte.agregats)


def part_type_tache(profil: ProfilRequetes, types: Sequence[str]) -> float:
    """Part des requêtes appartenant aux types de tâche donnés."""
    total = sum(profil.requetes_par_type_tache.values())
    if total <= 0:
        return 0.0
    choisi = sum(profil.requetes_par_type_tache.get(type_, 0) for type_ in types)
    return choisi / total


def part_niveau(profil: ProfilRequetes, niveau: ServiceTier) -> float:
    """Part des requêtes servies au niveau de service donné."""
    total = sum(profil.requetes_par_niveau.values())
    if total <= 0:
        return 0.0
    return profil.requetes_par_niveau.get(niveau.value, 0) / total


def total_non_interactif(profil: ProfilRequetes) -> int:
    """Nombre d'appels non interactifs : répartition horaire, sinon par niveau."""
    par_heure = sum(profil.requetes_non_interactive_par_heure)
    if par_heure > 0:
        return par_heure
    return sum(profil.requetes_non_interactive_par_niveau.values())


def part_non_interactive(profil: ProfilRequetes) -> float:
    """Part des requêtes non interactives (travail de fond)."""
    total = sum(profil.requetes_par_niveau.values())
    if total <= 0:
        total = sum(profil.requetes_par_heure)
    if total <= 0:
        return 0.0
    return min(1.0, total_non_interactif(profil) / total)


def part_non_interactive_niveau(profil: ProfilRequetes, niveau: ServiceTier) -> float:
    """Part non interactive **parmi** les requêtes du niveau donné."""
    total = profil.requetes_par_niveau.get(niveau.value, 0)
    if total <= 0:
        return 0.0
    return profil.requetes_non_interactive_par_niveau.get(niveau.value, 0) / total


def total_requetes_horaires(profil: ProfilRequetes) -> int:
    """Total des requêtes réparties par heure."""
    return sum(profil.requetes_par_heure)


def heures_de_rafale(profil: ProfilRequetes) -> tuple[int, ...]:
    """Heures les plus chargées : elles matérialisent une rafale de trafic."""
    if not profil.requetes_par_heure:
        return ()
    rang = sorted(
        range(len(profil.requetes_par_heure)),
        key=lambda heure: (-profil.requetes_par_heure[heure], heure),
    )
    return tuple(rang[:HEURES_RAFALE])


def part_trafic_nocturne(profil: ProfilRequetes) -> float:
    """Part du trafic située en heures creuses."""
    total = total_requetes_horaires(profil)
    if total <= 0:
        return 0.0
    nocturne = sum(
        profil.requetes_par_heure[h] for h in HEURES_NOCTURNES if h < len(profil.requetes_par_heure)
    )
    return nocturne / total


def part_trafic_rafale(profil: ProfilRequetes) -> float:
    """Part du trafic concentrée sur les heures de rafale."""
    total = total_requetes_horaires(profil)
    if total <= 0:
        return 0.0
    rafale = sum(profil.requetes_par_heure[h] for h in heures_de_rafale(profil))
    return rafale / total


def part_trafic_deferrable(profil: ProfilRequetes) -> float:
    """Part du trafic à la fois non interactif et situated en heures différables.

    Heures différables = heures creuses ∪ heures de rafale. L'intersection
    avec les appels non interactifs est calculée exactement (pas d'hypothèse
    d'indépendance entre les deux axes).
    """
    total = total_requetes_horaires(profil)
    if total <= 0:
        return 0.0
    heures = set(HEURES_NOCTURNES) | set(heures_de_rafale(profil))
    eligibles = [h for h in heures if h < len(profil.requetes_non_interactive_par_heure)]
    non_interactive = sum(profil.requetes_non_interactive_par_heure[h] for h in eligibles)
    return non_interactive / total


def niveau_dominant(profil: ProfilRequetes) -> ServiceTier:
    """Niveau de service le plus utilisé (Standard si le profil est vide)."""
    if not profil.requetes_par_niveau:
        return ServiceTier.STANDARD
    niveau, _ = max(profil.requetes_par_niveau.items(), key=lambda item: (item[1], item[0]))
    try:
        return ServiceTier(niveau)
    except ValueError:
        return ServiceTier.STANDARD


def formater_nombre(valeur: float, decimales: int = 0) -> str:
    """Nombre à la française : séparateur de milliers espace, décimale virgule."""
    texte = f"{valeur:,.{decimales}f}"
    return texte.replace(",", " ").replace(".", ",")


def pourcent(valeur: float) -> str:
    """Part en pourcentage, format français."""
    return f"{formater_nombre(valeur * 100)} %"


def construire_levier(
    contexte: ContexteLevier,
    type_levier: TypeLevier,
    indices: Sequence[str],
    action_proposee: str,
    preuve_exigee: str,
    economie: float,
    confiance: float,
    candidats: Sequence[str] | None = None,
) -> Levier:
    """Construit un `Levier` complet et reproductible (identifiant déterministe)."""
    empreinte = deterministic(
        contexte.tenant_id,
        contexte.application_id,
        type_levier.value,
        contexte.parametres.date_reference.isoformat(),
    )
    return Levier(
        id=f"lv-{empreinte}",
        tenant_id=contexte.tenant_id,
        application_id=contexte.application_id,
        type_levier=type_levier,
        indices=list(indices),
        action_proposee=action_proposee,
        preuve_exigee=preuve_exigee,
        economie_mensuelle_estimee_usd=round(max(0.0, economie), 2),
        confiance=round(min(1.0, max(0.0, confiance)), 2),
        candidats_suggeres=list(candidats or []),
    )
