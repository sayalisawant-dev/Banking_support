# EC2 Deployment Guide — Banking Support AgentCore Demo
### For Git Bash on Windows

---

## Before You Start — What You Need

| Item | Details |
|---|---|
| AWS Account | IAM user with **AdministratorAccess** attached |
| EC2 Key Pair | A `.pem` file (e.g. `Finance.pem`) downloaded to your Windows machine |
| Git Bash | Installed on Windows ([git-scm.com](https://git-scm.com/downloads)) |
| AWS Region | `us-east-1` (all steps assume this — change if needed) |

> ⚠️ **Critical:** AgentCore Runtime only accepts **arm64** Docker images.
> You must use an **arm64 EC2 instance** (e.g. `t4g.medium`). An x86_64 instance
> will fail at the AgentCore deploy step with "Architecture incompatible".

---

## STEP 1 — Enable Bedrock Model Access (AWS Console — One Time)

Do this before deploying so the model is ready when the agent starts.

1. Open: [https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess](https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess)
2. Click **Modify model access**
3. Find **DeepSeek V3.2** (model ID: `deepseek.deepseek-v3-2`) and check its box
4. Click **Next** → **Submit**
5. Wait until status shows **Access granted** (usually instant)

---

## STEP 2 — Launch an arm64 EC2 Instance (AWS Console)

1. Go to **EC2 → Launch Instance**
2. Set these values:

   | Setting | Value |
   |---|---|
   | **Name** | `banking-support-demo` |
   | **AMI** | Amazon Linux 2023 — select **64-bit (Arm)** tab |
   | **Instance type** | `t4g.medium` (2 vCPU, 4 GB RAM) |
   | **Key pair** | Your existing `.pem` key |
   | **Storage** | **30 GB** gp3 (increase from default 8 GB) |
   | **Security group** | Allow **SSH (port 22)** from your IP |

3. Launch and note the **Public IPv4 address** from the instance details

---

## STEP 3 — Connect to EC2 from Git Bash

Open **Git Bash** on Windows and run these commands.

**Fix .pem file permissions (required — SSH rejects world-readable keys):**
```bash
chmod 400 /c/Users/Shakti/Downloads/Finance.pem
```

**Connect to EC2:**
```bash
ssh -i "/c/Users/Shakti/Downloads/Finance.pem" ec2-user@<EC2-PUBLIC-IP>
```

Replace `<EC2-PUBLIC-IP>` with the IP from Step 2.

**Verify you're on arm64 (must show `aarch64`):**
```bash
uname -m
# Expected: aarch64
# If you see x86_64 — stop, terminate this instance, and launch a t4g.medium arm64 instance
```

---

## STEP 4 — System Setup on EC2

All commands below run **inside your SSH session** on EC2.

**Update system and install base tools:**
```bash
sudo dnf update -y
sudo dnf install -y git docker unzip
```

**Start Docker and add your user to the docker group:**
```bash
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
newgrp docker
docker --version
```

**Install Node.js 20 via nvm:**
```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash

# Load nvm into the current shell (don't skip this block)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

nvm install 20
nvm use 20
node --version   # Should show v20.x.x
npm --version    # Should show 10.x.x
```

**Install uv (Python package manager):**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv --version
```

**Install AWS CLI v2 for aarch64:**
```bash
# ⚠️ Use the aarch64 URL — NOT the x86_64 one
curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version   # Must be 2.32.0 or higher
```

**Configure AWS credentials:**
```bash
aws configure
# Enter when prompted:
#   AWS Access Key ID:     <your key>
#   AWS Secret Access Key: <your secret>
#   Default region:        us-east-1
#   Output format:         json

# Verify credentials work
aws sts get-caller-identity
```

Note your **12-digit Account ID** from the output — you'll need it in Step 7.

---

## STEP 5 — Clone the Repository and Apply Code Fixes

```bash
git clone https://github.com/sayalisawant-dev/Banking_support.git
cd ~/Banking_support
```

**Fix 1 — Change Docker image platform to arm64:**

AgentCore Runtime only accepts arm64 images. This fix adds `Platform.LINUX_ARM64` to the CDK stack.

```bash
cat > cdk/lib/stacks/docker-image-stack.ts << 'EOF'
import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs/lib/construct';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets'
import { Platform } from 'aws-cdk-lib/aws-ecr-assets'
import { BaseStackProps } from '../types';
import * as path from 'path';

export interface DockerImageStackProps extends BaseStackProps {}

export class DockerImageStack extends cdk.Stack {
    readonly imageUri: string

    constructor(scope: Construct, id: string, props: DockerImageStackProps) {
        super(scope, id, props);

        const asset = new ecr_assets.DockerImageAsset(this, `${props.appName}-AppImage`, {
            directory: path.join(__dirname, "../../../"),
            platform: Platform.LINUX_ARM64,
        });

        this.imageUri = asset.imageUri;
        new cdk.CfnOutput(this, 'ImageUri', { value: this.imageUri });
    }
}
EOF
```

**Fix 2 — Update MCP client for mcp SDK v2:**

The `mcp` SDK v2 renamed `streamablehttp_client` to `streamable_http_client` and moved the `headers`
parameter off the function onto an `httpx2.AsyncClient`.

```bash
cat > src/mcp_client/client.py << 'EOF'
import os
from typing import Optional
from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp.mcp_client import MCPClient


def get_streamable_http_mcp_client(user_token: Optional[str] = None) -> MCPClient:
    """
    Returns an MCP Client for AgentCore Gateway compatible with Strands.
    """
    gateway_url = os.getenv("GATEWAY_URL")

    if os.getenv("LOCAL_DEV") == "1":
        from contextlib import nullcontext
        from types import SimpleNamespace
        return nullcontext(SimpleNamespace(list_tools_sync=lambda: []))

    if not gateway_url:
        raise RuntimeError("Missing required environment variable: GATEWAY_URL")

    if not user_token:
        raise RuntimeError("User token is required for Gateway access")

    try:
        import httpx2
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=httpx2.Timeout(30, read=300),
        )
        return MCPClient(lambda: streamable_http_client(gateway_url, http_client=http_client))
    except ImportError:
        return MCPClient(lambda: streamable_http_client(gateway_url, headers={"Authorization": f"Bearer {user_token}"}))
EOF
```

**Fix 3 — Set model to DeepSeek V3.2:**

```bash
cat > src/model/load.py << 'EOF'
from strands.models import BedrockModel

MODEL_ID = "deepseek.deepseek-v3-2"


def load_model() -> BedrockModel:
    """
    Get Bedrock model client.
    Uses IAM authentication via the execution role.
    """
    return BedrockModel(model_id=MODEL_ID)
EOF
```

**Verify all three fixes applied:**
```bash
grep "platform" cdk/lib/stacks/docker-image-stack.ts
# Expected: platform: Platform.LINUX_ARM64,

grep "streamable_http_client\|httpx2" src/mcp_client/client.py
# Expected: lines referencing streamable_http_client and httpx2

grep "MODEL_ID" src/model/load.py
# Expected: MODEL_ID = "deepseek.deepseek-v3-2"
```

---

## STEP 6 — Install Dependencies

**Python dependencies:**
```bash
uv sync
```

**CDK (Node.js) dependencies:**
```bash
cd cdk
npm install
cd ..
```

---

## STEP 7 — Bootstrap CDK (One-Time Per Account/Region)

CDK Bootstrap creates the S3 staging bucket, ECR repository, and IAM roles that CDK needs to deploy.

```bash
cd cdk
npx cdk bootstrap aws://<ACCOUNT_ID>/us-east-1
cd ..
```

Replace `<ACCOUNT_ID>` with your 12-digit AWS account ID from Step 4.

**Expected output:**
```
✅  Environment aws://123456789012/us-east-1 bootstrapped.
```

---

## STEP 8 — Deploy All Stacks

This is the main deploy step. It builds the Docker image, pushes it to ECR, and provisions all AWS resources.

```bash
cd cdk
npx cdk deploy --all \
  --outputs-file ../cdk-outputs.json \
  --require-approval never
cd ..
```

**Expected time: 10–15 minutes**

**What gets deployed:**
- `supportAgentDemo-DockerImageStack` — builds and pushes the arm64 Docker image to ECR
- `supportAgentDemo-AgentCoreStack` — Cognito, Lambda functions, AgentCore Gateway, Memory, and Runtime

**Expected final output:**
```
✅  supportAgentDemo-DockerImageStack
✅  supportAgentDemo-AgentCoreStack
```

And a `cdk-outputs.json` file is created in the repo root.

> **If AgentCoreStack ends in ROLLBACK_COMPLETE state:**
> ```bash
> aws cloudformation delete-stack --stack-name supportAgentDemo-AgentCoreStack --region us-east-1
> aws cloudformation wait stack-delete-complete --stack-name supportAgentDemo-AgentCoreStack --region us-east-1
> rm -rf cdk/cdk.out
> cd cdk && npx cdk deploy --all --outputs-file ../cdk-outputs.json --require-approval never && cd ..
> ```

---

## STEP 9 — Add Marketplace Permissions to the Runtime Role

The AgentCore runtime role needs AWS Marketplace permissions to invoke DeepSeek (a marketplace model).

**Find the runtime role name:**
```bash
aws iam list-roles \
  --query "Roles[?contains(RoleName,'AgentCoreRuntime')].RoleName" \
  --output text
```

**Attach the policy (replace `<ROLE_NAME>` with the output above):**
```bash
ROLE_NAME="<ROLE_NAME>"

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "MarketplaceAndBedrockAccess" \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": [
        "aws-marketplace:ViewSubscriptions",
        "aws-marketplace:Subscribe",
        "aws-marketplace:Unsubscribe",
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    }]
  }'
```

---

## STEP 10 — Generate AgentCore CLI Config

This script reads `cdk-outputs.json` and generates `.bedrock_agentcore.yaml`, which the `agentcore` CLI uses to know which runtime to invoke.

```bash
python3 - cdk-outputs.json .bedrock_agentcore.yaml supportAgentDemo-AgentCoreStack << 'PYEOF'
import json, sys, pathlib

outputs_path, yaml_path, stack_key = sys.argv[1], sys.argv[2], sys.argv[3]
with open(outputs_path) as f:
    stack = json.load(f)[stack_key]

agent_id      = stack["RuntimeId"]
agent_arn     = stack["RuntimeArn"]
account       = stack["AccountId"]
region        = stack["Region"]
discovery_url = stack["AuthorizerDiscoveryUrl"]

yaml_content = f"""\
default_agent: supportAgentDemo_Agent
agents:
  supportAgentDemo_Agent:
    name: supportAgentDemo_Agent
    language: python
    entrypoint: ./src/main.py
    deployment_type: container
    platform: linux/arm64
    aws:
      account: '{account}'
      region: {region}
      network_configuration:
        network_mode: PUBLIC
      protocol_configuration:
        server_protocol: HTTP
      observability:
        enabled: true
    bedrock_agentcore:
      agent_id: {agent_id}
      agent_arn: {agent_arn}
    authorizer_configuration:
      customJWTAuthorizer:
        discoveryUrl: {discovery_url}
    request_header_configuration:
      requestHeaderAllowlist:
      - Authorization
"""
pathlib.Path(yaml_path).write_text(yaml_content)
print(f"Generated {yaml_path}")
PYEOF
```

> **If `cdk-outputs.json` doesn't have the AgentCoreStack outputs** (only DockerImageStack), fetch them from CloudFormation:
> ```bash
> aws cloudformation describe-stacks \
>   --stack-name supportAgentDemo-AgentCoreStack \
>   --region us-east-1 \
>   --query "Stacks[0].Outputs" \
>   --output json > /tmp/raw.json
> python3 -c "
> import json
> with open('/tmp/raw.json') as f: outputs = json.load(f)
> with open('cdk-outputs.json') as f: existing = json.load(f)
> existing['supportAgentDemo-AgentCoreStack'] = {o['OutputKey']: o['OutputValue'] for o in outputs}
> with open('cdk-outputs.json', 'w') as f: json.dump(existing, f, indent=2)
> print('Updated cdk-outputs.json')
> "
> ```

**Check agent status:**
```bash
uv run agentcore status
# Look for: READY or ACTIVE
# If CREATING — wait 1–2 minutes and re-run
```

---

## STEP 11 — Create a Cognito Test User

```bash
uv run scripts/cognito-user.py --create
```

When prompted, use one of these emails (they match the mock customer data in Lambda):

| Email | Customer |
|---|---|
| `john@example.com` | John Doe (CUST-001) |
| `jane@example.com` | Jane Smith (CUST-002) |

Set a password that meets Cognito requirements (example: `Support@123`):
- 8+ characters
- Uppercase + lowercase
- At least one number
- At least one special character

---

## STEP 12 — Get a Bearer Token (SSH Tunnel Method)

The agent requires a Cognito JWT with custom scopes. The browser-based PKCE flow is required —
the simple username/password flow does **not** include the custom scopes the agent needs.

**In Git Bash on Windows — open a NEW terminal window and set up an SSH tunnel:**
```bash
ssh -i "/c/Users/Shakti/Downloads/Finance.pem" -L 3000:localhost:3000 ec2-user@<EC2-PUBLIC-IP>
```

Keep this second terminal open. It tunnels `localhost:3000` on Windows → port 3000 on EC2.

**Back in your main EC2 SSH session — start the login flow:**
```bash
eval $(uv run scripts/cognito-user.py --login --export)
```

The script prints a long Cognito URL like:
```
https://supportagentdemo-....amazoncognito.com/oauth2/authorize?...
```

**Copy that URL and paste it into your Windows browser.**
Log in with `john@example.com` and the password you set in Step 11.
The browser redirects to `localhost:3000/callback` → the SSH tunnel forwards it to EC2 → the token is captured.

**Verify the token was set:**
```bash
echo "Token: ${BEDROCK_AGENTCORE_BEARER_TOKEN:0:20}..."
# Should show something like: Token: eyJraWQi...
```

**If the token was not automatically exported**, set it manually:
```bash
export BEDROCK_AGENTCORE_BEARER_TOKEN="<paste the full token here>"
```

> The token expires after **1 hour**. Re-run `eval $(uv run scripts/cognito-user.py --login --export)` when it expires.

---

## STEP 13 — Invoke the Agent

```bash
# Verify identity — agent reads your email and group from the JWT
uv run agentcore invoke '{"prompt": "Who am I?"}'

# List all customers
uv run agentcore invoke '{"prompt": "Show me all customers"}'

# List recent orders
uv run agentcore invoke '{"prompt": "Show me my recent orders"}'

# Small refund — should succeed
uv run agentcore invoke '{"prompt": "I need a refund for order ORD-12420. The phone case was damaged."}'

# Large refund — succeeds NOW (no policy yet)
uv run agentcore invoke '{"prompt": "I need a refund of $399 for order ORD-12430. The monitor has dead pixels."}'
```

---

## STEP 14 — Add Cedar Policies (AWS Console — Manual)

The Policy Engine is not managed by CDK. It must be created manually in the console.

**Create the Policy Engine:**
1. Open **AWS Console → Amazon Bedrock → AgentCore → Policy Engine**
2. Click **Create policy engine**, name it `supportAgentDemo-PolicyEngine`
3. Click **Add policies** and select `supportAgentDemo-Gateway` in the resource scope dropdown

**Add Policy 1 — Allow read-only tools:**

Type this prompt in the policy generator:
```
Allow all authenticated users to call get_customer, list_customers, get_order, and list_orders
```
Click **Generate Cedar** → review → **Create**

**Add Policy 2 — Restrict refund amount:**

Type this prompt:
```
Allow users in the standard cognito:groups to call process_refund only when the amount is less than or equal to 100
```
Click **Generate Cedar** → review → **Create**

**Associate the Policy Engine with the Gateway:**
1. In the Policy Engine page, click **Associate gateway**
2. Select `supportAgentDemo-Gateway`
3. Confirm

**Verify policy enforcement:**
```bash
# This should now be BLOCKED (>$100 for standard group)
uv run agentcore invoke '{"prompt": "I need a full refund of $399 for order ORD-12300."}'

# This should still work (<=$100)
uv run agentcore invoke '{"prompt": "I need a $50 refund for order ORD-12420. Wrong item."}'
```

---

## STEP 15 — Test Named Sessions (Memory)

```bash
SESSION_ID=$(uuidgen)
uv run agentcore invoke -s $SESSION_ID '{"prompt": "Show me my recent orders"}'
uv run agentcore invoke -s $SESSION_ID '{"prompt": "Refund the most recent one"}'
```

The agent remembers the conversation context within the same session.

---

## Cleanup — Destroy All Resources

```bash
chmod +x scripts/teardown.sh
scripts/teardown.sh
rm -f cdk-outputs.json .bedrock_agentcore.yaml
```

---

## Troubleshooting

### Architecture incompatible — Supported platforms: [arm64]
You launched an x86_64 instance. Terminate it and launch a `t4g.medium` (arm64). Also verify `docker-image-stack.ts` has `Platform.LINUX_ARM64` (Fix 1 in Step 5).

### ImportError: cannot import name 'streamablehttp_client'
Apply Fix 2 from Step 5. The `mcp` SDK v2 renamed this function.

### streamable_http_client() got unexpected keyword argument 'headers'
Same fix — the `mcp` SDK v2 moved `headers` onto `httpx2.AsyncClient`. Apply Fix 2 from Step 5.

### AgentCoreStack outputs are null / KeyError: 'supportAgentDemo-AgentCoreStack'
The stack deployed with no changes and didn't write outputs. Fetch them from CloudFormation — see the note in Step 10.

### Bearer token missing custom scopes (only has aws.cognito.signin.user.admin)
You used the wrong login method. The `USER_PASSWORD_AUTH` flow doesn't return custom resource scopes. You must use the SSH tunnel + browser PKCE flow from Step 12.

### 403 Forbidden — Authorization method mismatch
The runtime was updated without `authorizerConfiguration`, resetting it to SigV4 auth. Re-run `update_agent_runtime` with `authorizerConfiguration` included (see `commands_ec2` file for the full Python SDK call).

### AccessDeniedException — aws-marketplace:Subscribe
Apply Step 9 (Marketplace permissions). Also ensure DeepSeek V3.2 model access is enabled in the Bedrock console (Step 1).

### nvm: command not found after install
Run the manual load block:
```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

### CDK deploy exits instantly without rebuilding the image
CDK cached the image. Force a rebuild:
```bash
docker rmi $(docker images | grep cdk-hnb659fds | awk '{print $3}') 2>/dev/null || true
rm -rf cdk/cdk.out
touch src/__init__.py
cd cdk && npx cdk deploy --all --outputs-file ../cdk-outputs.json --require-approval never && cd ..
```

---

## Quick Reference

| Command | What it does |
|---|---|
| `uv sync` | Install Python dependencies |
| `cd cdk && npm install` | Install CDK dependencies |
| `npx cdk bootstrap aws://ACCOUNT/REGION` | One-time CDK account setup |
| `npx cdk deploy --all --outputs-file ../cdk-outputs.json --require-approval never` | Deploy all stacks |
| `uv run agentcore status` | Check if runtime is READY |
| `uv run scripts/cognito-user.py --create` | Create a Cognito test user |
| `eval $(uv run scripts/cognito-user.py --login --export)` | Get bearer token (needs SSH tunnel) |
| `uv run agentcore invoke '{"prompt": "..."}'` | Invoke the agent |
| `scripts/teardown.sh` | Destroy all deployed resources |
