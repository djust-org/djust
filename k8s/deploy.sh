#!/bin/bash
set -e

# Django Rust Live Deployment Script
# This script deploys Django Rust Live to Kubernetes

NAMESPACE="django-rust"
IMAGE_NAME="ghcr.io/johnrtipton/django-rust-live:latest"
DOMAIN="django-rust.k8.trylinux.org"

echo "======================================"
echo "Django Rust Live Deployment"
echo "======================================"
echo "Namespace: $NAMESPACE"
echo "Image: $IMAGE_NAME"
echo "Domain: $DOMAIN"
echo "======================================"
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found. Please install kubectl first."
    exit 1
fi

# Check cluster connection
echo "1. Checking Kubernetes cluster connection..."
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Cannot connect to Kubernetes cluster."
    echo "Please ensure your kubeconfig is set up correctly."
    exit 1
fi
echo "✓ Connected to Kubernetes cluster"
echo ""

# Create namespace
echo "2. Creating namespace..."
kubectl apply -f k8s/namespace.yaml
echo "✓ Namespace created/updated"
echo ""

# Create image pull secret if it doesn't exist
echo "3. Checking for image pull secret..."
if ! kubectl get secret ghcr-secret -n $NAMESPACE &> /dev/null; then
    echo "Image pull secret not found. Please create it manually:"
    echo ""
    echo "kubectl create secret docker-registry ghcr-secret \\"
    echo "  -n $NAMESPACE \\"
    echo "  --docker-server=ghcr.io \\"
    echo "  --docker-username=johnrtipton \\"
    echo "  --docker-password=<YOUR_GITHUB_TOKEN> \\"
    echo "  --docker-email=<YOUR_EMAIL>"
    echo ""
    read -p "Press enter when you've created the secret..."
fi
echo "✓ Image pull secret exists"
echo ""

# Create secrets
echo "4. Creating/updating secrets..."
if [ ! -f "k8s/secrets.yaml" ]; then
    echo "Error: k8s/secrets.yaml not found!"
    echo "Please copy k8s/secrets.yaml.template to k8s/secrets.yaml and fill in the values"
    exit 1
fi
kubectl apply -f k8s/secrets.yaml
echo "✓ Secrets created/updated"
echo ""

# Deploy application
echo "5. Deploying application..."
kubectl apply -f k8s/deployment.yaml
echo "✓ Deployment created/updated"
echo ""

# Force restart to pull new image
echo "6. Restarting deployment to pull new image..."
kubectl rollout restart deployment/django-rust-live -n $NAMESPACE
echo "✓ Rollout restart initiated"
echo ""

# Configure ingress
echo "7. Configuring ingress..."
kubectl apply -f k8s/ingress.yaml
echo "✓ Ingress created/updated"
echo ""

# Wait for deployment
echo "8. Waiting for deployment to be ready..."
kubectl rollout status deployment/django-rust-live -n $NAMESPACE
echo "✓ Deployment is ready"
echo ""

# Show status
echo "======================================"
echo "Deployment Status"
echo "======================================"
echo ""

echo "Pods:"
kubectl get pods -n $NAMESPACE
echo ""

echo "Services:"
kubectl get svc -n $NAMESPACE
echo ""

echo "Ingress:"
kubectl get ingress -n $NAMESPACE
echo ""

echo "Certificates:"
kubectl get certificates -n $NAMESPACE 2>/dev/null || echo "No certificates yet (will be created automatically)"
echo ""

echo "======================================"
echo "Deployment Complete!"
echo "======================================"
echo ""
echo "Your application should be available at:"
echo "https://$DOMAIN"
echo ""
echo "Note: SSL certificate may take a few minutes to be issued."
echo "Check certificate status with:"
echo "kubectl get certificate -n $NAMESPACE"
echo ""
echo "View logs with:"
echo "kubectl logs -f deployment/django-rust-live -n $NAMESPACE"
echo ""
