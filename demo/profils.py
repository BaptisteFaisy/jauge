"""Profils de trafic synthétique de la démonstration Jauge (§18.1).

Ce module est la **source unique de vérité** sur les volumes, les horaires et
les profils de jetons des quatre applications de démonstration :

- client A (`t-demo-a`) : `support-assistant`, `ticket-classifier`,
  `doc-summarizer` ;
- client B (`t-demo-b`) : `kb-search`, présente uniquement dans la vue parc.

Les volumes sont volontairement modestes : le trafic est **entièrement
synthétique** (§18.1 « aucune donnée réelle ») et sert à alimenter les écrans de
la démonstration, pas à une projection de production. Les montants affichés sont
la projection mensuelle du trafic observé, calculée par le code.

Aucun texte, volume ou horodatage de ce module ne provient d'une donnée réelle
(ENF-CONF-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from demo.socle import constantes
from jauge.models.pricing import ServiceTier

# Modèles, clients et région : lus dans les fixtures du socle (source unique).
_SOCLE = constantes()
GRAND_MODELE: str = _SOCLE.GRAND_MODELE
MOYEN_MODELE: str = _SOCLE.MOYEN_MODELE
PETIT_MODELE: str = _SOCLE.PETIT_MODELE
REGION: str = _SOCLE.REGION

TENANT_A = "t-demo-a"
TENANT_B = "t-demo-b"

# Fenêtre de la démonstration : les 14 derniers jours pleins (chapitre 13.1).
JOURS_DEFAUT = 14
DATE_FIN_DEFAUT = date(2026, 11, 10)
GRAINE_DEFAUT = 20261111


def poids_horaires(heures: dict[int, int]) -> tuple[int, ...]:
    """Convertit un dictionnaire {heure: poids} en tuple de 24 poids."""
    poids = [0] * 24
    for heure, valeur in heures.items():
        poids[heure % 24] = valeur
    return tuple(poids)


@dataclass(frozen=True)
class ProfilApplication:
    """Profil de trafic d'une application de démonstration.

    Les plages de jetons décrivent le **volume de contenu** transporté par
    l'appel ; le générateur les respecte en dimensionnant le contexte envoyé.
    """

    application_id: str
    tenant_id: str
    equipe: str
    environnement: str
    modele_id: str
    niveau: ServiceTier
    type_requete: str
    requetes_par_jour: int
    poids_horaires: tuple[int, ...]
    jetons_entree: tuple[int, int]
    jetons_sortie: tuple[int, int]
    latence_p50_ms: int
    latence_p95_ms: int
    max_tokens: int
    temperature: float = 0.0
    part_requetes_identiques: float = 0.0
    part_erreurs: float = 0.0
    facteur_weekend: float = 1.0

    @property
    def total_poids(self) -> int:
        """Somme des poids horaires (utilisée pour répartir le trafic)."""
        return sum(self.poids_horaires)


PROFILS: dict[str, ProfilApplication] = {
    # --- Client A ----------------------------------------------------------
    "support-assistant": ProfilApplication(
        application_id="support-assistant",
        tenant_id=TENANT_A,
        equipe="support",
        environnement="production",
        modele_id=GRAND_MODELE,
        niveau=ServiceTier.STANDARD,
        type_requete="question",
        requetes_par_jour=300,
        poids_horaires=poids_horaires(
            {8: 2, 9: 3, 10: 4, 11: 3, 12: 2, 13: 1, 14: 3, 15: 4, 16: 3, 17: 2, 18: 1}
        ),
        jetons_entree=(1400, 2600),
        jetons_sortie=(420, 900),
        latence_p50_ms=2400,
        latence_p95_ms=5200,
        max_tokens=4096,
        temperature=0.2,
        # Le préfixe système long et identique à chaque appel (levier « cache
        # de prompts absent ») est porté par `demo.apps.support_assistant`.
        part_requetes_identiques=0.12,
        part_erreurs=0.008,
        facteur_weekend=0.4,
    ),
    "ticket-classifier": ProfilApplication(
        application_id="ticket-classifier",
        tenant_id=TENANT_A,
        equipe="support",
        environnement="production",
        modele_id=GRAND_MODELE,
        niveau=ServiceTier.STANDARD,
        type_requete="classification",
        requetes_par_jour=800,
        poids_horaires=poids_horaires(
            {9: 3, 10: 5, 11: 4, 12: 2, 13: 1, 14: 4, 15: 5, 16: 3, 17: 1}
        ),
        # Requêtes courtes, sorties JSON courtes : levier « modèle surdimensionné ».
        jetons_entree=(90, 190),
        jetons_sortie=(14, 30),
        latence_p50_ms=700,
        latence_p95_ms=1400,
        max_tokens=256,
        temperature=0.0,
        part_requetes_identiques=0.18,
        part_erreurs=0.02,
        facteur_weekend=0.35,
    ),
    "doc-summarizer": ProfilApplication(
        application_id="doc-summarizer",
        tenant_id=TENANT_A,
        equipe="back-office",
        environnement="production",
        modele_id=GRAND_MODELE,
        niveau=ServiceTier.STANDARD,
        type_requete="resume",
        requetes_par_jour=100,
        # Trafic nocturne : levier « traitement différable » (Annexe A).
        poids_horaires=poids_horaires({1: 3, 2: 4, 3: 3, 4: 2}),
        jetons_entree=(2400, 5200),
        # Sorties anormalement longues, proches de la limite : levier
        # « sorties trop longues ».
        jetons_sortie=(1100, 3800),
        latence_p50_ms=6000,
        latence_p95_ms=11000,
        max_tokens=4096,
        temperature=0.1,
        facteur_weekend=1.0,
    ),
    # --- Client B (vue parc) ----------------------------------------------
    "kb-search": ProfilApplication(
        application_id="kb-search",
        tenant_id=TENANT_B,
        equipe="documentation",
        environnement="production",
        modele_id=MOYEN_MODELE,
        niveau=ServiceTier.STANDARD,
        type_requete="recherche",
        requetes_par_jour=120,
        poids_horaires=poids_horaires(
            {9: 2, 10: 3, 11: 3, 12: 2, 13: 1, 14: 2, 15: 3, 16: 2, 17: 1}
        ),
        jetons_entree=(250, 700),
        jetons_sortie=(120, 400),
        latence_p50_ms=900,
        latence_p95_ms=1800,
        max_tokens=2048,
        temperature=0.0,
        part_requetes_identiques=0.08,
        part_erreurs=0.005,
        facteur_weekend=0.4,
    ),
}
