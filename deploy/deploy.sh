#!/bin/sh
# Ship the generator to boris and (re)start it there. boris builds site/ itself from the drop
# folder on the telescopes share, so nothing is built or copied from this machine except code.
# Needs SSH access to $BORIS (no keys are created here).
set -eu
cd "$(dirname "$0")/.."
BORIS=${BORIS:-root@192.168.1.3}
APPDATA=${APPDATA:-/mnt/user/appdata/astrogallery}
GALLERY_DROP=${GALLERY_DROP:-/mnt/user/telescopes/Gallery}

ssh "$BORIS" "mkdir -p '$APPDATA/site' '$APPDATA/app' && test -d '$GALLERY_DROP/Finished' || echo 'warning: $GALLERY_DROP/Finished does not exist yet on boris' >&2"
rsync -aR --delete gallery.py minyaml.py watch.py Dockerfile static/ "$BORIS:$APPDATA/app/"
rsync -c deploy/nginx.conf deploy/docker-compose.yml "$BORIS:$APPDATA/"
ssh "$BORIS" "printf 'GALLERY_DROP=%s\n' '$GALLERY_DROP' > '$APPDATA/.env' && cd '$APPDATA' && docker compose up -d --build && docker restart astrogallery >/dev/null"
echo "deployed to $BORIS:$APPDATA; builder log: ssh $BORIS docker logs -f astrogallery-builder"
