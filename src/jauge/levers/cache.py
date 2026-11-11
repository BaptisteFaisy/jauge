"""Règle EF-LEV-02 — cache de prompts absent (Annexe A).

Indices de l'Annexe A : « Même préfixe long sur une grande part des requêtes,
aucun jeton lu en cache ». Action proposée : « Activer le cache de prompts ».
Preuve exigée : « Calcul sur les jetons ».

Le préfixe commun n'est connu que par son empreinte et sa longueur en jetons :
son texte ne quitte jamais le plan de données (ENF-CONF-01).
"""

from __future__ import annotations

from jauge.engine.stats import baisse_pourcentage
from jauge.levers.base import (
    ContexteLevier,
    construire_levier,
    formater_nombre,
    jetons_cache_lecture,
    pourcent,
    volume_mensuel,
)
from jauge.levers.estimate import estimer_cache_prompts, tarif_pour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.pricing import ServiceTier

ACTION_PROPOSEE = "Activer le cache de prompts"
PREUVE_EXIGEE = "Calcul sur les jetons"


class RegleCachePromptsAbsent:
    """Détecte un préfixe long et répété jamais servi depuis le cache."""

    type_levier = TypeLevier.CACHE_PROMPTS_ABSENT

    def detecter(self, contexte: ContexteLevier) -> Levier | None:
        """Renvoie le levier si le préfixe est long, répété et jamais mis en cache."""
        if not self._prefixe_long_repete(contexte):
            return None
        if jetons_cache_lecture(contexte) > 0:
            return None  # le cache est déjà actif : pas de levier
        economie = estimer_cache_prompts(contexte)
        if economie <= 0:
            return None
        return construire_levier(
            contexte,
            self.type_levier,
            self._indices(contexte),
            ACTION_PROPOSEE,
            PREUVE_EXIGEE,
            economie,
            self._confiance(contexte),
        )

    def _prefixe_long_repete(self, contexte: ContexteLevier) -> bool:
        """Le même préfixe long couvre une grande part des requêtes."""
        profil = contexte.profil
        parametres = contexte.parametres
        if profil.part_prefixe_long < parametres.seuil_part_prefixe_long:
            return False
        return profil.longueur_prefixe_long >= parametres.seuil_longueur_prefixe_long

    def _indices(self, contexte: ContexteLevier) -> list[str]:
        """Indices et hypothèses retenues pour le levier (Annexe A)."""
        profil = contexte.profil
        tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
        prix = (
            f"entrée {tarif.prix_entree_1k} $/1k, écriture {tarif.prix_cache_ecriture_1k} "
            f"$/1k, lecture {tarif.prix_cache_lecture_1k} $/1k"
            if tarif
            else "tarif inconnu"
        )
        cout_prefixe = (
            profil.longueur_prefixe_long * tarif.prix_entree_1k / 1000.0 if tarif else 0.0
        )
        return [
            f"Préfixe commun de {formater_nombre(profil.longueur_prefixe_long)} jetons sur "
            f"{pourcent(profil.part_prefixe_long)} des requêtes "
            f"(seuil {formater_nombre(contexte.parametres.seuil_longueur_prefixe_long)} jetons)",
            f"Aucun jeton lu en cache sur la période observée "
            f"({formater_nombre(jetons_cache_lecture(contexte))} jetons)",
            f"Réutilisation moyenne : {formater_nombre(profil.factor_reutilisation_prefixe, 1)} "
            f"requêtes par entrée de cache (1 écriture, "
            f"{formater_nombre(profil.factor_reutilisation_prefixe - 1, 1)} lectures)",
            f"Hypothèse : {formater_nombre(volume_mensuel(contexte))} requêtes/mois, "
            f"préfixe à {cout_prefixe:.6f} $/requête sans cache, prix {prix}",
            f"Baisse potentielle sur le préfixe mis en cache : "
            f"{self._baisse_potentielle(contexte):.2f} %",
        ]

    def _confiance(self, contexte: ContexteLevier) -> float:
        """Confiance d'après la part du trafic portant le préfixe commun."""
        return min(0.95, 0.7 + 0.2 * contexte.profil.part_prefixe_long)

    def _baisse_potentielle(self, contexte: ContexteLevier) -> float:
        """Baisse du coût du préfixe une fois le cache actif (indicateur du §4)."""
        tarif = tarif_pour(contexte, contexte.modele_actuel, ServiceTier.STANDARD)
        if tarif is None:
            return 0.0
        profil = contexte.profil
        avant = profil.longueur_prefixe_long * tarif.prix_entree_1k / 1000.0
        reutilisation = max(1.0, profil.factor_reutilisation_prefixe)
        apres = (
            profil.longueur_prefixe_long / reutilisation * tarif.prix_cache_ecriture_1k
            + profil.longueur_prefixe_long
            * (1.0 - 1.0 / reutilisation)
            * tarif.prix_cache_lecture_1k
        ) / 1000.0
        return baisse_pourcentage(avant, apres)
