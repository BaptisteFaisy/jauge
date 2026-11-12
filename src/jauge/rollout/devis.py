"""Devis d'un shadow : bornes de l'échantillon, coût chiffré, plafond.

Le shadow a un prix, et ce prix se connaît **avant** le lancement : on/sample
le trafic réel, on chiffre les appels du candidat et les jugements des paires,
et on refuse le lancement si le devis dépasse le plafond du client
(ENF-COUT-01). C'est le même schéma que le devis d'une campagne de rejeu, mais
pour du trafic réel.

ENF-IA-01 : les jetons du candidat sont les moyennes **observées** sur le
trafic échantillonné, les jetons du juge sont les hypothèses fournies par
l'appelant ; aucun chiffre n'est deviné, aucun ne vient d'un modèle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from jauge.common.errors import PlafondDepasseError, ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele

# Le shadow s'exécute sur le chemin de production : ses appels sont tarifés comme
# le trafic réel (niveau Standard), et non au niveau Flex du rejeu (§17.1).
NIVEAU_SHADOW = ServiceTier.STANDARD
# Le juge tourne au niveau Standard, comme dans le devis d'une campagne de rejeu.
NIVEAU_JUGE = ServiceTier.STANDARD


@dataclass(frozen=True)
class BornesEchantillon:
    """Bornes de l'échantillon : taille maximale et part du trafic observé.

    Les deux bornes s'appliquent ensemble : on tire au plus `taille_max`
    requêtes, et au plus `taux` du trafic observé. Le plafond de coût
    (ENF-COUT-01) reste la borne décisive : il est vérifié après le tirage,
    avant tout appel de modèle.
    """

    taille_max: int
    taux: float | None = None

    def __post_init__(self) -> None:
        if self.taille_max <= 0:
            raise ValidationError(
                "La taille maximale de l'échantillon doit être strictement positive.",
                taille_max=self.taille_max,
            )
        if self.taux is not None and not 0.0 < self.taux <= 1.0:
            raise ValidationError(
                "La part du trafic observé doit être dans ]0, 1].", taux=self.taux
            )

    def taille_pour(self, volume_observe: int) -> int:
        """Nombre de requêtes à tirer pour ce volume de trafic observé."""
        if volume_observe <= 0:
            return 0
        par_taux = volume_observe if self.taux is None else math.ceil(volume_observe * self.taux)
        return max(1, min(self.taille_max, par_taux, volume_observe))


@dataclass(frozen=True)
class EstimationShadow:
    """Devis du shadow, chiffré **avant** le lancement (ENF-COUT-01)."""

    nombre_requetes: int
    cout_candidat_usd: float
    cout_judge_usd: float
    cout_total_usd: float
    plafond_usd: float
    sous_plafond: bool

    def marge_usd(self) -> float:
        """Reste disponible sous le plafond."""
        return round(max(0.0, self.plafond_usd - self.cout_total_usd), 8)


def cout_unitaire(tarif: TarifModele, jetons_entree: int, jetons_sortie: int) -> float:
    """Coût d'une requête de jetons donnés, au tarif fourni (Annexe D)."""
    metriques = MetriquesRequete(jetons_entree=jetons_entree, jetons_sortie=jetons_sortie)
    return calculer_cout_requete(tarif, metriques).cout_total


def estimer_cout_shadow(
    *,
    tarifs: TableTarifs,
    region: str,
    a_la_date: date,
    modele_candidat: str,
    modele_judge: str,
    nombre_requetes: int,
    jetons_entree_moyens: float,
    jetons_sortie_moyens: float,
    jetons_entree_juge: int,
    jetons_sortie_juge: int,
    plafond_usd: float,
    niveau: ServiceTier = NIVEAU_SHADOW,
) -> EstimationShadow:
    """Chiffre le coût d'un shadow : appels du candidat + jugement des paires.

    Les jetons du candidat sont les **moyennes observées** sur le trafic réel
    échantillonné ; ceux du juge sont les hypothèses fournies par l'appelant.
    """
    if nombre_requetes <= 0:
        raise ValidationError("Un shadow sans requête n'a aucun coût à chiffrer.")
    if plafond_usd < 0:
        raise ValidationError("Le plafond du shadow ne peut pas être négatif.")
    tarif_candidat = tarifs.trouver(modele_candidat, region, niveau, a_la_date)
    tarif_juge = tarifs.trouver(modele_judge, region, NIVEAU_JUGE, a_la_date)
    cout_candidat = cout_unitaire(
        tarif_candidat, int(jetons_entree_moyens), int(jetons_sortie_moyens)
    )
    cout_judge = cout_unitaire(tarif_juge, jetons_entree_juge, jetons_sortie_juge)
    total = round(nombre_requetes * (cout_candidat + cout_judge), 8)
    return EstimationShadow(
        nombre_requetes=nombre_requetes,
        cout_candidat_usd=round(nombre_requetes * cout_candidat, 8),
        cout_judge_usd=round(nombre_requetes * cout_judge, 8),
        cout_total_usd=total,
        plafond_usd=plafond_usd,
        sous_plafond=total <= plafond_usd,
    )


def verifier_plafond(estimation: EstimationShadow, plafond_usd: float) -> EstimationShadow:
    """Refuse le shadow si le devis dépasse le plafond (ENF-COUT-01)."""
    if estimation.cout_total_usd > plafond_usd:
        raise PlafondDepasseError(
            "Le coût estimé du shadow dépasse le plafond du client.",
            cout_estime_usd=estimation.cout_total_usd,
            plafond_usd=plafond_usd,
            depassement_usd=round(estimation.cout_total_usd - plafond_usd, 8),
        )
    return estimation
