"""
Deploy beta.aber-owl.net nginx configuration to all 3 tiers.

Usage:
    cd /path/to/borg-infrastructure
    python3 ../aberowl2/deploy/deploy_nginx.py

Actions:
1. borg-server: Create beta.aber-owl.net SSL vhost + get cert
2. frontend/frontend1: Add /aberowl-beta/ proxy location block
"""

import pty
import os
import sys
import select
import time

# Path to borg-infrastructure passwords
INFRA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "borg-infrastructure")
PASS_FILE = os.path.join(INFRA_DIR, "password.md")
ONTO_PASS_FILE = os.path.join(INFRA_DIR, "password-ontolinator.md")

FRONTEND_USER = "a-hohndor"
FRONTEND_HOSTS = [
    ("10.254.146.242", "frontend"),
    ("10.254.147.211", "frontend1"),
]

BORG_HOST = "87.106.144.182"
BORG_USER = "root"

FRONTEND_LOCATION_BLOCK = """
                        location = /aberowl-beta {
                                return 301 /aberowl-beta/;
                        }

                        location ^~ /aberowl-beta/ {
                                proxy_pass http://10.67.24.207:8000/;
                                proxy_read_timeout 120s;
                                proxy_send_timeout 120s;
                                proxy_set_header Host               $host;
                                proxy_set_header X-Real-IP          $remote_addr;
                                proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
                                proxy_set_header X-Forwarded-Proto  $scheme;
                                proxy_set_header X-Forwarded-Host   beta.aber-owl.net;
                                proxy_buffering off;
                        }
"""

BORG_VHOST = """server {
    listen 80;
    server_name beta.aber-owl.net;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name beta.aber-owl.net;

    ssl_certificate /etc/letsencrypt/live/beta.aber-owl.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/beta.aber-owl.net/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://phenomebrowser.net:27004/aberowl-beta/;
        proxy_set_header Host phenomebrowser.net;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
"""


def main():
    print("=" * 60)
    print("AberOWL2 Beta Nginx Deployment")
    print("=" * 60)
    print()
    print("This script will configure nginx on:")
    print(f"  1. borg-server ({BORG_HOST}) - SSL vhost for beta.aber-owl.net")
    print(f"  2. frontend  ({FRONTEND_HOSTS[0][0]}) - /aberowl-beta/ proxy")
    print(f"  3. frontend1 ({FRONTEND_HOSTS[1][0]}) - /aberowl-beta/ proxy")
    print()
    print("Prerequisites:")
    print(f"  - Password files: {PASS_FILE}")
    print(f"  - onto server running AberOWL2 central stack on port 8000")
    print()

    if not os.path.exists(PASS_FILE):
        print(f"ERROR: {PASS_FILE} not found")
        print("Run this script from the borg-infrastructure directory or set INFRA_DIR")
        sys.exit(1)

    print("Manual deployment steps:")
    print()
    print("--- STEP 1: borg-server ---")
    print(f"  ssh root@{BORG_HOST}")
    print(f"  # Write the following to /etc/nginx/sites-available/beta.aber-owl.net:")
    print(f"  #   (see deploy/nginx/borg-server-beta.aber-owl.net.conf)")
    print(f"  ln -sf /etc/nginx/sites-available/beta.aber-owl.net /etc/nginx/sites-enabled/")
    print(f"  certbot --nginx -d beta.aber-owl.net")
    print(f"  nginx -t && systemctl reload nginx")
    print()
    print("--- STEP 2: frontend servers ---")
    print(f"  For each of {[h[0] for h in FRONTEND_HOSTS]}:")
    print(f"  # Add the following location block to the phenomebrowser.net config")
    print(f"  # (inside the server {{ listen 27004; }} block):")
    print(f"  #   (see deploy/nginx/frontend-aberowl-beta.conf)")
    print(f"  nginx -t && systemctl reload nginx")
    print()
    print("The nginx config files are in deploy/nginx/")
    print()
    print("After deployment, verify:")
    print("  curl -I https://beta.aber-owl.net/api/servers")


if __name__ == "__main__":
    main()
