"""Règle « diète de jetons » — gaspillage de jetons d'entrée (piste n° 8 du top 10).

Objectif : détecter le gaspillage de **jetons d'entrée** (prompt système long
répété à l'identique, exemples peu-shot nombreux, consignes redondantes,
contexte injecté inutilisé) et ne le proposer qu'**avec preuve** : campagne de
rejeu sur des variantes de prompt (Annexe A, « preuve exigée »).

La règle ne lit que le profil agrégé et les agrégats : jamais le texte des
requêtes (ENF-CONF-01). Tous les chiffres sont calculés ici (ENF-IA-01).

Choix du type de levier : `TypeLevier` ne possède pas encore de valeur
`DIETE_JETONS` et le modèle est verrouillé pour cette PR. On utilise donc un
**repli documenté** — `SORTIES_TROP_LONGUES` — et un **marqueur local**
(`diete_jetons`) qui entre dans l'empreinte de l'identifiant du levier : deux
leviers de types différents n'ont jamais le même `id`. L'évolution du socle
 demandée (ajout de `TypeLevier.DIETE_JETONS` et enregistrement de la règle
dans `REGLES_DEFAUT`) est signalée dans le corps de la PR.

Complémentarité : le gaspillage des **sorties** relève déjà de la règle
EF-LEV-04 (`RegleSortiesTropLongues`) ; la diète de jetons s'attaque au
**prompt**, c'est-à-dire aux jetons d'entrée.
"""

from __future__ import annotations

from jauge.common.ids import deterministic
from jauge.engine.stats import economie_mensuelle
from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    formater_nombre,
    jetons_cache_lecture,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import tarif_pour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier, TarifModele

# --- Repli de type (en attendant l'évolution du socle) ---------------------
TYPE_REPLI = TypeLevier.SORTIES_TROP_LONGUES
MARQUEUR_LOCAL = "diete_jetons"

# --- Seuils de détection (locaux : `ParametresLevier` est verrouillé) -----
# Part des requêtes portant le même préfixe : « la quasi-totalité des appels ».
SEUIL_PART_PREFIXE = 0.70
# Longueur minimale du préfixe stable, en jetons.
SEUIL_LONGUEUR_PREFIXE = 1024
# Part minimale du préfixe dans les jetons d'entrée moyens.
SEUIL_PART_PREFIXE_DANS_ENTREE = 0.50

# Hypothèse de compression : part du préfixe que les variantes déterministes
# peuvent retirer (exemples redondants, consignes répétées, politesses,
# contexte inutile). Hypothèse explicite, à confirmer par le rejeu.
PART_COMPRESSIBLE = 0.35

ACTION_PROPOSEE = "Compresser le prompt (diète de jetons) par variantes déterministes"
PREUVE_EXIGEE = "Campagne de rejeu sur variantes de prompt"


def part_prefixe_dans_entree(contexte: ContexteLevier) -> float:
    """Part des jetons d'entrée moyens occupée par le préfixe stable (0 à 1)."""
    moyenne = contexte.profil.longueur_entree_moyenne
    if moyenne <= 0:
        return 0.0
    return min(1.0, contexte.profil.longueur_prefixe_long / moyenne)


def dispersion_entree(contexte: ContexteLevier) -> float:
    """Dispersion des entrées : 95e centile / moyenne (1.0 si tout est égal)."""
    moyenne = contexte.profil.longueur_entree_moyenne
    if moyenne <= 0:
        return 0.0
    return round(contexte.profil.longueur_entree_p95 / moyenne, 2)


def prix_applicable_prefixe(contexte: ContexteLevier, tarif: TarifModele) -> float:
    """Prix moyen d'un jeton du préfixe, selon l'activation du cache de prompts.

    Sans cache actif : prix d'entrée plein. Avec cache actif : une écriture
    pour R requêtes puis (R − 1) lectures, soit la moyenne pondérée
    (1/R) × écriture + (1 − 1/R) × lecture (Annexe D).
    """
    if jetons_cache_lecture(contexte) == 0:
        return tarif.prix_entree_1k
    reutilisation = max(1.0, contexte.profil.factor_reutilisation_prefixe)
    ecriture = tarif.prix_cache_ecriture_1k / reutilisation
    lecture = tarif.prix_cache_lecture_1k * (1.0 - 1.0 / reutilisation)
    return ecriture + lecture


def jetons_evites_par_requete(contexte: ContexteLevier) -> float:
    """Jetons d'entrée évités par requête sous l'hypothèse de compression."""
    profil = contexte.profil
    return profil.longueur_prefixe_long * PART_COMPRESSIBLE * profil.part_prefixe_long


def estimer_diet_jetons(contexte: ContexteLevier) -> float:
    """Économie mensuelle d'une compression du préfixe récurrent.

    Formule (explicite) :
        jetons évités / requête = longueur_prefixe_long × PART_COMPRESSIBLE
                                  × part_prefixe_long
        prix applicable          = prix d'entrée (cache inactif)
                                = (1/R) × prix écriture + (1 − 1/R) × prix lecture
                                  (cache actif, R = réutilisations du préfixe)
        gain / requête            = jetons évités × prix applicable / 1000
        économie / mois          = gain × volume mensuel

    La formule ne s'applique qu'aux requêtes portant le préfixe commun
    (`part_prefixe_long`) ; le reste du trafic est inchangé.
    """
    tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
    if tarif is None:
        return 0.0
    if contexte.profil.longueur_prefixe_long <= 0 or contexte.profil.part_prefixe_long <= 0:
        return 0.0
    prix = prix_applicable_prefixe(contexte, tarif)
    gain = jetons_evites_par_requete(contexte) * prix / 1000.0
    return economie_mensuelle(gain, 0.0, volume_mensuel(contexte))


class RegleDieteJetons:
    """Détecte un gaspillage de jetons d'entrée réparable par compression."""

    type_levier = TYPE_REPLI

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si le prompt gaspille des jetons d'entrée."""
        if not self._gaspillage_detecte(contexte):
            return None
        economie = estimer_diet_jetons(contexte)
        if economie <= 0:
            return None
        levier = construire_levier(
            contexte,
            self.type_levier,
            self._indices(contexte, economie),
            ACTION_PROPOSEE,
            PREUVE_EXIGEE,
            economie,
            self._confiance(contexte),
        )
        return levier.model_copy(update={"id": self._identifiant(contexte)})

    def _gaspillage_detecte(self, contexte: ContexteLevier) -> bool:
        """Le même préfixe long est répété sur la quasi-totalité des appels."""
        profil = contexte.profil
        if profil.part_prefixe_long < SEUIL_PART_PREFIXE:
            return False
        if profil.longueur_prefixe_long < SEUIL_LONGUEUR_PREFIXE:
            return False
        return part_prefixe_dans_entree(contexte) >= SEUIL_PART_PREFIXE_DANS_ENTREE

    def _identifiant(self, contexte: ContexteLevier) -> str:
        """Identifiant déterministe : le marqueur local évite toute collision."""
        empreinte = deterministic(
            contexte.tenant_id,
            contexte.application_id,
            MARQUEUR_LOCAL,
            contexte.parametres.date_reference.isoformat(),
        )
        return f"lv-{empreinte}"

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part du trafic portant le préfixe commun."""
        return min(0.9, 0.6 + 0.3 * contexte.profil.part_prefixe_long)

    def _indices(self, contexte: ContexteLevier, economie: float) -> list[str]:
        """Indices chiffrés et hypothèses retenues (Annexe A)."""
        profil = contexte.profil
        tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
        prix = f"entrée {tarif.prix_entree_1k} $/1k" if tarif else "tarif inconnu"
        return [
            f"Préfixe stable de {formater_nombre(profil.longueur_prefixe_long)} jetons "
            f"répété à l'identique sur {pourcent(profil.part_prefixe_long)} des requêtes "
            f"(seuil {pourcent(SEUIL_PART_PREFIXE)})",
            f"Le préfixe occupe {pourcent(part_prefixe_dans_entree(contexte))} des jetons "
            f"d'entrée moyens ({formater_nombre(profil.longueur_entree_moyenne)} jetons)",
            f"Dispersion des entrées : 95e centile "
            f"{formater_nombre(profil.longueur_entree_p95)} jetons pour une moyenne de "
            f"{formater_nombre(profil.longueur_entree_moyenne)} (ratio "
            f"{dispersion_entree(contexte):.2f})",
            f"Hypothèses : {pourcent(PART_COMPRESSIBLE)} du préfixe compressible, "
            f"{formater_nombre(volume_mensuel(contexte))} requêtes/mois, prix {prix}",
            f"Économie estimée : {formater_nombre(economie, 2)} $/mois — "
            f"preuve exigée : {PREUVE_EXIGEE.lower()}",
        ]
