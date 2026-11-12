"""Tests de l'impact économique d'un changement de prix (piste n° 10).

Trois propriétés sont vérifiées :

- une variation sur un modèle que le client n'utilise pas n'a **aucun** impact ;
- l'impact est chiffré à volume constant, avec la formule du socle (Annexe D) et
  le volume mensuel extrapolé des agrégats observés (`jauge.levers.base`) ;
- les preuves à rejouer couvrent les trois règles annoncées (refus économique,
  économie à recalculer, campagne au prix caduc) — **sans rien réécrire** dans le
  store : la décision de rejouer reste humaine (ENF-IA-02).

Aucun réseau, aucun AWS : store en mémoire et table de tarifs fictive.
"""

from __future__ import annotations

from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION
from tests.pricing_data.conftest import (
    CAMPAGNE_ID,
    DATE_BAISSE,
    DATE_INITIALE,
    PRIX_APRES,
    PRIX_INITIAUX,
    PRIX_MOYEN,
    RECO_EN_ATTENTE,
    RECO_REFUSEE,
)

from jauge.engine.stats import baisse_pourcentage
from jauge.models.pricing import ServiceTier
from jauge.models.tenant import Tenant
from jauge.pricing_data.catalogue import CatalogueTarifs
from jauge.pricing_data.impact_prix import (
    JOURS_PAR_MOIS,
    impact_variation,
    preuves_a_rejouer,
)
from jauge.pricing_data.surveillance import (
    PrixParType,
    VariationPrix,
    variations_entre_dates,
)
from jauge.store.memory import MemoryStore


def variation(modele_id: str, avant: tuple[float, float], apres: tuple[float, float]):
    """Construit une variation de prix (les % suivent la convention du socle)."""
    return VariationPrix(
        modele_id=modele_id,
        region=REGION,
        niveau=ServiceTier.STANDARD,
        date_effet=DATE_BAISSE,
        date_effet_avant=DATE_INITIALE,
        prix_avant=PrixParType(entree=avant[0], sortie=avant[1]),
        prix_apres=PrixParType(entree=apres[0], sortie=apres[1]),
        variation_entree_pourcentage=baisse_pourcentage(avant[0], apres[0]),
        variation_sortie_pourcentage=baisse_pourcentage(avant[1], apres[1]),
        variation_cache_ecriture_pourcentage=0.0,
        variation_cache_lecture_pourcentage=0.0,
        sens="baisse" if apres[0] < avant[0] else "hausse",
    )


def variations_du_catalogue(catalogue: CatalogueTarifs) -> list[VariationPrix]:
    """Les deux variations de la table de test (baisse petit, hausse grand)."""
    return variations_entre_dates(catalogue, DATE_INITIALE, DATE_BAISSE)


def test_aucun_impact_sur_un_modele_non_utilise(
    store: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Une baisse sur un modèle que le client n'utilise pas ne vaut rien."""
    baisse_moyen = variation(MOYEN_MODELE, PRIX_MOYEN, (0.002, 0.010))

    assert impact_variation(store, tenant.id, baisse_moyen) is None


def test_impact_d_une_baisse_a_volume_constant(
    store: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Baisse de 25 % : coût par requête, économie mensuelle et surfaces chiffrés."""
    baisse = variation(PETIT_MODELE, PRIX_INITIAUX[PETIT_MODELE], PRIX_APRES[PETIT_MODELE])

    impact = impact_variation(store, tenant.id, baisse)

    assert impact is not None
    # 1 000 requêtes observées sur une journée, extrapolées sur 30 jours.
    assert impact.volume_mensuel_requetes == 1_000 * JOURS_PAR_MOIS
    assert impact.cout_par_requete_avant_usd == 0.00072
    assert impact.cout_par_requete_apres_usd == 0.00054
    assert impact.economie_mensuelle_usd == 5.4
    assert impact.baisse_pourcentage == 25.0
    assert impact.surface_mensuelle_avant_usd == 21.6
    assert impact.surface_mensuelle_apres_usd == 16.2
    assert impact.date_effet == DATE_BAISSE and impact.tenant_id == tenant.id


def test_impact_d_une_hausse_est_une_economie_negative(store: MemoryStore, tenant: Tenant) -> None:
    """Hausse de 20 % : l'économie est négative, et le signe est explicite."""
    hausse = variation(GRAND_MODELE, PRIX_INITIAUX[GRAND_MODELE], PRIX_APRES[GRAND_MODELE])

    impact = impact_variation(store, tenant.id, hausse)

    assert impact is not None
    assert impact.cout_par_requete_apres_usd > impact.cout_par_requete_avant_usd
    assert impact.economie_mensuelle_usd == -40.5
    assert impact.baisse_pourcentage == -20.0


def test_preuves_a_rejouer_couvrent_les_trois_regles(
    store: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Refus économique rouvert, économie à recalculer, campagne au prix caduc."""
    preuves = preuves_a_rejouer(store, tenant.id, variations_du_catalogue(catalogue))

    assert len(preuves) == 4
    cles = {(p.kind, p.identifiant, p.modele_id) for p in preuves}
    assert cles == {
        # Règle 1 : refus pour motif économique, le prix du modèle cible baisse…
        ("recommandation", RECO_REFUSEE, PETIT_MODELE),
        # …et le prix du modèle actuel monte, donc l'écart se creuse.
        ("recommandation", RECO_REFUSEE, GRAND_MODELE),
        # Règle 2 : recommandation en attente, son modèle courant a changé de prix.
        ("recommandation", RECO_EN_ATTENTE, GRAND_MODELE),
        # Règle 3 : campagne dont le candidat est mesuré sur un prix caduc.
        ("campagne", CAMPAGNE_ID, PETIT_MODELE),
    }
    assert all(p.date_effet == DATE_BAISSE for p in preuves)
    assert all(p.motif for p in preuves)
    assert all(p.application_id for p in preuves)


def test_variations_portees_par_chaque_preuve(
    store: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Chaque preuve porte la variation qui la motive, dans la convention du socle."""
    preuves = preuves_a_rejouer(store, tenant.id, variations_du_catalogue(catalogue))
    par_cible = {(p.identifiant, p.modele_id): p.variation_pourcentage for p in preuves}

    assert par_cible[(RECO_REFUSEE, PETIT_MODELE)] == 25.0  # baisse
    assert par_cible[(RECO_REFUSEE, GRAND_MODELE)] == -20.0  # hausse


def test_refus_pour_qualite_insuffisante_ne_rouvre_rien(
    store_qualite_insuffisante: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Un candidat recalé pour **qualité** ne redevient pas pertinent si le prix baisse."""
    preuves = preuves_a_rejouer(
        store_qualite_insuffisante, tenant.id, variations_du_catalogue(catalogue)
    )

    # Aucune recommandation n'est rouverte : le refus n'était pas économique.
    assert [p for p in preuves if p.kind == "recommandation"] == []
    # La campagne reste signalée : son coût par requête est mesuré sur un prix caduc.
    assert [(p.kind, p.identifiant) for p in preuves] == [("campagne", CAMPAGNE_ID)]


def test_rien_a_rejouer_sans_variation(store: MemoryStore, tenant) -> None:
    """Sans changement de prix, aucune preuve n'est signalée."""
    assert preuves_a_rejouer(store, tenant.id, []) == []


def test_les_preuves_ne_reecrivent_rien(
    store: MemoryStore, tenant: Tenant, catalogue: CatalogueTarifs
) -> None:
    """Signaler n'est pas décider : le store est inchangé après le calcul (ENF-IA-02)."""
    avant = [
        (r.id, r.status)
        for r in sorted(store.lister_recommandations(tenant.id), key=lambda r: r.id)
    ]
    campagne_avant = store.obtenir_campagne(tenant.id, CAMPAGNE_ID)

    preuves_a_rejouer(store, tenant.id, variations_du_catalogue(catalogue))

    apres = [
        (r.id, r.status)
        for r in sorted(store.lister_recommandations(tenant.id), key=lambda r: r.id)
    ]
    assert apres == avant
    assert store.obtenir_campagne(tenant.id, CAMPAGNE_ID).statut == campagne_avant.statut
