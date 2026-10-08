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
rsync -aR --delete gallery.py inventory.py minyaml.py watch.py Dockerfile static/ "$BORIS:$APPDATA/app/"
rsync -c deploy/nginx.conf deploy/docker-compose.yml "$BORIS:$APPDATA/"
ssh "$BORIS" "printf 'GALLERY_ROOT=%s\n' '$GALLERY_ROOT' > '$APPDATA/.env' && cd '$APPDATA' && docker compose up -d --build && docker restart astrogallery >/dev/null"
echo "deployed to $BORIS:$APPDATA; builder log: ssh $BORIS docker logs -f astrogallery-builder"
