# Datadog RUM SSI — Apache 2.4 on RHEL 9 (GCP)

End-to-end setup guide for Datadog RUM Auto-Instrumentation (Server-Side Injection) on Apache 2.4.62 running on RHEL 9.7 in GCP. Includes a demo frontend with all three user attribute source types pre-wired (JavaScript, Cookie, DOM).

---

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login`)
- Datadog account with a RUM application created (Application ID + Client Token)
- Datadog Agent API key

---

## 1. Create the GCP VM

```bash
gcloud compute instances create rum-apache-demo \
  --zone=asia-east2-a \
  --machine-type=e2-medium \
  --image-family=rhel-9 \
  --image-project=rhel-cloud \
  --boot-disk-size=20GB \
  --tags=http-server,https-server
```

### Firewall — restrict port 80 to your IP only

```bash
# Check if default-allow-http exists
gcloud compute firewall-rules describe default-allow-http

# Update source range to your IP (replace with your actual IP)
gcloud compute firewall-rules update default-allow-http \
  --source-ranges=<YOUR_IP>/32
```

> **Note:** GCP sandbox accounts often ship with `0.0.0.0/32` (no traffic) instead of `0.0.0.0/0`. Always verify source ranges after creation.

> **Note:** Mixing IPv4 and IPv6 in the same GCP firewall rule is not supported. Use a separate rule if you need both.

---

## 2. SSH into the VM

Direct SSH may be blocked depending on your project's firewall rules. Use IAP tunnelling:

```bash
gcloud compute ssh rum-apache-demo --zone=asia-east2-a --tunnel-through-iap
```

---

## 3. Install Apache

```bash
sudo dnf install -y httpd
sudo systemctl enable httpd
sudo systemctl start httpd
```

---

## 4. Deploy the demo frontend

Copy `frontend/index.html` from this repo to the VM:

```bash
# From your local machine
gcloud compute scp frontend/index.html rum-apache-demo:/tmp/index.html \
  --zone=asia-east2-a --tunnel-through-iap

# On the VM
sudo cp /tmp/index.html /var/www/html/index.html
sudo chown apache:apache /var/www/html/index.html
sudo chmod 644 /var/www/html/index.html
```

The frontend exposes user attributes via all three source types so you can test whichever you configure in the Datadog UI — see [User Attributes](#6-user-attributes) below.

---

## 5. Install the Datadog RUM SSI module

Run the installer from the Datadog UI (Digital Experience → Manage Applications → your app → Auto-Instrumentation → Apache httpd). The command looks like:

```bash
curl -sSL https://rum-auto-instrumentation.s3.amazonaws.com/installer/latest/install-proxy-datadog.sh \
  | sudo sh -s -- \
    --proxyKind httpd \
    --appId <DATADOG_APPLICATION_ID> \
    --site datadoghq.com \
    --clientToken <DATADOG_CLIENT_TOKEN> \
    --remoteConfigurationId <REMOTE_CONFIGURATION_ID>
```

### Fix: installer directory permissions

The installer creates `/opt/datadog-httpd/`. On RHEL 9, Apache cannot read it without correct permissions:

```bash
sudo chmod 755 /opt/datadog-httpd
sudo chmod 644 /opt/datadog-httpd/datadog.conf
```

### Fix: SELinux blocks Apache from connecting to port 8126

RHEL 9 ships with SELinux in Enforcing mode. By default, Apache is not allowed to make outbound network connections. This blocks the module from reaching the Datadog Agent and Remote Configuration:

```bash
# Confirm SELinux is the cause
sudo grep 'httpd' /var/log/audit/audit.log | grep 'denied' | tail -5

# Fix: enable the boolean (persistent across reboots)
sudo setsebool -P httpd_can_network_connect on
```

### Restart Apache

```bash
sudo systemctl restart httpd
```

### Verify injection

```bash
curl -s http://localhost/ | grep -i "datadog"
```

You should see the `DD_RUM.init(...)` snippet injected into the HTML response.

---

## 6. Install the Datadog Agent

Required for Remote Configuration (propagating user attribute config changes from the Datadog UI to the module) and APM correlation.

```bash
DD_API_KEY=<YOUR_API_KEY> DD_SITE="datadoghq.com" \
  bash -c "$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)"
```

Verify the Agent is listening on port 8126:

```bash
sudo ss -tlnp | grep 8126
```

---

## 7. User Attributes

The demo frontend (`frontend/index.html`) exposes mock user data via all three source types supported by Datadog RUM SSI. Configure whichever you prefer in the Datadog UI (Digital Experience → your app → SDK Configuration → User Attributes).

### Source reference

| Attribute  | Source     | Value to enter in Datadog UI                        |
|------------|------------|-----------------------------------------------------|
| User ID    | JavaScript | `window.currentUser.id`                             |
| User Name  | JavaScript | `window.currentUser.name`                           |
| User Email | JavaScript | `window.currentUser.email`                          |
| User ID    | Cookie     | Name: `dd_user` / Regex: `"id":"([^"]+)"`           |
| User Name  | Cookie     | Name: `dd_user` / Regex: `"name":"([^"]+)"`         |
| User Email | Cookie     | Name: `dd_user` / Regex: `"email":"([^"]+)"`        |
| User ID    | DOM        | CSS selector: `#user-id`                            |
| User Name  | DOM        | CSS selector: `#user-name`                          |
| User Email | DOM        | CSS selector: `#user-email`                         |

### Cookie gotcha — do not URL-encode

The `dd_user` cookie must be stored as **raw JSON** (not URL-encoded) for the regex extractor to work. The frontend handles this correctly, but if you set the cookie yourself:

```javascript
// Correct
document.cookie = "dd_user=" + JSON.stringify(user) + "; path=/; max-age=86400";

// Wrong — regex will not match
document.cookie = "dd_user=" + encodeURIComponent(JSON.stringify(user)) + "; path=/; max-age=86400";
```

If user attributes stop matching, check the raw cookie value in DevTools → Application → Cookies. Clear and reload if it looks URL-encoded (`%22`, `%7B`).

### Verify in browser console

```javascript
window.DD_RUM.getUser()
// Expected: { id: 'usr_001', name: 'Jonathan Lim', email: 'jonathan.lim@...' }
```

### Remote Configuration propagation time

After clicking **Save Changes** in the Datadog UI, changes propagate to the Apache module in **~30 seconds** (requires Agent running on port 8126 and `httpd_can_network_connect` SELinux boolean enabled).

---

## 8. RUM <> APM Correlation

The demo includes a Python Flask backend instrumented with `ddtrace` to test end-to-end trace correlation.

### Install and run the backend

```bash
# Install ddtrace globally (must use sudo so systemd can find the binary)
sudo pip3 install ddtrace flask flask-cors

# Copy files to the VM
gcloud compute scp backend/app.py backend/rum-backend.service rum-apache-demo:/tmp/ \
  --zone=asia-east2-a --tunnel-through-iap

# Deploy
gcloud compute ssh rum-apache-demo --zone=asia-east2-a --tunnel-through-iap --command="
  sudo mkdir -p /opt/rum-backend &&
  sudo cp /tmp/app.py /opt/rum-backend/app.py &&
  sudo cp /tmp/rum-backend.service /etc/systemd/system/rum-backend.service &&
  sudo systemctl daemon-reload &&
  sudo systemctl enable rum-backend &&
  sudo systemctl start rum-backend
"
```

> **Gotcha — port 5000 is taken by the Datadog Agent**: The Agent's debug/telemetry endpoint listens on port 5000. Running Flask on port 5000 conflicts. Use port 8000 (already set in `app.py` and the service file).

> **Gotcha — ddtrace-run path**: Install with `sudo pip3` so the binary lands at `/usr/local/bin/ddtrace-run`, accessible by the root systemd service. A user-level install (`pip3` without sudo) puts it at `~/.local/bin/ddtrace-run` which root cannot execute (systemd exit code 203/EXEC).

### Open firewall for port 8000

```bash
gcloud compute firewall-rules create allow-rum-backend \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:8000 \
  --source-ranges=<YOUR_IP>/32 \
  --target-tags=http-server \
  --description="RUM APM demo backend port 8000"
```

### Backend endpoints

| Endpoint | Behaviour |
|---|---|
| `GET /api/ping` | Returns immediately with `{"status":"ok"}` |
| `GET /api/slow` | Sleeps 400–1200ms (random), simulates latency |
| `GET /api/error` | Raises `RuntimeError`, returns HTTP 500 |

### Configure Allowed Tracing URLs in Datadog UI

<img width="1347" height="728" alt="image" src="https://github.com/user-attachments/assets/b667fbcf-d6b6-49e1-8e54-baa51bf92f5b" />

1. Navigate to **Digital Experience → Manage Applications → your app → SDK Configuration**
2. Scroll to the **Allowed Tracing URLs** section and click **Add URL**
3. **Toggle on the Regex switch** next to the URL field (important — without this, the value is treated as an exact string match and dots in the IP are not escaped)
4. Enter the regex pattern in the URL field:
   ```
   http://35\.241\.69\.52:8000.*
   ```
   > Dots in the IP must be escaped as `\.` in regex. The trailing `.*` matches all paths under that origin (`/api/ping`, `/api/slow`, etc.).
5. Set **Propagator Type** to `datadog`
6. Ensure a **service name** is configured in the **App Attributes** section (required for APM linking — tracing will not work without it)
7. Click **Save Changes** — configuration propagates to the Apache module in ~30 seconds via Remote Configuration

### Verify correlation

1. Open `http://35.241.69.52/` in Chrome
2. Click **Ping**, **Slow Request**, or **Trigger 500** in the RUM <> APM panel
3. In Datadog, open the RUM session → click the resource/fetch call → you should see a **View Trace** button linking to the backend APM span

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` on `datadog.conf` at Apache start | Wrong file permissions after install | `sudo chmod 755 /opt/datadog-httpd && sudo chmod 644 /opt/datadog-httpd/datadog.conf` |
| `Failed to connect to localhost port 8126` in error log | SELinux blocking outbound connections from httpd | `sudo setsebool -P httpd_can_network_connect on` |
| `Failed to connect to localhost port 8126` after SELinux fix | Datadog Agent not installed or not running | Install Agent; verify with `sudo ss -tlnp \| grep 8126` |
| RUM SDK not injected into HTML | `Content-Type` not `text/html` | Ensure Apache is serving HTML with correct content type |
| User Name `undefined` in `DD_RUM.getUser()` | Cookie is URL-encoded; regex cannot match | Clear `dd_user` cookie, reload page, re-verify |
| SSH connection timeout | No SSH firewall rule or rule restricted to wrong IP | Use `--tunnel-through-iap` flag with `gcloud compute ssh` |
| HTTP 403/no response from VM IP | Firewall `source-ranges` set to `0.0.0.0/32` instead of your IP | `gcloud compute firewall-rules update default-allow-http --source-ranges=<YOUR_IP>/32` |
| Backend service exit code 203/EXEC | `ddtrace-run` installed as user, not root — systemd can't find it | `sudo pip3 install ddtrace` then verify with `sudo which ddtrace-run` |
| Backend service exit code 1/FAILURE, `Address already in use` | Port 5000 taken by Datadog Agent debug endpoint | Use port 8000; avoid `fuser -k 5000/tcp` (kills the Agent) |
| Fetch calls from browser fail with CORS error | Backend missing CORS headers | `flask-cors` must be installed and `CORS(app)` called in `app.py` |
| No trace link on RUM resource | Allowed Tracing URL not configured or service name missing | Add URL in SDK Configuration and set a service name in App Attributes |
