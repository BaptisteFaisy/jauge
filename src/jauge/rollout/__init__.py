"""Module M6 — bascule par paliers, rollback automatique et vérification.

Répond aux exigences :
- EF-BAS-01 : créer la règle de passerelle qui fait servir le modèle cible.
- EF-BAS-02 : monter par paliers (10 %, 50 %, 100 %) et rejuger un échantillon
  du trafic réel à chaque palier.
- EF-BAS-03 : retirer la règle automatiquement si la qualité passe sous le seuil
  ou si le taux d'erreur monte.
- EF-BAS-04 : ticket décrivant le changement pour une application hors passerelle.
- EF-REC-02 / ENF-IA-02 : aucune bascule sans approbation humaine.
- EF-VER-01..02 : vérification de l'économie à volume constant, 30 jours après.
- ENF-CONF-01 : rien de ce que produit ce module ne contient un texte de requête.

Les quatre pièces du module :

| Module | Rôle |
|---|---|
| `gateway` | règles de passerelle AgentCore (statique + pondérée), protocole + fake |
| `controller` | machine à paliers, rejugement en ligne, rollback automatique |
| `verify` | économie constatée vs attendue, à volume constant |
| `ticket` | ticket de changement de modèle pour une application hors passerelle |
"""

from __future__ import annotations

from jauge.rollout.controller import (
    ControlleurBascule,
    MesureEnLigne,
    QualiteEnLigne,
    SuiviBascule,
)
from jauge.rollout.gateway import (
    PRIORITE_PAR_DEFAUT,
    AgentCoreGatewayClient,
    AppelPasserelle,
    FakePasserelleClient,
    PasserelleClient,
    ReglePasserelle,
)
from jauge.rollout.ticket import (
    EmetteurTicket,
    FakeEmetteurTicket,
    TicketChangementModele,
    generer_ticket,
)
from jauge.rollout.verify import (
    ConfirmationFacture,
    confirmer_par_etiquette,
    cout_par_requete,
    verifier_economie,
)

__all__ = [
    "PRIORITE_PAR_DEFAUT",
    "AgentCoreGatewayClient",
    "AppelPasserelle",
    "ConfirmationFacture",
    "ControlleurBascule",
    "EmetteurTicket",
    "FakeEmetteurTicket",
    "FakePasserelleClient",
    "MesureEnLigne",
    "PasserelleClient",
    "QualiteEnLigne",
    "ReglePasserelle",
    "SuiviBascule",
    "TicketChangementModele",
    "confirmer_par_etiquette",
    "cout_par_requete",
    "generer_ticket",
    "verifier_economie",
]
