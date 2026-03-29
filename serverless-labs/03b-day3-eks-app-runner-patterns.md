# Day 3 Addendum — EKS on Fargate & AWS App Runner

> **Patterns 24–27 | Add-on to Day 3**  
> Two patterns each for EKS on Fargate (serverless Kubernetes) and AWS App Runner (fully managed container hosting). Each lab is self-contained.

---

## Why EKS and App Runner?

Before diving in, here is where these services sit in the serverless container landscape:

```
Least ops overhead  ◄─────────────────────────────────►  Most control
                                                        
App Runner → ECS Fargate → EKS on Fargate → EKS on EC2
```

| Service | You manage | AWS manages | Best for |
|---------|-----------|-------------|----------|
| **App Runner** | Your code / container | Everything else | Simple web APIs, rapid deployment |
| **ECS Fargate** | Task definitions, services | Compute, networking | Containerised microservices without Kubernetes |
| **EKS on Fargate** | Kubernetes manifests | Control plane + compute | Teams already using Kubernetes |
| **EKS on EC2** | K8s manifests + EC2 nodes | Control plane only | Maximum control and performance |

---

## EKS on Fargate — Concept Primer

With EKS on Fargate there are **no worker nodes** — each pod runs on its own isolated Fargate micro-VM. AWS manages the underlying EC2 capacity entirely.

```
Standard EKS:   Control Plane (AWS) + Worker Nodes (EC2, YOU manage)
EKS + Fargate:  Control Plane (AWS) + Pod compute   (Fargate, AWS manages)
```

**Fargate profiles** tell EKS which pods to run on Fargate vs EC2 nodes, using namespace and label selectors.

**Limitations of EKS on Fargate to be aware of:**
- No DaemonSets (use sidecar containers instead)
- No privileged pods
- No EBS persistent volumes (use EFS or S3)
- Slightly higher per-pod cost than equivalent EC2 node
- Pod startup time: 30–90 seconds (cold start)

---

## Pattern 24: EKS on Fargate — Serverless Kubernetes Microservice

> **Deploy a Kubernetes-native REST API with zero node management**

### What This Pattern Solves

Your team already uses Kubernetes — Helm charts, kubectl, kustomize, GitOps pipelines. Moving to ECS would mean rewriting all that tooling. EKS on Fargate lets you keep the full Kubernetes workflow (manifests, namespaces, RBAC, Ingress, HPA) while eliminating EC2 node management entirely.

### Architecture

```
kubectl / CI-CD  →  EKS Control Plane  →  Fargate Profile  →  Pod (no EC2 node)
                                                |
                                         ALB Ingress Controller
                                                |
                                          Internet traffic
```

---

### Step 1 — Install Prerequisites

On your **Cloud9** or local machine:

```bash
# Install kubectl
curl -O https://s3.us-west-2.amazonaws.com/amazon-eks/1.31.0/2024-09-12/bin/linux/amd64/kubectl
chmod +x kubectl && sudo mv kubectl /usr/local/bin/

# Install eksctl (EKS cluster management CLI)
curl --silent --location \
  "https://github.com/eksctl-io/eksctl/releases/latest/download/eksctl_Linux_amd64.tar.gz" \
  | tar xz -C /tmp
sudo mv /tmp/eksctl /usr/local/bin/

# Verify
kubectl version --client
eksctl version
```

---

### Step 2 — Create the EKS Cluster with Fargate

```bash
export CLUSTER_NAME=serverless-labs-eks
export AWS_REGION=us-east-1

# Create cluster — this takes 15–20 minutes
# The --fargate flag creates a default Fargate profile for the default and kube-system namespaces
eksctl create cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --fargate \
  --version 1.31

# Update kubeconfig
aws eks update-kubeconfig \
  --name $CLUSTER_NAME \
  --region $AWS_REGION

# Verify nodes (Fargate nodes appear as virtual nodes)
kubectl get nodes
```

> ☕ **This step takes ~15–20 minutes.** Move on to building the application while it provisions.

---

### Step 3 — Create a Fargate Profile for Your App Namespace

```bash
# Create a dedicated namespace for the app
kubectl create namespace products-api

# Create a Fargate profile — pods in 'products-api' namespace run on Fargate
eksctl create fargateprofile \
  --cluster $CLUSTER_NAME \
  --name products-api-profile \
  --namespace products-api \
  --region $AWS_REGION
```

---

### Step 4 — Build and Push the Application Image

```bash
mkdir eks-fargate-lab && cd eks-fargate-lab
```

**`app.py`** — lightweight Flask products API:

```python
from flask import Flask, jsonify, request
import boto3
import uuid
import os

app = Flask(__name__)
dynamo = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
TABLE  = os.environ.get('TABLE_NAME', 'EKSProducts')

@app.get('/health')
def health():
    return jsonify({'status': 'healthy', 'service': 'products-api', 'runtime': 'eks-fargate'})

@app.get('/products')
def list_products():
    table = dynamo.Table(TABLE)
    result = table.scan(Limit=50)
    return jsonify({'products': result.get('Items', [])})

@app.post('/products')
def create_product():
    data = request.get_json()
    table = dynamo.Table(TABLE)
    item = {
        'productId': str(uuid.uuid4())[:8],
        'name':      data.get('name'),
        'price':     str(data.get('price', 0)),
        'category':  data.get('category', 'general')
    }
    table.put_item(Item=item)
    return jsonify(item), 201

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

**`requirements.txt`**:
```
flask
boto3
```

**`Dockerfile`**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8080
CMD ["python", "app.py"]
```

```bash
# Create ECR repo and push
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/eks-products

aws ecr create-repository \
  --repository-name serverless-labs/eks-products \
  --region $AWS_REGION

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -t eks-products --platform linux/amd64 .
docker tag eks-products:latest $ECR_URI:latest
docker push $ECR_URI:latest
```

```bash
# Create DynamoDB table for the app
aws dynamodb create-table \
  --table-name EKSProducts \
  --attribute-definitions AttributeName=productId,AttributeType=S \
  --key-schema AttributeName=productId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION
```

---

### Step 5 — Create Kubernetes Manifests

**`k8s/service-account.yaml`** — gives pods permission to access DynamoDB via IAM Roles for Service Accounts (IRSA):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: products-api-sa
  namespace: products-api
  annotations:
    # IRSA: link K8s service account to IAM role
    # Replace ACCOUNT_ID with your AWS account ID
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/LambdaLabRole
```

**`k8s/deployment.yaml`**:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: products-api
  namespace: products-api
  labels:
    app: products-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: products-api
  template:
    metadata:
      labels:
        app: products-api
    spec:
      serviceAccountName: products-api-sa
      containers:
        - name: products-api
          # Replace with your actual ECR URI
          image: ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/serverless-labs/eks-products:latest
          ports:
            - containerPort: 8080
          env:
            - name: TABLE_NAME
              value: "EKSProducts"
            - name: AWS_REGION
              value: "us-east-1"
          resources:
            requests:
              cpu: "256m"
              memory: "512Mi"
            limits:
              cpu: "512m"
              memory: "1Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 20
            periodSeconds: 15
```

**`k8s/service.yaml`**:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: products-api-svc
  namespace: products-api
spec:
  selector:
    app: products-api
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8080
  type: LoadBalancer
```

---

### Step 6 — Deploy to EKS

```bash
mkdir k8s

# Replace ACCOUNT_ID in the manifests
sed -i "s/ACCOUNT_ID/$AWS_ACCOUNT_ID/g" k8s/deployment.yaml
sed -i "s/ACCOUNT_ID/$AWS_ACCOUNT_ID/g" k8s/service-account.yaml

# Apply all manifests
kubectl apply -f k8s/

# Watch pods come up (Fargate = ~60s for first pod)
kubectl get pods -n products-api -w

# Get the LoadBalancer URL (takes ~2 minutes to provision)
kubectl get service products-api-svc -n products-api
```

---

### ✅ Verify — Pattern 24

```bash
# Get the external URL
LB_URL=$(kubectl get service products-api-svc -n products-api \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

echo "API URL: http://$LB_URL"

# Health check
curl http://$LB_URL/health

# Create a product
curl -X POST http://$LB_URL/products \
  -H "Content-Type: application/json" \
  -d '{"name":"EKS Widget","price":29.99,"category":"demo"}'

# List products
curl http://$LB_URL/products
```

**Kubernetes-specific checks:**

```bash
# Describe pods — confirm they show 'fargate' in the node name
kubectl describe pods -n products-api | grep "Node:"

# Check pod logs
kubectl logs -n products-api -l app=products-api --tail=20

# Scale to 4 replicas — each new pod gets its own Fargate compute
kubectl scale deployment products-api -n products-api --replicas=4
kubectl get pods -n products-api -w

# Scale back down
kubectl scale deployment products-api -n products-api --replicas=2
```

**Key insight:** Each pod descriptor shows a node name like `fargate-ip-10-0-x-x.us-east-1.compute.internal` — there are no EC2 worker nodes. Each pod IS its own isolated compute environment.

---

## Pattern 25: EKS on Fargate — Horizontal Pod Autoscaler (HPA)

> **Kubernetes-native auto-scaling with zero node management**

### What This Pattern Solves

One of Kubernetes' most powerful features is the Horizontal Pod Autoscaler — it automatically scales pods based on CPU, memory, or custom metrics. Combined with Fargate, every new pod gets its own compute slice automatically. This is pure serverless Kubernetes autoscaling: define your scaling rules in YAML, AWS handles everything else.

### Architecture

```
Traffic spike  →  HPA detects CPU > threshold  →  New pod scheduled
                                                         |
                                                  Fargate provisions
                                                  compute automatically
                                                         |
                                                  Pod starts, handles traffic
```

---

### Step 1 — Install the Kubernetes Metrics Server

HPA requires the metrics server to read pod CPU/memory usage:

```bash
# Install metrics server on EKS
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Wait for it to be ready (~60 seconds)
kubectl rollout status deployment/metrics-server -n kube-system

# Verify it works
kubectl top pods -n products-api
```

---

### Step 2 — Create a CPU-Based HPA

**`k8s/hpa.yaml`**:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: products-api-hpa
  namespace: products-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: products-api
  minReplicas: 1
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50    # Scale out when avg CPU > 50%
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30    # React quickly to load spikes
      policies:
        - type: Pods
          value: 2
          periodSeconds: 30             # Add up to 2 pods every 30 seconds
    scaleDown:
      stabilizationWindowSeconds: 120   # Wait 2 min before scaling down
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60             # Remove 1 pod per minute
```

```bash
kubectl apply -f k8s/hpa.yaml

# Monitor the HPA
kubectl get hpa -n products-api -w
```

---

### Step 3 — Create a Load Generator

**`k8s/load-generator.yaml`** — runs inside the cluster to generate CPU load:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: load-generator
  namespace: products-api
spec:
  containers:
    - name: load-generator
      image: busybox
      command:
        - /bin/sh
        - -c
        - |
          echo "Starting load generation..."
          while true; do
            wget -q -O- http://products-api-svc/products > /dev/null 2>&1
            wget -q -O- http://products-api-svc/health   > /dev/null 2>&1
          done
  restartPolicy: Never
```

```bash
# Start load generation
kubectl apply -f k8s/load-generator.yaml

# Watch HPA respond in real time (open a second terminal)
watch kubectl get hpa,pods -n products-api
```

---

### ✅ Verify — Pattern 25

```bash
# Terminal 1 — watch pods scale up
kubectl get pods -n products-api -w

# Terminal 2 — watch HPA metrics
kubectl describe hpa products-api-hpa -n products-api
```

1. Within 1–2 minutes of the load generator starting, HPA should scale pods from 1 → 2 → 4
2. Each new pod shows a new Fargate node — no EC2 instances were provisioned
3. Stop the load generator and watch scale-down:

```bash
kubectl delete pod load-generator -n products-api

# After ~2 minutes, pods scale back down to minReplicas: 1
kubectl get hpa -n products-api -w
```

**Compare with ECS Fargate auto-scaling:**

| Feature | ECS Fargate Auto-scaling | EKS Fargate HPA |
|---------|--------------------------|-----------------|
| Config | AWS Console / CloudFormation | YAML manifest |
| Metrics | CloudWatch | Metrics Server |
| Custom metrics | CloudWatch custom metrics | Prometheus / KEDA |
| Multi-cluster | Separate configs per cluster | Portable across any K8s |
| GitOps friendly | Limited | Native (kubectl apply) |

---

## Pattern 26: AWS App Runner — Zero-Config Container Hosting

> **Deploy a container as a web service in under 5 minutes**

### What This Pattern Solves

App Runner is the simplest serverless container option on AWS — simpler than ECS Fargate, far simpler than EKS. You point App Runner at an ECR image (or a GitHub repo), set CPU/memory, and get back a fully managed HTTPS URL. App Runner handles load balancing, auto-scaling, TLS certificates, health checks, and rolling deployments. Zero Kubernetes, zero ECS task definitions, zero ALB configuration.

### Architecture

```
ECR image  →  App Runner Service  →  Managed HTTPS URL
                    |
             (auto-scales 1→N instances on traffic)
             (scales to near-zero when idle)
```

### App Runner vs ECS Fargate — quick comparison

| | App Runner | ECS Fargate |
|--|-----------|-------------|
| Setup time | ~3 minutes | ~20 minutes |
| Load balancer | Included, automatic | Manual ALB setup |
| HTTPS | Automatic | Manual certificate |
| Auto-scaling | Automatic | Manual policy config |
| VPC integration | Optional | Standard |
| Custom domains | Yes | Via Route53 + ALB |
| Concurrency model | Per-instance (like Fargate) | Per-task |
| Best for | Simple web APIs, demos, MVPs | Complex microservices needing VPC/fine-grained control |

---

### Step 1 — Build and Push the App Runner Image

```bash
mkdir apprunner-lab && cd apprunner-lab
```

**`main.py`** — a FastAPI product catalogue service:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import boto3
import uuid
import os
from datetime import datetime

app = FastAPI(title="App Runner Products API", version="1.0.0")

dynamo = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
TABLE  = os.environ.get('TABLE_NAME', 'AppRunnerProducts')

class ProductRequest(BaseModel):
    name: str
    price: float
    category: str = "general"
    description: str = ""

@app.get("/")
def root():
    return {
        "service":   "App Runner Products API",
        "version":   "1.0.0",
        "runtime":   "AWS App Runner",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/products")
def list_products():
    table = dynamo.Table(TABLE)
    result = table.scan()
    return {"products": result.get("Items", []), "count": result.get("Count", 0)}

@app.post("/products", status_code=201)
def create_product(product: ProductRequest):
    table = dynamo.Table(TABLE)
    item = {
        "productId":   str(uuid.uuid4())[:8],
        "name":        product.name,
        "price":       str(product.price),
        "category":    product.category,
        "description": product.description,
        "createdAt":   datetime.utcnow().isoformat()
    }
    table.put_item(Item=item)
    return item

@app.get("/products/{product_id}")
def get_product(product_id: str):
    table = dynamo.Table(TABLE)
    result = table.get_item(Key={"productId": product_id})
    item = result.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return item

@app.delete("/products/{product_id}")
def delete_product(product_id: str):
    table = dynamo.Table(TABLE)
    table.delete_item(Key={"productId": product_id})
    return {"message": f"Product {product_id} deleted"}
```

**`requirements.txt`**:
```
fastapi
uvicorn[standard]
boto3
pydantic
```

**`Dockerfile`**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
export AR_ECR=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/apprunner

aws ecr create-repository \
  --repository-name serverless-labs/apprunner \
  --region $AWS_REGION

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -t apprunner-products --platform linux/amd64 .
docker tag apprunner-products:latest $AR_ECR:latest
docker push $AR_ECR:latest

# Create DynamoDB table
aws dynamodb create-table \
  --table-name AppRunnerProducts \
  --attribute-definitions AttributeName=productId,AttributeType=S \
  --key-schema AttributeName=productId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION
```

---

### Step 2 — Create the App Runner Service (Console)

1. Navigate to **App Runner → Create service**
2. Source:
   - Repository type: **Container registry**
   - Provider: **Amazon ECR**
   - Container image URI: browse and select `serverless-labs/apprunner:latest`
   - Deployment trigger: **Automatic** *(redeploys whenever you push a new image)*
3. Configure service:
   - Service name: `products-api`
   - CPU: `0.25 vCPU`
   - Memory: `0.5 GB`
   - Port: `8080`
   - Environment variables:
     - `TABLE_NAME` = `AppRunnerProducts`
     - `AWS_REGION` = `us-east-1`
4. Auto scaling:
   - Min instances: `1`
   - Max instances: `5`
   - Max concurrency: `100` *(scale out when a single instance handles > 100 concurrent requests)*
5. Health check:
   - Protocol: HTTP
   - Path: `/health`
6. Security — IAM role: `LambdaLabRole`
7. Click **Create & deploy** — takes ~3 minutes

---

### Step 3 — (Alternative) Deploy via AWS CLI

```bash
# Create an App Runner access role for ECR
aws iam create-role \
  --role-name AppRunnerECRRole \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"build.apprunner.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }' 2>/dev/null || true

aws iam attach-role-policy \
  --role-name AppRunnerECRRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess

ACCESS_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRRole \
  --query 'Role.Arn' --output text)

INSTANCE_ROLE_ARN=$(aws iam get-role --role-name LambdaLabRole \
  --query 'Role.Arn' --output text)

aws apprunner create-service \
  --service-name products-api \
  --source-configuration "{
    \"ImageRepository\": {
      \"ImageIdentifier\": \"$AR_ECR:latest\",
      \"ImageConfiguration\": {
        \"Port\": \"8080\",
        \"RuntimeEnvironmentVariables\": {
          \"TABLE_NAME\": \"AppRunnerProducts\",
          \"AWS_REGION\": \"$AWS_REGION\"
        }
      },
      \"ImageRepositoryType\": \"ECR\"
    },
    \"AutoDeploymentsEnabled\": true,
    \"AuthenticationConfiguration\": {
      \"AccessRoleArn\": \"$ACCESS_ROLE_ARN\"
    }
  }" \
  --instance-configuration "{
    \"Cpu\": \"0.25 vCPU\",
    \"Memory\": \"0.5 GB\",
    \"InstanceRoleArn\": \"$INSTANCE_ROLE_ARN\"
  }" \
  --health-check-configuration "{
    \"Protocol\": \"HTTP\",
    \"Path\": \"/health\"
  }" \
  --region $AWS_REGION

# Get the service URL
aws apprunner list-services \
  --query 'ServiceSummaryList[?ServiceName==`products-api`].ServiceUrl' \
  --output text \
  --region $AWS_REGION
```

---

### ✅ Verify — Pattern 26

```bash
# Get URL from console or CLI output — format: https://xxxx.us-east-1.awsapprunner.com
AR_URL="https://YOUR_SERVICE_URL"

# Root endpoint
curl $AR_URL/

# Health check
curl $AR_URL/health

# Create products
curl -X POST $AR_URL/products \
  -H "Content-Type: application/json" \
  -d '{"name":"App Runner Widget","price":19.99,"category":"demo"}'

curl -X POST $AR_URL/products \
  -H "Content-Type: application/json" \
  -d '{"name":"App Runner Pro","price":99.99,"category":"premium","description":"Top tier product"}'

# List products
curl $AR_URL/products

# Get individual product (use an ID from the list output)
curl $AR_URL/products/YOUR_PRODUCT_ID
```

**Notice:**
- The URL is already **HTTPS** — App Runner provisioned the TLS certificate automatically
- No ALB to configure, no target groups, no listeners
- Navigate to **App Runner → products-api → Logs** — see application logs without setting up CloudWatch manually
- Navigate to **App Runner → products-api → Metrics** — request count, latency, HTTP 2xx/5xx rates all pre-configured

**Test automatic redeployment:**
1. Change the version string in `main.py` to `"2.0.0"`
2. Rebuild and push: `docker build ... && docker push ...`
3. App Runner detects the new image and rolls out automatically — no manual trigger

---

## Pattern 27: App Runner + VPC Connector (Private Database Access)

> **App Runner connecting to private resources inside a VPC**

### What This Pattern Solves

By default App Runner runs outside your VPC — it cannot reach Aurora, ElastiCache, or other VPC resources. A **VPC Connector** bridges App Runner into your VPC, enabling private database connections while keeping the zero-ops simplicity of App Runner. This is the production-ready pattern: App Runner for the compute layer, Aurora Serverless v2 inside VPC for the data layer.

### Architecture

```
Internet  →  App Runner (managed HTTPS)
                   |
            VPC Connector
                   |
            Private Subnet
                   |
         RDS Proxy → Aurora Serverless v2
```

---

### Step 1 — Create the VPC Connector

```bash
# Get your default VPC and subnets
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=isDefault,Values=true" \
  --query "Vpcs[0].VpcId" --output text)

SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query "Subnets[*].SubnetId" \
  --output text | tr '\t' ',')

SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=default" \
  --query "SecurityGroups[0].GroupId" --output text)

echo "VPC: $VPC_ID"
echo "Subnets: $SUBNET_IDS"
echo "SG: $SG_ID"

# Create the VPC Connector
aws apprunner create-vpc-connector \
  --vpc-connector-name lab-vpc-connector \
  --subnets $(echo $SUBNET_IDS | tr ',' ' ') \
  --security-groups $SG_ID \
  --region $AWS_REGION

VPC_CONNECTOR_ARN=$(aws apprunner list-vpc-connectors \
  --query "VpcConnectors[?VpcConnectorName=='lab-vpc-connector'].VpcConnectorArn" \
  --output text \
  --region $AWS_REGION)

echo "VPC Connector ARN: $VPC_CONNECTOR_ARN"
```

---

### Step 2 — Create the Application with Direct DB Access

```bash
mkdir apprunner-vpc-lab && cd apprunner-vpc-lab
```

**`main.py`** — App Runner service with Aurora connectivity via VPC:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pymysql
import os
import uuid
from datetime import datetime
from contextlib import contextmanager

app = FastAPI(title="App Runner VPC API")

DB_CONFIG = {
    'host':        os.environ.get('DB_HOST', ''),        # RDS Proxy endpoint
    'user':        os.environ.get('DB_USER', 'admin'),
    'password':    os.environ.get('DB_PASSWORD', ''),
    'database':    os.environ.get('DB_NAME', 'labdb'),
    'port':        int(os.environ.get('DB_PORT', '3306')),
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': 5
}

@contextmanager
def get_db():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS catalogue (
                    id          VARCHAR(36)    PRIMARY KEY,
                    name        VARCHAR(100)   NOT NULL,
                    price       DECIMAL(10,2),
                    category    VARCHAR(50),
                    in_stock    BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

class CatalogueItem(BaseModel):
    name: str
    price: float
    category: str = "general"
    in_stock: bool = True

@app.on_event("startup")
def startup():
    try:
        init_db()
        print("Database initialised via VPC Connector")
    except Exception as e:
        print(f"DB init warning: {e}")

@app.get("/health")
def health():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "healthy", "db": "connected via VPC"}
    except Exception as e:
        return {"status": "degraded", "db": str(e)}

@app.get("/catalogue")
def list_catalogue():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM catalogue ORDER BY created_at DESC")
            items = cur.fetchall()
    return {"items": items, "count": len(items)}

@app.post("/catalogue", status_code=201)
def add_item(item: CatalogueItem):
    item_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO catalogue (id, name, price, category, in_stock) VALUES (%s,%s,%s,%s,%s)",
                (item_id, item.name, item.price, item.category, item.in_stock)
            )
    return {"id": item_id, **item.dict()}

@app.get("/catalogue/stats")
def catalogue_stats():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT category,
                       COUNT(*)    AS item_count,
                       AVG(price)  AS avg_price,
                       SUM(price)  AS total_value,
                       SUM(in_stock) AS in_stock_count
                FROM catalogue
                GROUP BY category
            """)
            stats = cur.fetchall()
    return {"stats": stats}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

**`requirements.txt`**:
```
fastapi
uvicorn[standard]
pymysql
pydantic
```

**`Dockerfile`** — same as Pattern 26, just different app code.

```bash
export AR_VPC_ECR=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/apprunner-vpc

aws ecr create-repository \
  --repository-name serverless-labs/apprunner-vpc \
  --region $AWS_REGION

docker build -t apprunner-vpc --platform linux/amd64 .
docker tag apprunner-vpc:latest $AR_VPC_ECR:latest
docker push $AR_VPC_ECR:latest
```

---

### Step 3 — Create App Runner Service with VPC Connector

```bash
INSTANCE_ROLE_ARN=$(aws iam get-role --role-name LambdaLabRole \
  --query 'Role.Arn' --output text)

ACCESS_ROLE_ARN=$(aws iam get-role --role-name AppRunnerECRRole \
  --query 'Role.Arn' --output text)

# Retrieve Aurora credentials from Secrets Manager
SECRET_ARN=$(aws secretsmanager list-secrets \
  --query "SecretList[?contains(Name,'serverless-labs-aurora')].ARN" \
  --output text)

DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id $SECRET_ARN \
  --query 'SecretString' --output text | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")

RDS_PROXY_ENDPOINT=$(aws rds describe-db-proxies \
  --query "DBProxies[?DBProxyName=='lambda-aurora-proxy'].Endpoint" \
  --output text)

aws apprunner create-service \
  --service-name catalogue-api-vpc \
  --source-configuration "{
    \"ImageRepository\": {
      \"ImageIdentifier\": \"$AR_VPC_ECR:latest\",
      \"ImageConfiguration\": {
        \"Port\": \"8080\",
        \"RuntimeEnvironmentVariables\": {
          \"DB_HOST\":     \"$RDS_PROXY_ENDPOINT\",
          \"DB_USER\":     \"admin\",
          \"DB_PASSWORD\": \"$DB_PASSWORD\",
          \"DB_NAME\":     \"labdb\"
        }
      },
      \"ImageRepositoryType\": \"ECR\"
    },
    \"AutoDeploymentsEnabled\": true,
    \"AuthenticationConfiguration\": {
      \"AccessRoleArn\": \"$ACCESS_ROLE_ARN\"
    }
  }" \
  --instance-configuration "{
    \"Cpu\": \"0.25 vCPU\",
    \"Memory\": \"0.5 GB\",
    \"InstanceRoleArn\": \"$INSTANCE_ROLE_ARN\"
  }" \
  --network-configuration "{
    \"EgressConfiguration\": {
      \"EgressType\": \"VPC\",
      \"VpcConnectorArn\": \"$VPC_CONNECTOR_ARN\"
    }
  }" \
  --health-check-configuration "{
    \"Protocol\": \"HTTP\",
    \"Path\": \"/health\"
  }" \
  --region $AWS_REGION
```

---

### ✅ Verify — Pattern 27

```bash
AR_VPC_URL="https://YOUR_CATALOGUE_SERVICE_URL"

# Health check — should show "db": "connected via VPC"
curl $AR_VPC_URL/health

# Add catalogue items
curl -X POST $AR_VPC_URL/catalogue \
  -H "Content-Type: application/json" \
  -d '{"name":"Aurora Widget","price":49.99,"category":"electronics"}'

curl -X POST $AR_VPC_URL/catalogue \
  -H "Content-Type: application/json" \
  -d '{"name":"Aurora Chair","price":299.99,"category":"furniture"}'

curl -X POST $AR_VPC_URL/catalogue \
  -H "Content-Type: application/json" \
  -d '{"name":"Aurora Keyboard","price":89.99,"category":"electronics"}'

# List catalogue
curl $AR_VPC_URL/catalogue

# Category stats (SQL GROUP BY running on Aurora Serverless)
curl $AR_VPC_URL/catalogue/stats
```

**Verify VPC connectivity:**
1. Navigate to **App Runner → catalogue-api-vpc → Configuration → Networking**
2. Confirm the VPC Connector is attached and shows your VPC ID
3. In **RDS → Proxies → lambda-aurora-proxy → Monitoring**, check `ClientConnections` — you should see connections from the App Runner service

**Key difference from Pattern 26:**
- Pattern 26: App Runner → DynamoDB (no VPC, works out of the box)
- Pattern 27: App Runner → VPC Connector → RDS Proxy → Aurora Serverless (private, SQL, relational)

---

## Updated Full Pattern Summary — All 3 Days

| Day | # | Pattern | Core Services |
|-----|---|---------|--------------|
| 1 | 1 | API-Driven Backend | Lambda · API GW · DynamoDB |
| 1 | 2 | Secure API | Cognito · Lambda Authorizer |
| 1 | 3 | Event-Driven Microservice | EventBridge |
| 1 | 4 | Queue-Based Load Leveling | SQS · DLQ |
| 1 | 5 | Fan-Out | SNS |
| 1 | 6 | File Processing | S3 Events |
| 2 | 7 | Stream Processing | Kinesis · Lambda |
| 2 | 8 | Orchestration | Step Functions |
| 2 | 9 | Choreography | EventBridge rules chain |
| 2 | 10 | Scheduled Automation | EventBridge Scheduler |
| 2 | 11 | Serverless RAG | Bedrock · Embeddings |
| 2 | 12 | Agentic AI | Bedrock Tool Use |
| 2 | 13 | Document Intelligence | Textract |
| 2 | 14 | Serverless ETL | S3 · Athena |
| 2 | 15 | Event Sourcing & CQRS | DynamoDB Streams |
| 3 | 16 | Containerised Lambda | Lambda · ECR |
| 3 | 17 | Fargate Task | ECS Fargate · ECR |
| 3 | 18 | Fargate HTTP API | ECS Fargate · ALB |
| 3 | 19 | Aurora Serverless v2 | Aurora · RDS Data API |
| 3 | 20 | RDS Proxy + Lambda | RDS Proxy · Aurora |
| 3 | 21 | DynamoDB → Aurora Sync | DynamoDB Streams · Aurora |
| 3 | 22 | Fargate + Aurora | ECS Fargate · RDS Proxy · Aurora |
| 3 | 23 | ElastiCache + Lambda | ElastiCache Serverless · Redis |
| 3 | 24 | EKS on Fargate — Microservice | EKS · Fargate · ECR |
| 3 | 25 | EKS on Fargate — HPA | EKS · Fargate · HPA · Metrics Server |
| 3 | 26 | App Runner — Zero Config | App Runner · ECR · DynamoDB |
| 3 | 27 | App Runner + VPC Connector | App Runner · VPC · RDS Proxy · Aurora |

---

## Updated Decision Framework — Serverless Container Compute

```
Do you use Kubernetes already?
  └─ Yes → EKS on Fargate
               └─ Need HPA / custom operators / Helm? → EKS + Fargate (Patterns 24–25)
               └─ Simpler workload, willing to leave K8s? → ECS Fargate (Pattern 18)
  └─ No  →
       How much do you want to configure?
         └─ As little as possible → App Runner (Patterns 26–27)
              └─ Only needs DynamoDB / public AWS services? → App Runner (Pattern 26)
              └─ Needs VPC resources (Aurora, ElastiCache)? → App Runner + VPC Connector (Pattern 27)
         └─ Need fine-grained VPC / IAM / networking control → ECS Fargate (Patterns 17–18, 22)

Short task, exits on completion (< 15 min)?  → Lambda (Patterns 1–2, 16)
Short task, exits on completion (> 15 min)?  → Fargate Task (Pattern 17)
Always-on HTTP service?                       → App Runner OR ECS Fargate Service OR EKS Fargate
```

---

## Troubleshooting — EKS and App Runner

### EKS Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `Pending` pods for > 5 min | No matching Fargate profile | Check namespace/label match in Fargate profile |
| `exec format error` in pod | Image built for wrong arch | Rebuild with `--platform linux/amd64` |
| `ImagePullBackOff` | Missing ECR permissions on node IAM role | Attach `AmazonEC2ContainerRegistryReadOnly` to the EKS node role |
| Metrics server `CrashLoopBackOff` | TLS issue on Fargate | Add `--kubelet-insecure-tls` arg to metrics-server deployment |
| HPA shows `<unknown>/50%` | Metrics server not ready | Wait 2–3 min after install; verify with `kubectl top pods` |
| `kubectl` auth failure | kubeconfig stale | Re-run `aws eks update-kubeconfig --name $CLUSTER_NAME` |

### App Runner Issues

| Error | Cause | Fix |
|-------|-------|-----|
| Service stuck in `OPERATION_IN_PROGRESS` | First deploy takes ~3 min | Wait; check Logs tab for errors |
| `Health check failed` | Wrong health check path or port | Confirm path is `/health` and port matches `EXPOSE` in Dockerfile |
| `AccessDenied` on DynamoDB | Instance role missing or wrong | Set instance role to `LambdaLabRole` in service configuration |
| VPC DB connection timeout | Security group not allowing inbound | Add inbound TCP 3306 rule from App Runner VPC Connector security group |
| `Cannot connect to ECR` | Missing ECR access role | Create `AppRunnerECRRole` with `AWSAppRunnerServicePolicyForECRAccess` |
| Auto-deploy not triggering | ECR scan on push setting | Verify `AutoDeploymentsEnabled: true` and the correct ECR repo is referenced |