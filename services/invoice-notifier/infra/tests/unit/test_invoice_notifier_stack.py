"""
Unit tests for InvoiceNotifierStack.

DockerImageFunction is replaced with a lightweight stand-in (inline Python)
so that the suite runs without a Docker daemon.

Run:
    cd services/invoice-notifier/infra
    pytest tests/ -v
"""
import aws_cdk as core
import aws_cdk.assertions as assertions
import aws_cdk.aws_lambda as _lambda
import pytest
from unittest.mock import patch

from stack.invoice_notifier_stack import InvoiceNotifierStack

# ── Constants ─────────────────────────────────────────────────────────────────

TEST_ACCOUNT = "123456789012"
TEST_REGION  = "ap-southeast-2"
TEST_ENV     = core.Environment(account=TEST_ACCOUNT, region=TEST_REGION)


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeDockerImageFunction(_lambda.Function):
    """Stand-in for DockerImageFunction that avoids a real Docker build."""

    def __init__(self, scope, id, *, function_name=None, timeout=None,
                 reserved_concurrent_executions=None, **kwargs):
        kwargs.pop("code", None)
        super().__init__(
            scope, id,
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.InlineCode("def handler(e, c): pass"),
            function_name=function_name,
            timeout=timeout,
            reserved_concurrent_executions=reserved_concurrent_executions,
        )


@pytest.fixture(scope="module")
def template() -> assertions.Template:
    """Synthesise InvoiceNotifierStack once and share across all tests."""
    app = core.App(context={"env_name": "test"})
    with patch("stack.invoice_notifier_stack._lambda.DockerImageFunction", _FakeDockerImageFunction):
        stack = InvoiceNotifierStack(app, "TestStack", env=TEST_ENV)
    return assertions.Template.from_stack(stack)


# ── Lambda ────────────────────────────────────────────────────────────────────

def test_lambda_function_name(template):
    """Invoice notifier Lambda name must follow InvoiceNotifier-<env_name>."""
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "InvoiceNotifier-test",
    })


def test_lambda_timeout_is_five_minutes(template):
    """Lambda timeout must be 300 s (5 min)."""
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "InvoiceNotifier-test",
        "Timeout": 300,
    })


# ── IAM ───────────────────────────────────────────────────────────────────────

def test_bedrock_invoke_model_policy_attached(template):
    """Lambda execution role must include bedrock:InvokeModel."""
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": assertions.Match.array_with([
                assertions.Match.object_like({
                    "Action": assertions.Match.array_with(["bedrock:InvokeModel"]),
                    "Effect": "Allow",
                    "Resource": "*",
                })
            ])
        }
    })


# ── API Gateway ───────────────────────────────────────────────────────────────

def test_api_gateway_rest_api_created(template):
    """Stack must define exactly one REST API."""
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)


def test_api_gateway_notify_resource_path(template):
    """The /notify path part must be defined in the REST API."""
    template.has_resource_properties("AWS::ApiGateway::Resource", {
        "PathPart": "notify",
    })


def test_api_gateway_post_method_exists(template):
    """A POST method must be wired to the /notify resource."""
    template.has_resource_properties("AWS::ApiGateway::Method", {
        "HttpMethod": "POST",
    })


# ── Outputs ───────────────────────────────────────────────────────────────────

def test_notifier_api_url_output_exported(template):
    """CloudFormation stack must export NotifierApiUrl."""
    template.has_output("NotifierApiUrl", {})
