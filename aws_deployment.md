# AWS Deployment Guide — OFAC Screening API

**Free tier services used:**
- **EC2 t2.micro** — 750 hrs/month free (12 months)
- **Security Group** — free
- **Elastic IP** — 1 free if attached to a running instance

**Architecture:** Your Mac → SSH → EC2 t2.micro → Docker → FastAPI on port 8000

---

## Phase 1: Set Up AWS CLI on Your Mac

### Step 1 — Install AWS CLI

```bash
# On Mac with Homebrew:
brew install awscli

# Verify:
aws --version
```

### Step 2 — Create an IAM User (do NOT use your root account)

1. Go to **AWS Console** → search "IAM" → click **IAM**
2. Left sidebar → **Users** → **Create user**
3. Username: `ofac-deploy-user` → click **Next**
4. Select **"Attach policies directly"**
5. Search and check: `AmazonEC2FullAccess`
6. Click **Next** → **Create user**
7. Click on the user you just created → **Security credentials** tab
8. Scroll to **Access keys** → **Create access key**
9. Select **"Command Line Interface (CLI)"** → check the confirmation box → **Next** → **Create**
10. **SAVE BOTH keys now** — you won't see the secret key again

### Step 3 — Configure AWS CLI

```bash
aws configure
```

Enter when prompted:
```
AWS Access Key ID: <paste your access key>
AWS Secret Access Key: <paste your secret key>
Default region name: us-east-1
Default output format: json
```

Verify it works:
```bash
aws sts get-caller-identity
```

You should see your account ID and user ARN.

---

## Phase 2: Launch EC2 Instance

### Step 4 — Create a Key Pair (for SSH access)

```bash
# Create the key pair and save it locally
aws ec2 create-key-pair \
  --key-name ofac-key \
  --query 'KeyMaterial' \
  --output text > ~/ofac-key.pem

# Lock down permissions (required for SSH to work)
chmod 400 ~/ofac-key.pem
```

### Step 5 — Create a Security Group

```bash
# Create the security group
aws ec2 create-security-group \
  --group-name ofac-sg \
  --description "OFAC API security group"
```

This prints a `GroupId` like `sg-0abc123...`. Copy it.

```bash
# Get your current public IP
MY_IP=$(curl -s https://checkip.amazonaws.com)/32

# Allow SSH only from YOUR IP (more secure)
aws ec2 authorize-security-group-ingress \
  --group-name ofac-sg \
  --protocol tcp \
  --port 22 \
  --cidr $MY_IP

# Allow API traffic on port 8000 from anywhere
aws ec2 authorize-security-group-ingress \
  --group-name ofac-sg \
  --protocol tcp \
  --port 8000 \
  --cidr 0.0.0.0/0
```

### Step 6 — Find the Amazon Linux 2023 AMI ID

```bash
aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=al2023-ami-2023*" "Name=architecture,Values=x86_64" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text
```

This prints an AMI ID like `ami-0abcdef1234567890`. Copy it.

### Step 7 — Launch the EC2 Instance (t2.micro = free tier)

```bash
aws ec2 run-instances \
  --image-id <PASTE_AMI_ID_FROM_ABOVE> \
  --instance-type t2.micro \
  --key-name ofac-key \
  --security-groups ofac-sg \
  --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=ofac-api}]'
```

This prints JSON — find `"InstanceId"` (e.g. `i-0abc123...`). Copy it.

### Step 8 — Wait for the instance to start & get its IP

```bash
# Re-run until State = running (takes ~1-2 minutes)
aws ec2 describe-instances \
  --instance-ids <YOUR_INSTANCE_ID> \
  --query 'Reservations[0].Instances[0].{State:State.Name,IP:PublicIpAddress}'
```

Once state is `running`, copy the `IP` value.

---

## Phase 3: Connect to EC2 and Install Docker

### Step 9 — SSH into the instance

```bash
ssh -i ~/ofac-key.pem ec2-user@<YOUR_INSTANCE_IP>
```

If asked `Are you sure you want to continue connecting?` → type `yes`.

### Step 10 — Install Docker

```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker       # auto-start Docker on reboot
sudo usermod -aG docker ec2-user   # allow ec2-user to run docker without sudo
```

Log out and back in so the group change takes effect:
```bash
exit
ssh -i ~/ofac-key.pem ec2-user@<YOUR_INSTANCE_IP>
```

Verify Docker works:
```bash
docker --version
```

---

## Phase 4: Deploy the Application

### Step 11 — Clone the repo

```bash
git clone https://github.com/nishankster/ofac_check.git
cd ofac_check
```

### Step 12 — Create the `.env` file with your secrets

Generate a strong signing key:
```bash
openssl rand -hex 32
```

Copy that output. Now create the env file:
```bash
nano .env
```

Paste this content (replace the values):
```bash
OAUTH_SIGNING_KEY=<paste_the_hex_key_you_just_generated>
OAUTH_CLIENTS={"my-client-id":"my-strong-password-here"}
OAUTH_TOKEN_EXPIRE_SECONDS=3600
```

Save and exit: `Ctrl+X` → `Y` → `Enter`

> **Important:** Choose a real `client_id` and `client_secret` you'll remember — these are what you'll use to authenticate to the API.

### Step 13 — Build the Docker image

```bash
docker build -t ofac-api .
```

This takes 2-3 minutes the first time (downloads Python, installs packages).

### Step 14 — Run the container

```bash
docker run -d \
  --name ofac-api \
  --env-file .env \
  -p 8000:8000 \
  --restart unless-stopped \
  ofac-api
```

- `-d` = run in background
- `--restart unless-stopped` = auto-restart if the instance reboots
- `--env-file .env` = pass your secrets from the file

### Step 15 — Watch startup logs

```bash
docker logs -f ofac-api
```

Wait until you see: `SDN list ready: X entries`. Press `Ctrl+C` to stop watching.

---

## Phase 5: Test Your API

Run these from your Mac (not EC2).

### Health check

```bash
curl http://<YOUR_INSTANCE_IP>:8000/health
```

Expected response:
```json
{"status":"ok","sdn_entries":11234,"sdn_list_date":"...","timestamp":"..."}
```

### Get an OAuth token

```bash
curl -X POST http://<YOUR_INSTANCE_IP>:8000/oauth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=my-client-id" \
  -d "client_secret=my-strong-password-here"
```

Copy the `access_token` from the response.

### Screen an identity

```bash
curl -X POST http://<YOUR_INSTANCE_IP>:8000/screen \
  -H "Authorization: Bearer <PASTE_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "John Smith"}'
```

### Trigger SDN list refresh

```bash
curl -X GET http://<YOUR_INSTANCE_IP>:8000/sdn/refresh \
  -H "Authorization: Bearer <PASTE_TOKEN>"
```

### Verify refresh completed

Poll `/health` until `sdn_list_date` updates to today:
```bash
watch -n 5 'curl -s http://<YOUR_INSTANCE_IP>:8000/health | python3 -m json.tool'
```

Press `Ctrl+C` when the date changes. Refresh takes 30–90 seconds.

### Interactive API docs (Swagger UI)

Open in browser:
```
http://<YOUR_INSTANCE_IP>:8000/docs
```

> **Note:** Browsers cannot hit authenticated endpoints directly — they don't send the `Authorization` header. Always use `curl` or the `/docs` Swagger UI for protected routes.

---

## Phase 6: Assign a Static IP (Optional but Recommended)

The EC2 public IP changes every time you stop/start the instance. Fix it with an Elastic IP (free while attached to a running instance):

```bash
# Allocate a static IP
aws ec2 allocate-address --domain vpc

# Copy the "AllocationId" from the output (eipalloc-xxx)
# Associate it with your instance
aws ec2 associate-address \
  --instance-id <YOUR_INSTANCE_ID> \
  --allocation-id <YOUR_ALLOCATION_ID>
```

Use the `PublicIp` from the `allocate-address` output going forward — it will never change.

---

## Viewing the SDN List

### Option A — Inspect the cached XML on EC2

```bash
# First 100 lines
docker exec ofac-api head -n 100 sdn_cache.xml

# Search for a specific name
docker exec ofac-api grep -i "gaddafi" sdn_cache.xml

# Count total entries
docker exec ofac-api grep -c "<sdnEntry>" sdn_cache.xml

# Copy the full file to EC2 for inspection
docker cp ofac-api:/app/sdn_cache.xml ~/sdn_cache.xml
```

### Option B — View the official OFAC source directly

- **Browsable list:** https://sanctionslist.ofac.treas.gov/Home/SdnList
- **Raw XML** (what the app downloads): https://www.treasury.gov/ofac/downloads/sdn.xml

---

## Quick Reference

| Task | Command |
|------|---------|
| SSH into EC2 | `ssh -i ~/ofac-key.pem ec2-user@<IP>` |
| View API logs | `docker logs -f ofac-api` |
| Stop the API | `docker stop ofac-api` |
| Start the API | `docker start ofac-api` |
| Rebuild after code change | `git pull && docker build -t ofac-api . && docker stop ofac-api && docker rm ofac-api && docker run -d --name ofac-api --env-file .env -p 8000:8000 --restart unless-stopped ofac-api` |
| Refresh SDN list | `curl -H "Authorization: Bearer <token>" http://<IP>:8000/sdn/refresh` |

---

## Free Tier Limits to Watch

| Resource | Free Tier Limit | Notes |
|----------|----------------|-------|
| EC2 t2.micro | 750 hrs/month for 12 months | One instance running 24/7 = ~744 hrs — within limit |
| Elastic IP | Free while attached to a running instance | Charges ~$0.005/hr if instance is stopped |
| Data transfer | 15 GB outbound/month | SDN XML is ~27 MB per refresh |
