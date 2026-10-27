#!/bin/sh
# Ship the generator to boris and (re)start it there. boris builds site/ itself from the
# telescopes share, so nothing is built or copied from this machine except code.
# Needs SSH access to $BORIS (no keys are created here). ./logs is chowned to uid 101, the nginx
# worker in nginx:alpine, because the access log's name carries the month and is opened by the worker.
set -eu
cd "$(dirname "$0")/.."
BORIS=${BORIS:-root@192.168.1.3}
APPDATA=${APPDATA:-/mnt/user/appdata/astrogallery}
GALLERY_ROOT=${GALLERY_ROOT:-/mnt/user/Telescopes}

ssh "$BORIS" "mkdir -p '$APPDATA/site' '$APPDATA/app' '$APPDATA/logs' && chown 101:101 '$APPDATA/logs' && { test -f '$GALLERY_ROOT/Gallery/picks.yaml' || echo 'warning: $GALLERY_ROOT/Gallery/picks.yaml does not exist yet on boris' >&2; }"
rsync -aR --delete gallery.py autopicks.py inventory.py minyaml.py watch.py Dockerfile static/ "$BORIS:$APPDATA/app/"
rsync -c deploy/nginx.conf deploy/docker-compose.yml deploy/docker-compose.tunnel.yml deploy/docker-compose.stats.yml deploy/telegraf.conf deploy/cfstats.py "$BORIS:$APPDATA/"
# With a tunnel.env on boris the Cloudflare Tunnel connector (docker-compose.tunnel.yml) is started
# too, with a stats.env the traffic stats (docker-compose.stats.yml); see deploy/PUBLISH.md. Without
# them nothing about those is touched. nginx, Telegraf and cfstats are restarted on every deploy because their
# config files are bind-mounted: compose does not notice a changed file, and they only read it at start.
VERSION=$(git describe --always --dirty 2>/dev/null || echo unknown)
ssh "$BORIS" "printf 'GALLERY_ROOT=%s\nGALLERY_VERSION=%s\n' '$GALLERY_ROOT' '$VERSION' > '$APPDATA/.env' && cd '$APPDATA' && cf=docker-compose.yml; if [ -f tunnel.env ]; then cf=\$cf:docker-compose.tunnel.yml; echo 'tunnel.env found: starting the Cloudflare Tunnel connector too'; fi; if [ -f stats.env ]; then cf=\$cf:docker-compose.stats.yml; echo 'stats.env found: starting the traffic stats (Telegraf + cfstats) too'; fi; export COMPOSE_FILE=\$cf && docker compose up -d --build && docker restart astrogallery >/dev/null && if [ -f stats.env ]; then docker restart astrogallery-stats astrogallery-cfstats >/dev/null; fi"
echo "deployed to $BORIS:$APPDATA; builder log: ssh $BORIS docker logs -f astrogallery-builder"
