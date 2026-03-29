# Day 3 — Serverless Containers & Databases

> **Patterns 16–23 | Duration: 2.5–3 hours**  
> Extend the serverless toolkit with container-based compute and managed serverless databases. Each lab is fully independent.

---

## Day 3 at a Glance

| # | Pattern | Core Services | What You Build |
|---|---------|--------------|----------------|
| 16 | Containerised Lambda | Lambda + ECR + Docker | Lambda running a custom container image |
| 17 | AWS Fargate Task | ECS Fargate + ECR | A long-running container with no server management |
| 18 | Fargate + API Gateway | ECS Fargate + ALB + API GW | HTTP API backed by a containerised microservice |
| 19 | Aurora Serverless v2 | Aurora Serverless v2 + Lambda | Auto-scaling relational DB called from Lambda |
| 20 | RDS Proxy + Lambda | RDS Aurora + RDS Proxy + Lambda | Connection-pooled Lambda-to-RDS integration |
| 21 | Lambda + DynamoDB Streams → Aurora | DynamoDB + Streams + Lambda + Aurora | Sync NoSQL events into a relational read model |
| 22 | Fargate + Aurora Serverless | ECS Fargate + Aurora Serverless v2 | Containerised app with a serverless relational backend |
| 23 | ElastiCache Serverless + Lambda | ElastiCache (Redis) Serverless + Lambda | Sub-millisecond caching layer in front of RDS |

---

## Key Concepts Before You Start

### Containers vs. Lambda — When to Choose Which

| Dimension | Lambda | Fargate Container |
|-----------|--------|-------------------|
| Max runtime | 15 minutes | Unlimited |
| Max memory | 10 GB | 120 GB |
| Cold start | Yes (ms–seconds) | Yes (seconds–minutes) |
| Custom runtimes | Via layers or container | Any language/framework |
| Packaging | ZIP or container image | Container image |
| Scaling | Automatic, per-request | Task-level, configured |
| Best for | Short bursts, event-driven | Long-running, heavy workloads |

### Serverless Databases Covered Today

| Service | Type | Serverless Characteristic |
|---------|------|--------------------------|
| Aurora Serverless v2 | Relational (MySQL/PostgreSQL) | Scales ACUs from 0.5 to 128 automatically |
| RDS Proxy | Connection pool | Absorbs Lambda connection bursts |
| ElastiCache Serverless | Redis / Valkey | Pay-per-use, auto-scales, no cluster management |
| DynamoDB | NoSQL key-value | Already covered in Days 1–2 |

---

## Start-of-Day Setup (10 minutes)

### Verify Prerequisites

```bash
# Confirm AWS CLI and identity
aws sts get-caller-identity

# Confirm Docker is available (needed for container labs)
docker --version
```

> **Note:** AWS CloudShell does NOT have Docker. For container labs (Patterns 16–18, 22), use one of:
> - Your local machine with Docker + AWS CLI configured
> - AWS Cloud9 (has Docker pre-installed)
> - An EC2 instance with Docker

### Set Up Cloud9 (if needed)

1. Navigate to **Cloud9 → Create environment**
2. Name: `ServerlessLabs`
3. Instance type: `t3.small`
4. Platform: Amazon Linux 2023
5. Click **Create** — takes ~2 minutes
6. Click **Open** to launch the IDE terminal

```bash
# In Cloud9 terminal — verify Docker
docker --version
aws sts get-caller-identity
```

### Extend LambdaLabRole Permissions

```bash
# Add ECR and ECS permissions needed for container labs
aws iam attach-role-policy \
  --role-name LambdaLabRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess

aws iam attach-role-policy \
  --role-name LambdaLabRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

aws iam attach-role-policy \
  --role-name LambdaLabRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonRDSFullAccess

aws iam attach-role-policy \
  --role-name LambdaLabRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonElastiCacheFullAccess

# Add EventBridge PutEvents (if not already added)
aws iam put-role-policy \
  --role-name LambdaLabRole \
  --policy-name EventBridgePutEvents \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "events:PutEvents", "Resource": "*"}]
  }'

echo "Permissions updated"
```

### Get Your Account ID (you will need it throughout)

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
echo "Account: $AWS_ACCOUNT_ID  Region: $AWS_REGION"
```

---

## Pattern 16: Containerised Lambda

> **Custom Container Images for Lambda Functions**

### What This Pattern Solves

Lambda's standard ZIP deployment has limits: 250 MB unzipped, no custom system libraries, no exotic runtimes. Container image support removes these limits — up to 10 GB images, any runtime, any system dependency. Use this when you need: ML model files, compiled binaries, custom Python/Node versions, or complex dependency trees.

### Architecture

```
ECR (Container Image)  →  Lambda (container runtime)  →  DynamoDB / any target
```

---

### Step 1 — Create an ECR Repository

```bash
aws ecr create-repository \
  --repository-name serverless-labs/lambda-container \
  --region $AWS_REGION

# Note the repositoryUri from the output — e.g.:
# 975454609718.dkr.ecr.us-east-1.amazonaws.com/serverless-labs/lambda-container
export ECR_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/lambda-container
```

---

### Step 2 — Create the Container Application

Create a working directory and the required files:

```bash
mkdir lambda-container-lab && cd lambda-container-lab
```

**`app.py`** — the Lambda handler:

```python
import json
import platform
import sys
import boto3

def lambda_handler(event, context):
    # Demonstrate custom runtime capabilities
    action = event.get('action', 'info')

    if action == 'info':
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Running in a container!',
                'pythonVersion': sys.version,
                'platform': platform.platform(),
                'architecture': platform.machine(),
                'customLibraryAvailable': True
            })
        }

    elif action == 'process':
        # Simulate heavy processing that benefits from container packaging
        data = event.get('data', [1, 2, 3, 4, 5])
        result = {
            'input': data,
            'sum': sum(data),
            'mean': sum(data) / len(data),
            'max': max(data),
            'min': min(data)
        }
        return {'statusCode': 200, 'body': json.dumps(result)}

    return {'statusCode': 400, 'body': json.dumps({'error': 'Unknown action'})}
```

**`requirements.txt`**:

```
boto3
numpy
pandas
```

**`Dockerfile`**:

```dockerfile
# Use AWS-provided Lambda base image for Python 3.12
FROM public.ecr.aws/lambda/python:3.12

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Set the Lambda handler
CMD ["app.lambda_handler"]
```

---

### Step 3 — Build, Tag, and Push the Image

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Build the image
docker build -t lambda-container-lab .

# Tag for ECR
docker tag lambda-container-lab:latest $ECR_URI:latest

# Push to ECR
docker push $ECR_URI:latest

echo "Image pushed: $ECR_URI:latest"
```

---

### Step 4 — Create Lambda from Container Image

1. Navigate to **Lambda → Create function**
2. Select **Container image**
3. Function name: `ContainerLambda`
4. Container image URI: click **Browse images** → select your ECR repo → `latest`
5. Execution role: `LambdaLabRole`
6. Click **Create function**
7. Set memory to **512 MB** and timeout to **30 seconds** (Configuration → General)

---

### ✅ Verify — Pattern 16

Test with:
```json
{"action": "info"}
```
```json
{"action": "process", "data": [10, 25, 8, 42, 17, 33]}
```

- Confirm the response shows Python version and platform details
- In ECR console, navigate to your repository — see the pushed image with size and digest
- Note: first invocation may be slower (cold start includes image pull); subsequent invocations are fast

**Key difference from ZIP Lambda:** Your container has `numpy` and `pandas` available — libraries that would exceed ZIP size limits if combined with other dependencies.

---

## Pattern 17: AWS Fargate Task

> **Long-Running Serverless Containers**

### What This Pattern Solves

Some workloads don't fit Lambda's 15-minute limit: batch jobs, video encoding, ML training runs, data migrations. Fargate runs containers on-demand with no EC2 instances to manage. You define CPU and memory, Fargate handles the rest. Pay only for the seconds your task runs.

### Architecture

```
EventBridge / API call  →  ECS Fargate Task  →  S3 / DynamoDB / External APIs
```

---

### Step 1 — Create an ECS Cluster

1. Navigate to **ECS → Clusters → Create cluster**
2. Cluster name: `ServerlessLabs`
3. Infrastructure: ✅ **AWS Fargate (serverless)** only (uncheck EC2)
4. Click **Create**

---

### Step 2 — Create an ECR Repository and Push a Fargate Image

```bash
cd ~
mkdir fargate-task-lab && cd fargate-task-lab

# Create ECR repo
aws ecr create-repository \
  --repository-name serverless-labs/fargate-task \
  --region $AWS_REGION

export FARGATE_ECR=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/fargate-task
```

**`task.py`** — the Fargate task script:

```python
import boto3
import json
import os
import time
from datetime import datetime

def run_batch_job():
    job_id = os.environ.get('JOB_ID', 'job-001')
    bucket = os.environ.get('OUTPUT_BUCKET', '')
    
    print(f"[{datetime.utcnow().isoformat()}] Starting batch job: {job_id}")
    
    # Simulate a long-running batch process
    results = []
    for batch in range(1, 6):
        print(f"  Processing batch {batch}/5...")
        time.sleep(2)  # Simulate work
        results.append({
            'batch': batch,
            'recordsProcessed': batch * 1000,
            'timestamp': datetime.utcnow().isoformat()
        })
    
    summary = {
        'jobId': job_id,
        'completedAt': datetime.utcnow().isoformat(),
        'totalBatches': len(results),
        'totalRecords': sum(r['recordsProcessed'] for r in results),
        'batches': results
    }
    
    print(f"Job complete: {summary['totalRecords']} records processed")
    
    # Write result to S3 if bucket provided
    if bucket:
        s3 = boto3.client('s3')
        s3.put_object(
            Bucket=bucket,
            Key=f'results/{job_id}.json',
            Body=json.dumps(summary, indent=2)
        )
        print(f"Results written to s3://{bucket}/results/{job_id}.json")
    
    return summary

if __name__ == '__main__':
    run_batch_job()
```

**`Dockerfile`**:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install boto3

COPY task.py .

CMD ["python", "task.py"]
```

```bash
# Build and push
docker build -t fargate-task .
docker tag fargate-task:latest $FARGATE_ECR:latest

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker push $FARGATE_ECR:latest
```

---

### Step 3 — Create a Task Definition

1. Navigate to **ECS → Task definitions → Create new task definition**
2. Task definition family: `BatchJobTask`
3. Infrastructure: **AWS Fargate**
4. CPU: `.25 vCPU` | Memory: `0.5 GB`
5. Task role: `LambdaLabRole` *(allows S3 writes from within the container)*
6. Task execution role: Create new → name it `ecsTaskExecutionRole` (or use existing)
7. Under **Container**:
   - Name: `batch-job`
   - Image URI: `YOUR_FARGATE_ECR:latest`
   - Essential: Yes
8. Click **Create**

---

### Step 4 — Run the Fargate Task

1. Navigate to **ECS → Clusters → ServerlessLabs → Tasks → Run new task**
2. Compute options: **Launch type** → Fargate
3. Task definition: `BatchJobTask` → latest revision
4. Networking: choose your **default VPC** and any subnet, assign public IP: **ENABLED**
5. Click **Create**

---

### ✅ Verify — Pattern 17

1. In **ECS → Clusters → ServerlessLabs → Tasks** — watch the task status go: `PROVISIONING → PENDING → RUNNING → STOPPED`
2. Click the task → **Logs tab** — see the batch processing output including "Job complete"
3. The task exits cleanly with code 0 (shown in **Stopped reason**)

**Key insight:** The container ran for ~10 seconds and stopped — Fargate billed only for that compute time. No EC2 instance was provisioned, patched, or terminated by you.

---

## Pattern 18: Fargate + API Gateway (HTTP Microservice)

> **Containerised HTTP Microservice with Persistent Compute**

### What This Pattern Solves

When you need an always-on HTTP service with containers — too much logic for Lambda, or requires WebSockets, long connections, or frameworks like FastAPI/Express — run it on Fargate with an Application Load Balancer. API Gateway v2 can sit in front for auth, throttling, and unified API management.

### Architecture

```
Client  →  API Gateway (HTTP API)  →  ALB  →  ECS Fargate Service  →  Aurora Serverless
```

---

### Step 1 — Create the FastAPI Application

```bash
mkdir fargate-api-lab && cd fargate-api-lab
```

**`main.py`**:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import boto3
import json
import os
import uvicorn
from datetime import datetime

app = FastAPI(title="Serverless Container API", version="1.0.0")

# In production this would connect to Aurora Serverless
# For the lab we use DynamoDB for simplicity
dynamo = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
TABLE_NAME = os.environ.get('TABLE_NAME', 'Orders')

class Order(BaseModel):
    customerId: str
    product: str
    quantity: int
    price: float

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/orders")
def list_orders():
    try:
        table = dynamo.Table(TABLE_NAME)
        result = table.scan(Limit=20)
        return {"orders": result.get('Items', []), "count": result.get('Count', 0)}
    except Exception as e:
        return {"orders": [], "note": str(e)}

@app.post("/orders")
def create_order(order: Order):
    import uuid
    table = dynamo.Table(TABLE_NAME)
    item = {
        'orderId': str(uuid.uuid4())[:8],
        'customerId': order.customerId,
        'product': order.product,
        'quantity': order.quantity,
        'price': str(order.price),
        'createdAt': datetime.utcnow().isoformat(),
        'status': 'pending'
    }
    table.put_item(Item=item)
    return {"message": "Order created", "order": item}

@app.get("/orders/{order_id}")
def get_order(order_id: str):
    table = dynamo.Table(TABLE_NAME)
    result = table.get_item(Key={'orderId': order_id})
    item = result.get('Item')
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    return item

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
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

CMD ["python", "main.py"]
```

---

### Step 2 — Create DynamoDB Table and Push Image

```bash
# Create Orders table
aws dynamodb create-table \
  --table-name Orders \
  --attribute-definitions AttributeName=orderId,AttributeType=S \
  --key-schema AttributeName=orderId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION

# Build and push image
aws ecr create-repository \
  --repository-name serverless-labs/fargate-api \
  --region $AWS_REGION

export FARGATE_API_ECR=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/fargate-api

docker build -t fargate-api .
docker tag fargate-api:latest $FARGATE_API_ECR:latest
docker push $FARGATE_API_ECR:latest
```

---

### Step 3 — Create ECS Service with ALB

1. Navigate to **ECS → Clusters → ServerlessLabs → Services → Create**
2. Compute options: **Launch type → Fargate**
3. Task definition: create a new one first:
   - Family: `FargateAPITask`
   - CPU: `.5 vCPU` | Memory: `1 GB`
   - Container name: `api`, Image: `YOUR_FARGATE_API_ECR:latest`
   - Port mapping: Container port `8080`, Protocol `TCP`
   - Environment variable: `TABLE_NAME` = `Orders`
4. Back in **Create Service**:
   - Service name: `orders-api-service`
   - Desired tasks: `1`
   - Load balancing: **Application Load Balancer** → Create new ALB
   - ALB name: `fargate-orders-alb`
   - Target group: create new, health check path `/health`
5. Click **Create**

---

### ✅ Verify — Pattern 18

1. Wait ~3 minutes for the service to reach **RUNNING** state
2. Find the ALB DNS name: **EC2 → Load Balancers → fargate-orders-alb → DNS name**

```bash
ALB_DNS="YOUR_ALB_DNS_HERE"

# Health check
curl http://$ALB_DNS/health

# Create an order
curl -X POST http://$ALB_DNS/orders \
  -H "Content-Type: application/json" \
  -d '{"customerId":"cust-001","product":"Widget","quantity":3,"price":9.99}'

# List orders
curl http://$ALB_DNS/orders
```

3. In **ECS → Clusters → ServerlessLabs → Services → orders-api-service → Tasks** — confirm the task is running

---

## Pattern 19: Aurora Serverless v2

> **Auto-Scaling Relational Database with Zero Idle Cost**

### What This Pattern Solves

Traditional RDS requires you to choose an instance size upfront. Aurora Serverless v2 scales capacity automatically in fine-grained increments (0.5 ACU steps) from near-zero to 128 ACUs within seconds. You pay only for the ACUs consumed. Perfect for variable workloads, dev/test environments, and any app where relational data is needed without fixed DB costs.

### Architecture

```
Lambda  →  VPC  →  Aurora Serverless v2 (MySQL/PostgreSQL)
```

> **Note:** Aurora runs inside a VPC. Lambda must also be in the same VPC (or use RDS Data API which skips VPC). This lab uses the **RDS Data API** to keep setup simple — no VPC configuration required.

---

### Step 1 — Create Aurora Serverless v2 Cluster

1. Navigate to **RDS → Create database**
2. Engine: **Aurora (MySQL Compatible)**
3. Engine version: Aurora MySQL 3.x (latest)
4. Templates: **Dev/Test** *(reduces minimum ACUs for cost)*
5. DB cluster identifier: `serverless-labs-aurora`
6. Credentials:
   - Master username: `admin`
   - Managed in AWS Secrets Manager: ✅ Yes *(auto-generates and stores password)*
7. Instance configuration:
   - DB instance class: **Serverless v2**
   - Min ACUs: `0.5` | Max ACUs: `4`
8. Connectivity:
   - VPC: default
   - Public access: **Yes** *(for lab simplicity — in production, keep private)*
9. Additional configuration:
   - Initial database name: `labdb`
   - ✅ Enable RDS Data API *(critical — allows Lambda to call Aurora without VPC)*
10. Click **Create database** — takes ~5 minutes

---

### Step 2 — Get Connection Details

```bash
# Get the cluster ARN
aws rds describe-db-clusters \
  --query "DBClusters[?DBClusterIdentifier=='serverless-labs-aurora'].DBClusterArn" \
  --output text

# Get the Secrets Manager ARN (for the DB password)
aws secretsmanager list-secrets \
  --query "SecretList[?contains(Name,'serverless-labs-aurora')].ARN" \
  --output text
```

Note both ARNs — you will need them in the Lambda code.

---

### Step 3 — Create the Aurora Lambda

Create Lambda: Name `AuroraLambda`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **30 seconds**

Replace `YOUR_CLUSTER_ARN` and `YOUR_SECRET_ARN`:

```python
import json
import boto3

rds_data = boto3.client('rds-data')

CLUSTER_ARN = 'YOUR_CLUSTER_ARN'   # Replace
SECRET_ARN  = 'YOUR_SECRET_ARN'    # Replace
DATABASE    = 'labdb'

def execute_sql(sql, parameters=None):
    kwargs = {
        'resourceArn': CLUSTER_ARN,
        'secretArn':   SECRET_ARN,
        'database':    DATABASE,
        'sql':         sql,
        'formatRecordsAs': 'JSON'
    }
    if parameters:
        kwargs['parameters'] = parameters
    return rds_data.execute_statement(**kwargs)

def lambda_handler(event, context):
    action = event.get('action', 'list')

    # Create table on first run
    execute_sql("""
        CREATE TABLE IF NOT EXISTS products (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            name        VARCHAR(100) NOT NULL,
            price       DECIMAL(10,2) NOT NULL,
            category    VARCHAR(50),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    if action == 'insert':
        execute_sql(
            "INSERT INTO products (name, price, category) VALUES (:name, :price, :category)",
            parameters=[
                {'name': 'name',     'value': {'stringValue': event['name']}},
                {'name': 'price',    'value': {'doubleValue': float(event['price'])}},
                {'name': 'category', 'value': {'stringValue': event.get('category', 'general')}}
            ]
        )
        return {'status': 'inserted', 'product': event['name']}

    elif action == 'list':
        result = execute_sql("SELECT * FROM products ORDER BY created_at DESC LIMIT 20")
        rows = json.loads(result.get('formattedRecords', '[]'))
        return {'products': rows, 'count': len(rows)}

    elif action == 'aggregate':
        result = execute_sql("""
            SELECT category,
                   COUNT(*)       AS product_count,
                   AVG(price)     AS avg_price,
                   MIN(price)     AS min_price,
                   MAX(price)     AS max_price
            FROM products
            GROUP BY category
            ORDER BY product_count DESC
        """)
        rows = json.loads(result.get('formattedRecords', '[]'))
        return {'summary': rows}

    return {'error': 'Unknown action'}
```

---

### ✅ Verify — Pattern 19

```json
// Insert products
{"action": "insert", "name": "Laptop", "price": 999.99, "category": "electronics"}
{"action": "insert", "name": "Desk Chair", "price": 349.99, "category": "furniture"}
{"action": "insert", "name": "Keyboard", "price": 79.99, "category": "electronics"}
{"action": "insert", "name": "Monitor", "price": 449.99, "category": "electronics"}
{"action": "insert", "name": "Bookshelf", "price": 129.99, "category": "furniture"}

// List all products
{"action": "list"}

// Aggregation query
{"action": "aggregate"}
```

- Confirm the aggregate returns category-level stats showing electronics vs furniture counts and average prices
- In **RDS Console → serverless-labs-aurora → Monitoring** — watch ACUs scale from 0.5 upward during queries, then drop back after idle

**Key difference from standard RDS:** With Aurora Serverless v2, you paid only for the seconds queries were executing. A traditional `db.t3.medium` instance would cost ~$50/month whether idle or not.

---

## Pattern 20: RDS Proxy + Lambda

> **Connection Pooling for Lambda-to-RDS Integration**

### What This Pattern Solves

Lambda functions can create thousands of concurrent database connections during traffic spikes, exhausting the RDS connection limit and causing failures. RDS Proxy sits between Lambda and RDS, maintaining a warm pool of connections and multiplexing thousands of Lambda invocations through a small number of actual DB connections.

### Architecture

```
Lambda (1000s of concurrent invocations)  →  RDS Proxy (connection pool)  →  Aurora / RDS
```

---

### Step 1 — Create the RDS Proxy

> **Pre-requisite:** Requires the Aurora cluster from Pattern 19 to be running.

1. Navigate to **RDS → Proxies → Create proxy**
2. Proxy identifier: `lambda-aurora-proxy`
3. Engine family: **MySQL**
4. Database: select `serverless-labs-aurora`
5. Connectivity:
   - Secrets Manager ARN: select the Aurora secret from Pattern 19
   - IAM role: Create new *(AWS creates a role with Secrets Manager access)*
6. VPC: default
7. Click **Create proxy** — takes ~5 minutes

---

### Step 2 — Update Lambda to Use the Proxy Endpoint

Once the proxy status is **Available**, copy its **Endpoint**.

Update the `CLUSTER_ARN` in your `AuroraLambda` function — or create a new Lambda `RDSProxyLambda` that demonstrates connection efficiency:

```python
import json
import boto3
import os

rds_data = boto3.client('rds-data')

# Using RDS Data API still — but in a real VPC setup, you'd use
# the proxy endpoint as the host in a direct PyMySQL connection.
# This lab demonstrates the proxy architecture and monitoring.

CLUSTER_ARN = 'YOUR_CLUSTER_ARN'
SECRET_ARN  = 'YOUR_SECRET_ARN'
DATABASE    = 'labdb'
PROXY_ENDPOINT = 'YOUR_PROXY_ENDPOINT'  # Note: Data API uses cluster ARN, not proxy endpoint

def lambda_handler(event, context):
    """
    Simulate 10 rapid sequential queries — in production, these would
    come from 10 concurrent Lambda invocations hitting the proxy.
    The proxy reuses DB connections rather than opening 10 new ones.
    """
    results = []
    for i in range(10):
        resp = rds_data.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql="SELECT COUNT(*) as product_count FROM products",
            formatRecordsAs='JSON'
        )
        rows = json.loads(resp.get('formattedRecords', '[]'))
        results.append({'query': i+1, 'result': rows})
    
    print(f'Completed {len(results)} queries via connection pool')
    print(f'Proxy endpoint (for direct connections): {PROXY_ENDPOINT}')
    return {'queriesRun': len(results), 'proxyEndpoint': PROXY_ENDPOINT}
```

---

### ✅ Verify — Pattern 20

1. Test `RDSProxyLambda` — confirm 10 queries complete successfully
2. Navigate to **RDS → Proxies → lambda-aurora-proxy → Monitoring**
3. Check the **ClientConnections** and **DatabaseConnections** metrics
   - ClientConnections will spike per Lambda invocation
   - DatabaseConnections stays low (the proxy reuses them)
4. Run Lambda 5 times concurrently by triggering multiple test invocations rapidly — compare DB connections with and without proxy

**When the proxy matters most:** At 1,000 concurrent Lambda invocations, without RDS Proxy each would attempt a new DB connection. Aurora MySQL max_connections is ~1,000 for small instances — so without pooling, about half your Lambdas would get connection errors.

---

## Pattern 21: DynamoDB Streams → Aurora (NoSQL to Relational Sync)

> **Real-Time Sync from NoSQL Events into a Relational Read Model**

### What This Pattern Solves

DynamoDB excels at high-throughput writes and flexible schemas. Aurora excels at complex SQL queries, joins, and aggregations. This pattern combines both: write at NoSQL speed into DynamoDB, then stream changes via DynamoDB Streams into Aurora for rich SQL analytics. Same data, two models, each optimised for its purpose.

### Architecture

```
API  →  DynamoDB (write model)  →  DynamoDB Streams  →  Lambda  →  Aurora (SQL read model)
```

---

### Step 1 — Create DynamoDB Table with Streams

1. Navigate to **DynamoDB → Create table**
   - Table name: `SalesEvents`
   - Partition key: `saleId` (String)
   - Billing: On-demand
2. After creation: **Exports and streams → Enable DynamoDB Streams → New and old images**

---

### Step 2 — Create the Sync Lambda

Create Lambda: Name `DynamoToAuroraSync`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **60 seconds**

Replace ARNs with your values from Pattern 19:

```python
import json
import boto3

rds_data = boto3.client('rds-data')

CLUSTER_ARN = 'YOUR_CLUSTER_ARN'
SECRET_ARN  = 'YOUR_SECRET_ARN'
DATABASE    = 'labdb'

def ensure_table():
    rds_data.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql="""
            CREATE TABLE IF NOT EXISTS sales_analytics (
                sale_id     VARCHAR(50) PRIMARY KEY,
                customer_id VARCHAR(50),
                product     VARCHAR(100),
                quantity    INT,
                revenue     DECIMAL(10,2),
                region      VARCHAR(50),
                sale_date   VARCHAR(30),
                synced_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
    )

def upsert_sale(item):
    rds_data.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql="""
            INSERT INTO sales_analytics
                (sale_id, customer_id, product, quantity, revenue, region, sale_date)
            VALUES
                (:saleId, :customerId, :product, :quantity, :revenue, :region, :saleDate)
            ON DUPLICATE KEY UPDATE
                quantity   = VALUES(quantity),
                revenue    = VALUES(revenue),
                synced_at  = CURRENT_TIMESTAMP
        """,
        parameters=[
            {'name': 'saleId',      'value': {'stringValue': item.get('saleId', {}).get('S', '')}},
            {'name': 'customerId',  'value': {'stringValue': item.get('customerId', {}).get('S', 'unknown')}},
            {'name': 'product',     'value': {'stringValue': item.get('product', {}).get('S', '')}},
            {'name': 'quantity',    'value': {'longValue':   int(item.get('quantity', {}).get('N', '0'))}},
            {'name': 'revenue',     'value': {'doubleValue': float(item.get('revenue', {}).get('N', '0'))}},
            {'name': 'region',      'value': {'stringValue': item.get('region', {}).get('S', '')}},
            {'name': 'saleDate',    'value': {'stringValue': item.get('saleDate', {}).get('S', '')}}
        ]
    )

def lambda_handler(event, context):
    ensure_table()
    synced = 0
    for record in event['Records']:
        if record['eventName'] in ('INSERT', 'MODIFY'):
            item = record['dynamodb']['NewImage']
            upsert_sale(item)
            print(f"Synced sale {item.get('saleId',{}).get('S')} to Aurora")
            synced += 1
    return {'synced': synced}
```

Add DynamoDB Streams trigger: **Add trigger → DynamoDB → SalesEvents → Batch size 10 → Latest → Enable → Add**

---

### Step 3 — Create the Write Lambda (DynamoDB side)

Create Lambda: Name `SalesWriter`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3, uuid
from datetime import datetime, timedelta
import random

dynamo = boto3.resource('dynamodb').Table('SalesEvents')

PRODUCTS = ['Widget', 'Gadget', 'Device', 'Pro', 'Starter']
REGIONS  = ['North', 'South', 'East', 'West', 'Central']

def lambda_handler(event, context):
    count = event.get('count', 5)
    written = []
    for i in range(count):
        sale = {
            'saleId':     str(uuid.uuid4())[:8],
            'customerId': f'cust-{random.randint(1,100):03d}',
            'product':    random.choice(PRODUCTS),
            'quantity':   random.randint(1, 20),
            'revenue':    str(round(random.uniform(10, 500), 2)),
            'region':     random.choice(REGIONS),
            'saleDate':   (datetime.utcnow() - timedelta(days=random.randint(0,30))).strftime('%Y-%m-%d')
        }
        dynamo.put_item(Item=sale)
        written.append(sale['saleId'])
    return {'written': count, 'saleIds': written}
```

---

### ✅ Verify — Pattern 21

1. Test `SalesWriter` with: `{"count": 20}` — writes 20 random sales to DynamoDB
2. Wait 10 seconds, then test `AuroraLambda` (from Pattern 19) with:

```json
{"action": "custom_query"}
```

Or update `AuroraLambda` to add this action:

```python
elif action == 'sales_report':
    result = execute_sql("""
        SELECT region,
               product,
               COUNT(*)         AS transactions,
               SUM(quantity)    AS total_units,
               SUM(revenue)     AS total_revenue,
               AVG(revenue)     AS avg_sale
        FROM sales_analytics
        GROUP BY region, product
        ORDER BY total_revenue DESC
        LIMIT 15
    """)
    rows = json.loads(result.get('formattedRecords', '[]'))
    return {'salesReport': rows}
```

3. Test with: `{"action": "sales_report"}`
4. Confirm the report shows data that originated as DynamoDB writes

**Key insight:** DynamoDB handled the high-throughput writes (no schema, no connections). Aurora handles the complex GROUP BY analytics query that would be expensive in DynamoDB. The Streams + Lambda bridge keeps them in sync automatically.

---

## Pattern 22: Fargate + Aurora Serverless

> **Containerised Application with Serverless Relational Backend**

### What This Pattern Solves

Many existing applications use traditional ORMs (SQLAlchemy, Hibernate, Sequelize) or frameworks that expect a persistent DB connection — they can't use the RDS Data API. Run these applications in Fargate containers, which maintain persistent connections to Aurora Serverless v2 via RDS Proxy. This pattern is the standard lift-and-shift path for containerised apps that need a relational DB.

### Architecture

```
ALB  →  ECS Fargate Service  →  RDS Proxy  →  Aurora Serverless v2
```

---

### Step 1 — Create the Application

```bash
mkdir fargate-aurora-lab && cd fargate-aurora-lab
```

**`app.py`** — Flask API with SQLAlchemy:

```python
from flask import Flask, jsonify, request
import pymysql
import os
import json

app = Flask(__name__)

DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', 'localhost'),       # RDS Proxy endpoint
    'user':     os.environ.get('DB_USER', 'admin'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'labdb'),
    'port':     int(os.environ.get('DB_PORT', '3306')),
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': 10
}

def get_connection():
    return pymysql.connect(**DB_CONFIG)

def init_db():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    sku         VARCHAR(50) UNIQUE NOT NULL,
                    name        VARCHAR(100),
                    stock       INT DEFAULT 0,
                    reserved    INT DEFAULT 0,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
        conn.commit()

@app.route('/health')
def health():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        return jsonify({'status': 'healthy', 'db': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/inventory', methods=['GET'])
def list_inventory():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM inventory ORDER BY name")
            items = cursor.fetchall()
    return jsonify({'items': items, 'count': len(items)})

@app.route('/inventory', methods=['POST'])
def add_stock():
    data = request.get_json()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO inventory (sku, name, stock)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     stock = stock + VALUES(stock),
                     updated_at = CURRENT_TIMESTAMP""",
                (data['sku'], data['name'], data['stock'])
            )
        conn.commit()
    return jsonify({'message': 'Stock updated', 'sku': data['sku']})

@app.route('/inventory/<sku>/reserve', methods=['POST'])
def reserve_stock(sku):
    qty = request.get_json().get('quantity', 1)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE inventory SET reserved = reserved + %s WHERE sku = %s AND stock >= reserved + %s",
                (qty, sku, qty)
            )
            affected = cursor.rowcount
        conn.commit()
    if affected:
        return jsonify({'message': f'Reserved {qty} units of {sku}'})
    return jsonify({'error': 'Insufficient stock or SKU not found'}), 400

if __name__ == '__main__':
    try:
        init_db()
        print("Database initialised")
    except Exception as e:
        print(f"DB init skipped: {e}")
    app.run(host='0.0.0.0', port=8080, debug=False)
```

**`requirements.txt`**:
```
flask
pymysql
```

**`Dockerfile`**:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "app.py"]
```

---

### Step 2 — Build, Push, and Deploy

```bash
aws ecr create-repository \
  --repository-name serverless-labs/fargate-aurora \
  --region $AWS_REGION

export AURORA_ECR=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/serverless-labs/fargate-aurora

docker build -t fargate-aurora .
docker tag fargate-aurora:latest $AURORA_ECR:latest
docker push $AURORA_ECR:latest
```

Create an ECS Task Definition for `FargateAuroraTask` with these environment variables set on the container:

| Variable | Value |
|----------|-------|
| `DB_HOST` | Your RDS Proxy endpoint |
| `DB_USER` | `admin` |
| `DB_PASSWORD` | *(retrieve from Secrets Manager)* |
| `DB_NAME` | `labdb` |

Deploy as an ECS Service (same process as Pattern 18) with 1 desired task and an ALB.

---

### ✅ Verify — Pattern 22

```bash
ALB_DNS="YOUR_ALB_DNS"

# Health check (confirms DB connectivity)
curl http://$ALB_DNS/health

# Add inventory
curl -X POST http://$ALB_DNS/inventory \
  -H "Content-Type: application/json" \
  -d '{"sku":"WGT-001","name":"Widget","stock":100}'

curl -X POST http://$ALB_DNS/inventory \
  -H "Content-Type: application/json" \
  -d '{"sku":"GDG-001","name":"Gadget","stock":50}'

# List inventory
curl http://$ALB_DNS/inventory

# Reserve stock
curl -X POST http://$ALB_DNS/inventory/WGT-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 5}'

# Try to over-reserve (should fail)
curl -X POST http://$ALB_DNS/inventory/GDG-001/reserve \
  -H "Content-Type: application/json" \
  -d '{"quantity": 100}'
```

- Confirm health check shows `"db": "connected"` — the Fargate container reached Aurora via RDS Proxy
- Confirm the over-reserve returns a 400 error — SQL constraints enforced at the DB level

---

## Pattern 23: ElastiCache Serverless + Lambda

> **Sub-Millisecond Caching Layer in Front of Aurora**

### What This Pattern Solves

Even Aurora Serverless v2 has query latency of 1–10ms. For high-traffic read endpoints (product listings, user profiles, dashboards), cache the results in ElastiCache Serverless (Redis). First request hits Aurora; subsequent requests return from cache in < 1ms. ElastiCache Serverless requires no cluster sizing — it scales automatically and you pay per GB-hour stored and per ECPUs consumed.

### Architecture

```
Lambda  →  ElastiCache Serverless (Redis)  →  Cache HIT: return cached result
                   |
               Cache MISS
                   |
                Aurora  →  cache result  →  return to caller
```

---

### Step 1 — Create ElastiCache Serverless Cache

1. Navigate to **ElastiCache → Serverless caches → Create serverless cache**
2. Cache name: `labs-cache`
3. Engine: **Valkey** (Redis-compatible, AWS open source fork)
4. VPC: default
5. Security group: default *(or create one allowing port 6379 inbound from Lambda)*
6. Click **Create** — takes ~3 minutes

Once created, copy the **Endpoint** (format: `labs-cache.serverless.use1.cache.amazonaws.com:6379`).

---

### Step 2 — Add Lambda to the Same VPC

> ElastiCache requires Lambda to be in the same VPC. Configure VPC settings on your Lambda.

1. Open `AuroraLambda` → **Configuration → VPC**
2. Edit → select your default VPC, at least 2 subnets, default security group
3. Save — Lambda will now be able to reach ElastiCache

---

### Step 3 — Create the Cached Query Lambda

Create Lambda: Name `CachedQueryLambda`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **30 seconds**

Add a Lambda layer with `redis-py`:

```bash
# In Cloud9 — create a layer package
mkdir redis-layer && cd redis-layer
mkdir python
pip install redis -t python/
zip -r redis-layer.zip python/

# Publish the layer
aws lambda publish-layer-version \
  --layer-name redis-py \
  --zip-file fileb://redis-layer.zip \
  --compatible-runtimes python3.12 \
  --region $AWS_REGION
```

Add this layer to `CachedQueryLambda`, then use this code (replace all ARN/endpoint values):

```python
import json
import boto3
import redis
import os
import time

# Cache client — connects via VPC
cache = redis.Redis(
    host=os.environ.get('CACHE_ENDPOINT', 'YOUR_ELASTICACHE_ENDPOINT'),
    port=6379,
    decode_responses=True,
    ssl=True,
    socket_connect_timeout=2
)

rds_data = boto3.client('rds-data')
CLUSTER_ARN = os.environ.get('CLUSTER_ARN', 'YOUR_CLUSTER_ARN')
SECRET_ARN  = os.environ.get('SECRET_ARN',  'YOUR_SECRET_ARN')
DATABASE    = 'labdb'
CACHE_TTL   = 60  # Cache results for 60 seconds

def query_aurora(sql):
    result = rds_data.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=sql,
        formatRecordsAs='JSON'
    )
    return json.loads(result.get('formattedRecords', '[]'))

def lambda_handler(event, context):
    query_key = event.get('query', 'product_summary')
    cache_key = f'query:{query_key}'
    
    # Try cache first
    start = time.time()
    cached = cache.get(cache_key)
    
    if cached:
        latency_ms = round((time.time() - start) * 1000, 2)
        print(f'CACHE HIT [{cache_key}] — {latency_ms}ms')
        return {
            'source':    'cache',
            'latencyMs': latency_ms,
            'data':      json.loads(cached)
        }
    
    # Cache miss — query Aurora
    print(f'CACHE MISS [{cache_key}] — querying Aurora...')
    db_start = time.time()
    
    sql_map = {
        'product_summary': """
            SELECT category,
                   COUNT(*)    AS count,
                   AVG(price)  AS avg_price,
                   SUM(price)  AS total_value
            FROM products
            GROUP BY category
        """,
        'recent_sales': """
            SELECT product, region, SUM(revenue) AS total_revenue
            FROM sales_analytics
            GROUP BY product, region
            ORDER BY total_revenue DESC
            LIMIT 10
        """
    }
    
    sql = sql_map.get(query_key, "SELECT 1 AS result")
    data = query_aurora(sql)
    
    db_latency = round((time.time() - db_start) * 1000, 2)
    
    # Store in cache
    cache.setex(cache_key, CACHE_TTL, json.dumps(data))
    print(f'Cached result for {CACHE_TTL}s. Aurora query took {db_latency}ms')
    
    return {
        'source':    'aurora',
        'latencyMs': db_latency,
        'data':      data,
        'cachedFor': f'{CACHE_TTL}s'
    }
```

Set these Lambda environment variables:
- `CACHE_ENDPOINT` — your ElastiCache endpoint (without port)
- `CLUSTER_ARN` — Aurora cluster ARN
- `SECRET_ARN` — Secrets Manager ARN

---

### ✅ Verify — Pattern 23

1. Test `CachedQueryLambda` with: `{"query": "product_summary"}`
   - First call: `"source": "aurora"` with latency ~5–20ms
2. Test again immediately with the same input:
   - Second call: `"source": "cache"` with latency < 1ms
3. Test with: `{"query": "recent_sales"}`
4. Wait 60 seconds — test again — Aurora is hit again (TTL expired)

**Observe the latency difference:**

| Call | Source | Typical Latency |
|------|--------|----------------|
| First | Aurora | 5–20ms |
| Second–Nth (within 60s) | Cache | < 1ms |
| After 60s TTL | Aurora | 5–20ms |

**Production cache strategies to explore:**
- **Write-through:** Update cache whenever you write to Aurora (always fresh)
- **Cache-aside (this lab):** Lazy loading — populate on first miss
- **Read-through:** Cache layer handles Aurora calls transparently
- **TTL tuning:** Shorter TTL = fresher data, more Aurora load; longer TTL = staler data, less cost

---

## End of Day 3

All 8 container and database patterns complete.

### Full Pattern Summary — All 3 Days

| Day | # | Pattern | Key Services |
|-----|---|---------|-------------|
| 1 | 1 | API-Driven Backend | Lambda · API GW · DynamoDB |
| 1 | 2 | Secure API | + Cognito · Lambda Authorizer |
| 1 | 3 | Event-Driven Microservice | + EventBridge |
| 1 | 4 | Queue-Based Load Leveling | + SQS · DLQ |
| 1 | 5 | Fan-Out | + SNS |
| 1 | 6 | File Processing | + S3 |
| 2 | 7 | Stream Processing | Kinesis · Lambda |
| 2 | 8 | Orchestration | Step Functions |
| 2 | 9 | Choreography | EventBridge rules chain |
| 2 | 10 | Scheduled Automation | EventBridge Scheduler |
| 2 | 11 | Serverless RAG | Bedrock · embeddings |
| 2 | 12 | Agentic AI | Bedrock tool use |
| 2 | 13 | Document Intelligence | Textract |
| 2 | 14 | Serverless ETL | S3 · Athena |
| 2 | 15 | Event Sourcing & CQRS | DynamoDB Streams |
| 3 | 16 | Containerised Lambda | Lambda · ECR |
| 3 | 17 | Fargate Task | ECS Fargate · ECR |
| 3 | 18 | Fargate HTTP API | ECS · ALB · API GW |
| 3 | 19 | Aurora Serverless v2 | Aurora · RDS Data API |
| 3 | 20 | RDS Proxy + Lambda | RDS Proxy · Aurora |
| 3 | 21 | DynamoDB → Aurora Sync | DynamoDB Streams · Aurora |
| 3 | 22 | Fargate + Aurora | ECS Fargate · RDS Proxy · Aurora |
| 3 | 23 | ElastiCache + Lambda | ElastiCache Serverless · Redis |

### Decision Framework — Compute

```
Task < 15 minutes AND stateless AND event-driven?
  └─ Yes → Lambda (ZIP or Container image)
  └─ No  → Fargate
              └─ Needs persistent connection / heavy framework? → Fargate + RDS Proxy
              └─ Short batch job, exit on completion?          → Fargate Task (run-to-completion)
              └─ Always-on HTTP service?                       → Fargate Service + ALB
```

### Decision Framework — Database

```
Need flexible schema + high write throughput?
  └─ Yes → DynamoDB (Days 1–2)

Need SQL + joins + transactions?
  └─ Workload is variable / unpredictable → Aurora Serverless v2
  └─ Many concurrent Lambda connections  → + RDS Proxy in front
  └─ Need both NoSQL speed AND SQL analytics → DynamoDB + Streams → Aurora (Pattern 21)

Need sub-millisecond reads on hot data?
  └─ Add ElastiCache Serverless as a cache layer (Pattern 23)
```

---

## Common Troubleshooting

### Container / ECR Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `no basic auth credentials` | Docker not logged in to ECR | Re-run `aws ecr get-login-password \| docker login ...` |
| `exec format error` | Image built for wrong CPU arch | Build on same arch as Lambda/Fargate target: add `--platform linux/amd64` to `docker build` |
| `ImagePullBackOff` in ECS | Wrong image URI or ECR permissions | Confirm the task execution role has `AmazonEC2ContainerRegistryReadOnly` |

### Aurora / RDS Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `Data API not enabled` | RDS Data API not turned on | RDS Console → Cluster → Modify → Enable Data API |
| `Communications link failure` | Lambda not in same VPC as RDS | Add Lambda to VPC via Configuration → VPC |
| `Too many connections` | No connection pooling | Add RDS Proxy (Pattern 20) |
| `AccessDeniedException` on Data API | Missing `rds-data:ExecuteStatement` permission | Add inline policy to LambdaLabRole |

### ElastiCache Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `Connection refused` | Lambda not in VPC | Add Lambda to same VPC as ElastiCache |
| `SSL: WRONG_VERSION_NUMBER` | Missing `ssl=True` in Redis client | Add `ssl=True` to `redis.Redis(...)` |
| `Timeout` | Security group blocking port 6379 | Add inbound rule: TCP 6379 from Lambda security group |