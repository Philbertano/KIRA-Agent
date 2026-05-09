"""Legal-sources CDK stack: S3 corpus + lookup Lambda + ingest Lambda + schedule.

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
from constructs import Construct

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
            environment={"LEGAL_CORPUS_BUCKET": bucket.bucket_name},
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read(lookup_fn)
        kms_key.grant_decrypt(lookup_fn)

        ingest_fn = lambda_.Function(
            self,
            "IngestFn",
            function_name="kira-legal-ingest",
            runtime=lambda_.Runtime.PYTHON_3_11,
            architecture=arch,
            handler="kira.legal_sources.adapters.ingest_handler.handler",
            code=code,
            memory_size=1024,
            timeout=cdk.Duration.minutes(5),
            environment={"LEGAL_CORPUS_BUCKET": bucket.bucket_name},
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

        cdk.CfnOutput(self, "LookupFnArn", value=lookup_fn.function_arn)
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
