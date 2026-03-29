# CI/CD Pipeline — GitHub Actions for the AI Doc Processor

> **Automating Test, Synth, and Deploy with Keyless AWS Authentication**
> A step-by-step guide to wiring GitHub Actions into the serverless CDK application so every pull request is validated and every merge to `main` automatically deploys to AWS.

---

## Table of Contents

- [What This Lab Covers](#what-this-lab-covers)
- [How the Pipeline Works](#how-the-pipeline-works)
- [Prerequisites](#prerequisites)
- [Part 1 — One-Time AWS Setup](#part-1--one-time-aws-setup)
  - [Step 0 — Configure Your Lab Account for CDK Bootstrap](#step-0--configure-your-lab-account-for-cdk-bootstrap)
- [Part 2 — CDK Bootstrap](#part-2--cdk-bootstrap)
- [Part 3 — GitHub Repository Setup](#part-3--github-repository-setup)
- [Part 4 — Understanding the Workflow File](#part-4--understanding-the-workflow-file)
  - [Job 2 — Copilot Code Review](#job-2--code-review-pr-only-runs-in-parallel-with-test)
  - [Job 3 — Security Scan](#job-3--security-always-runs-in-parallel-with-test)
- [Part 5 — Test the Pipeline End-to-End](#part-5--test-the-pipeline-end-to-end)
- [Verify & Validate](#verify--validate)
- [Troubleshooting](#troubleshooting)

---

## What This Lab Covers

Manual `cdk deploy` works on a developer's machine but breaks down on a team — different local environments, no audit trail, and no gate on broken code reaching AWS. This lab replaces that manual step with a fully automated pipeline using **GitHub Actions**.

By the end you will have:

- Pull requests automatically **tested** (pytest) and **synthesised** (CDK template validation) before merge
- Pull requests automatically **reviewed** by GitHub Copilot AI with inline code comments
- Every run **security-scanned** across four layers: Python SAST, dependency CVEs, IaC misconfigurations, and filesystem vulnerabilities
- Merges to `main` automatically **deployed** to the `dev` AWS environment
- AWS credentials handled via **OIDC (keyless auth)** — no IAM access keys stored anywhere
- All security findings surfaced in the **GitHub Security tab** (Code Scanning Alerts)

---

## How the Pipeline Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PULL REQUEST                                                            │
│                                                                          │
│         ┌─► test job ─────────────────────────────────► synth job        │
│         │   (pytest)                                    (cdk synth)      │
│  push ──┤                                                                │
│         ├─► code-review job  (runs in parallel)                          │
│         │   (GitHub Copilot AI review → PR comments)                     │
│         │                                                                │
│         └─► security job     (runs in parallel)                          │
│             ├─ bandit   (Python SAST)                                    │
│             ├─ pip-audit (dependency CVEs)                               │
│             ├─ checkov  (Dockerfile misconfigurations)                   │
│             └─ trivy    (filesystem vulnerabilities)                     │
│                 └──► GitHub Security tab (Code Scanning Alerts)          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│  PUSH TO MAIN                                                            │
│                                                                          │
│         ┌─► test job ─────────────────────────────────► deploy job       │
│         │   (pytest)                                    (cdk deploy)     │
│  push ──┤                                               ├─ Docker build  │
│         │                                               ├─ ECR push      │
│         └─► security job     (runs in parallel)         └─ CloudFormation│
│             ├─ bandit                                                    │
│             ├─ pip-audit                                                 │
│             ├─ checkov                                                   │
│             └─ trivy                                                     │
└──────────────────────────────────────────────────────────────────────────┘
```

**Job dependency summary:**

| Job | Trigger | Depends on | Blocks |
|-----|---------|-----------|--------|
| `test` | PR + push | — | `synth`, `deploy` |
| `code-review` | PR only | — | nothing |
| `security` | PR + push | — | `synth`, `deploy` |
| `synth` | PR only | `test` + `security` | — |
| `deploy` | push to main | `test` + `security` | — |

Both `test` **and** `security` must pass before `synth` or `deploy` can start.

**Authentication flow:**

```
GitHub Actions runner
       │
       │  presents OIDC token
       ▼
AWS STS (AssumeRoleWithWebIdentity)
       │
       │  returns short-lived credentials
       ▼
IAM Role (GitHubActionsDeployRole)
       │
       ▼
CDK deploy → ECR push → CloudFormation update
```

No AWS access keys are stored anywhere. GitHub's OIDC provider issues a short-lived token for each run.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| AWS Account | With permissions to create IAM roles, ECR, Lambda, S3, API Gateway, CloudFormation |
| GitHub Repository | The serverless-app project pushed to a GitHub repo |
| GitHub Copilot | Individual, Business, or Enterprise subscription with **code review** enabled (for the `code-review` job) |
| AWS CLI | Installed and configured locally (for the one-time bootstrap step) |
| Node.js | v18 or later (for CDK CLI) |
| Python | 3.12 (matches the Lambda runtime) |

> **No Copilot subscription?** The `code-review` job will be skipped or fail gracefully. All other jobs (test, security, synth, deploy) are unaffected — they have no dependency on `code-review`.

> **Confirm your repo name before starting.** You will need the exact `owner/repo-name` string (e.g. `acme-org/serverless-app`) in Step 3 of Part 1.

---

## Part 1 — One-Time AWS Setup

These steps are performed **once per AWS account**. They create the trust relationship that allows GitHub Actions to authenticate to AWS without storing any credentials.

### Step 0 — Configure Your Lab Account for CDK Bootstrap

Before creating any IAM resources, confirm your local AWS CLI is authenticated to the correct lab account and that the account has no service restrictions that would block CDK bootstrap. CDK bootstrap creates an S3 bucket, an ECR repository, and several IAM roles — all of which require the permissions below.

#### Configure AWS CLI credentials

**Option A — Named profile (recommended for lab accounts):**

```bash
aws configure --profile lab
# AWS Access Key ID:     <paste your lab account key>
# AWS Secret Access Key: <paste your lab account secret>
# Default region name:   ap-southeast-2
# Default output format: json

# Verify the profile works
aws sts get-caller-identity --profile lab
```

Set the profile for the rest of this session so you do not need to pass `--profile` on every command:

```bash
export AWS_PROFILE=lab
```

**Option B — Environment variables (common in temporary/session-based lab accounts):**

```bash
export AWS_ACCESS_KEY_ID="ASIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."          # required if using temporary credentials
export AWS_DEFAULT_REGION="ap-southeast-2"

# Verify
aws sts get-caller-identity
```

> **Lab account tip:** Many training environments (AWS Academy, event-based sandbox accounts) issue temporary credentials that include a `SessionToken`. If `aws sts get-caller-identity` returns `InvalidClientTokenId`, your session has expired — refresh the credentials from the lab portal and re-export.

#### Confirm the account ID and region

```bash
# Print account ID and region — you will use these values throughout this lab
echo "Account : $(aws sts get-caller-identity --query Account --output text)"
echo "Region  : ${AWS_DEFAULT_REGION:-$(aws configure get region)}"
```

Note both values. They go into the GitHub Secrets in Part 3.

#### Verify IAM permissions for CDK bootstrap

CDK bootstrap requires the ability to create S3 buckets, ECR repositories, IAM roles, and SSM parameters. Run these checks before proceeding:

```bash
# Check S3 access (CDK assets bucket)
aws s3 ls > /dev/null 2>&1 && echo "✅ S3 access OK" || echo "❌ S3 access denied"

# Check IAM access (CDK roles)
aws iam list-roles --max-items 1 > /dev/null 2>&1 && echo "✅ IAM access OK" || echo "❌ IAM access denied"

# Check ECR access (CDK container assets)
aws ecr describe-repositories > /dev/null 2>&1 && echo "✅ ECR access OK" || echo "❌ ECR access denied"

# Check CloudFormation access (CDKToolkit stack)
aws cloudformation list-stacks > /dev/null 2>&1 && echo "✅ CloudFormation access OK" || echo "❌ CloudFormation access denied"

# Check SSM access (CDK feature flags)
aws ssm get-parameters-by-path --path /cdk-bootstrap > /dev/null 2>&1 && echo "✅ SSM access OK" || echo "❌ SSM access denied"
```

All five should return `✅`. If any return `❌`, your lab account does not have the required permissions — contact your AWS administrator or trainer before continuing.

#### Check for S3 Block Public Access at the account level

CDK's assets S3 bucket is private, but account-level Block Public Access settings can still interfere with bucket creation in some restricted environments. Confirm the setting:

```bash
aws s3control get-public-access-block \
  --account-id $(aws sts get-caller-identity --query Account --output text)
```

All four values (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`) can remain `true` — CDK does not need public access. This check is only to confirm the command runs without an access-denied error.

#### Install CDK CLI locally (if not already installed)

```bash
# Requires Node.js v18+
node --version        # confirm Node is installed

# Install CDK globally
npm install -g aws-cdk

# Verify
cdk --version         # expected: 2.x.x (build ...)
```

---

### Step 1 — Add the GitHub OIDC Identity Provider

GitHub Actions uses OpenID Connect (OIDC) to prove its identity to AWS. AWS needs to be told to trust tokens issued by GitHub.

**Option A — AWS Console:**

1. Open the **IAM Console** → **Identity providers** (left sidebar)
2. Click **Add provider**
3. Fill in:
   - Provider type: **OpenID Connect**
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Click **Get thumbprint**
   - Audience: `sts.amazonaws.com`
4. Click **Add provider**

**Option B — AWS CLI (faster):**

```bash
# Add GitHub as a trusted OIDC identity provider
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

echo "OIDC provider created"
```

> **Note:** The thumbprint is a fixed value published by GitHub. It does not change between accounts.

---

### Step 2 — Create the GitHub Actions IAM Deploy Role

This role is assumed by GitHub Actions during every pipeline run. It needs enough permissions to run CDK (CloudFormation, ECR, Lambda, S3, IAM, API Gateway).

**Option A — AWS Console:**

1. Open **IAM → Roles → Create role**
2. Trusted entity: **Web identity**
3. Identity provider: `token.actions.githubusercontent.com`
4. Audience: `sts.amazonaws.com`
5. Click **Next**
6. Add condition (click **Add condition**):
   - Condition key: `token.actions.githubusercontent.com:sub`
   - Operator: `StringEquals`
   - Value: `repo:YOUR_GITHUB_ORG/YOUR_REPO_NAME:ref:refs/heads/main`

   > Replace `YOUR_GITHUB_ORG/YOUR_REPO_NAME` with your actual GitHub org and repository name.

7. Attach permission policy: **AdministratorAccess**

   > **Note for production:** `AdministratorAccess` is used here because CDK needs to create and manage a wide range of services. In a production environment, scope this down to only the services your stack uses.

8. Role name: `GitHubActionsDeployRole`
9. Click **Create role**
10. Open the newly created role and **copy the Role ARN** — you will need it in Part 3.

**Option B — AWS CLI:**

```bash
# Set your GitHub org and repo name
GITHUB_ORG="your-github-org"
REPO_NAME="your-repo-name"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create the IAM role with GitHub OIDC trust policy
aws iam create-role \
  --role-name GitHubActionsDeployRole \
  --assume-role-policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Principal\": {
          \"Federated\": \"arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com\"
        },
        \"Action\": \"sts:AssumeRoleWithWebIdentity\",
        \"Condition\": {
          \"StringEquals\": {
            \"token.actions.githubusercontent.com:aud\": \"sts.amazonaws.com\",
            \"token.actions.githubusercontent.com:sub\": \"repo:${GITHUB_ORG}/${REPO_NAME}:ref:refs/heads/main\"
          }
        }
      }
    ]
  }"

# Attach AdministratorAccess (scope down for production)
aws iam attach-role-policy \
  --role-name GitHubActionsDeployRole \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

# Print the role ARN — save this for Part 3
aws iam get-role \
  --role-name GitHubActionsDeployRole \
  --query 'Role.Arn' \
  --output text
```

**Save the Role ARN** — it looks like:
```
arn:aws:iam::123456789012:role/GitHubActionsDeployRole
```

---

### Step 3 — Verify the Trust Policy

Confirm the trust relationship is correct before moving on:

```bash
aws iam get-role \
  --role-name GitHubActionsDeployRole \
  --query 'Role.AssumeRolePolicyDocument' \
  --output json
```

You should see the `token.actions.githubusercontent.com` principal and your repo's `sub` condition in the output. If the condition shows the wrong org/repo name, the role assumption will silently fail during pipeline runs.

---

## Part 2 — CDK Bootstrap

CDK Bootstrap creates the supporting infrastructure in your AWS account that CDK needs to operate — an S3 bucket for assets, an ECR repository for staging Docker images, and a set of IAM roles for the CloudFormation deployment process.

> **This command must be run once per AWS account per region.** If you have previously run `cdk bootstrap` in this account and region, skip this step.

```bash
# Set your account and region
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION="ap-southeast-2"   # Change if you use a different region

# Bootstrap CDK
cd infra
cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_REGION} --profile lab
```

Expected output ends with:
```
 ✅  Environment aws://123456789012/ap-southeast-2 bootstrapped.
```

**What was created (visible in CloudFormation):**

| Resource | Name | Purpose |
|----------|------|---------|
| CloudFormation Stack | `CDKToolkit` | Manages all bootstrap resources |
| S3 Bucket | `cdk-hnb659fds-assets-ACCOUNT-REGION` | Stores Lambda zips and CloudFormation templates |
| ECR Repository | `cdk-hnb659fds-container-assets-ACCOUNT-REGION` | Stages Docker images before Lambda deployment |
| IAM Roles | `cdk-*-deploy-role-*`, `cdk-*-cfn-exec-role-*` | Used by CDK during deployment |

> **Verify:** Navigate to **CloudFormation → Stacks** in the AWS Console. You should see a stack named `CDKToolkit` with status `CREATE_COMPLETE`.

---

## Part 3 — GitHub Repository Setup

GitHub Actions reads secrets from the repository's settings. Three secrets are required.

### Step 1 — Gather the values

| Secret | How to get it |
|--------|---------------|
| `AWS_ACCOUNT_ID` | Run `aws sts get-caller-identity --query Account --output text` |
| `AWS_REGION` | Your deployment region, e.g. `ap-southeast-2` |
| `AWS_DEPLOY_ROLE_ARN` | The Role ARN you saved at the end of Part 1, Step 2 |

### Step 2 — Add secrets to GitHub

1. Open your repository on GitHub
2. Go to **Settings** (top tab bar) → **Secrets and variables** (left sidebar) → **Actions**
3. Click **New repository secret** for each of the three secrets:

**Secret 1:**
- Name: `AWS_ACCOUNT_ID`
- Secret: *(your 12-digit account number)*

**Secret 2:**
- Name: `AWS_REGION`
- Secret: `ap-southeast-2`

**Secret 3:**
- Name: `AWS_DEPLOY_ROLE_ARN`
- Secret: `arn:aws:iam::123456789012:role/GitHubActionsDeployRole` *(your actual ARN)*

4. After adding all three, the **Actions secrets** page should show:

```
AWS_ACCOUNT_ID      Updated just now
AWS_DEPLOY_ROLE_ARN Updated just now
AWS_REGION          Updated just now
```

> **Tip:** Secret values are never shown after saving. If you enter a wrong value, use the **Update** button to overwrite it.

---

## Part 4 — Understanding the Workflow File

The pipeline is defined in `.github/workflows/pipeline.yml`. Let's walk through what each section does.

### Trigger

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

The workflow runs on two events:
- Any **push to `main`** (direct commit or merged PR)
- Any **pull request targeting `main`**

### Permissions

```yaml
permissions:
  id-token: write        # GitHub mints OIDC token → AWS STS (synth + deploy)
  contents: read         # checkout
  security-events: write # upload SARIF files to GitHub Security tab (security job)
```

The third line is new. `security-events: write` lets the security job post SARIF results to **Security → Code Scanning Alerts** in the repository. The `code-review` job overrides this at job level and adds `pull-requests: write` instead (it needs to post review comments, not OIDC tokens).

### Job 1 — `test` (always runs)

```yaml
test:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
        cache: pip
    - run: pip install -r requirements.txt -r requirements-dev.txt
      working-directory: infra
    - run: pytest tests/ -v
      working-directory: infra
```

Runs `pytest` against `infra/tests/`. The pip cache is keyed on `requirements.txt` — unchanged dependencies are restored from cache, making subsequent runs faster.

### Job 2 — `code-review` (PR only, runs in parallel with `test`)

```yaml
code-review:
  if: github.event_name == 'pull_request'
  permissions:
    contents: read
    pull-requests: write  # post review comments on the PR
  steps:
    - uses: actions/checkout@v4
    - uses: github/copilot-code-review@v1
      with:
        github-token: ${{ secrets.GITHUB_TOKEN }}
```

GitHub Copilot reads the diff of the pull request and posts AI-generated inline review comments — identifying potential bugs, security concerns, and style issues — directly on the PR, just like a human reviewer would.

**Before this job will work**, Copilot code review must be enabled for the repository:

1. Go to **Settings → Copilot → Code review** (repository settings)
2. Toggle **Enable automatic code review** → On
3. Optionally configure which file paths to include/exclude

> `pull-requests: write` is a job-level override. It replaces the workflow-level `id-token: write` for this job — Copilot review does not need AWS credentials.

### Job 3 — `security` (always runs, in parallel with `test`)

Four scanners run sequentially within the job. All upload SARIF to the GitHub Security tab. `continue-on-error: true` on each scanner ensures subsequent scanners always run even if one finds issues.

#### Layer 1 — Bandit (Python SAST)

```yaml
- name: Bandit — Python static security analysis
  run: |
    bandit -r app/ infra/infra/ \
      -f sarif -o bandit.sarif --exit-zero
  continue-on-error: true

- name: Upload Bandit SARIF to GitHub Security
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: bandit.sarif
    category: bandit
```

Bandit scans Python source files for common security issues: hardcoded credentials, SQL injection patterns, unsafe `subprocess` usage, insecure hash functions, etc. `--exit-zero` keeps the step green so subsequent scanners always run; severity is visible in the Security tab.

**Scanned paths:** `app/` (Lambda handler) and `infra/infra/` (CDK stack).

#### Layer 2 — pip-audit (Dependency CVEs)

```yaml
- name: pip-audit — dependency vulnerability scan
  run: |
    pip-audit \
      -r app/orchestrator/requirements.txt \
      -r infra/requirements.txt
  continue-on-error: true
```

`pip-audit` checks every pinned package in both `requirements.txt` files against the [PyPI Advisory Database](https://github.com/pypa/advisory-database). No API key required. Exits non-zero if vulnerabilities are found (recorded as a failed step, but pipeline continues).

#### Layer 3 — Checkov (IaC misconfigurations)

```yaml
- name: Checkov — Dockerfile security scan
  uses: bridgecrewio/checkov-action@master
  with:
    directory: app/orchestrator   # targets the Dockerfile
    framework: dockerfile
    output_format: sarif
    output_file_path: checkov.sarif
    soft_fail: true

- name: Upload Checkov SARIF to GitHub Security
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: checkov.sarif
    category: checkov
```

Checkov checks the Dockerfile against 50+ best-practice rules: running as root, using `latest` tags, missing `HEALTHCHECK`, exposing unnecessary ports, etc. `soft_fail: true` means Checkov findings never block the pipeline — they surface as alerts for the team to review.

#### Layer 4 — Trivy (Filesystem vulnerability scan)

```yaml
- name: Trivy — filesystem and dependency vulnerability scan
  uses: aquasecurity/trivy-action@master
  with:
    scan-type: fs
    scan-ref: .
    format: sarif
    output: trivy.sarif
    severity: CRITICAL,HIGH
    exit-code: "0"

- name: Upload Trivy SARIF to GitHub Security
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: trivy.sarif
    category: trivy
```

Trivy scans the entire repository filesystem — Python packages, OS packages referenced in the Dockerfile, and IaC files. Only `CRITICAL` and `HIGH` findings are reported. `exit-code: "0"` keeps the step green; results appear in the Security tab.

### Job 4 — `synth` (PR only, needs `test` + `security`)

```yaml
synth:
  needs: [test, security]
  if: github.event_name == 'pull_request'
  ...
  run: cdk synth
```

`cdk synth` renders the full CloudFormation template including **building the Docker image**. If the Dockerfile has a syntax error, a missing pip package, or the CDK Python code is broken, this step fails — before anything reaches AWS. It gates all PRs so broken infrastructure code cannot be merged.

Note that `synth` now waits for **both** `test` and `security` to pass. A PR with known critical vulnerabilities in dependencies will not be synthable.

### Job 5 — `deploy` (push to main only, needs `test` + `security`)

```yaml
deploy:
  needs: [test, security]
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  ...
  run: |
    cdk deploy --all \
      --require-approval never \
      --outputs-file ../cdk-outputs.json
```

`--require-approval never` suppresses the interactive confirmation prompt (required in CI). `--outputs-file` captures stack outputs (like the API Gateway URL) to a JSON file that is then printed to the job summary for easy access.

**What CDK deploy actually does during this step:**

```
1. Synthesises the CloudFormation template
2. Builds the Docker image (from app/orchestrator/Dockerfile)
3. Authenticates to ECR (using the OIDC role)
4. Pushes the Docker image to ECR
5. Uploads CloudFormation template to the CDK assets S3 bucket
6. Calls CloudFormation CreateChangeSet / ExecuteChangeSet
7. Waits for the stack to reach UPDATE_COMPLETE
```

---

## Part 5 — Test the Pipeline End-to-End

### Test A — Pull Request (triggers `test` + `synth`)

1. **Create a feature branch:**

```bash
git checkout -b feature/test-pipeline
```

2. **Make a small visible change** (e.g. add a comment to the Lambda function):

```bash
# Open app/orchestrator/lambda_function.py and add a comment at the top
# # Pipeline test - 2026
```

3. **Commit and push:**

```bash
git add app/orchestrator/lambda_function.py
git commit -m "test: trigger CI pipeline"
git push origin feature/test-pipeline
```

4. **Open a Pull Request** on GitHub:
   - Base: `main`
   - Compare: `feature/test-pipeline`

5. **Watch the checks appear** on the PR:
   - Navigate to the **Checks** tab on the pull request
   - You should see four jobs: `Unit Tests`, `Copilot Code Review`, `Security Scan`, and `CDK Synth (PR validation)`
   - `Unit Tests`, `Copilot Code Review`, and `Security Scan` start immediately in parallel
   - `CDK Synth (PR validation)` starts only after `Unit Tests` AND `Security Scan` finish

6. **Expected outcome:**

```
✅ Unit Tests                  — pytest passes
✅ Copilot Code Review         — AI review posted as PR comments (see Files Changed tab)
✅ Security Scan               — all four scanners ran; findings in Security tab
✅ CDK Synth (PR validation)   — CloudFormation template generated successfully
```

7. **View Copilot review comments:**
   - Click the **Files changed** tab on the pull request
   - GitHub Copilot's inline comments appear alongside the diff, marked with the Copilot icon

8. **View security findings:**
   - Go to **Security → Code scanning** (top navigation)
   - Findings from `bandit`, `checkov`, and `trivy` appear here categorised by severity
   - Each finding links back to the exact line of code

> **If `CDK Synth` fails:** The most common cause is missing secrets. Check that all three secrets are set correctly in **Settings → Secrets and variables → Actions**.
>
> **If `Copilot Code Review` fails with "Resource not accessible by integration":** Copilot code review is not enabled for the repository. Go to **Settings → Copilot → Code review** and enable it.

---

### Test B — Merge to Main (triggers `test` + `deploy`)

1. **Merge the pull request** by clicking **Merge pull request** on GitHub

   > Alternatively, push directly to main:
   > ```bash
   > git checkout main
   > git merge feature/test-pipeline
   > git push origin main
   > ```

2. **Navigate to Actions:**
   - Go to your repository → **Actions** tab
   - Click the most recent workflow run (triggered by your merge)

3. **Watch the jobs progress** — `Unit Tests` and `Security Scan` start in parallel immediately; `Deploy to Dev` starts after both pass. Total time is approximately 8–12 minutes:

```
✅ Unit Tests               (≈ 1 min)
  └─ Set up Python 3.12
  └─ Install CDK dependencies
  └─ Run unit tests

✅ Security Scan            (≈ 2 min, runs in parallel with Unit Tests)
  └─ Bandit — Python SAST
  └─ pip-audit — dependency CVE scan
  └─ Checkov — Dockerfile scan
  └─ Trivy — filesystem scan

⏳ Deploy to Dev            (starts after both above pass, ≈ 5–10 min)
  └─ Set up Python 3.12
  └─ Install CDK dependencies
  └─ Set up Node.js
  └─ Install AWS CDK CLI
  └─ Configure AWS credentials (OIDC)    ← short-lived token minted here
  └─ Set up Docker Buildx
  └─ CDK Deploy                          ← Docker build + ECR push + CloudFormation
  └─ Show stack outputs
```

4. **Read the stack outputs** from the job summary:

   After the deploy job completes, click **Summary** (top of the job page). You will see a section like:
   ```json
   {
     "AIDocProcessorStack": {
       "ApiUrl": "https://abc123.execute-api.ap-southeast-2.amazonaws.com/prod/"
     }
   }
   ```

---

## Verify & Validate

After a successful deploy, confirm the infrastructure is live in the AWS Console.

### Check 1 — CloudFormation Stack

1. Open **CloudFormation → Stacks**
2. Find `AIDocProcessorStack`
3. Status must be `UPDATE_COMPLETE` (or `CREATE_COMPLETE` on first deploy)
4. Click **Outputs** tab — verify `ApiUrl` is present

### Check 2 — ECR Image

1. Open **ECR → Repositories**
2. Find `ai-doc-processor-repo-dev`
3. Click the repository — verify a new image was pushed with a recent timestamp

### Check 3 — Lambda Function

1. Open **Lambda → Functions**
2. Find `OrchestratorContainer-dev`
3. Click the function → **Configuration** → **General configuration**
4. Verify the image URI matches the ECR image you just pushed

### Check 4 — API Gateway

```bash
# Get the API URL from CloudFormation outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name AIDocProcessorStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

echo "API URL: ${API_URL}"

# Call the /items endpoint
curl "${API_URL}items"
```

A `200 OK` response (even an empty one) confirms the full stack — API Gateway → Lambda container → response — is working end-to-end.

### Check 5 — GitHub Security Tab (Code Scanning Alerts)

1. Go to your repository → **Security** tab (top navigation) → **Code scanning**
2. You should see alerts from three tools: `bandit`, `checkov`, and `trivy`
3. Each alert shows:
   - The tool that found it (e.g. `bandit`)
   - Severity: `Critical`, `High`, `Medium`, or `Low`
   - The file and line number
   - A description of the finding and how to fix it
4. Alerts from `bandit` and `trivy` that were present in a **closed PR** will be automatically marked as fixed once the PR is merged

> **Tip:** If you see zero alerts, the scans either found nothing (good!) or the SARIF files were empty. Check the **Security Scan** job logs in Actions to distinguish between the two.

### Check 6 — Copilot Code Review Comments

1. Open any merged pull request
2. Click **Files changed**
3. Copilot's inline comments appear in the diff with the **Copilot icon** (a robot head)
4. Comments are also visible in the **Conversation** tab under the PR timeline

### Check 7 — Pipeline Run Summary

In GitHub → **Actions** → click the latest run → click **Deploy to Dev** job → scroll to the bottom. Confirm:

```
✅ CDK Deploy          exit code 0
✅ Show stack outputs  outputs printed to summary
```

---

## Troubleshooting

### `Copilot Code Review` fails with "Resource not accessible by integration"

**Cause:** GitHub Copilot code review is not enabled for the repository, or the `GITHUB_TOKEN` does not have `pull-requests: write`.

**Fix — Enable Copilot code review:**
1. Go to **Settings → Copilot → Code review** (repository settings)
2. Toggle **Enable automatic code review** → On
3. Re-run the failed workflow

**Fix — Check token permissions:**
The `code-review` job has a job-level `permissions` block. Confirm it includes:
```yaml
permissions:
  contents: read
  pull-requests: write
```
If the job is missing this block, the `GITHUB_TOKEN` defaults to read-only and cannot post review comments.

> **No Copilot subscription?** Remove or comment out the `code-review` job entirely. It has no dependency relationship with any other job so the rest of the pipeline is unaffected.

---

### `Security Scan` job — `Error: Path does not exist: bandit.sarif` (or checkov / trivy)

**Cause:** A scanner step errored before it could write its SARIF output file. Because `github/codeql-action/upload-sarif` then has no file to read, it fails with "Path does not exist" even though `if: always()` is set.

Common reasons a scanner exits without writing a file:
- The tool crashed or received a signal before opening the output file
- The network was unavailable when the action container started (Checkov, Trivy)
- A flag or parameter name changed in a newer version of the action

**How the pipeline prevents this:** The `Initialise SARIF placeholders` step runs immediately after installing the scanners and writes minimal valid empty SARIF files for all three tools:
```yaml
- name: Initialise SARIF placeholders
  run: |
    EMPTY='{"version":"2.1.0","$schema":"https://json.schemastore.org/sarif-2.1.0.json","runs":[]}'
    echo "$EMPTY" > bandit.sarif
    echo "$EMPTY" > checkov.sarif
    echo "$EMPTY" > trivy.sarif
```
Each scanner overwrites its placeholder on success. If a scanner fails mid-run, the placeholder (not a partial/corrupt file) is uploaded.

**If you still see this error** despite the placeholder step, check which step in the job failed *before* the placeholder step ran. Look at the `Initialise SARIF placeholders` step in the Actions log — if it shows as skipped or failed, a prior step (checkout, Python setup, pip install) caused the job to exit before reaching it.

---

### `Security Scan` job — SARIF upload fails with "Advanced Security must be enabled"

**Cause:** GitHub Advanced Security is not enabled on the repository. Code Scanning (SARIF upload) requires Advanced Security, which is free for public repositories but requires a **GitHub Enterprise** or **GitHub Team** plan for private repositories.

**Fix for private repositories:**
1. Go to **Settings → Security & analysis**
2. Enable **GitHub Advanced Security**
3. Enable **Code scanning**
4. Re-run the security job

**Workaround (no Advanced Security):** Replace the `github/codeql-action/upload-sarif@v3` steps with plain output:
```yaml
- name: Bandit — Python static security analysis
  run: bandit -r app/ infra/infra/ --exit-zero
  # outputs findings to stdout only — no Security tab integration
```

---

### `pip-audit` reports vulnerabilities — should this block the deploy?

**Current behaviour:** `pip-audit` runs with `continue-on-error: true`, so a vulnerable dependency shows the step as failed (orange ⚠) but does not block the pipeline.

**To make it blocking:** Remove `continue-on-error: true` from the pip-audit step. `pip-audit` exits non-zero when vulnerabilities are found, which will cause the `security` job to fail and therefore block `deploy` and `synth`.

**To fix the vulnerability itself:**
```bash
# Update the specific package to a patched version
pip-audit -r app/orchestrator/requirements.txt --fix
# or manually update the version pin in requirements.txt
```

---

### `Error: Not authorized to perform: sts:AssumeRoleWithWebIdentity`

**Cause:** The OIDC condition in the IAM trust policy does not match the GitHub context.

**Fix:** Check the `sub` condition in the trust policy:

```bash
aws iam get-role \
  --role-name GitHubActionsDeployRole \
  --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition'
```

The `sub` value must exactly match `repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main`. Common mistakes:
- Wrong org name or repo name (case-sensitive)
- Missing `ref:refs/heads/main` suffix
- Trailing slash

---

### `Error: Context value for 'account' and 'region' not provided`

**Cause:** `CDK_DEFAULT_ACCOUNT` or `CDK_DEFAULT_REGION` environment variables are not set, and the CDK context flags were not passed.

**Fix:** Confirm the secrets `AWS_ACCOUNT_ID` and `AWS_REGION` exist in GitHub and are correctly named. The workflow passes them as environment variables:
```yaml
env:
  CDK_DEFAULT_ACCOUNT: ${{ secrets.AWS_ACCOUNT_ID }}
  CDK_DEFAULT_REGION: ${{ secrets.AWS_REGION }}
```
If the secret name is wrong (e.g. `AWS_ACCOUNT` instead of `AWS_ACCOUNT_ID`), the variable will be empty.

---

### `Docker build failed` during `CDK Synth` or `CDK Deploy`

**Cause:** An error in `app/orchestrator/Dockerfile` or `app/orchestrator/requirements.txt`.

**Fix:** Reproduce locally:
```bash
cd app/orchestrator
docker build --build-arg MODEL_ID=test --build-arg PROMPT_BUCKET=test \
  --build-arg PROMPT_KEY=test .
```
Fix the error, commit, and push again.

---

### `CDKToolkit stack not found` or bootstrap errors

**Cause:** CDK bootstrap has not been run in the target account/region.

**Fix:** Run bootstrap locally (this is a one-time operation):
```bash
cd infra
CDK_DEFAULT_ACCOUNT=<YOUR_ACCOUNT_ID> \
CDK_DEFAULT_REGION=ap-southeast-2 \
cdk bootstrap
```

---

### `synth` job passes but `deploy` job is never triggered

**Cause:** The `deploy` job only runs on `push` events to `main`, not on pull request events.

**This is expected behaviour.** The `synth` job validates the PR. The `deploy` job only fires after the PR is merged and the resulting push to `main` is detected.

---

### Pipeline is slow (10+ minutes)

**Cause:** Docker image is being rebuilt from scratch on every run.

**Note:** CDK currently does not use Docker layer caching in GitHub Actions out of the box. Each `cdk deploy` triggers a full `docker build`. This is normal for the first implementation. Advanced optimisation (using `cache-from` in the Dockerfile and a dedicated ECR cache repo) can be added later.

---

*For questions about the application architecture itself, refer to `README.md` at the project root.*
