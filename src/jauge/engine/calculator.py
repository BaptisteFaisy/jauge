"""Calcul déterministe du coût d'une requête (Annexe D, EF-ATT-04).

Règle absolue (ENF-IA-01) : AUCUN coût n'est estimé par un LLM. La formule
décompose entrée, sortie et cache conformément à la tarification Bedrock.
"""

from __future__ import annotations

from datetime import date

from jauge.common.errors import TarifInconnuError
from jauge.models.pricing import CoutRequete, MetriquesRequete, ServiceTier, TarifModele


class TableTarifs:
    """Table des tarifs Bedrock en mémoire, datée et consultable par clé."""

    def __init__(self, tarifs: list[TarifModele] | None = None) -> None:
        self._tarifs: list[TarifModele] = list(tarifs or [])

    def ajouter(self, tarif: TarifModele) -> None:
        self._tarifs.append(tarif)

    def trouver(
        self, modele_id: str, region: str, niveau: ServiceTier, a_la_date: date
    ) -> TarifModele:
        """Trouve le tarif applicable (le plus récent antérieur ou égal à la date)."""
        candidats = [
            t
            for t in self._tarifs
            if t.modele_id == modele_id
            and t.region == region
            and t.niveau == niveau
            and t.date_effet <= a_la_date
        ]
        if not candidats and niveau != ServiceTier.STANDARD:
            # Repli sur le niveau Standard si Flex/Priority n'a pas de ligne propre
            candidats = [
                t
                for t in self._tarifs
                if t.modele_id == modele_id
                and t.region == region
                and t.niveau == ServiceTier.STANDARD
                and t.date_effet <= a_la_date
            ]
        if not candidats:
            raise TarifInconnuError(
                f"Tarif introuvable pour {modele_id} en {region} ({niveau.value}) au {a_la_date}",
                modele=modele_id,
                region=region,
                niveau=niveau.value,
                date=str(a_la_date),
            )
        # Le plus récent
        candidats.sort(key=lambda t: t.date_effet, reverse=True)
        return candidats[0]


def calculer_cout_requete(tarif: TarifModele, metriques: MetriquesRequete) -> CoutRequete:
    """Calcule le coût exact en USD à partir des tarifs pour 1 000 jetons.

    Formule Annexe D :
        coût = entrée × prix_entrée + sortie × prix_sortie
             + écriture_cache × prix_cache_écriture + lecture_cache × prix_cache_lecture
    """
    cout_in = (metriques.jetons_entree / 1000.0) * tarif.prix_entree_1k
    cout_out = (metriques.jetons_sortie / 1000.0) * tarif.prix_sortie_1k
    cout_cw = (metriques.jetons_cache_ecriture / 1000.0) * tarif.prix_cache_ecriture_1k
    cout_cr = (metriques.jetons_cache_lecture / 1000.0) * tarif.prix_cache_lecture_1k
    total = cout_in + cout_out + cout_cw + cout_cr
    return CoutRequete(
        cout_entree=round(cout_in, 8),
        cout_sortie=round(cout_out, 8),
        cout_cache_ecriture=round(cout_cw, 8),
        cout_cache_lecture=round(cout_cr, 8),
        cout_total=round(total, 8),
        devise=tarif.devise,
    )
