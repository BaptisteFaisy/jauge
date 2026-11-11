#!/usr/bin/env bash
# deploy.sh — deploy the Jauge client-account data plane with one command.
#
# Usage:
#   ./deploy.sh --msp-account 111122223333 --logs-bucket acme-jauge-logs \
#               [--external-id VALUE] [--profile NAME] [--region eu-west-1] \
#               [--environment demo] [--retention 90] [--budget 200] \
#               [--alert-email ops@acme.example] [--stack-name jauge-client] \
#               [--dry-run]
#
# The external ID is generated locally when omitted (32 characters, URL safe).
# It is printed once: hand it to the MSP, it is the only secret of the exchange.
set -euo pipefail

GABARIT="$(cd "$(dirname "$0")" && pwd)/template.yaml"
PROFIL=""
REGION="${AWS_REGION:-eu-west-1}"
COMPTE_MSP=""
IDENTIFIANT_EXTERNE=""
ENVIRONNEMENT="demo"
RETENTION="90"
BUDGET="200"
COURRIEL=""
NOM_PILE="jauge-client"
BUCKET=""
SIMULATION="false"

die() { echo "ERREUR : $*" >&2; exit 1; }

generer_identifiant() {
  # 32 caracteres de l'alphabet accepte par le parametre ExternalId.
  local brut
  brut="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-32)"
  [ "${#brut}" -ge 16 ] || die "generation de l'identifiant externe impossible"
  printf '%s' "$brut"
}

analyser_arguments() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --msp-account) COMPTE_MSP="${2:?}"; shift 2 ;;
      --external-id) IDENTIFIANT_EXTERNE="${2:?}"; shift 2 ;;
      --logs-bucket) BUCKET="${2:?}"; shift 2 ;;
      --profile) PROFIL="${2:?}"; shift 2 ;;
      --region) REGION="${2:?}"; shift 2 ;;
      --environment) ENVIRONNEMENT="${2:?}"; shift 2 ;;
      --retention) RETENTION="${2:?}"; shift 2 ;;
      --budget) BUDGET="${2:?}"; shift 2 ;;
      --alert-email) COURRIEL="${2:?}"; shift 2 ;;
      --stack-name) NOM_PILE="${2:?}"; shift 2 ;;
      --dry-run) SIMULATION="true"; shift ;;
      -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
      *) die "argument inconnu : $1" ;;
    esac
  done
}

valider_arguments() {
  [ -f "$GABARIT" ] || die "gabarit introuvable : $GABARIT"
  [[ "$COMPTE_MSP" =~ ^[0-9]{12}$ ]] || die "--msp-account doit etre un identifiant de compte a 12 chiffres"
  [ -n "$BUCKET" ] || die "--logs-bucket est obligatoire (nom unique au monde)"
  case "$RETENTION" in
    7|14|30|60|90|180|365|400|731) ;;
    *) die "--retention doit valoir 7, 14, 30, 60, 90, 180, 365, 400 ou 731" ;;
  esac
  [ -n "$IDENTIFIANT_EXTERNE" ] || IDENTIFIANT_EXTERNE="$(generer_identifiant)"
  [ "${#IDENTIFIANT_EXTERNE}" -ge 16 ] || die "--external-id doit faire au moins 16 caracteres"
}

construire_commande() {
  local -a commande=(
    aws cloudformation deploy
    --template-file "$GABARIT"
    --stack-name "$NOM_PILE"
    --region "$REGION"
    --capabilities CAPABILITY_NAMED_IAM
    --no-fail-on-empty-changeset
    --parameter-overrides
    "MspAccountId=$COMPTE_MSP"
    "ExternalId=$IDENTIFIANT_EXTERNE"
    "EnvironmentName=$ENVIRONNEMENT"
    "DataRegion=$REGION"
    "LogsBucketName=$BUCKET"
    "RetentionDays=$RETENTION"
    "MonthlyBudgetUsd=$BUDGET"
    "AlertEmail=$COURRIEL"
  )
  [ -n "$PROFIL" ] && commande+=(--profile "$PROFIL")
  printf '%s\n' "${commande[@]}"
}

simuler() {
  echo "==> Simulation (aucune ressource creee)"
  echo "    region      : $REGION"
  echo "    compte MSP  : $COMPTE_MSP"
  echo "    bucket      : $BUCKET"
  echo "    retention   : $RETENTION jours"
  echo "    budget      : $BUDGET USD/mois"
  if command -v cfn-lint >/dev/null 2>&1; then
    cfn-lint "$GABARIT" >/dev/null && echo "    gabarit valide (cfn-lint)"
  elif command -v uv >/dev/null 2>&1; then
    uv run cfn-lint "$GABARIT" >/dev/null && echo "    gabarit valide (cfn-lint via uv)"
  else
    echo "    (cfn-lint absent : validation du gabarit ignoree)"
  fi
  if command -v aws >/dev/null 2>&1; then
    local -a options=(--region "$REGION")
    [ -n "$PROFIL" ] && options+=(--profile "$PROFIL")
    aws "${options[@]}" cloudformation validate-template \
      --template-body "file://$GABARIT" >/dev/null \
      && echo "    gabarit valide (cloudformation validate-template)"
  else
    echo "    (AWS CLI absente : validate-template ignore)"
  fi
  echo "    commande :"
  mapfile -t commande < <(construire_commande)
  printf '      '; printf '%q ' "${commande[@]}"; printf '\n'
}

main() {
  analyser_arguments "$@"
  valider_arguments
  if [ "$SIMULATION" = "true" ]; then
    simuler
    return 0
  fi

  command -v aws >/dev/null 2>&1 || die "AWS CLI introuvable (https://aws.amazon.com/cli/)"
  mapfile -t commande < <(construire_commande)
  echo "==> Deploiement de la pile $NOM_PILE dans $REGION"
  "${commande[@]}"

  echo "==> Sorties de la pile"
  local -a lecture=(cloudformation describe-stacks --stack-name "$NOM_PILE" --region "$REGION")
  [ -n "$PROFIL" ] && lecture+=(--profile "$PROFIL")
  aws "${lecture[@]}" --query 'Stacks[0].Outputs' --output table

  echo
  echo "Identifiant externe a remettre au MSP (a conserver) : $IDENTIFIANT_EXTERNE"
  echo "Etape suivante : activer les journaux d'invocation Bedrock vers le bucket $BUCKET (EF-ATT-02)."
}

main "$@"
