#!/usr/bin/env bash
# deploy.sh — build, push to ECR, and roll out a new ECS deployment
#
# Prerequisites:
#   aws-cli v2  |  docker  |  jq
#
# Usage:
#   ./deploy/deploy.sh [--env production] [--region us-east-1] [--tag latest]
#
# The script reads AWS_ACCOUNT_ID from the environment (or auto-detects it).
# All other values can be overridden via flags or environment variables.

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
ENV="${DEPLOY_ENV:-production}"
REGION="${AWS_REGION:-us-east-1}"
TAG="${IMAGE_TAG:-latest}"
REPO_NAME="ofac-screening-api"
CLUSTER_STACK="${CLUSTER_STACK:-ofac-api-${ENV}}"

# ── Parse flags ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --env)     ENV="$2";     shift 2 ;;
    --region)  REGION="$2";  shift 2 ;;
    --tag)     TAG="$2";     shift 2 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

# ── Resolve account ID ────────────────────────────────────────────────────────
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${ECR_REGISTRY}/${REPO_NAME}:${TAG}"

echo "==> Deploying OFAC Screening API"
echo "    Environment : ${ENV}"
echo "    Region      : ${REGION}"
echo "    Account     : ${ACCOUNT_ID}"
echo "    Image       : ${IMAGE_URI}"

# ── Step 1: Authenticate Docker to ECR ───────────────────────────────────────
echo ""
echo "==> [1/4] Authenticating Docker to ECR..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# ── Step 2: Build the Docker image ───────────────────────────────────────────
echo ""
echo "==> [2/4] Building Docker image..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build \
  --platform linux/amd64 \
  --tag "${IMAGE_URI}" \
  --tag "${ECR_REGISTRY}/${REPO_NAME}:${TAG}" \
  "${SCRIPT_DIR}/.."

# ── Step 3: Push to ECR ───────────────────────────────────────────────────────
echo ""
echo "==> [3/4] Pushing image to ECR..."
docker push "${IMAGE_URI}"

# ── Step 4: Update ECS service ────────────────────────────────────────────────
echo ""
echo "==> [4/4] Triggering ECS rolling deployment..."

# Look up cluster and service names from CloudFormation exports
CLUSTER=$(aws cloudformation list-exports --region "${REGION}" \
  --query "Exports[?Name=='${ENV}-ofac-api-cluster'].Value" \
  --output text)
SERVICE=$(aws cloudformation list-exports --region "${REGION}" \
  --query "Exports[?Name=='${ENV}-ofac-api-service'].Value" \
  --output text)

if [[ -z "${CLUSTER}" || -z "${SERVICE}" ]]; then
  echo "ERROR: Could not resolve cluster/service from CloudFormation exports." >&2
  echo "       Have you deployed the CloudFormation stack yet?" >&2
  exit 1
fi

aws ecs update-service \
  --region "${REGION}" \
  --cluster "${CLUSTER}" \
  --service "${SERVICE}" \
  --force-new-deployment \
  --query "service.{Status:status,Running:runningCount,Desired:desiredCount}" \
  --output table

echo ""
echo "==> Deployment initiated. Monitor progress with:"
echo "    aws ecs describe-services --cluster ${CLUSTER} --services ${SERVICE} --region ${REGION}"
echo ""
echo "    Or watch the ALB target group until all targets are healthy:"
echo "    watch -n 5 'aws ecs describe-services --cluster ${CLUSTER} --services ${SERVICE} \\
      --region ${REGION} --query services[0].deployments --output table'"
