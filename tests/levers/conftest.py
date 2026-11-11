"""Fixtures du module leviers (M3).

Le socle fournit les fixtures communes (`tenant`, `tarifs`) ; ce fichier
complète avec les agrégats, le profil et le contexte d'analyse d'une
application de démonstration.

Repère de calcul (tarifs du socle, grand modèle, eu-west-1, Standard) :
    entrée  0,015 $/1k jetons → 200 jetons = 0,003000 $
    sortie  0,075 $/1k jetons →  50 jetons = 0,003750 $
    coût d'une requête                                     = 0,006750 $
    100 requêtes × 30 jours = 3 000 requêtes/mois → 20,25 $/mois
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from tests.conftest import GRAND_MODELE, REGION

from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.levers.base import ContexteLevier, ParametresLevier, ProfilRequetes
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele

JOUR_DEBUT = date(2026, 10, 1)
NB_JOURS = 30
REQUETES_PAR_JOUR = 100
ENTREE_PAR_REQUETE = 200
SORTIE_PAR_REQUETE = 50
VOLUME_MENSUEL = REQUETES_PAR_JOUR * NB_JOURS

# Trafic horaire du scénario « nuit » : 2 700 requêtes entre 0 h et 5 h,
# 240 réparties en journée (8 h – 23 h), 60 en fin de soirée.
HEURES_NUIT = (450, 450, 450, 450, 450, 450) + (15,) * 16 + (30, 30)
HEURES_NUIT_NON_INTERACTIVES = (450,) * 6 + (0,) * 18

# Trafic horaire du scénario « journée » : 250 requêtes de 8 h à 19 h.
HEURES_JOUR = (0,) * 8 + (250,) * 12 + (0,) * 4


@pytest.fixture
def table_tarifs(tarifs: list[TarifModele]) -> TableTarifs:
    """Table des tarifs du socle complétée par une ligne Priority sur le grand modèle.

    Priority est facturé le double du Standard : c'est ce différentiel que la
    règle EF-LEV-05 doit transformer en économie estimée.
    """
    lignes = list(tarifs)
    lignes.append(
        TarifModele(
            modele_id=GRAND_MODELE,
            region=REGION,
            niveau=ServiceTier.PRIORITY,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.03,
            prix_sortie_1k=0.15,
        )
    )
    return TableTarifs(lignes)


@pytest.fixture
def cout_requete(table_tarifs: TableTarifs) -> float:
    """Coût d'une requête de référence (200 jetons d'entrée, 50 de sortie)."""
    tarif = table_tarifs.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, JOUR_DEBUT)
    metriques = MetriquesRequete(jetons_entree=ENTREE_PAR_REQUETE, jetons_sortie=SORTIE_PAR_REQUETE)
    return calculer_cout_requete(tarif, metriques).cout_total


@pytest.fixture
def agregats_30j(cout_requete: float) -> tuple[AgregatQuotidien, ...]:
    """Trente agrégats quotidiens cohérents : 100 requêtes, 200/50 jetons."""
    return tuple(
        AgregatQuotidien(
            tenant_id="t-demo-a",
            application_id="ticket-classifier",
            equipe="support",
            environnement="production",
            modele_id=GRAND_MODELE,
            jour=JOUR_DEBUT + timedelta(days=index),
            nombre_requetes=REQUETES_PAR_JOUR,
            jetons_entree_total=REQUETES_PAR_JOUR * ENTREE_PAR_REQUETE,
            jetons_sortie_total=REQUETES_PAR_JOUR * SORTIE_PAR_REQUETE,
            cout_recalcule_usd=round(cout_requete * REQUETES_PAR_JOUR, 8),
        )
        for index in range(NB_JOURS)
    )


@pytest.fixture
def profil_neutre() -> ProfilRequetes:
    """Profil sans aucun signal : aucune règle ne doit se déclencher."""
    return ProfilRequetes(
        longueur_entree_moyenne=ENTREE_PAR_REQUETE,
        longueur_entree_p95=260,
        longueur_sortie_moyenne=SORTIE_PAR_REQUETE,
        longueur_sortie_mediane=45,
        longueur_sortie_p95=60,
        part_requetes_sortie_longue=0.0,
        limite_sortie_modele=4096,
        requetes_par_type_tache={"generation": VOLUME_MENSUEL},
        part_sortie_structuree=0.0,
        diversite_sorties=1.0,
        part_prefixe_long=0.0,
        longueur_prefixe_long=0,
        factor_reutilisation_prefixe=1.0,
        requetes_par_heure=HEURES_JOUR,
        requetes_non_interactive_par_heure=(0,) * 24,
        nombre_erreurs=0,
        nombre_relances_apres_erreur=0,
        requetes_par_niveau={ServiceTier.STANDARD.value: VOLUME_MENSUEL},
        requetes_non_interactive_par_niveau={},
        part_requetes_identiques=0.0,
    )


@pytest.fixture
def contexte(
    tenant,
    agregats_30j: tuple[AgregatQuotidien, ...],
    profil_neutre: ProfilRequetes,
    table_tarifs: TableTarifs,
) -> ContexteLevier:
    """Contexte d'analyse de l'application de démonstration « ticket-classifier »."""
    return ContexteLevier(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        modele_actuel=GRAND_MODELE,
        profil=profil_neutre,
        parametres=ParametresLevier(
            date_reference=JOUR_DEBUT,
            region=REGION,
            volume_mensuel_requetes=VOLUME_MENSUEL,
        ),
        agregats=agregats_30j,
        modeles_autorises=tuple(tenant.modeles_autorises),
        table_tarifs=table_tarifs,
    )


@pytest.fixture
def fabriquer_contexte(contexte: ContexteLevier):
    """Fabrique un contexte dérivé : champs de profil et agrégats modifiables."""

    def _fabriquer(profil: dict[str, object] | None = None, **champs) -> ContexteLevier:
        nouveau_profil = replace(contexte.profil, **(profil or {}))
        return replace(contexte, profil=nouveau_profil, **champs)

    return _fabriquer
