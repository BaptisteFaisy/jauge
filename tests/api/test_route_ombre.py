"""Tests de la route d'observation fantôme puis canari (`/ombre`, `/canari`).

Ce que la route promet :

- le shadow démarre sur approbation humaine et rend un **bilan chiffré**
  (agrégats, taux, coûts) — jamais la réponse du candidat (ENF-CONF-01) ;
- un devis au-delà du plafond est refusé **avant** tout appel de modèle
  (ENF-COUT-01) ;
- le kill-switch du client arrête l'observation, comme toute opération qui
  engage le compte ;
- le cloisonnement par client est strict (ENF-SEC-02) ;
- la séquence imposée est shadow → canary → paliers : sans shadow concluant, le
  canari est refusé (409).

Aucun appel réseau : les doubles hors ligne du dépôt (client de modèle, juge,
passerelle, dépôt de paires) sont les seuls utilisés.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import entetes
from tests.conftest import PETIT_MODELE

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

CLIENT_A = "t-demo-a"
APP_A = "ticket-classifier"
ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
MARQUEUR = "TEXTE-CONFIDENTIEL-DU-CLIENT"
APPROBATION = "responsable-applicatif"


def observations(nombre: int = 10) -> list[dict[str, object]]:
    """Trafic réel observé : le marqueur témoin y figure, il ne doit pas sortir."""
    return [
        {
            "requete_id": f"req-{index:03d}",
            "texte_requete": f"classer le ticket {index} {MARQUEUR}",
            "reponse_reelle": f"categorie-{index} {MARQUEUR}",
            "jetons_entree": 120,
            "jetons_sortie": 40,
            "latence_ms": 300,
        }
        for index in range(nombre)
    ]


def corps(**surcharges: object) -> dict[str, object]:
    """Corps d'une demande d'observation minimale, surchargeable."""
    demande: dict[str, object] = {
        "modele_candidat": PETIT_MODELE,
        "taille_max": 10,
        "plafond_usd": 5.0,
        "approuve_par": APPROBATION,
        "observations": observations(),
    }
    demande.update(surcharges)
    return demande


def lancer_ombre(client: TestClient, **surcharges: object) -> dict[str, object]:
    """Lance l'observation et rend son bilan (le test échoue si la route refuse)."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(**surcharges),
        headers=entetes(CLIENT_A, ANALYSTE),
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def test_le_shadow_demarre_et_rend_un_bilan_chiffre(client: TestClient) -> None:
    """L'observation démarre (201) et rend un bilan de chiffres, pas de texte."""
    bilan = lancer_ombre(client)

    assert bilan["application_id"] == APP_A
    assert bilan["tenant_id"] == CLIENT_A
    assert bilan["modele_candidat"] == PETIT_MODELE
    assert bilan["run_id"]
    assert bilan["nombre_requetes_tirees"] == 10
    assert bilan["volume_observe"] == 10
    assert bilan["nombre_paires"] == 10
    assert bilan["taux_equivalence"] == 1.0
    assert bilan["concluant"] is True


def test_le_devis_est_chiffre_dans_le_bilan(client: TestClient) -> None:
    """Le coût du shadow est chiffré : devis avant, dépense après."""
    bilan = lancer_ombre(client)

    assert bilan["cout_estime_usd"] > 0
    assert bilan["cout_shadow_usd"] > 0
    assert bilan["cout_candidat_usd"] > 0
    assert bilan["cout_judge_usd"] > 0
    assert bilan["cout_estime_usd"] <= bilan["plafond_usd"]
    assert bilan["plafond_usd"] == 5.0


def test_un_devis_au_dela_du_plafond_est_refuse(client: TestClient) -> None:
    """Au-delà du plafond, le shadow ne démarre pas : aucun dollar dépensé."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(plafond_usd=0.0),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["error"]["code"] == "campaign_cost_cap_exceeded"


def test_sans_approbation_humaine_le_shadow_est_refuse(client: TestClient) -> None:
    """Aucune observation sans décision humaine nommée (ENF-IA-02)."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(approuve_par=None),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["error"]["code"] == "approval_required"


def test_le_kill_switch_arrete_le_shadow(client: TestClient) -> None:
    """Un client qui a coupé ses traitements ne voit plus son trafic réobservé."""
    client.post(
        f"/v1/tenants/{CLIENT_A}/kill-switch",
        json={"action": "activer", "motif": "coupure de test"},
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["error"]["code"] == "traitement_suspendu"


def test_un_client_ne_lance_pas_le_shadow_d_un_autre(client: TestClient) -> None:
    """Cloisonnement : l'application d'un autre client est inaccessible."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(),
        headers=entetes("t-demo-b", ANALYSTE),
    )

    assert reponse.status_code == 403, reponse.text
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_un_role_client_ne_lance_pas_le_shadow(client: TestClient) -> None:
    """Observer le trafic du client est un rôle d'exploitation."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(),
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    assert reponse.status_code == 403, reponse.text
    assert reponse.json()["error"]["code"] == "forbidden"


def test_aucun_texte_ne_sort_dans_la_reponse(client: TestClient) -> None:
    """Ni la requête ni la réponse réelle ne quittent le plan de données."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/ombre",
        json=corps(),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 201, reponse.text
    assert MARQUEUR not in reponse.text
    assert "categorie-" not in reponse.text
    assert "classer le ticket" not in reponse.text


def test_aucun_texte_ne_sort_dans_le_journal_d_audit(
    client: TestClient, audit: MemoryAuditStore
) -> None:
    """L'audit porte des identifiants et des chiffres, jamais les textes."""
    lancer_ombre(client)

    evenements = audit.lister(tenant_id=CLIENT_A, action="ombre_lancee")
    assert len(evenements) == 1
    details = evenements[0].details
    assert MARQUEUR not in str(details)
    assert details["modele_candidat"] == PETIT_MODELE
    assert details["concluant"] is True
    assert details["cout_estime_usd"] > 0


def test_le_shadow_compare_le_trafic_reel_au_rejeu(client: TestClient) -> None:
    """L'écart entre la preuve de laboratoire et le trafic réel est publié."""
    bilan = lancer_ombre(client)

    assert bilan["taux_equivalence_rejeu"] == 0.97
    assert bilan["ecart_labo_production"] == 0.03
    assert bilan["divergence"] is False
    assert bilan["seuil_ecart_labo_production"] == 0.05


def test_l_etat_du_shadow_se_lit(client: TestClient) -> None:
    """L'état courant donne la phase, le bilan et l'absence de canari."""
    bilan = lancer_ombre(client)

    reponse = client.get(
        f"/v1/applications/{APP_A}/ombre",
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 200, reponse.text
    etat = reponse.json()
    assert etat["phase"] == "ombre"
    assert etat["application_id"] == APP_A
    assert etat["tenant_id"] == CLIENT_A
    assert etat["bilan"]["run_id"] == bilan["run_id"]
    assert etat["etapes"]["shadow"] == "concluante"
    assert etat["canari"] is None


def test_l_etat_est_vide_avant_toute_observation(client: TestClient) -> None:
    """Avant toute observation, l'état est « aucune » — pas une erreur."""
    reponse = client.get(
        f"/v1/applications/{APP_A}/ombre",
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 200, reponse.text
    etat = reponse.json()
    assert etat["phase"] == "aucune"
    assert etat["bilan"] is None
    assert etat["canari"] is None


def test_le_canari_ouvre_une_regle_ponderee_a_un_pourcent(client: TestClient) -> None:
    """Après un shadow concluant, le canari sert 1 % du trafic réel."""
    lancer_ombre(client)

    reponse = client.post(
        f"/v1/applications/{APP_A}/canari",
        json={"approuve_par": APPROBATION, "taux_erreur_reference": 0.001},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 201, reponse.text
    canari = reponse.json()
    assert canari["palier_pourcentage"] == 1
    assert canari["regle_id"]
    assert canari["application_id"] == APP_A
    assert canari["tenant_id"] == CLIENT_A
    assert canari["declencheurs"]["seuil_qualite"] == 0.95
    assert canari["declencheurs"]["taux_erreur_reference"] == 0.001
    assert canari["bascule"]["actif"] is True


def test_le_canari_est_refuse_sans_shadow_concluant(client: TestClient) -> None:
    """La séquence est imposée : sans shadow, le canari n'existe pas."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/canari",
        json={"approuve_par": APPROBATION, "taux_erreur_reference": 0.001},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["error"]["code"] == "preceding_step_missing"


def test_un_shadow_divergent_n_est_pas_concluant(client: TestClient) -> None:
    """Un écart trop grand avec le rejeu est un résultat, pas une note."""
    bilan = lancer_ombre(client, seuil_ecart_labo_production=0.01)

    assert bilan["divergence"] is True
    assert bilan["concluant"] is False
    assert bilan["ecart_labo_production"] == 0.03


def test_le_canari_est_refuse_quand_le_shadow_divergente(client: TestClient) -> None:
    """Un shadow divergent ne débloque pas le canari : l'écart est connu."""
    lancer_ombre(client, seuil_ecart_labo_production=0.01)

    reponse = client.post(
        f"/v1/applications/{APP_A}/canari",
        json={"approuve_par": APPROBATION, "taux_erreur_reference": 0.001},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["error"]["code"] == "preceding_step_missing"


def test_le_canari_est_refuse_sans_approbation(client: TestClient) -> None:
    """Ouvrir le canari est une bascule : elle exige une décision humaine."""
    lancer_ombre(client)

    reponse = client.post(
        f"/v1/applications/{APP_A}/canari",
        json={"approuve_par": None, "taux_erreur_reference": 0.001},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["error"]["code"] == "approval_required"


def test_le_canari_est_journalise_sans_texte(client: TestClient, audit: MemoryAuditStore) -> None:
    """L'ouverture du canari laisse une trace chiffrée dans l'audit."""
    lancer_ombre(client)
    client.post(
        f"/v1/applications/{APP_A}/canari",
        json={"approuve_par": APPROBATION, "taux_erreur_reference": 0.001},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    evenements = audit.lister(tenant_id=CLIENT_A, action="canari_lance")
    assert len(evenements) == 1
    details = evenements[0].details
    assert MARQUEUR not in str(details)
    assert details["palier_pourcentage"] == 1
    assert details["seuil_qualite"] == 0.95
