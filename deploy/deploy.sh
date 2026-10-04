#!/bin/sh
# Ship site/ (plus the compose file and nginx.conf) to boris. nginx serves the files straight
# from the bind mount, so nothing needs reloading after a content change; the container is only
# restarted when nginx.conf changed. Needs SSH access to $BORIS (no keys are created here).
set -eu
cd "$(dirname "$0")/.."
BORIS=${BORIS:-root@192.168.1.3}
APPDATA=${APPDATA:-/mnt/user/appdata/astrogallery}

[ -f site/index.html ] || { echo "site/ is empty; run make site first" >&2; exit 1; }

ssh "$BORIS" "mkdir -p '$APPDATA/site'"
conf_changed=$(rsync -ci deploy/nginx.conf deploy/docker-compose.yml "$BORIS:$APPDATA/" | grep nginx.conf || true)
# --delay-updates: new files land together at the end, so nobody sees a page whose images aren't there yet
rsync -a --delete --delay-updates --exclude .manifest.json site/ "$BORIS:$APPDATA/site/"

if [ -n "$conf_changed" ]; then
    ssh "$BORIS" "docker restart astrogallery >/dev/null 2>&1 && echo 'nginx.conf changed: container restarted' || echo 'container not running yet: cd $APPDATA && docker compose up -d'"
fi
echo "deployed to $BORIS:$APPDATA/site"
