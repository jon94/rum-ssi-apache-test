#!/bin/bash
# GCP VM startup script — installs Apache and deploys the demo frontend.
# Pass this via --metadata-from-file=startup-script=scripts/startup.sh when creating the VM.
set -e

dnf install -y httpd
systemctl enable httpd
systemctl start httpd
