from aws_cdk import Stack, Tags
from constructs import Construct


class BaseServiceStack(Stack):
    """Base CDK Stack for all services in this mono-repo.

    Provides common tagging and exposes self.env_name so service
    stacks do not each need to repeat the context-lookup boilerplate.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        service_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.service_name = service_name
        self.env_name = self.node.try_get_context("env_name") or "dev"

        Tags.of(self).add("Service", service_name)
        Tags.of(self).add("ManagedBy", "CDK")
        Tags.of(self).add("Environment", self.env_name)
