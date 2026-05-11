"""Legal-sources CDK stack: S3 corpus + lookup Lambda + ingest Lambda + schedule + search.

Region pinned to eu-central-1.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3vectors as s3vectors,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

REQUIRED_REGION = "eu-central-1"

REPO_ROOT = Path(__file__).resolve().parents[2]


class LegalSourcesStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        if self.region != "eu-central-1":
            raise RuntimeError(
                f"LegalSourcesStack must deploy to eu-central-1, got {self.region!r}"
            )

        kms_key = kms.Key(
            self,
            "CorpusKey",
            description="KIRA legal corpus encryption key",
            enable_key_rotation=True,
        )

        bucket = s3.Bucket(
            self,
            "CorpusBucket",
            bucket_name=f"kira-legal-corpus-{self.account}-eu-central-1",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            versioned=True,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ARM64 matches the bundling host on Apple Silicon (Docker pulls
        # arm64 layers by default), so compiled wheels (pydantic_core) load
        # natively. ARM64 Lambda is also ~20% cheaper than x86_64.
        arch = lambda_.Architecture.ARM_64

        # Code bundling: zip src/kira/ as the Lambda payload.
        # `platform="linux/arm64"` forces Docker to pull the matching image
        # variant even on multi-arch hosts.
        code = lambda_.Code.from_asset(
            str(REPO_ROOT),
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                platform="linux/arm64",
                command=[
                    "bash",
                    "-c",
                    " && ".join([
                        "pip install . -t /asset-output",
                        "cp -r src/kira /asset-output/kira",
                    ]),
                ],
            ),
        )

        lookup_fn = lambda_.Function(
            self,
            "LookupNormFn",
            function_name="kira-legal-lookup-norm",
            runtime=lambda_.Runtime.PYTHON_3_11,
            architecture=arch,
            handler="kira.legal_sources.adapters.lookup_handler.handler",
            code=code,
            memory_size=512,
            timeout=cdk.Duration.seconds(10),
            ephemeral_storage_size=cdk.Size.mebibytes(1024),
            environment={"LEGAL_CORPUS_BUCKET": bucket.bucket_name},
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read(lookup_fn)
        kms_key.grant_decrypt(lookup_fn)

        # Cloudflare Worker proxy URL — public, hardcoded here for visibility.
        proxy_url = (
            "https://kira-legaltext-gii-proxy.philip-trempler.workers.dev"
        )
        # Reference an EXISTING SecretsManager secret. Pre-create it via:
        #   aws secretsmanager create-secret \
        #     --region eu-central-1 \
        #     --name kira-legal/juris-proxy-auth \
        #     --secret-string '<the-value>'
        # This decouples secret lifecycle from stack lifecycle: rotate the
        # secret value without redeploying CFN.
        proxy_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "JurisProxySecret",
            "kira-legal/juris-proxy-auth",
        )

        ingest_environment = {
            "LEGAL_CORPUS_BUCKET": bucket.bucket_name,
            "LEGAL_INGEST_PROXY_URL": proxy_url,
            # The CFN template stores a `{{resolve:secretsmanager:...}}`
            # dynamic reference, not the literal secret. CloudFormation
            # resolves it at deploy time and writes the value into the
            # Lambda's environment configuration. Anyone with
            # lambda:GetFunctionConfiguration on this function can read the
            # plaintext secret — acceptable here because the secret only
            # rate-limits an open Cloudflare Worker proxying public legal
            # text, not a high-value auth boundary.
            "LEGAL_INGEST_PROXY_AUTH_VALUE": proxy_secret.secret_value.unsafe_unwrap(),
        }

        ingest_fn = lambda_.Function(
            self,
            "IngestFn",
            function_name="kira-legal-ingest",
            runtime=lambda_.Runtime.PYTHON_3_11,
            architecture=arch,
            handler="kira.legal_sources.adapters.ingest_handler.handler",
            code=code,
            memory_size=1536,
            timeout=cdk.Duration.minutes(15),
            environment=ingest_environment,
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read_write(ingest_fn)
        kms_key.grant_encrypt_decrypt(ingest_fn)

        events.Rule(
            self,
            "DailyIngest",
            rule_name="kira-legal-ingest-daily",
            schedule=events.Schedule.cron(minute="0", hour="2"),
            targets=[
                targets.LambdaFunction(
                    ingest_fn,
                    retry_attempts=2,
                ),
            ],
        )

        # CloudWatch supports periods up to 1 day (86400s); 36h is rejected.
        # Equivalent semantics: sum invocations over the last two 24h windows
        # and alarm if either window has zero invocations.
        cw.Alarm(
            self,
            "StaleCorpusAlarm",
            alarm_name="kira-legal-stale-corpus",
            metric=cw.Metric(
                namespace="AWS/Lambda",
                metric_name="Invocations",
                dimensions_map={"FunctionName": ingest_fn.function_name},
                period=cdk.Duration.hours(24),
                statistic="Sum",
            ),
            threshold=1,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description="Ingest has not run in 48h — corpus may be stale.",
        )

        # S3 Vectors bucket + index for semantic norm search.
        # The aws_s3vectors module only exposes L1 (Cfn*) constructs in this CDK version.
        vector_bucket = s3vectors.CfnVectorBucket(
            self,
            "LegalNormsVectorBucket",
            vector_bucket_name="kira-legal-norms",
        )

        vector_index = s3vectors.CfnIndex(
            self,
            "LegalNormsVectorIndex",
            vector_bucket_name="kira-legal-norms",
            index_name="kira-legal-norms",
            dimension=1024,
            distance_metric="cosine",
            data_type="float32",
        )
        vector_index.add_dependency(vector_bucket)

        # Search Lambda: embed query via Cohere, query S3 Vectors index.
        search_fn = lambda_.Function(
            self,
            "SearchNormFn",
            function_name="kira-legal-search",
            runtime=lambda_.Runtime.PYTHON_3_11,
            architecture=arch,
            handler="kira.legal_sources.adapters.search_handler.handler",
            code=code,
            memory_size=512,
            timeout=cdk.Duration.seconds(5),
            environment={
                "LEGAL_VECTOR_INDEX_NAME": "kira-legal-norms",
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
        )

        # Search Lambda: bedrock:InvokeModel on Cohere + s3vectors:QueryVectors
        search_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{REQUIRED_REGION}::foundation-model/cohere.embed-multilingual-v3"
                ],
            )
        )
        search_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3vectors:QueryVectors"],
                resources=["*"],  # tighten once ARN format is stable in CDK
            )
        )

        # Ingest Lambda: bedrock:InvokeModel + s3vectors:PutVectors/DeleteVectors
        ingest_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{REQUIRED_REGION}::foundation-model/cohere.embed-multilingual-v3"
                ],
            )
        )
        ingest_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3vectors:PutVectors", "s3vectors:DeleteVectors"],
                resources=["*"],
            )
        )

        cdk.CfnOutput(self, "LookupFnArn", value=lookup_fn.function_arn)
        cdk.CfnOutput(self, "SearchFnArn", value=search_fn.function_arn)
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
