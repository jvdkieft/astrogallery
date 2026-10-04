# astrogallery

Static gallery of Joe's finished astrophotos, built from the curation on scopessd.

```
picks.yaml + inventory.json + Finished/*.jpg  --gallery.py-->  site/  --deploy.sh-->  boris (nginx:alpine)
```

## Use

```
make site      # build site/ from /Volumes/scopessd (SCOPESSD=... to override)
make preview   # build, then serve it at http://localhost:8000
make deploy    # rsync site/ to boris
make all       # site + deploy
```

`site/` also works opened straight from disk (`open site/index.html`); there is no JavaScript.

Needs Python 3 and Pillow. PyYAML is used if installed; otherwise `minyaml.py` reads picks.yaml
(block maps and lists, flow lists, comments; nothing fancier).

## Inputs (read only, never written)

- `Gallery/picks.yaml`: one entry per target. `natural`, `hoo`, `alt: [...]`, `inset: {natural, hoo}`
  are file names in `Finished/`. `sessions` are `"<scope>|<target>"` keys matched exactly against
  inventory.json. `goal_h` is the depth goal. `show: false` hides the target.
- `Gallery/inventory.json`: written by `SirilWork/PI/py/inventory.py`. Sessions with 0 stacked
  frames are left out of the tables and totals.
- `Finished/*.jpg`. TIFs are never published.

See [PROCESSING.md](PROCESSING.md) for getting a newly processed image into the gallery.

Missing files and session keys that are not in the inventory are reported as warnings; a target
with neither a natural nor a HOO image is skipped.

## Output

- `index.html`: cards grouped by kind (emission, reflection, SNR, planetary, galaxies, solar
  system) with filter chips, thumbnail (HOO when there is one), hours against the goal, scopes,
  nights and date range, filters.
- `<id>.html`: large image with a HOO / Natural toggle (HOO first), detail inset with its own
  toggle, other versions, and the session table.
- `img/<id>-<role>-600.jpg` and `-2560.jpg`: progressive sRGB JPEGs. `full/<id>-<role>.jpg`: a byte
  copy of the Finished JPG, offered as a download under its original name.
- `.manifest.json`: source mtime and size per image; only changed sources are re-rendered.
  Bump `GEN_VERSION` in gallery.py to force a full rebuild.

## Deploy on boris

One-time setup on boris (Unraid, 192.168.1.3):

```
make deploy                                  # creates /mnt/user/appdata/astrogallery and fills it
ssh root@192.168.1.3 'cd /mnt/user/appdata/astrogallery && docker compose up -d'
```

Without the Compose plugin, the equivalent is:

```
docker run -d --name astrogallery --restart unless-stopped -p 8088:80 \
  -v /mnt/user/appdata/astrogallery/site:/usr/share/nginx/html:ro \
  -v /mnt/user/appdata/astrogallery/nginx.conf:/etc/nginx/conf.d/default.conf:ro nginx:alpine
```

The container serves `site/` read-only on port 8088. Then add a Nginx Proxy Manager host
(suggested `astro.home.vandekieft.net` -> `192.168.1.3:8088`, LAN only).

After that, `make deploy` only rsyncs changed files; nginx needs no reload. If `deploy/nginx.conf`
changes, deploy.sh restarts the container.

Override the target with `make deploy BORIS=user@host APPDATA=/path`.

## Rebuild loop

After a processing session: rerun `inventory.py`, update picks.yaml if a new image replaces a pick,
then `make all`.
