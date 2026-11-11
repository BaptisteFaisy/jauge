"""Estimation de l'économie mensuelle par levier (EF-LEV-06).

Toutes les formules sont explicites et déterministes : aucun chiffre n'est
produit par un LLM (ENF-IA-01). Les prix viennent de la table de tarifs datée
du socle, les volumes et les coûts des agrégats.

Outils du socle réutilisés :
- `jauge.engine.stats.economie_mensuelle` : (coût avant − coût après) × volume ;
- `jauge.engine.calculator.TableTarifs` : prix par modèle, région, niveau de
  service et date d'effet.

Chaque estimation est bornée par les hypothèses du profil (part du trafic
concernée, longueur du préfixe, réutilisation) ; ces hypothèses sont
reprises dans les `indices` du levier.
"""

from __future__ import annotations

import math

from jauge.common.errors import TarifInconnuError
from jauge.engine.stats import economie_mensuelle
from jauge.levers.base import (
    TACHES_SIMPLES,
    ContexteLevier,
    cout_par_requete,
    jetons_par_requete,
    jours_observes,
    niveau_dominant,
    part_niveau,
    part_non_interactive_niveau,
    part_trafic_deferrable,
    part_type_tache,
    volume_mensuel,
)
from jauge.models.pricing import ServiceTier, TarifModele


def tarif_pour(contexte: ContexteLevier, modele_id: str, niveau: ServiceTier) -> TarifModele | None:
    """Tarif applicable à la date de référence, `None` si le modèle n'est pas tarifé."""
    try:
        return contexte.table_tarifs.trouver(
            modele_id,
            contexte.parametres.region,
            niveau,
            contexte.parametres.date_reference,
        )
    except TarifInconnuError:
        return None


def cout_requete_niveau(contexte: ContexteLevier, tarif: TarifModele) -> float:
    """Coût d'une requête aux prix d'un tarif (jetons moyens des agrégats)."""
    entree, sortie = jetons_par_requete(contexte)
    return (entree * tarif.prix_entree_1k + sortie * tarif.prix_sortie_1k) / 1000.0


def cout_requete_modele(contexte: ContexteLevier, modele_id: str, niveau: ServiceTier) -> float:
    """Coût d'une requête pour un modèle et un niveau donnés (0 si non tarifé)."""
    tarif = tarif_pour(contexte, modele_id, niveau)
    if tarif is None:
        return 0.0
    return cout_requete_niveau(contexte, tarif)


def cout_actuel_par_requete(contexte: ContexteLevier) -> float:
    """Coût moyen observé ; à défaut, recalculé avec la table de tarifs."""
    observe = cout_par_requete(contexte)
    if observe > 0:
        return observe
    return cout_requete_modele(contexte, contexte.modele_actuel, niveau_dominant(contexte.profil))


def candidats_autorises(contexte: ContexteLevier) -> list[str]:
    """Modèles autorisés par le client, hors modèle courant, du moins cher au plus cher."""
    elements: list[tuple[float, str]] = []
    for modele in contexte.modeles_autorises:
        if modele == contexte.modele_actuel:
            continue
        tarif = tarif_pour(contexte, modele, ServiceTier.STANDARD)
        prix = tarif.prix_entree_1k if tarif else math.inf
        elements.append((prix, modele))
    return [modele for _, modele in sorted(elements)]


def candidats_cheapers(contexte: ContexteLevier) -> list[str]:
    """Candidats autorisés dont le coût par requête est inférieur à celui du modèle courant."""
    niveau = niveau_dominant(contexte.profil)
    cout_actuel = cout_requete_modele(contexte, contexte.modele_actuel, niveau)
    if cout_actuel <= 0:
        return []
    retenus = []
    for modele in candidats_autorises(contexte):
        cout = cout_requete_modele(contexte, modele, niveau)
        if 0 < cout < cout_actuel:
            retenus.append(modele)
    return retenus


def estimer_modele_surdimensionne(contexte: ContexteLevier) -> float:
    """Économie mensuelle d'un passage à un modèle plus petit (EF-LEV-01).

    Formule :
        coût_avant = coût moyen observé par requête (agrégats recalculés)
        coût_apres = jetons_entrée_moyens  × prix_entrée(candidat)  / 1000
                   + jetons_sortie_moyens × prix_sortie(candidat) / 1000
        économie   = (coût_avant − coût_apres) × volume_mensuel × part_éligible

    `part_éligible` = part des requêtes de tâche simple (classification,
    extraction). Le candidat retenu pour l'estimation est le modèle autorisé
    du client le moins cher ; le rejeu (§8.4) tranchera ensuite.
    """
    candidats = candidats_cheapers(contexte)
    if not candidats:
        return 0.0
    cout_avant = cout_actuel_par_requete(contexte)
    niveau = niveau_dominant(contexte.profil)
    cout_apres = cout_requete_modele(contexte, candidats[0], niveau)
    if cout_avant <= 0 or cout_apres <= 0:
        return 0.0
    part = part_type_tache(contexte.profil, TACHES_SIMPLES)
    return economie_mensuelle(cout_avant, cout_apres, round(volume_mensuel(contexte) * part))


def estimer_cache_prompts(contexte: ContexteLevier) -> float:
    """Économie mensuelle de l'activation du cache de prompts (EF-LEV-02).

    Formule, par requête portant le préfixe commun (P jetons, R réutilisations) :
        coût_sans_cache = P × prix_entrée / 1000
        coût_avec_cache = (P / R) × prix_écriture_cache / 1000
                        + P × (1 − 1/R) × prix_lecture_cache / 1000
    Puis, sur le mois :
        économie = part_préfixe × (coût_sans_cache − coût_avec_cache) × volume

    Hypothèse : le préfixe est écrit une fois puis relu ; R est le nombre moyen
    de requêtes partageant une même entrée de cache.
    """
    profil = contexte.profil
    tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
    if tarif is None:
        return 0.0
    prefixe = float(profil.longueur_prefixe_long)
    reutilisation = max(1.0, profil.factor_reutilisation_prefixe)
    part = profil.part_prefixe_long
    if prefixe <= 0 or part <= 0:
        return 0.0
    cout_sans = prefixe * tarif.prix_entree_1k / 1000.0
    ecritures = prefixe / reutilisation
    lectures = prefixe * (1.0 - 1.0 / reutilisation)
    cout_avec = (
        ecritures * tarif.prix_cache_ecriture_1k + lectures * tarif.prix_cache_lecture_1k
    ) / 1000.0
    gain = part * (cout_sans - cout_avec)
    return economie_mensuelle(gain, 0.0, volume_mensuel(contexte))


def estimer_traitement_differable(contexte: ContexteLevier) -> float:
    """Économie mensuelle du passage au niveau Flex (EF-LEV-03).

    Formule :
        part_déférable = part du trafic non interactif situé en heures différables
        coût_Standard  = jetons_entrée × prix_entrée(Standard) / 1000
                       + jetons_sortie × prix_sortie(Standard) / 1000
        coût_Flex      = mêmes jetons aux prix Flex
        remise         = 1 − coût_Flex / coût_Standard
        économie       = part_déférable × volume × coût_Standard × remise

    soit, de façon équivalente : (coût_Standard − coût_Flex) × volume_déférable.
    """
    tarif_standard = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
    tarif_flex = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.FLEX)
    if tarif_standard is None or tarif_flex is None:
        return 0.0
    if tarif_flex.niveau != ServiceTier.FLEX:
        return 0.0  # aucun tarif Flex : aucune remise possible
    cout_standard = cout_requete_niveau(contexte, tarif_standard)
    cout_flex = cout_requete_niveau(contexte, tarif_flex)
    if cout_standard <= 0 or cout_flex <= 0 or cout_flex >= cout_standard:
        return 0.0
    volume = round(volume_mensuel(contexte) * part_trafic_deferrable(contexte.profil))
    return economie_mensuelle(cout_standard, cout_flex, volume)


def estimer_sorties_trop_longues(contexte: ContexteLevier) -> float:
    """Économie mensuelle d'une limite de sortie resserrée (EF-LEV-04).

    Formule :
        tokens_excédentaires = longueur_sortie_p95 − longueur_sortie_médiane
        gain_par_requête     = part_requêtes_longues × tokens_excédentaires
                               × prix_sortie / 1000
        économie             = gain_par_requête × volume_mensuel
    """
    profil = contexte.profil
    tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
    if tarif is None:
        return 0.0
    excedent = profil.longueur_sortie_p95 - profil.longueur_sortie_mediane
    if excedent <= 0:
        return 0.0
    gain = profil.part_requetes_sortie_longue * excedent * tarif.prix_sortie_1k / 1000.0
    return economie_mensuelle(gain, 0.0, volume_mensuel(contexte))


def estimer_requetes_repetees(contexte: ContexteLevier) -> float:
    """Économie mensuelle d'un cache applicatif (EF-LEV-05, requêtes répétées).

    Formule :
        gain_par_requête = part_requêtes_identiques × coût moyen par requête
        économie         = gain_par_requête × volume_mensuel
    """
    gain = contexte.profil.part_requetes_identiques * cout_par_requete(contexte)
    return economie_mensuelle(gain, 0.0, volume_mensuel(contexte))


def estimer_erreurs_et_relances(contexte: ContexteLevier) -> float:
    """Économie mensuelle de la correction d'une cause d'erreur (EF-LEV-05).

    Formule :
        relances_mensuelles = (relances observées / jours observés) × jours par mois
        économie = relances_mensuelles × coût moyen par requête

    Hypothèse : chaque relance est une requête en doublon dont le coût
    complet disparaît quand la cause est corrigée.
    """
    profil = contexte.profil
    jours = jours_observes(contexte)
    if jours <= 0 or profil.nombre_relances_apres_erreur <= 0:
        return 0.0
    relances = profil.nombre_relances_apres_erreur / jours * contexte.parametres.jours_par_mois
    return economie_mensuelle(cout_par_requete(contexte), 0.0, round(relances))


def estimer_priority_sans_besoin(contexte: ContexteLevier) -> float:
    """Économie mensuelle du retour au niveau Standard (EF-LEV-05).

    Formule :
        part_priority = part du trafic servie au niveau Priority
        part_eligible = part non interactive parmi ce trafic
        volume_éligible = volume_mensuel × part_priority × part_eligible
        économie = (coût_requête_Priority − coût_requête_Standard) × volume_éligible
    """
    profil = contexte.profil
    tarif_priority = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.PRIORITY)
    tarif_standard = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
    if tarif_priority is None or tarif_standard is None:
        return 0.0
    if tarif_priority.niveau != ServiceTier.PRIORITY:
        return 0.0  # aucun tarif Priority : rien à comparer
    cout_priority = cout_requete_niveau(contexte, tarif_priority)
    cout_standard = cout_requete_niveau(contexte, tarif_standard)
    if cout_priority <= cout_standard:
        return 0.0
    part = part_niveau(profil, ServiceTier.PRIORITY) * part_non_interactive_niveau(
        profil, ServiceTier.PRIORITY
    )
    volume = round(volume_mensuel(contexte) * part)
    return economie_mensuelle(cout_priority, cout_standard, volume)
