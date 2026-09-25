from strands.models import BedrockModel

# Amazon Nova Pro — free tier, no payment instrument required
# Replaced Claude Sonnet 4.5 which requires AWS Marketplace subscription
# https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
MODEL_ID = "amazon.nova-pro-v1:0"


def load_model() -> BedrockModel:
    """
    Get Bedrock model client.
    Uses IAM authentication via the execution role.
    """
    return BedrockModel(model_id=MODEL_ID)
