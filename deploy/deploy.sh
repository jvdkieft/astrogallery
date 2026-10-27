#!/bin/sh
# Ship the generator to boris and (re)start it there. boris builds site/ itself from the
# telescopes share, so nothing is built or copied from this machine except code.
# Needs SSH access to $BORIS (no keys are created here).
set -eu
cd "$(dirname "$0")/.."
BORIS=${BORIS:-root@192.168.1.3}
APPDATA=${APPDATA:-/mnt/user/appdata/astrogallery}
GALLERY_ROOT=${GALLERY_ROOT:-/mnt/user/Telescopes}

ssh "$BORIS" "mkdir -p '$APPDATA/site' '$APPDATA/app' && { test -f '$GALLERY_ROOT/Gallery/picks.yaml' || echo 'warning: $GALLERY_ROOT/Gallery/picks.yaml does not exist yet on boris' >&2; }"
rsync -aR --delete gallery.py autopicks.py inventory.py minyaml.py watch.py Dockerfile static/ "$BORIS:$APPDATA/app/"
rsync -c deploy/nginx.conf deploy/docker-compose.yml deploy/docker-compose.tunnel.yml "$BORIS:$APPDATA/"
# With a tunnel.env on boris (Cloudflare Tunnel token, see deploy/PUBLISH.md) the connector from
# docker-compose.tunnel.yml is started too; without one, nothing about the tunnel is touched.
VERSION=$(git describe --always --dirty 2>/dev/null || echo unknown)
ssh "$BORIS" "printf 'GALLERY_ROOT=%s\nGALLERY_VERSION=%s\n' '$GALLERY_ROOT' '$VERSION' > '$APPDATA/.env' && cd '$APPDATA' && if [ -f tunnel.env ]; then export COMPOSE_FILE=docker-compose.yml:docker-compose.tunnel.yml; echo 'tunnel.env found: starting the Cloudflare Tunnel connector too'; fi && docker compose up -d --build && docker restart astrogallery >/dev/null"
echo "deployed to $BORIS:$APPDATA; builder log: ssh $BORIS docker logs -f astrogallery-builder"
