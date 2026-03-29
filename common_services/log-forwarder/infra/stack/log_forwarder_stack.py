"""
LogForwarderStack
=================
A shared, reusable CDK stack that provisions:

  1. Amazon OpenSearch Service domain  — stores structured log documents
  2. Log Forwarder Lambda              — decodes CloudWatch Logs batches
                                         and bulk-indexes them into OpenSearch

Other service stacks (e.g. AiDocProcessorStack) subscribe their CloudWatch
Log Groups to the Forwarder Lambda ARN, which is exported as a CloudFormation
named export so any stack in the same account/region can reference it:

    Fn.import_value(f"LogForwarderArn-{env_name}")
    Fn.import_value(f"OpenSearchDashboardUrl-{env_name}")
    Fn.import_value(f"OpenSearchEndpoint-{env_name}")
"""

import json
import os
import shutil
import subprocess
import sys

import aws_cdk as cdk
import jsii
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_opensearchservice as opensearch,
    aws_secretsmanager as secretsmanager,
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from constructs_lib.base_lambda_stack import BaseServiceStack

# Absolute path to the Lambda source directory, resolved relative to this file
# so it works regardless of where `cdk` is invoked from.
_APP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "app", "log_forwarder")
)


@jsii.implements(cdk.ILocalBundling)
class _LocalPipBundler:
    """Installs Python deps and copies source using the *host* pip.

    CDK tries this first; if it raises or returns False, it falls back to
    Docker.  This avoids the ``docker exited with status 127`` error that
    occurs on Windows when the SAM build image doesn't have ``bash`` on PATH.
    """

    def try_bundle(self, output_dir: str, options: cdk.BundlingOptions) -> bool:  # type: ignore[override]
        try:
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "-r", os.path.join(_APP_DIR, "requirements.txt"),
                    "-t", output_dir,
                    "--quiet",
                ],
                check=True,
            )
            # Copy Lambda source files on top of the installed packages
            for item in os.listdir(_APP_DIR):
                src = os.path.join(_APP_DIR, item)
                dst = os.path.join(output_dir, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalBundler] host pip install failed ({exc}); falling back to Docker.")
            return False


class LogForwarderStack(BaseServiceStack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, service_name="log-forwarder", **kwargs)

        # ── Master-user secret ────────────────────────────────────────────
        # A random 16-char password is generated at deploy time and stored in
        # Secrets Manager.  Retrieve it after deployment with:
        #
        #   aws secretsmanager get-secret-value \
        #     --secret-id /opensearch/common-logs-<env>/master-user \
        #     --query SecretString --output text
        #
        master_user_secret = secretsmanager.Secret(
            self,
            "OpenSearchMasterUserSecret",
            secret_name=f"/opensearch/common-logs-{self.env_name}/master-user",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"username": "admin"}),
                generate_string_key="password",
                exclude_characters=' %+~`#$&*()|[]{}:;<>?!\'/@"\\',
                password_length=16,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Amazon OpenSearch Service domain ──────────────────────────────
        # t3.small.search + 20 GB EBS is cost-efficient for a dev environment.
        # For production: upgrade to m6g.large.search + Multi-AZ.
        #
        # Fine-Grained Access Control (FGAC) is enabled so Dashboards shows a
        # native login screen instead of requiring IAM-signed requests from the
        # browser.  The domain-level access policy is opened to * — FGAC's
        # index/document-level permissions handle the actual authorisation.
        domain = opensearch.Domain(
            self,
            "ObservabilityDomain",
            domain_name=f"common-logs-{self.env_name}",
            version=opensearch.EngineVersion.OPENSEARCH_2_19,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type="t3.small.search",
                multi_az_with_standby_enabled=False,  # T3 instances don't support Multi-AZ with standby
            ),
            ebs=opensearch.EbsOptions(
                enabled=True,
                volume_size=20,  # GB — gp2 by default (adequate for dev)
            ),
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            node_to_node_encryption=True,
            enforce_https=True,
            # FGAC requires all three security options above to be enabled.
            fine_grained_access_control=opensearch.AdvancedSecurityOptions(
                master_user_name="admin",
                master_user_password=master_user_secret.secret_value_from_json("password"),
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Open domain-level access policy — FGAC handles fine-grained authz.
        # Without this, the domain would require every request to carry IAM
        # credentials, which browsers cannot provide.
        domain.add_access_policies(
            iam.PolicyStatement(
                principals=[iam.AnyPrincipal()],
                actions=["es:*"],
                resources=[f"{domain.domain_arn}/*"],
            )
        )

        # ── Log Forwarder Lambda ───────────────────────────────────────────
        # Triggered by CloudWatch Logs subscription filters on any service
        # log group. Decodes the gzip+base64 envelope and bulk-indexes each
        # structured log event into the OpenSearch domain above.
        #
        # _LocalPipBundler is tried first (host pip, no Docker needed).
        # Docker is used as a fallback for CI environments where host Python
        # may not have the right architecture for Lambda-compatible wheels.
        log_forwarder_lambda = _lambda.Function(
            self,
            "LogForwarderLambda",
            function_name=f"LogForwarder-{self.env_name}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(
                _APP_DIR,
                bundling=cdk.BundlingOptions(
                    local=_LocalPipBundler(),
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        (
                            "pip install -r requirements.txt -t /asset-output "
                            "&& cp -au . /asset-output"
                        ),
                    ],
                ),
            ),
            timeout=Duration.minutes(1),
            memory_size=256,
            environment={
                "OPENSEARCH_ENDPOINT": domain.domain_endpoint,
                "INDEX_NAME": "lambda-logs",
                "OPENSEARCH_SECRET_ARN": master_user_secret.secret_arn,
                # AWS_REGION is injected automatically by the Lambda runtime —
                # setting it manually is rejected by CDK/Lambda as a reserved var.
            },
        )

        # Grant the forwarder Lambda read/write access to OpenSearch
        domain.grant_read_write(log_forwarder_lambda)

        # Grant the forwarder Lambda permission to read the FGAC master-user secret.
        # With FGAC enabled the Lambda authenticates via HTTP basic auth rather than
        # IAM SigV4 (which would require an OpenSearch internal role mapping).
        master_user_secret.grant_read(log_forwarder_lambda)

        # ── CloudFormation Outputs (exported for cross-stack references) ───
        # Other stacks import these with:
        #   cdk.Fn.import_value(f"LogForwarderArn-{env_name}")
        #   cdk.Fn.import_value(f"OpenSearchDashboardUrl-{env_name}")
        #   cdk.Fn.import_value(f"OpenSearchEndpoint-{env_name}")

        CfnOutput(
            self,
            "LogForwarderArn",
            value=log_forwarder_lambda.function_arn,
            export_name=f"LogForwarderArn-{self.env_name}",
            description="Log Forwarder Lambda ARN — import into service stacks for subscription filters",
        )

        CfnOutput(
            self,
            "OpenSearchDashboardUrl",
            value=f"https://{domain.domain_endpoint}/_dashboards",
            export_name=f"OpenSearchDashboardUrl-{self.env_name}",
            description="OpenSearch Dashboards URL — sign in with username 'admin' and the password from the master-user secret",
        )

        CfnOutput(
            self,
            "OpenSearchMasterUserSecretArn",
            value=master_user_secret.secret_arn,
            description=(
                "Retrieve Dashboards admin password: "
                "aws secretsmanager get-secret-value "
                f"--secret-id /opensearch/common-logs-{self.env_name}/master-user "
                "--query SecretString --output text"
            ),
        )

        CfnOutput(
            self,
            "OpenSearchDomainEndpoint",
            value=domain.domain_endpoint,
            export_name=f"OpenSearchEndpoint-{self.env_name}",
            description="OpenSearch domain endpoint for direct REST API access",
        )
