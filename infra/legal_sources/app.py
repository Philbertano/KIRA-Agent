import os

import aws_cdk as cdk

from stack import LegalSourcesStack


app = cdk.App()
LegalSourcesStack(
    app,
    "KiraLegalSources",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region="eu-central-1",
    ),
)
app.synth()
