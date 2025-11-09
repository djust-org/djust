#!/bin/bash
set -e

# Django Rust Live Build Script
# This script builds and pushes the Docker image to GitHub Container Registry

IMAGE_NAME="ghcr.io/johnrtipton/django-rust-live"
TAG="${1:-latest}"
FULL_IMAGE="${IMAGE_NAME}:${TAG}"

echo "======================================"
echo "Django Rust Live Docker Build"
echo "======================================"
echo "Image: $FULL_IMAGE"
echo "Platform: linux/amd64 (for Kubernetes cluster)"
echo "======================================"
echo ""

# Check if docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: docker not found. Please install Docker first."
    exit 1
fi

# Check if docker buildx is available
if ! docker buildx version &> /dev/null; then
    echo "Setting up docker buildx..."
    docker buildx create --use
    docker buildx inspect --bootstrap
fi

# Check if logged into ghcr.io
echo "1. Checking GitHub Container Registry authentication..."

# Try to authenticate with GITHUB_PUSH_TOKEN if available
if [ -n "$GITHUB_PUSH_TOKEN" ]; then
    echo "Using GITHUB_PUSH_TOKEN for authentication..."
    if echo "$GITHUB_PUSH_TOKEN" | docker login ghcr.io -u johnrtipton --password-stdin > /dev/null 2>&1; then
        echo "✓ Authenticated with ghcr.io using GITHUB_PUSH_TOKEN"
    else
        echo "Error: Failed to authenticate with GITHUB_PUSH_TOKEN"
        exit 1
    fi
elif ! cat ~/.docker/config.json 2>/dev/null | grep -q '"ghcr.io"'; then
    echo "Not logged into ghcr.io. Please either:"
    echo ""
    echo "1. Set GITHUB_PUSH_TOKEN environment variable:"
    echo "   export GITHUB_PUSH_TOKEN=<your_token>"
    echo ""
    echo "2. Or log in manually:"
    echo "   echo \$GITHUB_TOKEN | docker login ghcr.io -u johnrtipton --password-stdin"
    echo ""
    echo "You can create a token at: https://github.com/settings/tokens"
    echo "Required scopes: write:packages, read:packages"
    exit 1
else
    echo "✓ Already authenticated with ghcr.io"
fi
echo ""

# Build the image for AMD64
echo "2. Building Docker image for linux/amd64..."
docker buildx build \
    --platform linux/amd64 \
    -t "$FULL_IMAGE" \
    --push \
    .

echo ""
echo "======================================"
echo "Build Complete!"
echo "======================================"
echo "Image: $FULL_IMAGE"
echo ""
echo "To deploy to Kubernetes:"
echo "  ./k8s/deploy.sh"
echo ""
