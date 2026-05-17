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

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` on `datadog.conf` at Apache start | Wrong file permissions after install | `sudo chmod 755 /opt/datadog-httpd && sudo chmod 644 /opt/datadog-httpd/datadog.conf` |
| `Failed to connect to localhost port 8126` in error log | SELinux blocking outbound connections from httpd | `sudo setsebool -P httpd_can_network_connect on` |
| `Failed to connect to localhost port 8126` after SELinux fix | Datadog Agent not installed or not running | Install Agent; verify with `sudo ss -tlnp \| grep 8126` |
| RUM SDK not injected into HTML | `Content-Type` not `text/html` | Ensure Apache is serving HTML with correct content type |
| User Name `undefined` in `DD_RUM.getUser()` | Cookie is URL-encoded; regex cannot match | Clear `dd_user` cookie, reload page, re-verify |
| SSH connection timeout | No SSH firewall rule or rule restricted to wrong IP | Use `--tunnel-through-iap` flag with `gcloud compute ssh` |
| HTTP 403/no response from VM IP | Firewall `source-ranges` set to `0.0.0.0/32` instead of your IP | `gcloud compute firewall-rules update default-allow-http --source-ranges=<YOUR_IP>/32` |
