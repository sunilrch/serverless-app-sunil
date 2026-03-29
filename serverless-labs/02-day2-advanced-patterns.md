# Day 2 — Advanced Serverless Patterns

> **Patterns 7–15 | Duration: 2.5–3 hours**  
> Each lab is fully independent. Resources are created fresh. No dependency on Day 1.

---

## Day 2 onwards at a Glance

| # | Pattern | Core Services | Lab Type |
|---|---------|--------------|--------------|
| 7 | Stream Processing | Kinesis · Lambda · DynamoDB | Workshop |
| 8 | Orchestration Workflow | Step Functions · Lambda | Lab |
| 9 | Choreography with EventBridge | EventBridge · Lambda | Homework |
| 10 | Scheduled Automation | EventBridge Scheduler · Lambda | Homework |
| 11 | Serverless RAG | Bedrock · DynamoDB · Lambda | Homework |
| 12 | Agentic AI | Bedrock Tool Use · Lambda | Workshop |
| 13 | Document Intelligence | Textract · S3 · Lambda | Workshop |
| 14 | Serverless ETL & Data Lake | S3 · Lambda · Athena | Homework |
| 15 | Event Sourcing & CQRS | DynamoDB Streams · Lambda | Homework |

---

---

## Start-of-Day Setup (10 minutes)

### Recreate LambdaLabRole (if deleted overnight)

```bash
# In CloudShell
aws iam create-role \
  --role-name LambdaLabRole \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"lambda.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }' 2>/dev/null || echo "Role already exists"

for policy in \
  AWSLambdaBasicExecutionRole \
  AmazonDynamoDBFullAccess \
  AmazonSQSFullAccess \
  AmazonSNSFullAccess \
  AmazonS3FullAccess \
  AWSStepFunctionsFullAccess \
  AmazonKinesisFullAccess \
  AmazonTextractFullAccess \
  AmazonAthenaFullAccess \
  AWSGlueServiceRole \
  AmazonBedrockFullAccess; do
  aws iam attach-role-policy \
    --role-name LambdaLabRole \
    --policy-arn "arn:aws:iam::aws:policy/${policy}" 2>/dev/null || true
done

echo "LambdaLabRole ready"
```

### Verify Bedrock Model Access (for Patterns 11 & 12)

1. Open **Amazon Bedrock Console**
2. Go to **Model access** → confirm these models show **Access granted**:
   - `Amazon Titan Text Embeddings V2`
   - `Anthropic Claude 3 Haiku`
3. If not granted: **Modify model access** → enable both → Submit (takes 1–5 minutes)

---

## Pattern 7: Stream Processing

> **Real-Time Analytics with Kinesis Data Streams**

### What This Pattern Solves

Kinesis handles continuous streams of data — IoT sensors, application logs, financial transactions — at any scale. Lambda subscribes to the stream and processes records in micro-batches within milliseconds of arrival, with automatic checkpointing and retry logic.

### Architecture

```
Data Producer  →  Kinesis Data Streams  →  Lambda  →  DynamoDB / CloudWatch Alerts
```

---

### Step 1 — Create Kinesis Stream and DynamoDB Table

1. Navigate to **Kinesis → Create data stream**
   - Name: `SensorStream`
   - Capacity mode: **Provisioned**
   - Shards: `1`
   - Click **Create data stream**

2. Navigate to **DynamoDB → Create table**
   - Table name: `SensorMetrics`
   - Partition key: `sensorId` (String)
   - Sort key: `timestamp` (String)
   - Billing mode: On-demand
   - Click **Create table**

---

### Step 2 — Create the Stream Processor Lambda

Create Lambda: Name `SensorStreamProcessor`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **60 seconds**

```python
import json, boto3, base64
from datetime import datetime

dynamo = boto3.resource('dynamodb').Table('SensorMetrics')

def lambda_handler(event, context):
    for record in event['Records']:
        # Kinesis data is base64 encoded
        payload = base64.b64decode(record['kinesis']['data']).decode('utf-8')
        data = json.loads(payload)
        
        ts = datetime.utcnow().isoformat()
        item = {
            'sensorId': data.get('sensorId', 'unknown'),
            'timestamp': ts,
            'temperature': str(data.get('temperature', 0)),
            'humidity': str(data.get('humidity', 0)),
            'shardId': record['eventID']
        }
        dynamo.put_item(Item=item)
        
        # Real-time alerting
        if data.get('temperature', 0) > 80:
            print(f"🚨 ALERT: High temperature {data['temperature']}°C on {data['sensorId']}")
        else:
            print(f"✅ Sensor {data['sensorId']}: {data.get('temperature')}°C, {data.get('humidity')}% humidity")
    
    print(f'Processed {len(event["Records"])} sensor records')
    return {'records': len(event['Records'])}
```

Add the Kinesis trigger: **Add trigger → Kinesis → SensorStream**
- Batch size: `10`
- Starting position: **Latest**
- Enable: Yes → **Add**

---

### Step 3 — Create the Data Producer Lambda

Create Lambda: Name `SensorProducer`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3, random

kinesis = boto3.client('kinesis')

def lambda_handler(event, context):
    count = event.get('count', 5)
    for i in range(count):
        record = {
            'sensorId': f'sensor-{(i % 3) + 1:02d}',
            'temperature': round(random.uniform(60, 95), 1),
            'humidity': round(random.uniform(30, 90), 1)
        }
        kinesis.put_record(
            StreamName='SensorStream',
            Data=json.dumps(record),
            PartitionKey=record['sensorId']
        )
    print(f'Published {count} sensor readings to Kinesis')
    return {'sent': count}
```

---

### ✅ Verify — Pattern 7

1. Test `SensorProducer` with: `{"count": 10}`
2. Wait 10–15 seconds, then check **CloudWatch → /aws/lambda/SensorStreamProcessor**
3. Query **DynamoDB → SensorMetrics → Explore items** — confirm rows with sensor IDs and timestamps
4. Modify the producer to force `"temperature": 90` — verify the `🚨 ALERT` log appears
5. Check **Lambda → SensorStreamProcessor → Monitor → Iterator age** to see stream processing latency

---

## Pattern 8: Orchestration Workflow

> **Business Process Automation with AWS Step Functions**

### What This Pattern Solves

Complex business processes require coordination, error handling, retries, and audit trails. Step Functions defines these workflows as state machines. Each execution is fully visible in the console, with every step's input, output, and timing recorded automatically.

### Architecture

```
Step Functions  →  CheckInventory Lambda  →  ProcessPayment Lambda  →  ShipOrder Lambda
                          |
                    (error: out of stock)
                          |
                     OrderFailed (Fail state)
```

---

### Step 1 — Create the Worker Lambdas

**Lambda 1:** Name `CheckInventory`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, random

def lambda_handler(event, context):
    order_id = event.get('orderId', 'ORD-001')
    # Simulate 75% in-stock rate
    in_stock = random.choice([True, True, True, False])
    print(f'Inventory check for {order_id}: {"IN STOCK" if in_stock else "OUT OF STOCK"}')
    if not in_stock:
        raise Exception(f'Item out of stock for order {order_id}')
    return {**event, 'inventoryStatus': 'confirmed'}
```

**Lambda 2:** Name `ProcessPayment`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json

def lambda_handler(event, context):
    order_id = event.get('orderId')
    amount = event.get('amount', 0)
    print(f'Processing payment of ${amount} for order {order_id}')
    return {**event, 'paymentStatus': 'charged', 'transactionId': 'TXN-99887'}
```

**Lambda 3:** Name `ShipOrder`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json

def lambda_handler(event, context):
    order_id = event.get('orderId')
    print(f'Shipping order {order_id} — tracking: TRK-{order_id[-4:]}')
    return {**event, 'shipmentStatus': 'shipped', 'trackingId': f'TRK-{order_id[-4:]}'}
```

---

### Step 2 — Create the Step Functions State Machine

1. Navigate to **Step Functions → Create state machine**
2. Choose **Write your workflow in code**
3. Type: **Standard**
4. Paste the following ASL definition, replacing `REGION` and `ACCOUNT` with your values:

```json
{
  "Comment": "Order Processing Pipeline",
  "StartAt": "CheckInventory",
  "States": {
    "CheckInventory": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:CheckInventory",
      "Retry": [{
        "ErrorEquals": ["States.TaskFailed"],
        "MaxAttempts": 1
      }],
      "Catch": [{
        "ErrorEquals": ["States.ALL"],
        "Next": "OrderFailed",
        "ResultPath": "$.error"
      }],
      "Next": "ProcessPayment"
    },
    "ProcessPayment": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:ProcessPayment",
      "Next": "ShipOrder"
    },
    "ShipOrder": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:ShipOrder",
      "Next": "OrderComplete"
    },
    "OrderComplete": {
      "Type": "Succeed"
    },
    "OrderFailed": {
      "Type": "Fail",
      "Error": "OutOfStock",
      "Cause": "Item unavailable at time of order"
    }
  }
}
```

5. Name: `OrderPipeline`
6. Execution role: **Create new role** (let AWS create it)
7. Click **Create state machine**

> **Find your ARNs:** In the Lambda console, open each function → the ARN is shown in the top-right corner of the function overview.

---

### ✅ Verify — Pattern 8

1. Click **Start execution**
2. Input:
```json
{"orderId": "ORD-20250225", "amount": 149.99, "customerId": "cust-007"}
```
3. Watch the **visual execution graph** light up green as each step completes
4. Run 4–5 executions — the 25% out-of-stock case will eventually fire, routing to `OrderFailed`
5. Click any execution → **Event history** — see the complete audit trail with timestamps and payloads at each step
6. Check CloudWatch Logs for each Lambda — confirm correct order of invocation

**Key insight:** Step Functions automatically retried `CheckInventory` once before failing. The `Catch` block handled the exception without any code in the calling service knowing about it.

---

## Pattern 9: Choreography with EventBridge

> **Fully Decoupled Microservices — No Central Coordinator**

### What This Pattern Solves

Unlike Step Functions orchestration, choreography has no central coordinator. Each service publishes events on completion, and others react independently. Services don't know about each other — they only know about events. Teams can deploy, scale, and change services with zero coordination.

### Architecture

```
OrderService  →  EventBridge  →  PaymentService  →  EventBridge  →  FulfillmentService
                                                                  →  EventBridge  →  OrderNotifier
```

---

### Step 1 — Recreate the EventBridge Bus

If `ecommerce-events` was deleted overnight:

1. Navigate to **EventBridge → Event buses → Create event bus**
   - Name: `ecommerce-events`
   - Click **Create**

---

### Step 2 — Create the Choreography Service Lambdas

**PaymentService** — reacts to `OrderPlaced`, publishes `PaymentProcessed`

Create Lambda: Name `PaymentService`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3

events = boto3.client('events')

def lambda_handler(event, context):
    detail = event.get('detail', {})
    order_id = detail.get('orderId')
    print(f'[PAYMENT] Processing payment for order {order_id}')
    
    # Publish next event — no knowledge of who consumes it
    events.put_events(Entries=[{
        'Source': 'ecommerce.payments',
        'DetailType': 'PaymentProcessed',
        'Detail': json.dumps({'orderId': order_id, 'transactionId': 'TXN-555', 'amount': detail.get('amount')}),
        'EventBusName': 'ecommerce-events'
    }])
    return {'status': 'payment published'}
```

**FulfillmentService** — reacts to `PaymentProcessed`, publishes `OrderShipped`

Create Lambda: Name `FulfillmentService`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3

events = boto3.client('events')

def lambda_handler(event, context):
    detail = event.get('detail', {})
    order_id = detail.get('orderId')
    print(f'[FULFILLMENT] Packing and shipping order {order_id}')
    
    events.put_events(Entries=[{
        'Source': 'ecommerce.fulfillment',
        'DetailType': 'OrderShipped',
        'Detail': json.dumps({'orderId': order_id, 'tracking': f'TRK-{order_id[-4:]}', 'carrier': 'FedEx'}),
        'EventBusName': 'ecommerce-events'
    }])
    return {'status': 'fulfillment published'}
```

**NotificationService** — reacts to `OrderShipped`

Create Lambda: Name `NotificationService`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json

def lambda_handler(event, context):
    detail = event.get('detail', {})
    order_id = detail.get('orderId')
    tracking = detail.get('tracking', 'unknown')
    print(f'[NOTIFY] Order {order_id} shipped! Sending email with tracking {tracking}')
    return {'status': 'customer notified'}
```

---

### Step 3 — Wire the Choreography Rules

Create 3 EventBridge rules on the `ecommerce-events` bus:

**Rule 1** — triggers payment when order is placed
- Name: `PayOnOrder`
- Pattern: `{"source":["ecommerce.orders"],"detail-type":["OrderPlaced"]}`
- Target: `PaymentService`

**Rule 2** — triggers fulfillment when payment succeeds
- Name: `FulfillOnPayment`
- Pattern: `{"source":["ecommerce.payments"],"detail-type":["PaymentProcessed"]}`
- Target: `FulfillmentService`

**Rule 3** — triggers notification when order ships
- Name: `NotifyOnShip`
- Pattern: `{"source":["ecommerce.fulfillment"],"detail-type":["OrderShipped"]}`
- Target: `NotificationService`

---

### Step 4 — Create the Order Publisher Lambda

Create Lambda: Name `OrderPublisher`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3, uuid

events = boto3.client('events')

def lambda_handler(event, context):
    order = {
        'orderId': 'ORD-' + str(uuid.uuid4())[:6].upper(),
        'amount': event.get('amount', 99.99),
        'customerId': event.get('customerId', 'cust-001')
    }
    events.put_events(Entries=[{
        'Source': 'ecommerce.orders',
        'DetailType': 'OrderPlaced',
        'Detail': json.dumps(order),
        'EventBusName': 'ecommerce-events'
    }])
    print(f'Published OrderPlaced: {order["orderId"]}')
    return {'statusCode': 200, 'order': order}
```

---

### ✅ Verify — Pattern 9

1. Test `OrderPublisher` with: `{"amount": 75.00, "customerId": "cust-042"}`
2. Check CloudWatch logs for all three services — the chain should complete within 2–3 seconds:
   - `PaymentService` logs `[PAYMENT] Processing...`
   - `FulfillmentService` logs `[FULFILLMENT] Packing...`
   - `NotificationService` logs `[NOTIFY] Order ... shipped!`
3. Confirm in **EventBridge → Event buses → ecommerce-events → Monitoring** that 3 separate events were received

**Key insight:** Add a `LoyaltyPointsService` Lambda that reacts to `OrderShipped` and awards points — create one new Lambda, one new rule. **Zero changes** to PaymentService, FulfillmentService, NotificationService, or OrderPublisher.

---

## Pattern 10: Scheduled Automation

> **Serverless Cron Jobs with EventBridge Scheduler**

### What This Pattern Solves

EventBridge Scheduler replaces traditional cron daemons entirely. Define a schedule using cron or rate syntax, and EventBridge invokes your Lambda automatically — no servers to manage, no daemon to monitor. Cost: pennies per month for typical automation tasks.

### Architecture

```
EventBridge Schedule  →  Lambda  →  AWS APIs / External Services / DynamoDB
```

---

### Step 1 — Create the Automation Lambda

Create Lambda: Name `DailyHealthCheck`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3
from datetime import datetime

def lambda_handler(event, context):
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f'[HEALTH CHECK] Running automated check at {ts}')
    
    # Inventory of Lambda functions as a health proxy
    lam = boto3.client('lambda')
    funcs = lam.list_functions()['Functions']
    names = [f['FunctionName'] for f in funcs]
    print(f'Found {len(names)} Lambda functions active')
    print(f'Functions: {", ".join(names[:5])}{"..." if len(names) > 5 else ""}')
    
    # Check DynamoDB tables
    dynamo = boto3.client('dynamodb')
    tables = dynamo.list_tables()['TableNames']
    print(f'Found {len(tables)} DynamoDB tables: {tables}')
    
    print(f'[HEALTH CHECK] All systems nominal ✅')
    return {
        'status': 'ok',
        'timestamp': ts,
        'lambdaCount': len(names),
        'tableCount': len(tables)
    }
```

---

### Step 2 — Create the EventBridge Schedule

1. Navigate to **EventBridge → Schedules → Create schedule**
2. Fill in:
   - Schedule name: `HealthCheckEvery5Min`
   - Schedule pattern: **Recurring schedule**
   - Schedule type: **Rate-based**
   - Rate: `5` minutes
3. Target:
   - Target type: **AWS Lambda**
   - Lambda function: `DailyHealthCheck`
4. Retry policy:
   - Maximum age: `1 hour`
   - Retry attempts: `2`
5. Click **Create schedule**

---

### ✅ Verify — Pattern 10

1. Wait 5 minutes, then check **CloudWatch → /aws/lambda/DailyHealthCheck** — confirm automatic invocation
2. Navigate to **EventBridge → Schedules → HealthCheckEvery5Min** — review last invocation status
3. **Test a one-time schedule:**
   - Create schedule → **One-time schedule** → Set time 2 minutes from now → same Lambda target
   - Confirm it fires exactly once and never again
4. Change the recurring schedule rate to `1 minute` and observe 3 consecutive invocations, then delete the schedule

**Cost reminder:** 14,400 invocations/month (every 5 min × 24h × 30d) at Lambda free tier costs essentially $0.

---

## Pattern 11: Serverless RAG

> **Retrieval-Augmented Generation with Amazon Bedrock**

### What This Pattern Solves

RAG combines large language models with your private knowledge base. Documents are converted to vector embeddings and stored. At query time, semantically similar documents are retrieved and given to the LLM as context, producing accurate answers grounded in your actual data rather than generic training data.

### Architecture

```
Ingest: S3 → Lambda → Bedrock (Embeddings) → DynamoDB (Vector Store)
Query:  Lambda → Bedrock (Embeddings) → DynamoDB (Similarity Search) → Bedrock (LLM) → Answer
```

> **Pre-requisite:** Confirm Bedrock model access is enabled (see Start-of-Day Setup).

---

### Step 1 — Create the Knowledge Base Table

1. Navigate to **DynamoDB → Create table**
   - Table name: `KnowledgeBase`
   - Partition key: `docId` (String)
   - Billing: On-demand
   - Click **Create table**

---

### Step 2 — Create the Document Ingestion Lambda

Create Lambda: Name `DocumentIngester`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **30 seconds**

```python
import json, boto3, hashlib

bedrock = boto3.client('bedrock-runtime')
dynamo = boto3.resource('dynamodb').Table('KnowledgeBase')

def get_embedding(text):
    resp = bedrock.invoke_model(
        modelId='amazon.titan-embed-text-v2:0',
        body=json.dumps({'inputText': text[:8000]}),
        contentType='application/json'
    )
    return json.loads(resp['body'].read())['embedding']

def lambda_handler(event, context):
    docs = event.get('documents', [])
    for doc in docs:
        text = doc['text']
        doc_id = hashlib.md5(text.encode()).hexdigest()[:8]
        embedding = get_embedding(text)
        dynamo.put_item(Item={
            'docId': doc_id,
            'title': doc.get('title', ''),
            'text': text,
            'embeddingPreview': json.dumps(embedding[:5])  # Store preview only
        })
        print(f'Ingested: [{doc_id}] {doc.get("title", "")}')
    return {'ingested': len(docs)}
```

Test with:

```json
{
  "documents": [
    {"title": "Lambda Basics", "text": "AWS Lambda runs code without provisioning servers. It scales automatically from zero to thousands of concurrent executions. You pay only for the compute time consumed — there is no charge when code is not running."},
    {"title": "DynamoDB", "text": "Amazon DynamoDB is a fully managed serverless NoSQL database. It delivers single-digit millisecond performance at any scale and supports both key-value and document data models."},
    {"title": "API Gateway", "text": "Amazon API Gateway creates, publishes, maintains, monitors, and secures APIs at any scale. It acts as the front door for applications to access data, business logic, or functionality from backend services."},
    {"title": "EventBridge", "text": "Amazon EventBridge is a serverless event bus that makes it easy to connect applications using events from your own apps, integrated SaaS applications, and AWS services."}
  ]
}
```

---

### Step 3 — Create the RAG Query Lambda

Create Lambda: Name `RAGQuery`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **30 seconds**

```python
import json, boto3

bedrock = boto3.client('bedrock-runtime')
dynamo = boto3.resource('dynamodb').Table('KnowledgeBase')

def lambda_handler(event, context):
    question = event.get('question', 'What is serverless?')
    
    # Retrieve all docs (simplified — production uses vector similarity search)
    docs = dynamo.scan()['Items']
    context_text = '\n'.join([f"[{d['title']}]: {d['text']}" for d in docs])
    
    prompt = f"""Using ONLY the context below, answer the question concisely.
If the answer is not in the context, say "I don't have that information in my knowledge base."

Context:
{context_text}

Question: {question}
Answer:"""

    resp = bedrock.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 500,
            'messages': [{'role': 'user', 'content': prompt}]
        }),
        contentType='application/json'
    )
    answer = json.loads(resp['body'].read())['content'][0]['text']
    print(f'Q: {question}')
    print(f'A: {answer}')
    return {'question': question, 'answer': answer, 'sourceCount': len(docs)}
```

---

### ✅ Verify — Pattern 11

Test `RAGQuery` with each of these:

```json
{"question": "How does Lambda handle scaling?"}
```
```json
{"question": "What database would you use for millisecond performance?"}
```
```json
{"question": "What is the capital of France?"}
```

- First two should give accurate answers from your ingested documents
- Third should return "I don't have that information..." — the model is constrained to your context
- Check DynamoDB `KnowledgeBase` table — confirm 4 documents were ingested

**Key insight:** The model's answer is grounded in verifiable source material. If a document is wrong, the answer will be wrong — which is why production RAG systems include source citations.

---

## Pattern 12: Agentic AI on Serverless

> **Autonomous Agents with Bedrock Tool Use**

### What This Pattern Solves

Agents go beyond Q&A — they break down complex tasks, decide which tools to call, invoke those tools, interpret results, and continue until the task is complete. Bedrock's tool use feature enables this pattern. Lambda provides the execution environment; Step Functions can be added for multi-step workflows.

### Architecture

```
User Task  →  Lambda Agent  →  Bedrock (reasoning)  →  Tool Lambda (execution)  →  Result
```

---

### Step 1 — Create Tool Lambdas

**PriceCalculator** — a tool the agent can call

Create Lambda: Name `PriceCalculator`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json

PRICES = {'Widget': 9.99, 'Gadget': 49.99, 'Device': 199.99, 'Pro': 399.99}

def lambda_handler(event, context):
    product = event.get('product', 'Widget')
    qty = int(event.get('quantity', 1))
    unit_price = PRICES.get(product, 0)
    total = round(unit_price * qty, 2)
    discount = round(total * 0.1, 2) if qty >= 5 else 0
    final = round(total - discount, 2)
    print(f'Price calc: {qty}x {product} = ${total} (discount: ${discount}) = ${final}')
    return {
        'product': product, 'quantity': qty,
        'unitPrice': unit_price, 'subtotal': total,
        'discount': discount, 'total': final
    }
```

**InventoryChecker** — a second tool

Create Lambda: Name `InventoryChecker`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json

STOCK = {'Widget': 150, 'Gadget': 23, 'Device': 8, 'Pro': 0}

def lambda_handler(event, context):
    product = event.get('product', 'Widget')
    stock = STOCK.get(product, 0)
    status = 'in stock' if stock > 10 else ('low stock' if stock > 0 else 'out of stock')
    return {'product': product, 'stock': stock, 'status': status}
```

---

### Step 2 — Create the Agent Orchestrator Lambda

Create Lambda: Name `AgentOrchestrator`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **60 seconds**

```python
import json, boto3

bedrock = boto3.client('bedrock-runtime')
lam = boto3.client('lambda')

TOOLS = [
    {
        'name': 'PriceCalculator',
        'description': 'Calculate the total price for a product and quantity, including bulk discounts.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'product': {'type': 'string', 'description': 'Product name: Widget, Gadget, Device, or Pro'},
                'quantity': {'type': 'integer', 'description': 'Number of units'}
            },
            'required': ['product', 'quantity']
        }
    },
    {
        'name': 'InventoryChecker',
        'description': 'Check current inventory levels for a product.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'product': {'type': 'string', 'description': 'Product name to check'}
            },
            'required': ['product']
        }
    }
]

def call_tool(tool_name, tool_input):
    result = lam.invoke(FunctionName=tool_name, Payload=json.dumps(tool_input))
    return json.loads(result['Payload'].read())

def lambda_handler(event, context):
    task = event.get('task', 'What is the price of 3 Gadgets?')
    messages = [{'role': 'user', 'content': task}]
    tool_calls_made = []
    
    # Agentic loop — continue until model stops calling tools
    for _ in range(5):  # safety limit
        resp = bedrock.invoke_model(
            modelId='anthropic.claude-3-haiku-20240307-v1:0',
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 1000,
                'tools': TOOLS,
                'messages': messages
            }),
            contentType='application/json'
        )
        result = json.loads(resp['body'].read())
        stop_reason = result.get('stop_reason')
        
        if stop_reason == 'end_turn':
            # Model has finished — extract final text
            final = next((b['text'] for b in result['content'] if b['type'] == 'text'), '')
            print(f'Agent complete. Tools used: {tool_calls_made}')
            return {'task': task, 'answer': final, 'toolsUsed': tool_calls_made}
        
        if stop_reason == 'tool_use':
            # Model wants to call a tool
            messages.append({'role': 'assistant', 'content': result['content']})
            tool_results = []
            for block in result['content']:
                if block['type'] == 'tool_use':
                    print(f'Agent calling {block["name"]} with {block["input"]}')
                    tool_output = call_tool(block['name'], block['input'])
                    tool_calls_made.append(block['name'])
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block['id'],
                        'content': json.dumps(tool_output)
                    })
            messages.append({'role': 'user', 'content': tool_results})
    
    return {'task': task, 'error': 'Max iterations reached'}
```

---

### ✅ Verify — Pattern 12

Test `AgentOrchestrator` with these tasks:

```json
{"task": "I need to buy 5 Gadgets. What will the total cost be?"}
```
```json
{"task": "Check if Devices are available and tell me the price for 2 units."}
```
```json
{"task": "I want to order some Pros but first check if they're in stock."}
```

- Observe how the agent autonomously decides to call `PriceCalculator`, `InventoryChecker`, or both
- Check CloudWatch logs — see exactly which tools were invoked and with what parameters
- The third task should reveal Pros are out of stock and the agent should report this before calculating price

**Key insight:** The agent selected and combined tools based on **natural language reasoning** — no hard-coded routing or if/else logic. Adding a new tool requires only adding its definition to the `TOOLS` list.

---

## Pattern 13: Document Intelligence

> **Amazon Textract + Automated Extraction Pipeline**

### What This Pattern Solves

Textract uses ML to extract text, tables, and form fields from documents — including handwriting, complex layouts, and scanned images. This creates automated pipelines for KYC verification, invoice processing, claims handling, and contract review without manual data entry.

### Architecture

```
S3 Upload  →  S3 Event  →  Lambda  →  Textract  →  DynamoDB (extracted data)
```

---

### Step 1 — Create S3 Bucket and DynamoDB Table

1. Create S3 bucket: `document-intelligence-[YOUR_INITIALS]-[4_DIGITS]` (same region, default settings)

2. Navigate to **DynamoDB → Create table**
   - Table name: `ExtractedDocuments`
   - Partition key: `documentKey` (String)
   - Billing: On-demand
   - Click **Create table**

---

### Step 2 — Create the Textract Extraction Lambda

Create Lambda: Name `TextractExtractor`, Runtime `Python 3.12`, Role `LambdaLabRole`, Timeout **60 seconds**

```python
import json, boto3
from datetime import datetime

textract = boto3.client('textract')
dynamo = boto3.resource('dynamodb').Table('ExtractedDocuments')

def lambda_handler(event, context):
    # Support both direct invocation and S3 event trigger
    if 'Records' in event:
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']
    else:
        bucket = event['bucket']
        key = event['key']
    
    print(f'Extracting text from s3://{bucket}/{key}')
    
    # Call Textract
    resp = textract.detect_document_text(
        Document={'S3Object': {'Bucket': bucket, 'Name': key}}
    )
    
    blocks = resp['Blocks']
    lines = [b['Text'] for b in blocks if b['BlockType'] == 'LINE']
    words = [b['Text'] for b in blocks if b['BlockType'] == 'WORD']
    
    print(f'Extracted {len(lines)} lines, {len(words)} words')
    print(f'First 3 lines: {lines[:3]}')
    
    # Store results in DynamoDB
    dynamo.put_item(Item={
        'documentKey': key,
        'bucket': bucket,
        'processedAt': datetime.utcnow().isoformat(),
        'lineCount': len(lines),
        'wordCount': len(words),
        'extractedLines': lines[:50]  # Store first 50 lines
    })
    
    return {
        'documentKey': key,
        'lineCount': len(lines),
        'wordCount': len(words),
        'sampleText': lines[:5]
    }
```

---

### Step 3 — Configure S3 Event Trigger

1. Navigate to your `document-intelligence-*` bucket → **Properties**
2. **Event notifications → Create event notification**
   - Name: `DocumentUploaded`
   - Event types: ✅ All object create events
   - Destination: Lambda function → `TextractExtractor`
3. Click **Save changes**

---

### ✅ Verify — Pattern 13

1. Upload any document containing text to your S3 bucket:
   - A photo of a printed page (`.jpg` / `.png`)
   - A simple PDF with text
   - A screenshot of any text document
2. Check **CloudWatch → /aws/lambda/TextractExtractor** — verify extracted lines appear
3. Check **DynamoDB → ExtractedDocuments** — confirm the extraction result was stored
4. Try uploading an image of a **table** (e.g. a screenshot of a spreadsheet) — Textract preserves the table structure

**Test form extraction (bonus):**

```python
# Modify the Lambda to also extract key-value pairs from forms:
resp = textract.analyze_document(
    Document={'S3Object': {'Bucket': bucket, 'Name': key}},
    FeatureTypes=['FORMS', 'TABLES']
)
```

Upload an image of a form (e.g. a registration form with Name:, Email:, Date: fields) — Textract will identify and pair each label with its value.

---

## Pattern 14: Serverless ETL & Data Lake

> **S3 + Lambda Transform + Athena SQL Analytics**

### What This Pattern Solves

The Serverless Data Lake ingests raw data into S3, transforms it with Lambda into queryable format, and enables SQL analytics via Athena — no clusters, no databases to manage, no idle infrastructure costs. Athena charges only for data scanned per query.

### Architecture

```
S3 (Raw CSV)  →  Lambda (Transform + Enrich)  →  S3 (Processed JSON)  →  Athena (SQL)
```

---

### Step 1 — Create the Data Lake S3 Structure

1. Create S3 bucket: `datalake-[YOUR_INITIALS]-[4_DIGITS]`
2. Inside the bucket, upload a file named `sales.csv` under the prefix `raw/`. Create a text file locally with this content and upload it:

```csv
date,product,region,quantity,revenue
2025-01-01,Widget,North,10,99.90
2025-01-01,Gadget,South,5,249.95
2025-01-02,Widget,East,8,79.92
2025-01-02,Device,West,2,399.98
2025-01-03,Gadget,North,12,599.88
2025-01-03,Widget,South,6,59.94
2025-01-04,Device,East,1,199.99
2025-01-04,Pro,North,3,1199.97
```

---

### Step 2 — Create the ETL Transform Lambda

Create Lambda: Name `DataLakeTransformer`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3, csv, io
from datetime import datetime

s3 = boto3.client('s3')

def lambda_handler(event, context):
    bucket = event['bucket']
    raw_key = event['key']
    
    # Read raw CSV from S3
    obj = s3.get_object(Bucket=bucket, Key=raw_key)
    content = obj['Body'].read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    
    # Transform: enrich with computed fields
    transformed = []
    for row in rows:
        qty = int(row['quantity'])
        revenue = float(row['revenue'])
        row['avgUnitPrice'] = round(revenue / qty, 2)
        row['revenueCategory'] = 'high' if revenue > 200 else 'medium' if revenue > 100 else 'low'
        row['processedAt'] = datetime.utcnow().isoformat()
        transformed.append(row)
    
    # Write transformed data to processed/ prefix
    out_key = raw_key.replace('raw/', 'processed/').replace('.csv', '.json')
    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=json.dumps(transformed, indent=2),
        ContentType='application/json'
    )
    
    # Also write a flattened CSV version for Athena
    csv_out_key = raw_key.replace('raw/', 'processed/')
    fieldnames = list(transformed[0].keys())
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(transformed)
    s3.put_object(Bucket=bucket, Key=csv_out_key, Body=csv_buffer.getvalue())
    
    print(f'Transformed {len(transformed)} rows')
    print(f'Written to: s3://{bucket}/{out_key}')
    return {'rows': len(transformed), 'output': out_key}
```

Test with:
```json
{"bucket": "YOUR_DATALAKE_BUCKET", "key": "raw/sales.csv"}
```

---

### Step 3 — Query with Amazon Athena

1. Navigate to **Athena → Query editor**
2. If prompted, set a query results S3 location: `s3://YOUR_DATALAKE_BUCKET/athena-results/`
3. Run these queries in order:

```sql
-- Create database
CREATE DATABASE IF NOT EXISTS sales_datalake;
```

```sql
-- Create table pointing to processed/ prefix
CREATE EXTERNAL TABLE IF NOT EXISTS sales_datalake.sales_processed (
  date STRING,
  product STRING,
  region STRING,
  quantity INT,
  revenue DOUBLE,
  avgUnitPrice DOUBLE,
  revenueCategory STRING,
  processedAt STRING
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
LINES TERMINATED BY '\n'
LOCATION 's3://YOUR_DATALAKE_BUCKET/processed/'
TBLPROPERTIES ('skip.header.line.count'='1');
```

```sql
-- Revenue by region
SELECT region,
       SUM(revenue) AS total_revenue,
       SUM(quantity) AS total_units,
       ROUND(AVG(avgUnitPrice), 2) AS avg_unit_price
FROM sales_datalake.sales_processed
GROUP BY region
ORDER BY total_revenue DESC;
```

```sql
-- Top products
SELECT product,
       COUNT(*) AS transactions,
       SUM(quantity) AS total_units,
       SUM(revenue) AS total_revenue
FROM sales_datalake.sales_processed
GROUP BY product
ORDER BY total_revenue DESC;
```

---

### ✅ Verify — Pattern 14

1. Confirm the transform Lambda runs successfully and creates `processed/sales.csv` and `processed/sales.json`
2. Run all four Athena queries — confirm results showing revenue by region and product rankings
3. Note the **Data scanned** shown after each query — Athena charges $5 per TB scanned
4. Add more rows to `sales.csv`, upload again, re-run the transform, and re-query — new data appears immediately

**Key insight:** Athena queried the CSV file directly in S3 — no ETL pipeline to a data warehouse, no cluster to spin up. For production scale, convert CSV to **Parquet** format (10x less data scanned, 10x cheaper queries).

---

## Pattern 15: Event Sourcing & CQRS

> **Immutable Event Log + Separate Read/Write Models**

### What This Pattern Solves

Event Sourcing stores every state change as an immutable, append-only event. CQRS separates the write model (commands that generate events) from the read model (projections optimised for queries). The result: complete audit trail, point-in-time reconstruction, and multiple independent read views — all from a single source of truth.

### Architecture

```
Command Lambda  →  EventStore (DynamoDB)  →  DynamoDB Streams
                                                    |
                                           Projection Lambda
                                                    |
                                        AccountBalance (DynamoDB read model)
```

---

### Step 1 — Create DynamoDB Tables

1. Create table: `EventStore`
   - Partition key: `aggregateId` (String)
   - Sort key: `eventId` (String)
   - **Enable DynamoDB Streams**: Table details → Exports and streams → Enable DynamoDB Streams → **New and old images**

2. Create table: `AccountBalance` (the read model)
   - Partition key: `accountId` (String)
   - Billing: On-demand

---

### Step 2 — Create the Command Lambda (Write Side)

Create Lambda: Name `AccountCommand`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3, uuid
from datetime import datetime

dynamo = boto3.resource('dynamodb')
event_store = dynamo.Table('EventStore')

def lambda_handler(event, context):
    cmd = event.get('command')        # 'deposit' or 'withdraw'
    account_id = event.get('accountId', 'ACC-001')
    amount = float(event.get('amount', 0))
    
    event_type = 'MoneyDeposited' if cmd == 'deposit' else 'MoneyWithdrawn'
    
    # Append immutable event — NEVER update or delete
    event_record = {
        'aggregateId': account_id,
        'eventId': datetime.utcnow().isoformat() + '#' + str(uuid.uuid4())[:6],
        'eventType': event_type,
        'amount': str(amount),
        'initiatedBy': event.get('userId', 'system'),
        'timestamp': datetime.utcnow().isoformat()
    }
    event_store.put_item(Item=event_record)
    print(f'Event appended: {event_type} ${amount} on {account_id}')
    return {'status': 'event recorded', 'eventId': event_record['eventId']}
```

---

### Step 3 — Create the Projection Lambda (Stream Processor)

Create Lambda: Name `BalanceProjection`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3
from decimal import Decimal

dynamo = boto3.resource('dynamodb')
balance_table = dynamo.Table('AccountBalance')

def lambda_handler(event, context):
    for record in event['Records']:
        if record['eventName'] != 'INSERT':
            continue  # Only process new events
        
        new_image = record['dynamodb']['NewImage']
        account_id = new_image['aggregateId']['S']
        event_type = new_image['eventType']['S']
        amount = Decimal(new_image['amount']['S'])
        timestamp = new_image['timestamp']['S']
        
        # Update the read model: current account balance
        if event_type == 'MoneyDeposited':
            balance_table.update_item(
                Key={'accountId': account_id},
                UpdateExpression='ADD balance :amt SET lastUpdated = :ts',
                ExpressionAttributeValues={':amt': amount, ':ts': timestamp}
            )
            print(f'Projection: +${amount} deposited to {account_id}')
        elif event_type == 'MoneyWithdrawn':
            balance_table.update_item(
                Key={'accountId': account_id},
                UpdateExpression='ADD balance :amt SET lastUpdated = :ts',
                ExpressionAttributeValues={':amt': -amount, ':ts': timestamp}
            )
            print(f'Projection: -${amount} withdrawn from {account_id}')
    
    return {'projected': len(event['Records'])}
```

Add the DynamoDB Streams trigger: **Add trigger → DynamoDB → EventStore**
- Batch size: `10`
- Starting position: **Latest**
- Enable: Yes → **Add**

---

### Step 4 — Create the Query Lambda (Read Side)

Create Lambda: Name `AccountQuery`, Runtime `Python 3.12`, Role `LambdaLabRole`

```python
import json, boto3
from decimal import Decimal

dynamo = boto3.resource('dynamodb')
balance_table = dynamo.Table('AccountBalance')
event_store = dynamo.Table('EventStore')

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def lambda_handler(event, context):
    account_id = event.get('accountId', 'ACC-001')
    
    # Fast read from optimised read model
    balance_item = balance_table.get_item(Key={'accountId': account_id}).get('Item', {})
    
    # Full audit trail from event store
    history = event_store.query(
        KeyConditionExpression='aggregateId = :aid',
        ExpressionAttributeValues={':aid': account_id},
        ScanIndexForward=True  # Chronological order
    )['Items']
    
    return json.loads(json.dumps({
        'accountId': account_id,
        'currentBalance': balance_item.get('balance', 0),
        'lastUpdated': balance_item.get('lastUpdated', 'never'),
        'totalEvents': len(history),
        'auditTrail': history  # Complete immutable history
    }, cls=DecimalEncoder))
```

---

### ✅ Verify — Pattern 15

Run these commands in sequence:

```json
// 1. Deposit
{"command": "deposit", "accountId": "ACC-001", "amount": 1000, "userId": "alice"}
```
```json
// 2. Withdraw
{"command": "withdraw", "accountId": "ACC-001", "amount": 250, "userId": "alice"}
```
```json
// 3. Deposit again
{"command": "deposit", "accountId": "ACC-001", "amount": 500, "userId": "bob"}
```
```json
// 4. Query the balance
{"accountId": "ACC-001"}
```

- Balance should be **1250** (1000 - 250 + 500)
- Query returns **all 3 events** in the audit trail
- Check **EventStore DynamoDB table** — 3 rows, none updated or deleted, each with a unique eventId
- Check **AccountBalance table** — single row with the current computed balance

**Reconstruct historical balance:** Query all events for `ACC-001` and sum only the first two — you get the balance *before* the last deposit. This is point-in-time reconstruction without any additional infrastructure.

**Key insight:** The `EventStore` is immutable — commands only append. The `AccountBalance` table is derived and disposable. If you need a new read model (e.g. monthly statement), create a new Lambda that replays the event history and builds a different projection.

---

## End of Day 2

All 15 serverless patterns complete.

| Day | Patterns | Foundation Built |
|-----|---------|-----------------|
| 1 | 1–6 | Lambda · API Gateway · DynamoDB · Cognito · EventBridge · SQS · SNS · S3 |
| 2 | 7–15 | Kinesis · Step Functions · Bedrock · Textract · Athena · DynamoDB Streams |

### Pattern Selection Cheat Sheet

| Requirement | Pattern to Use |
|-------------|---------------|
| Simple REST API | Pattern 1: API-Driven Backend |
| Need authentication | Pattern 2: Secure API with Cognito |
| Services should be decoupled | Pattern 3: Event-Driven Microservice |
| Protect against traffic spikes | Pattern 4: Queue-Based Load Leveling |
| Same event → multiple processors | Pattern 5: Fan-Out |
| Process files on upload | Pattern 6: File Processing Pipeline |
| Continuous data streams (IoT, logs) | Pattern 7: Stream Processing |
| Multi-step process with error handling | Pattern 8: Orchestration (Step Functions) |
| Microservices teams need full autonomy | Pattern 9: Choreography (EventBridge) |
| Replace cron jobs | Pattern 10: Scheduled Automation |
| AI Q&A over private documents | Pattern 11: Serverless RAG |
| AI that takes actions autonomously | Pattern 12: Agentic AI |
| Extract data from scanned documents | Pattern 13: Document Intelligence |
| Analytics over large datasets cheaply | Pattern 14: Serverless Data Lake |
| Need complete audit trail + replay | Pattern 15: Event Sourcing & CQRS |

> Real applications combine multiple patterns. An order management system might use Pattern 1 (REST API) + Pattern 8 (Step Functions) + Pattern 9 (EventBridge choreography) + Pattern 10 (nightly reconciliation) all working together.
