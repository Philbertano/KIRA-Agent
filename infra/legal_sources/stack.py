"""Legal-sources CDK stack: S3 corpus + lookup Lambda + ingest Lambda + schedule.

Region pinned to eu-central-1.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_kms as kms,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_cloudwatch as cw,
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

        # Code bundling: zip src/kira/ as the Lambda payload.
        code = lambda_.Code.from_asset(
            str(REPO_ROOT),
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
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
            targets=[targets.LambdaFunction(ingest_fn)],
        )

        cw.Alarm(
            self,
            "StaleCorpusAlarm",
            alarm_name="kira-legal-stale-corpus",
            metric=cw.Metric(
                namespace="AWS/Lambda",
                metric_name="Invocations",
                dimensions_map={"FunctionName": ingest_fn.function_name},
                period=cdk.Duration.hours(36),
                statistic="Sum",
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description="Ingest has not run in 36h — corpus may be stale.",
        )

        cdk.CfnOutput(self, "LookupFnArn", value=lookup_fn.function_arn)
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
