#!/usr/bin/env bash
# ============================================================================
# Construye las imagenes Docker de las Lambdas (frame_extractor, inference),
# las publica en ECR y despliega/actualiza todo el stack (buckets, Lambdas,
# Step Functions, EventBridge) -- usando credenciales de AWS cargadas desde
# un archivo .env, sin necesitar `aws configure` ni perfiles del CLI.
#
# Uso:
#   cp .env.example .env      # completa tus credenciales y variables
#   ./scripts/deploy.sh                # usa .env en la raiz del repo
#   ./scripts/deploy.sh mi-stack       # nombre de stack por argumento
#   ENV_FILE=.env.prod ./scripts/deploy.sh   # otro archivo de variables
#
# Requisitos: AWS CLI, SAM CLI y Docker corriendo localmente.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."  # raiz del repo, sin importar desde donde se invoque

ENV_FILE="${ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: no se encontro '$ENV_FILE'."
  echo "Copia .env.example a .env y completa tus credenciales/variables."
  exit 1
fi

# Carga el .env como variables de entorno reales (para que tanto `aws` como
# `sam` las levanten automaticamente, igual que si hubieras hecho `export`).
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AWS_ACCESS_KEY_ID:?Falta AWS_ACCESS_KEY_ID en $ENV_FILE}"
: "${AWS_SECRET_ACCESS_KEY:?Falta AWS_SECRET_ACCESS_KEY en $ENV_FILE}"
: "${AWS_DEFAULT_REGION:?Falta AWS_DEFAULT_REGION en $ENV_FILE}"
: "${ENDPOINT_URL:?Falta ENDPOINT_URL en $ENV_FILE}"

STACK_NAME="${1:-${STACK_NAME:-ocular-pipeline}}"

MODEL_FILE="functions/inference/model/yolo26_seg.onnx"
if [[ ! -f "$MODEL_FILE" ]]; then
  echo "ERROR: falta el modelo $MODEL_FILE."
  echo "Corre primero: python scripts/export_model.py --weights /ruta/a/tu-modelo.pt"
  exit 1
fi

echo ">> Verificando credenciales de AWS (sts get-caller-identity)..."
aws sts get-caller-identity --output table

echo ">> Compilando imagenes Docker (arm64) y empaquetando la Lambda notifier..."
sam build

echo ">> Publicando imagenes en ECR y desplegando/actualizando el stack '$STACK_NAME'..."
# `sam deploy --parameter-overrides Key=Value` no acepta un Value vacio, por
# eso EndpointApiKey solo se agrega si esta definido (si no, se usa el
# Default: "" del template.yaml).
PARAM_OVERRIDES=(
  "EndpointUrl=$ENDPOINT_URL"
  "MaxConcurrency=${MAX_CONCURRENCY:-1000}"
  "MaxItemsPerBatch=${MAX_ITEMS_PER_BATCH:-1}"
  "OutputFormat=${OUTPUT_FORMAT:-json}"
  "GzipFile=${GZIP_FILE:-true}"
  "PresignedUrlExpirationSeconds=${PRESIGNED_URL_EXPIRATION_SECONDS:-3600}"
  "ExtractorMemory=${EXTRACTOR_MEMORY:-2048}"
  "InferenceMemory=${INFERENCE_MEMORY:-2048}"
)
if [[ -n "${ENDPOINT_API_KEY:-}" ]]; then
  PARAM_OVERRIDES+=("EndpointApiKey=$ENDPOINT_API_KEY")
fi

sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --resolve-s3 \
  --resolve-image-repos \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAM_OVERRIDES[@]}"

echo ""
echo ">> Listo. Bucket de videos para subir tus .mp4:"
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='VideosBucketName'].OutputValue" \
  --output text
