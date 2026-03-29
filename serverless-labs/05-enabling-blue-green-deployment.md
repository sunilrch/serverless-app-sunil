# Blue-Green Deployment — Lambda Traffic Shifting with CDK & CodeDeploy

Safely roll out new Lambda container images with automatic rollback — without ever taking your function offline.

---

## Table of Contents

1. [What This Lab Covers](#what-this-lab-covers)
2. [How Blue-Green Deployment Works on Lambda](#how-blue-green-deployment-works-on-lambda)
3. [Prerequisites](#prerequisites)
4. [Part 1 — Lambda Versions and Aliases](#part-1--lambda-versions-and-aliases)
5. [Part 2 — Update the CDK Stack](#part-2--update-the-cdk-stack)
6. [Part 3 — Update the CI/CD Pipeline](#part-3--update-the-cicd-pipeline)
7. [Part 4 — Test the Deployment](#part-4--test-the-deployment)
8. [Verify & Validate](#verify--validate)
9. [Troubleshooting](#troubleshooting)

---

## What This Lab Covers

| Capability | How it is implemented |
|---|---|
| Immutable deployment snapshots | Lambda **Versions** — frozen copies of function code + config |
| Named, stable invocation endpoint | Lambda **Alias** (`live`) — a pointer that API Gateway and S3 always call |
| Gradual traffic shifting | AWS **CodeDeploy** canary config — routes 10% to new, 90% to old for 10 minutes |
| Automatic rollback | **CloudWatch Alarm** — any Lambda error during the shift triggers instant revert |
| CDK-native wiring | All resources defined as CDK constructs in `ai_doc_processor_stack.py` |

After completing this lab your deployments will look like this:

```
Every push to main
        │
        ▼
CDK creates new Lambda Version  ──────────────────────────┐
        │                                                  │
        ▼                                                  │
CodeDeploy starts canary shift                             │
        │                                                  │
   ┌────┴───────────────────────┐                          │
   │  live alias routing        │         10 minutes       │
   │  ├─ 90% → old version  ◄───┘  ◄── observation window─┘
   │  └─ 10% → new version
   └────────────────────────────┘
        │
   [CloudWatch checks for errors every 1 minute]
        │
   ┌────┴────────────────────────────────┐
   │ Error detected?                     │
   ├── YES → CodeDeploy auto-rollback    │
   │         alias reverts to 100% old  │
   └── NO  → after 10 min, 100% to new  │
             deployment complete        │
```

---

## How Blue-Green Deployment Works on Lambda

### The problem with direct function invocation

By default, API Gateway and S3 event notifications point directly at the Lambda function ARN. Every `cdk deploy` updates that function in-place:

```
Client → API Gateway → arn:aws:lambda:…:function:OrchestratorContainer-dev
                                              ▲
                                    Updated in place
                                    (old code gone immediately)
```

If the new code has a bug, **all traffic** hits the broken version until you manually redeploy.

### The solution: versions + aliases + CodeDeploy

Lambda provides three primitives that, together, enable blue-green deployment:

**Lambda Version**
An immutable snapshot of a function at a point in time. Once published, a version's code and configuration never change. Versions are numbered: `$LATEST`, `1`, `2`, `3`, …

**Lambda Alias**
A named pointer to a specific version. Aliases can split traffic between two versions using a weighted routing configuration:

```
Alias: live
  ├── 90% → version 2  (the "blue" — currently live)
  └── 10% → version 3  (the "green" — the new candidate)
```

API Gateway invokes the alias ARN, not the function ARN. The alias decides which version actually runs.

**AWS CodeDeploy**
A fully-managed deployment service that automates the weight transition. You define the deployment configuration (how fast to shift) and attach CloudWatch alarms (when to stop and roll back). CodeDeploy communicates with CloudFormation via an `UpdatePolicy` that CDK injects automatically when you wire an alias to a `LambdaDeploymentGroup`.

### Deployment configuration options

| Config name | Behaviour | Best for |
|---|---|---|
| `ALL_AT_ONCE` | Instant 0% → 100% cutover | Non-critical functions, dev environments |
| `CANARY_10PERCENT_5MINUTES` | 10% for 5 min, then 100% | Short-running functions (< 5 min timeout) |
| `CANARY_10PERCENT_10MINUTES` | 10% for 10 min, then 100% | **This Lambda** — timeout = 10 min; one full execution can be observed |
| `LINEAR_10PERCENT_EVERY_1MINUTE` | +10% every minute (done in 10 min) | High-traffic APIs where 1-minute windows get enough sample data |
| `LINEAR_10PERCENT_EVERY_10MINUTES` | +10% every 10 min (done in 100 min) | Critical, low-traffic services needing maximum caution |

> **Tip:** For this project's `OrchestratorContainer` Lambda, `CANARY_10PERCENT_10MINUTES` is the right choice. The Lambda timeout is 10 minutes — the canary window is exactly long enough to observe a complete execution before committing the full shift.

---

## Prerequisites

- Completed [Lab 04 — GitHub Actions CI/CD](./04-github-actions-cicd.md) (pipeline is deployed and running)
- The mono-repo structure from the earlier refactor (`services/ai-doc-processor/`)
- AWS CDK v2 installed (`npm install -g aws-cdk`)
- Python 3.12 virtual environment active in `services/ai-doc-processor/infra/`

---

## Part 1 — Lambda Versions and Aliases

Before writing code, verify that the current stack has no versions or aliases configured.

### Check current state

```bash
# List all Lambda aliases for the orchestrator function
aws lambda list-aliases \
  --function-name OrchestratorContainer-dev \
  --region ap-southeast-2
```

Expected output (no aliases yet):
```json
{
    "Aliases": []
}
```

```bash
# List all published versions
aws lambda list-versions-by-function \
  --function-name OrchestratorContainer-dev \
  --region ap-southeast-2
```

Expected output (only $LATEST):
```json
{
    "Versions": [
        {
            "FunctionName": "OrchestratorContainer-dev",
            "Version": "$LATEST",
            ...
        }
    ]
}
```

This confirms the function is currently invoked at `$LATEST` — no immutable snapshots, no traffic control.

### Why `current_version` alone is not enough for DockerImageFunction

CDK's `function.current_version` property creates a `Lambda::Version` CloudFormation resource whose logical ID is derived from the function configuration at synth time. For a `DockerImageFunction`, if the `build_args`, `timeout`, and environment variables are unchanged between two deploys, CDK synthesises the **same logical ID** → CloudFormation sees no difference → no new version is published.

**The fix:** pass a value that changes on every deploy (the Git commit SHA) as a CDK context variable and embed it in the version description. CDK hashes the description into the logical ID, so a different SHA = a different logical ID = CloudFormation publishes a new Lambda version.

```bash
# Manually test the pattern locally
cdk synth -c git_sha=abc123 2>/dev/null | grep -A5 "OrchestratorVersion"
# Shows Description: deploy-abc123

cdk synth -c git_sha=def456 2>/dev/null | grep -A5 "OrchestratorVersion"
# Shows Description: deploy-def456  ← different logical ID → new version
```

---

## Part 2 — Update the CDK Stack

All changes are in one file:
`services/ai-doc-processor/infra/stack/ai_doc_processor_stack.py`

### Step 1: Add two imports

Find the existing import tuple at the top of the file and add two new services:

```python
from aws_cdk import (
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_apigateway as apigw,
    CfnOutput,
    aws_ecr as ecr,
    aws_iam as iam,
    RemovalPolicy,
    Duration,
    aws_cloudwatch as cloudwatch,    # ← ADD THIS
    aws_codedeploy as codedeploy,    # ← ADD THIS
)
```

Both `aws_cloudwatch` and `aws_codedeploy` are part of `aws-cdk-lib` — no changes to `requirements.txt` are needed.

### Step 2: Read the Git SHA from CDK context

Add this line immediately after the `account`/`region` lines in `__init__`:

```python
account  = self.node.try_get_context("account") or self.account
region   = self.node.try_get_context("region")  or self.region
git_sha  = self.node.try_get_context("git_sha") or "local"   # ← ADD THIS
```

The fallback value `"local"` means local synth/deploy (without passing `-c git_sha=…`) still works — it just won't create a new version each time, which is fine for developer workstations.

### Step 3: Update the Lambda function

Two changes to the `DockerImageFunction` definition:

**a)** Change `reserved_concurrent_executions` from `1` to `2`.

During the 10-minute canary window, both the old and new Lambda versions may have in-flight executions simultaneously. With `reserved_concurrent_executions=1`, the second concurrent request would be throttled with `TooManyRequestsException`. Setting it to `2` allows one execution per version.

**b)** Add `current_version_options` with `RemovalPolicy.RETAIN`.

This tells CloudFormation never to delete old Lambda versions — essential because CodeDeploy still needs the old version available during and after a rollback.

```python
orchestrator_lambda = _lambda.DockerImageFunction(
    self,
    "DocumentProcessingOrchestrator",
    function_name=orchestrator_lambda_name,
    code=_lambda.DockerImageCode.from_image_asset(
        "../app/orchestrator",
        build_args={
            "MODEL_ID": f"arn:aws:bedrock:{region}:{account}:inference-profile/apac.anthropic.claude-sonnet-4-20250514-v1:0",
            "PROMPT_BUCKET": "prompts-dev",
            "PROMPT_KEY": "orchestrator/Orchestrator.txt"
        },
    ),
    timeout=Duration.minutes(10),
    reserved_concurrent_executions=2,              # ← CHANGED from 1
    current_version_options=_lambda.VersionOptions( # ← ADD THIS BLOCK
        removal_policy=RemovalPolicy.RETAIN,
    ),
)
```

### Step 4: Create the Lambda Version and Alias

Add this block **immediately after** the `DockerImageFunction` definition:

```python
# ── Lambda Version ─────────────────────────────────────────────────────────
# The description changes on every deploy (git_sha), which changes the
# CloudFormation logical ID, forcing CloudFormation to publish a new version.
orchestrator_version = _lambda.Version(
    self,
    "OrchestratorVersion",
    lambda_=orchestrator_lambda,
    description=f"deploy-{git_sha}",
    removal_policy=RemovalPolicy.RETAIN,   # never delete — needed for rollback
)

# ── Live Alias ──────────────────────────────────────────────────────────────
# API Gateway and S3 always invoke this alias.
# CodeDeploy manages the version weights during traffic shifts.
live_alias = _lambda.Alias(
    self,
    "LiveAlias",
    alias_name="live",
    version=orchestrator_version,
)
```

### Step 5: Update the S3 trigger to use the alias

Find the existing S3 event notification line and change the destination:

```python
# BEFORE:
bucket.add_event_notification(
    s3.EventType.OBJECT_CREATED,
    s3n.LambdaDestination(orchestrator_lambda)   # direct function ref
)

# AFTER:
bucket.add_event_notification(
    s3.EventType.OBJECT_CREATED,
    s3n.LambdaDestination(live_alias)            # via alias
)
```

> **Note:** Both API Gateway and S3 now invoke the same `live` alias ARN. This means CodeDeploy's traffic weights apply equally to both invocation paths — S3-triggered document processing and API Gateway calls both participate in the same canary rollout.

### Step 6: Add the CloudWatch error alarm

Add this block **after** the IAM policy statements (after `orchestrator_lambda.add_to_role_policy(bedrock_policy)`):

```python
# ── CloudWatch Error Alarm ──────────────────────────────────────────────────
# CodeDeploy polls this alarm during the canary window.
# One Lambda error in any 1-minute period triggers an automatic rollback.
error_alarm = cloudwatch.Alarm(
    self,
    "OrchestratorErrorAlarm",
    alarm_name=f"OrchestratorContainer-{self.env_name}-Errors",
    alarm_description=(
        "Triggers CodeDeploy auto-rollback if the live alias "
        "reports any Lambda errors during traffic shifting."
    ),
    metric=live_alias.metric_errors(
        period=Duration.minutes(1),
        statistic="Sum",
    ),
    threshold=1,
    evaluation_periods=1,
    datapoints_to_alarm=1,
    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
)
```

**Why `treat_missing_data=NOT_BREACHING`?**
This Lambda is triggered by S3 uploads — it may go many minutes without any invocations. If CloudWatch has no data points in an evaluation window, treating missing data as `NOT_BREACHING` (rather than `BREACHING`) prevents the alarm from going into ALARM state during idle periods, which would abort otherwise-healthy deployments.

### Step 7: Add the CodeDeploy deployment group

Add this block **after** the `error_alarm` definition:

```python
# ── CodeDeploy Application ──────────────────────────────────────────────────
code_deploy_app = codedeploy.LambdaApplication(
    self,
    "OrchestratorCodeDeployApp",
    application_name=f"OrchestratorContainer-{self.env_name}",
)

# ── CodeDeploy Deployment Group ─────────────────────────────────────────────
# When CDK wires a LambdaDeploymentGroup to an alias, it automatically injects
# an UpdatePolicy on the Lambda::Alias CloudFormation resource so that
# CloudFormation delegates all alias updates to CodeDeploy instead of
# performing a direct property update.
deployment_group = codedeploy.LambdaDeploymentGroup(
    self,
    "OrchestratorDeploymentGroup",
    application=code_deploy_app,
    alias=live_alias,
    deployment_config=codedeploy.LambdaDeploymentConfig.CANARY_10PERCENT_10MINUTES,
    alarms=[error_alarm],
    auto_rollback=codedeploy.AutoRollbackConfig(
        deployment_in_alarm=True,    # rollback if alarm fires mid-shift
        failed_deployment=True,      # rollback if deployment itself errors
        stopped_deployment=False,    # do not auto-rollback manual stops
    ),
    deployment_group_name=f"OrchestratorContainer-{self.env_name}-DG",
)
```

### Step 8: Update the API Gateway handler

Find the `LambdaRestApi` definition and change its `handler` argument:

```python
# BEFORE:
api = apigw.LambdaRestApi(
    self, "AIDocProcessorApi",
    handler=orchestrator_lambda,    # direct function ref
    proxy=False
)

# AFTER:
api = apigw.LambdaRestApi(
    self, "AIDocProcessorApi",
    handler=live_alias,             # via alias
    proxy=False
)
```

### Step 9: Update the unit tests

Open `services/ai-doc-processor/infra/tests/unit/test_ai_doc_processor_stack.py` and make the following changes.

#### 9a. Update the mock to accept `current_version_options`

```python
class _FakeDockerImageFunction(_lambda.Function):
    def __init__(self, scope, id, *, function_name=None, timeout=None,
                 reserved_concurrent_executions=None,
                 current_version_options=None,   # ← ADD THIS PARAMETER
                 **kwargs):
        kwargs.pop("code", None)
        super().__init__(
            scope, id,
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.InlineCode("def handler(e, c): pass"),
            function_name=function_name,
            timeout=timeout,
            reserved_concurrent_executions=reserved_concurrent_executions,
            current_version_options=current_version_options,  # ← FORWARD IT
        )
```

#### 9b. Update the fixture to include `git_sha`

```python
@pytest.fixture(scope="module")
def template() -> assertions.Template:
    app = core.App(context={"env_name": "test", "git_sha": "abc1234"})  # ← ADD git_sha
    with patch("stack.ai_doc_processor_stack._lambda.DockerImageFunction", _FakeDockerImageFunction):
        stack = AiDocProcessorStack(app, "TestStack", env=TEST_ENV)
    return assertions.Template.from_stack(stack)
```

#### 9c. Rename the reserved concurrency test

```python
# REMOVE this test:
# def test_lambda_reserved_concurrency_is_one(template): ...

# ADD this test instead:
def test_lambda_reserved_concurrency_is_two(template):
    """Reserved concurrency must be 2 to allow concurrent execution during traffic shifts."""
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "OrchestratorContainer-test",
        "ReservedConcurrentExecutions": 2,
    })
```

#### 9d. Add new tests — Lambda Version and Alias

```python
# ── Lambda Version + Alias ─────────────────────────────────────────────────

def test_lambda_version_created(template):
    """Stack must define a Lambda Version for blue-green deployment."""
    template.resource_count_is("AWS::Lambda::Version", 1)


def test_lambda_version_description_contains_sha(template):
    """Lambda Version description must embed the deploy identifier."""
    template.has_resource_properties("AWS::Lambda::Version", {
        "Description": "deploy-abc1234",
    })


def test_lambda_version_has_retain_policy(template):
    """Lambda Version must have DeletionPolicy=Retain so old versions survive rollbacks."""
    versions = template.find_resources("AWS::Lambda::Version")
    assert len(versions) == 1
    version_resource = list(versions.values())[0]
    assert version_resource.get("DeletionPolicy") == "Retain"


def test_lambda_alias_created(template):
    """Stack must define a Lambda Alias for stable traffic routing."""
    template.resource_count_is("AWS::Lambda::Alias", 1)


def test_lambda_alias_name_is_live(template):
    """The Lambda Alias must be named 'live'."""
    template.has_resource_properties("AWS::Lambda::Alias", {
        "Name": "live",
    })
```

#### 9e. Add new tests — CloudWatch Alarm

```python
# ── CloudWatch Alarm ───────────────────────────────────────────────────────

def test_cloudwatch_error_alarm_created(template):
    """Stack must define exactly one CloudWatch alarm for CodeDeploy rollback."""
    template.resource_count_is("AWS::CloudWatch::Alarm", 1)


def test_cloudwatch_error_alarm_name(template):
    """CloudWatch alarm name must identify the service and environment."""
    template.has_resource_properties("AWS::CloudWatch::Alarm", {
        "AlarmName": "OrchestratorContainer-test-Errors",
    })


def test_cloudwatch_error_alarm_triggers_on_single_error(template):
    """Any single Lambda error must trigger the rollback alarm."""
    template.has_resource_properties("AWS::CloudWatch::Alarm", {
        "Threshold": 1,
        "EvaluationPeriods": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "TreatMissingData": "notBreaching",
    })
```

#### 9f. Add new tests — CodeDeploy

```python
# ── CodeDeploy ─────────────────────────────────────────────────────────────

def test_codedeploy_application_created(template):
    """Stack must define exactly one CodeDeploy application."""
    template.resource_count_is("AWS::CodeDeploy::Application", 1)


def test_codedeploy_application_name(template):
    """CodeDeploy application must use Lambda compute platform."""
    template.has_resource_properties("AWS::CodeDeploy::Application", {
        "ApplicationName": "OrchestratorContainer-test",
        "ComputePlatform": "Lambda",
    })


def test_codedeploy_deployment_group_created(template):
    """Stack must define exactly one CodeDeploy deployment group."""
    template.resource_count_is("AWS::CodeDeploy::DeploymentGroup", 1)


def test_codedeploy_deployment_config_is_canary(template):
    """Deployment config must be CANARY_10PERCENT_10MINUTES."""
    template.has_resource_properties("AWS::CodeDeploy::DeploymentGroup", {
        "DeploymentConfigName": "CodeDeployDefault.LambdaCanary10Percent10Minutes",
    })


def test_codedeploy_auto_rollback_on_alarm(template):
    """Auto-rollback must fire on alarm and failed deployment."""
    template.has_resource_properties("AWS::CodeDeploy::DeploymentGroup", {
        "AutoRollbackConfiguration": {
            "Enabled": True,
            "Events": assertions.Match.array_with([
                "DEPLOYMENT_FAILURE",
                "DEPLOYMENT_STOP_ON_ALARM",
            ]),
        }
    })
```

Run the tests to confirm everything passes:

```bash
cd services/ai-doc-processor/infra
pytest tests/ -v
```

Expected: all existing tests pass + 13 new tests added.

---

## Part 3 — Update the CI/CD Pipeline

The pipeline needs to pass `git_sha` to CDK on every deploy so that a new Lambda version is published on each push.

Open `.github/workflows/pipeline.yml` and find the `deploy-ai-doc-processor` job. Update the **CDK Deploy** step:

```yaml
# BEFORE:
- name: CDK Deploy
  working-directory: services/ai-doc-processor/infra
  env:
    CDK_DEFAULT_ACCOUNT: ${{ secrets.AWS_ACCOUNT_ID }}
    CDK_DEFAULT_REGION: ${{ secrets.AWS_REGION }}
  run: cdk deploy AIDocProcessorStack --require-approval never

# AFTER:
- name: CDK Deploy
  working-directory: services/ai-doc-processor/infra
  env:
    CDK_DEFAULT_ACCOUNT: ${{ secrets.AWS_ACCOUNT_ID }}
    CDK_DEFAULT_REGION: ${{ secrets.AWS_REGION }}
  run: |
    cdk deploy AIDocProcessorStack \
      --require-approval never \
      -c git_sha=${{ github.sha }}
```

`${{ github.sha }}` is the 40-character Git commit hash that GitHub Actions provides automatically for every workflow run. It is unique per push, so every deploy creates a new Lambda version.

---

## Part 4 — Test the Deployment

### Test A: Observe a canary deployment in the AWS Console

1. Push any change to `services/ai-doc-processor/` to trigger the pipeline.
2. Wait for the deploy job to reach the **CDK Deploy** step and show `in progress`.
3. Open the AWS Console → **CodeDeploy → Applications → OrchestratorContainer-dev**.
4. Click **Deployment groups → OrchestratorContainer-dev-DG**.
5. Under **Deployments**, click the most recent deployment ID.

You should see a traffic shifting diagram:

```
Original revision (version N)    ██████████████████  90%
Replacement revision (version N+1)  ██               10%

Status: In progress — waiting 10 minutes before completing shift
```

6. Wait 10 minutes (if no errors occur). The deployment completes:

```
Original revision (version N)    ░░░░░░░░░░░░░░░░░░   0%
Replacement revision (version N+1)  ██████████████████ 100%

Status: Succeeded
```

### Test B: Trigger an automatic rollback

To observe the rollback mechanism working, you can manually put the CloudWatch alarm into ALARM state:

```bash
# Put the alarm into ALARM state (simulates a Lambda error)
aws cloudwatch set-alarm-state \
  --alarm-name "OrchestratorContainer-dev-Errors" \
  --state-value ALARM \
  --state-reason "Manual test of rollback mechanism" \
  --region ap-southeast-2
```

Then start a new deployment (push a commit). Within minutes of the deployment starting, CodeDeploy will detect the alarm in ALARM state and roll back to the previous version.

```bash
# Reset the alarm after testing
aws cloudwatch set-alarm-state \
  --alarm-name "OrchestratorContainer-dev-Errors" \
  --state-value OK \
  --state-reason "Manual reset after rollback test" \
  --region ap-southeast-2
```

> **Note:** Remember to reset the alarm state before pushing real production changes.

### Test C: Verify the alias ARN is used by API Gateway

```bash
# Check which ARN API Gateway is invoking
aws apigateway get-integration \
  --rest-api-id <YOUR_API_ID> \
  --resource-id <ITEMS_RESOURCE_ID> \
  --http-method GET \
  --region ap-southeast-2 \
  --query 'uri'
```

The URI should contain `:function:OrchestratorContainer-dev:live/invocations` (the `:live` qualifier indicates the alias is being used, not the raw function).

---

## Verify & Validate

After a successful deployment, run these checks:

#### ✅ 1. Lambda alias exists and points to the new version

```bash
aws lambda get-alias \
  --function-name OrchestratorContainer-dev \
  --name live \
  --region ap-southeast-2
```

Expected:
```json
{
    "AliasArn": "arn:aws:lambda:ap-southeast-2:…:function:OrchestratorContainer-dev:live",
    "Name": "live",
    "FunctionVersion": "2"
}
```

#### ✅ 2. Old Lambda version is retained

```bash
aws lambda list-versions-by-function \
  --function-name OrchestratorContainer-dev \
  --region ap-southeast-2 \
  --query 'Versions[].Version'
```

Expected: `["$LATEST", "1", "2"]` — old versions are **not** deleted.

#### ✅ 3. CloudWatch alarm is in OK state

```bash
aws cloudwatch describe-alarms \
  --alarm-names "OrchestratorContainer-dev-Errors" \
  --region ap-southeast-2 \
  --query 'MetricAlarms[0].StateValue'
```

Expected: `"OK"` (or `"INSUFFICIENT_DATA"` if no invocations have occurred yet).

#### ✅ 4. CodeDeploy deployment succeeded

```bash
aws deploy list-deployments \
  --application-name OrchestratorContainer-dev \
  --deployment-group-name OrchestratorContainer-dev-DG \
  --region ap-southeast-2 \
  --query 'deployments[0]'
```

Use the returned deployment ID to check status:

```bash
aws deploy get-deployment \
  --deployment-id <DEPLOYMENT_ID> \
  --region ap-southeast-2 \
  --query 'deploymentInfo.status'
```

Expected: `"Succeeded"`.

#### ✅ 5. All unit tests pass

```bash
cd services/ai-doc-processor/infra
pytest tests/ -v --tb=short
```

Expected: 22 tests passed (13 original + 9 new version/alias/alarm/CodeDeploy assertions).

#### ✅ 6. API Gateway still returns responses

```bash
# Get the API URL from CloudFormation outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name AIDocProcessorStack \
  --region ap-southeast-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

curl -s "${API_URL}items"
```

Expected: an HTTP 200 response (the Lambda returns the same result as before — only the invocation path changed, not the function logic).

---

## Troubleshooting

#### `UpdatePolicy is not supported for resource type AWS::Lambda::Alias`

This error means you are running an older version of CDK or the `aws-cdk-lib` package. Verify your version:

```bash
cdk --version           # should be 2.x
pip show aws-cdk-lib    # should be 2.208.0 or later
```

Upgrade if needed:
```bash
npm install -g aws-cdk@latest
pip install --upgrade aws-cdk-lib
```

---

#### `CodeDeploy deployment fails: Alias function version is $LATEST`

CodeDeploy cannot manage traffic to `$LATEST` — it requires an explicit numbered version. This error means the `_lambda.Version` construct did not create a new version.

Confirm `git_sha` is being passed:

```bash
# In the pipeline, check that the deploy command includes -c git_sha=...
cdk deploy AIDocProcessorStack --require-approval never -c git_sha=abc1234
```

If running locally without passing `git_sha`, the fallback value `"local"` is used. Run a second deploy with a different value:

```bash
cdk deploy AIDocProcessorStack -c git_sha=test-001 -c account=… -c region=…
cdk deploy AIDocProcessorStack -c git_sha=test-002 -c account=… -c region=…
```

Each call will publish a new version.

---

#### Deployment stuck in `IN_PROGRESS` for more than 15 minutes

The canary window for `CANARY_10PERCENT_10MINUTES` is 10 minutes, not 15. If the deployment is stuck beyond that, check:

```bash
# View deployment lifecycle events
aws deploy get-deployment \
  --deployment-id <DEPLOYMENT_ID> \
  --region ap-southeast-2 \
  --query 'deploymentInfo.deploymentOverview'
```

If `Failed` or `Stopped`, check the lifecycle event log in the CodeDeploy console for the specific failure reason.

---

#### `TooManyRequestsException` during traffic shift

This means both Lambda versions are receiving concurrent requests but `reserved_concurrent_executions` is still set to `1`. Verify the stack change was applied:

```bash
aws lambda get-function-concurrency \
  --function-name OrchestratorContainer-dev \
  --region ap-southeast-2
```

Expected: `{ "ReservedConcurrentExecutions": 2 }`. If it still shows `1`, the CDK change has not been deployed yet — run `cdk deploy` with the correct context variables.

---

#### CloudWatch alarm fires on every deployment (false positives)

If your alarm fires during deployments when the Lambda is actually healthy, the threshold or period may be too aggressive. Consider adjusting:

```python
# More lenient: require 3 errors in a 5-minute window before rolling back
error_alarm = cloudwatch.Alarm(
    ...
    metric=live_alias.metric_errors(period=Duration.minutes(5), statistic="Sum"),
    threshold=3,
    evaluation_periods=1,
    ...
)
```

> **Caution:** A more lenient alarm means more bad traffic reaches users before rollback is triggered. Only loosen the threshold if false positives are causing deployment failures on a demonstrably healthy function.

---

#### Old Lambda versions accumulating (storage costs)

Lambda versions are retained indefinitely due to `RemovalPolicy.RETAIN`. Lambda stores all published versions — each version of a container-based function counts toward your ECR storage. To clean up old versions periodically:

```bash
# List all versions older than the current alias target
CURRENT_VERSION=$(aws lambda get-alias \
  --function-name OrchestratorContainer-dev \
  --name live \
  --query 'FunctionVersion' \
  --output text \
  --region ap-southeast-2)

echo "Active version: $CURRENT_VERSION"

# List all versions (do not delete $LATEST or the current version)
aws lambda list-versions-by-function \
  --function-name OrchestratorContainer-dev \
  --region ap-southeast-2 \
  --query "Versions[?Version != '$LATEST' && Version != '$CURRENT_VERSION'].Version" \
  --output text
```

Delete old versions manually after confirming they are not referenced by any alias:

```bash
aws lambda delete-function \
  --function-name OrchestratorContainer-dev:1 \
  --region ap-southeast-2
```

> **Tip:** Automate version cleanup with a scheduled Lambda function or a GitHub Actions workflow that runs after each successful deployment.
