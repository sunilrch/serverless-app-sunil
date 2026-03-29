# Serverless AI Agents — Bedrock & Lambda Demonstration

This repository demonstrates a serverless pattern for running AI agents inside AWS Lambda that invoke AWS Bedrock models. It shows how an orchestrator Lambda coordinates specialized tool Lambdas (agents) to process documents (for example, invoices) using a tool-driven pipeline (Textract, validation, posting to backend systems, notifications), where the orchestration decision-making is powered by a Bedrock model.

Key project components
- Orchestrator Lambda: coordinates the pipeline and uses a Bedrock-backed model (see app/orchestrator/lambda_function.py).
- Tool Lambdas (agents): e.g., a Textract extraction agent invoked by the orchestrator.
- CDK infra: AWS CDK (Python) stacks create the Lambda (as a Docker image), S3 bucket with event trigger, ECR repo, IAM policies for Textract & Bedrock, and an API Gateway (see infra/infra/infra_stack.py).
- Docker image for Lambda: container image built from app/orchestrator/Dockerfile so the orchestrator can be deployed as a DockerImageFunction.

Where to look in the repo
- Orchestrator implementation: app/orchestrator/lambda_function.py — the orchestrator registers tools and invokes a BedrockModel-based Agent.
  - Link: [app/orchestrator/lambda_function.py](https://github.com/prashant-baj/serverless-app/blob/752acd3be63eb4c58fd44a299ef4542d670459b9/app/orchestrator/lambda_function.py)
- Lambda container Dockerfile: app/orchestrator/Dockerfile
  - Link: [app/orchestrator/Dockerfile](https://github.com/prashant-baj/serverless-app/blob/752acd3be63eb4c58fd44a299ef4542d670459b9/app/orchestrator/Dockerfile)
- Infra (CDK) stack that wires S3 trigger, ECR, Lambda, and Bedrock IAM permissions: infra/infra/infra_stack.py
  - Link: [infra/infra/infra_stack.py](https://github.com/prashant-baj/serverless-app/blob/752acd3be63eb4c58fd44a299ef4542d670459b9/infra/infra/infra_stack.py)

Quick summary of how this demo works
- A file upload to the S3 bucket triggers the Orchestrator Lambda (CDK config attaches S3 event notifications).
- The Orchestrator creates an Agent instance that uses a BedrockModel (MODEL_ID via env) to coordinate several tools:
  - A Textract extraction tool (implemented as an invocation to another Lambda container)
  - Data validation and business-system posting tools (stubs in the repo)
  - A notification tool (WhatsApp stub)
- The Orchestrator sends instructions to the Bedrock model; the model's responses guide which tool to call and in what order. The orchestration and tool calls are deterministic functions inside the orchestrator Lambda.

Prerequisites
- AWS account with Bedrock access (and Textract permissions).
- AWS CLI configured with credentials.
- AWS CDK v2 for Python installed and configured.
- Docker (for building the Lambda container).
- Python 3.12 runtime is used in the orchestrator Dockerfile (adjust locally if needed).

Environment variables and config (important)
- MODEL_ID — Bedrock model ARN/identifier used by the orchestrator (set via CDK build args / container env).
- PROMPT_BUCKET — S3 bucket containing prompts (orchestrator reads prompt files).
- PROMPT_KEY — Key for the prompt file within PROMPT_BUCKET.
- AWS_REGION / AWS_DEFAULT_REGION — AWS region.
- ENV_NAME — environment name appended to some resource names (dev by default).
- EXTRACTION_AGENT_LAMBDA — name of the Textract extraction agent Lambda (invoked by the orchestrator).

You can find these variables referenced in:
- app/orchestrator/Dockerfile (build-time args and runtime ENV)
- app/orchestrator/lambda_function.py (reads MODEL_ID, PROMPT_BUCKET, PROMPT_KEY, AWS_REGION, etc.)
- infra/infra/infra_stack.py (build args passed into Docker image and IAM policies)

Local development & testing
- Build the orchestrator container locally:
  - cd app/orchestrator
  - docker build -t orchestrator:local .
- To run locally in a basic way (quick smoke test), start the container with required environment variables:
  - docker run --rm -e MODEL_ID="<model-arn>" -e AWS_REGION="ap-southeast-2" -e PROMPT_BUCKET="prompts-dev" orchestrator:local
- For testing the Lambda handler locally you can:
  - Use AWS SAM local invoke with an event file, or
  - Use a simple docker run and curl against local Lambda invocation wrappers if you add them; the repo currently is structured for AWS Lambda runtime container invocation.
- Unit tests: add pytest / unittest-based tests for the orchestrator logic and individual tool functions.

Deploying to AWS (CDK)
1. From repository root, ensure CDK context has account and region:
   - cd infra
   - pip install -r requirements.txt
2. Synthesize and deploy (example):
   - cdk synth -c account=<ACCOUNT_ID> -c region=<REGION> -c env_name=dev
   - cdk deploy -c account=<ACCOUNT_ID> -c region=<REGION> -c env_name=dev
3. CDK will:
   - Create an ECR repo for the container image
   - Build the Docker image (from app/orchestrator) and push it
   - Create a Lambda function from the Docker image
   - Create S3 bucket and wire object-created notification to the Lambda
   - Attach IAM policies for Textract and Bedrock usage to the Lambda role
4. After deployment, you will get an API Gateway URL (if enabled) and the S3 bucket name.

Security & permissions
- Bedrock access is granted via IAM policies in the CDK stack; ensure the Bedrock model ARN and Bedrock permissions are restricted to only what is needed.
- Never commit secrets (API keys, DB credentials). Use Parameter Store, Secrets Manager, or deploy-time secrets (CDK context / pipeline secrets).
- The CDK infra currently sets removal policies that are suitable for demo/dev only. Review and change for production.

Common adjustments you may want
- Replace demo/stub tool implementations in app/orchestrator/lambda_function.py with real integrations (Textract parsing, SAP posting, WhatsApp provider).
- Replace the prompt file in S3 with tuned system prompts for your chosen Bedrock model.
- Adjust concurrency limits and timeouts on the Orchestrator Lambda — orchestration can be long-running depending on model latency and tool calls.
- Add safer IAM scoping to Bedrock and Textract actions (avoid resource: "*").

Examples & sample events
- The orchestrator expects S3 object-created events. A minimal S3 put event (for local testing or SAM) should include the standard S3 event fields and point to the bucket & key used by the CDK stack.

Where to go next
- If you want, I can:
  - Commit this README to the repo.
  - Add a `.env.example` or `infra/README.md` deployment guide with exact CDK commands and sample CDK context.
  - Add a small local test harness (event JSON + sam local or docker run instructions) to make local development easier.
  - Replace the Textract invocation stubs with a working extraction lambda example and sample payloads for end-to-end demo.

If you'd like me to commit the README and/or add any of the follow-ups above, tell me which items to include (and whether to use the current MODEL_ID in the CDK build args or a placeholder).
