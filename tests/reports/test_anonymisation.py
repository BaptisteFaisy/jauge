"""Tests de la k-anonymité et du bruit DP sur les agrégats publiés (piste n° 9).

Couverts : la politique par défaut (k=5, epsilon=1) et son plancher (k=2)
refusé, le seuil configurable par client et la journalisation de ses
modifications (EF-ADM-01/02), la suppression ou le regroupement « autres » des
cellules sous le seuil avec le compte rendu visible, le bruit de Laplace
reproductible à graine fixée et d'amplitude cohérente avec epsilon, l'epsilon
toujours rendu avec la valeur publiée, et la **preuve de non-fuite** : aucune
sortie publique (rapport, agrégats publiés, benchmark de parc) ne contient de
texte de requête, d'identifiant de requête non haché ni de cellule sous le
seuil k (ENF-CONF-01). Aucun réseau, aucun AWS.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from pydantic import ValidationError as ErreurPydantic

from jauge.common.errors import ValidationError
from jauge.models.campaign import EchantillonRequete, PaireRejoueeDataPlane
from jauge.models.cost import AgregatQuotidien
from jauge.models.tenant import Tenant
from jauge.reports.anonymisation import (
    EPSILON_DEFAUT,
    K_DEFAUT,
    K_PLANCHER,
    LIBELLE_AUTRES,
    CelluleAgregat,
    MemorySeuilsAnonymiteStore,
    PolitiqueAnonymite,
    anonymiser_cellules,
    bruit_laplace,
    cellules_par_client,
    cellules_par_dimension,
    compte_rendu_suppressions,
    echelle_laplace,
    graine_cellule,
    identifiant_publique,
    publier_agregats_client,
    publier_benchmark,
    publier_benchmark_parc,
)
from jauge.reports.builder import construire_rapport
from jauge.reports.storage import rendre_json, rendre_markdown
from jauge.store.memory import MemoryStore

GRAND_MODELE = "anthropic.claude-opus-4-20250514-v1:0"
PETIT_MODELE = "anthropic.claude-3-5-haiku-20241022-v1:0"
JOUR = date(2026, 10, 15)

#: Données du plan de données : elles ne doivent apparaître dans AUCUNE sortie.
TEXTE_REQUETE = "Prompt confidentiel : carte bancaire 4242 4242 4242 4242"
REPONSE_CANDIDATE = "Réponse candidate confidentielle du modèle"
REQUETE_ID_BRUT = "req-01j8zxsecret42"


def _cellule(cle: str, valeur: float, entites: int) -> CelluleAgregat:
    """Cellule d'agrégat minimale pour les tests."""
    return CelluleAgregat(cle=cle, valeur=valeur, entites=entites)


def _moments(valeurs: list[float]) -> tuple[float, float]:
    """Moyenne et écart-type d'une série (statistiques sans dépendance)."""
    n = len(valeurs)
    moyenne = sum(valeurs) / n
    variance = sum((valeur - moyenne) ** 2 for valeur in valeurs) / n
    return moyenne, math.sqrt(variance)


def _tenant(identifiant: str) -> Tenant:
    """Client fictif du parc de test."""
    return Tenant(
        id=identifiant,
        nom=f"Client {identifiant}",
        compte_aws="123456789012",
        role_arn=f"arn:aws:iam::123456789012:role/{identifiant}",
        external_id=f"ext-{identifiant}",
    )


def _agregat(tenant_id: str, modele: str, requetes: int, cout: float) -> AgregatQuotidien:
    """Agrégat quotidien d'octobre 2026 pour un client et un modèle."""
    return AgregatQuotidien(
        tenant_id=tenant_id,
        application_id=f"app-{tenant_id}",
        equipe="support",
        environnement="production",
        modele_id=modele,
        jour=JOUR,
        nombre_requetes=requetes,
        jetons_entree_total=requetes * 400,
        jetons_sortie_total=requetes * 120,
        cout_recalcule_usd=cout,
    )


@pytest.fixture
def store_parc() -> MemoryStore:
    """Parc de 6 clients : 5 sur le grand modèle, 1 seul sur le petit."""
    store = MemoryStore()
    for index in range(1, 6):
        identifiant = f"t-parc-{index}"
        store.enregistrer_tenant(_tenant(identifiant))
        store.enregistrer_agregats([_agregat(identifiant, GRAND_MODELE, 100 * index, 10.0 * index)])
    store.enregistrer_tenant(_tenant("t-solitaire"))
    store.enregistrer_agregats([_agregat("t-solitaire", PETIT_MODELE, 3, 0.42)])
    return store


def test_politique_par_defaut_documentee() -> None:
    """Défaut : k=5, epsilon=1.0, plancher k=2 rendu avec la politique."""
    politique = PolitiqueAnonymite()
    assert (politique.k, politique.epsilon, politique.plancher_k) == (
        K_DEFAUT,
        EPSILON_DEFAUT,
        K_PLANCHER,
    )


@pytest.mark.parametrize("k", [0, 1])
def test_plancher_k_refuse_partout(k: int) -> None:
    """Descendre sous le plancher est refusé : modèle, magasin et anonymisation."""
    with pytest.raises(ErreurPydantic):
        PolitiqueAnonymite(k=k)
    with pytest.raises(ValidationError):
        MemorySeuilsAnonymiteStore().definir_seuil("t-1", k)
    with pytest.raises(ValidationError):
        anonymiser_cellules([_cellule("a", 1.0, 10)], k=k)
    with pytest.raises(ValidationError):
        MemorySeuilsAnonymiteStore(k_defaut=k)


def test_seuil_configurable_par_client() -> None:
    """Chaque client a son seuil ; les autres gardent le défaut (EF-ADM-01)."""
    seuils = MemorySeuilsAnonymiteStore()
    seuils.definir_seuil("t-a", 7, auteur="u-admin")
    assert seuils.obtenir_politique("t-a").k == 7
    assert seuils.obtenir_politique("t-b").k == K_DEFAUT


def test_modification_du_seuil_journalisee() -> None:
    """Le paramètre et chacune de ses modifications sont journalisés."""
    seuils = MemorySeuilsAnonymiteStore()
    seuils.definir_seuil("t-a", 6, auteur="u-admin")
    seuils.definir_seuil("t-a", 9, epsilon=0.5, auteur="u-rssi")
    seuils.definir_seuil("t-b", 5, auteur="u-admin")
    journal_a = seuils.journal("t-a")
    assert [ligne.k for ligne in journal_a] == [6, 9]
    assert journal_a[-1].auteur == "u-rssi"
    assert journal_a[-1].epsilon == 0.5
    assert journal_a[-1].quand
    assert len(seuils.journal()) == 3


def test_cellule_unique_supprimee_avec_compte() -> None:
    """Stratégie `supprimer` : la cellule à 1 élément disparaît, le compte reste."""
    resultat = anonymiser_cellules(
        [_cellule("solitaire", 99.0, 1), _cellule("solide", 10.0, 40)],
        k=5,
        strategie="supprimer",
    )
    assert [cellule.cle for cellule in resultat.cellules] == ["solide"]
    assert resultat.nombre_supprimees == 1
    assert resultat.entites_supprimees == 1
    assert "1 cellule(s) supprimée(s)" in compte_rendu_suppressions(resultat)


def test_cellules_sous_seuil_regroupees_dans_autres() -> None:
    """Stratégie `regrouper` : les petites cellules fusionnent dans « autres »."""
    resultat = anonymiser_cellules(
        [
            _cellule("minuscule-1", 1.0, 1),
            _cellule("minuscule-2", 2.0, 2),
            _cellule("minuscule-3", 3.0, 3),
            _cellule("solide", 50.0, 100),
        ],
        k=5,
    )
    cles = {cellule.cle for cellule in resultat.cellules}
    assert cles == {"solide", LIBELLE_AUTRES}
    autres = next(c for c in resultat.cellules if c.cle == LIBELLE_AUTRES)
    assert autres.entites == 6
    assert autres.valeur == 6.0
    assert autres.regroupee is True
    assert resultat.nombre_regroupees == 3
    assert resultat.nombre_supprimees == 0
    assert "regroupée(s) dans « autres »" in compte_rendu_suppressions(resultat)


def test_autres_encore_sous_seuil_supprime() -> None:
    """Un « autres » dont l'effectif reste sous k est supprimé à son tour."""
    resultat = anonymiser_cellules([_cellule("a", 1.0, 1), _cellule("b", 2.0, 2)], k=5)
    assert resultat.cellules == ()
    assert resultat.nombre_supprimees == 2
    assert resultat.entites_supprimees == 3


def test_aucune_cellule_sous_k_nest_jamais_publiee() -> None:
    """Invariant : toute cellule publiée repose sur au moins k entités."""
    entrees = [_cellule(f"c{index}", float(index), index) for index in range(1, 12)]
    for strategie in ("supprimer", "regrouper"):
        resultat = anonymiser_cellules(entrees, k=5, strategie=strategie)
        assert all(cellule.entites >= 5 for cellule in resultat.cellules)


def test_strategie_inconnue_refusee() -> None:
    """Une stratégie hors contrat est refusée explicitement."""
    with pytest.raises(ValidationError):
        anonymiser_cellules([_cellule("a", 1.0, 10)], k=5, strategie="publier_quand_meme")


def test_bruit_reproductible_a_graine_fixee() -> None:
    """Graine fixée → même bruit, à l'unité près (testable sans réseau)."""
    premier = bruit_laplace(100.0, 1.0, 0.5, graine=42)
    second = bruit_laplace(100.0, 1.0, 0.5, graine=42)
    assert premier == second
    assert premier != 100.0  # la valeur publiée est bien bruitée


def test_bruit_non_correle_entre_cellules() -> None:
    """À graine globale fixée, chaque cellule reçoit un tirage distinct."""
    premier = bruit_laplace(10.0, 1.0, 1.0, graine=7)
    a = bruit_laplace(10.0, 1.0, 1.0, graine=graine_cellule(7, "modele-a"))
    b = bruit_laplace(10.0, 1.0, 1.0, graine=graine_cellule(7, "modele-b"))
    assert a != b
    assert premier != a


def test_amplitude_coherente_avec_epsilon() -> None:
    """Moyenne nulle et écart-type √2·Δ/ε : l'amplitude suit la calibration DP."""
    sensibilite, epsilon = 1.0, 0.5
    echelle = echelle_laplace(sensibilite, epsilon)
    assert echelle == sensibilite / epsilon
    tirages = [bruit_laplace(0.0, sensibilite, epsilon, graine=graine) for graine in range(20000)]
    moyenne, ecart_type = _moments(tirages)
    assert abs(moyenne) < 0.1
    assert abs(ecart_type - math.sqrt(2.0) * echelle) < 0.15


def test_un_epsilon_plus_petit_bruite_davantage() -> None:
    """Diviser epsilon double l'écart-type du bruit (calibration 1/ε)."""
    fins = [bruit_laplace(0.0, 1.0, 1.0, graine=g) for g in range(8000)]
    grossiers = [bruit_laplace(0.0, 1.0, 0.5, graine=g) for g in range(8000)]
    _, ecart_fin = _moments(fins)
    _, ecart_grossier = _moments(grossiers)
    assert 0.4 < ecart_fin / ecart_grossier < 0.6


@pytest.mark.parametrize(
    ("sensibilite", "epsilon"), [(1.0, 0.0), (1.0, -0.5), (0.0, 1.0), (-2.0, 1.0)]
)
def test_parametres_du_bruit_refuses(sensibilite: float, epsilon: float) -> None:
    """epsilon <= 0 ou sensibilité <= 0 : refus explicite, jamais de bruit nul."""
    with pytest.raises(ValidationError):
        bruit_laplace(10.0, sensibilite, epsilon, graine=1)


def test_benchmark_publie_epsilon_avec_chaque_valeur(store_parc: MemoryStore) -> None:
    """Un chiffre publié sans son epsilon n'a pas de sens : il est toujours rendu."""
    politique = PolitiqueAnonymite(k=5, epsilon=0.8)
    publication = publier_benchmark_parc(
        store_parc,
        [f"t-parc-{index}" for index in range(1, 6)] + ["t-solitaire"],
        politique,
        sensibilite=10.0,
        graine=2026,
    )
    assert publication.epsilon == 0.8
    assert publication.k_applique == 5
    assert publication.nombre_supprimees == 1  # le modèle du client solitaire
    assert len(publication.valeurs) == 1
    valeur = publication.valeurs[0]
    assert valeur.epsilon == 0.8
    assert valeur.sensibilite == 10.0
    assert valeur.entites == 5
    charge = json.dumps(publication.vers_dict())
    assert '"epsilon": 0.8' in charge
    assert '"nombre_supprimees": 1' in charge


def test_benchmark_reproductible_a_graine_fixee(store_parc: MemoryStore) -> None:
    """Même graine → même publication, exactement (reproductible en test)."""
    clients = [f"t-parc-{index}" for index in range(1, 6)]
    cellules = cellules_par_client(store_parc, clients)
    premier = publier_benchmark(cellules, k=5, epsilon=1.0, sensibilite=10.0, graine=99)
    second = publier_benchmark(cellules, k=5, epsilon=1.0, sensibilite=10.0, graine=99)
    assert premier.vers_dict() == second.vers_dict()


def test_cellules_inter_clients_comptent_les_contributeurs(store_parc: MemoryStore) -> None:
    """L'effectif d'une cellule inter-clients est le nombre de clients, pas de requêtes."""
    clients = [f"t-parc-{index}" for index in range(1, 6)] + ["t-solitaire"]
    cellules = cellules_par_client(store_parc, clients)
    par_cle = {cellule.cle: cellule for cellule in cellules}
    assert par_cle[GRAND_MODELE].entites == 5
    assert par_cle[PETIT_MODELE].entites == 1
    assert par_cle[GRAND_MODELE].valeur == 150.0


def test_cellules_client_comptent_les_requetes(store_parc: MemoryStore) -> None:
    """Pour un client, l'effectif d'une cellule est le nombre de requêtes."""
    cellules = cellules_par_dimension(store_parc, "t-parc-2", dimension="modele")
    assert cellules == [CelluleAgregat(cle=GRAND_MODELE, valeur=20.0, entites=200)]
    with pytest.raises(ValidationError):
        cellules_par_dimension(store_parc, "t-parc-2", dimension="inconnue")


def test_cellules_par_jour(store_parc: MemoryStore) -> None:
    """Le niveau « jour » est disponible : la clé est la date compacte."""
    cellules = cellules_par_dimension(store_parc, "t-parc-3", dimension="jour")
    assert cellules == [CelluleAgregat(cle="2026-10-15", valeur=30.0, entites=300)]


def test_identifiant_de_requete_hache() -> None:
    """Un identifiant publié passe par son empreinte, jamais en clair."""
    empreinte = identifiant_publique(REQUETE_ID_BRUT)
    assert empreinte != REQUETE_ID_BRUT
    assert REQUETE_ID_BRUT not in empreinte
    assert empreinte == identifiant_publique(REQUETE_ID_BRUT)
    assert len(empreinte) == 16


def test_preuve_de_non_fuite_des_sorties_publiques(store_parc: MemoryStore) -> None:
    """Aucune sortie publique ne transporte de texte, d'identifiant brut ni de
    cellule sous le seuil k (ENF-CONF-01)."""
    paire = PaireRejoueeDataPlane(
        campagne_id="c-1",
        requete_id=REQUETE_ID_BRUT,
        modele_candidat_id=PETIT_MODELE,
        texte_requete=TEXTE_REQUETE,
        reponse_reference="Réponse de référence",
        reponse_candidate=REPONSE_CANDIDATE,
        score_equivalence=0.9,
    )
    echantillon = EchantillonRequete(
        requete_id=REQUETE_ID_BRUT,
        application_id="app-t-parc-1",
        longueur_caracteres=len(paire.texte_requete),
        jetons_entree_origine=120,
    )
    sortie_rapport = construire_rapport(store_parc, "t-parc-1", 2026, 10)
    agregats = publier_agregats_client(store_parc, "t-parc-1", k=5)
    clients = [f"t-parc-{index}" for index in range(1, 6)] + ["t-solitaire"]
    benchmark = publier_benchmark_parc(
        store_parc, clients, PolitiqueAnonymite(k=5, epsilon=1.0), sensibilite=10.0, graine=5
    )
    sorties = {
        "rapport_json": rendre_json(sortie_rapport),
        "rapport_markdown": rendre_markdown(sortie_rapport),
        "agregats_client": json.dumps(
            [cellule.__dict__ for cellule in agregats.cellules], default=str
        ),
        "benchmark_parc": json.dumps(benchmark.vers_dict()),
    }
    for nom, contenu in sorties.items():
        assert TEXTE_REQUETE not in contenu, nom
        assert REPONSE_CANDIDATE not in contenu, nom
        assert REQUETE_ID_BRUT not in contenu, nom
        assert "texte_requete" not in contenu, nom
        assert "reponse_candidate" not in contenu, nom
        assert echantillon.requete_id not in contenu, nom
    assert all(cellule.entites >= 5 for cellule in agregats.cellules)
    assert all(valeur.entites >= 5 for valeur in benchmark.valeurs)
    assert "t-solitaire" not in sorties["benchmark_parc"]
    assert "Client t-solitaire" not in sorties["benchmark_parc"]
