# astrogallery

Static gallery of Joe's finished astrophotos. A builder container on boris watches a drop folder on
the telescopes share and rebuilds the site whenever something in it changes.

```
<drop>/Finished/*.jpg + picks.yaml + inventory.json  --watch.py + gallery.py (boris)-->  site/  --nginx:alpine-->  :8088
```

The drop folder defaults to `/mnt/user/telescopes/Gallery` on boris (an assumption; set
`GALLERY_DROP` if the share is elsewhere). It holds:

```
Gallery/
  Finished/       finished JPGs, top level only
  picks.yaml      what is shown
  inventory.json  hours and sessions (optional)
```

Drop a JPG in `Finished/`, point picks.yaml at it, and the live site updates within a couple of
minutes. The builder polls every 60 s and builds once the inputs have stopped changing for one poll,
so a file still being copied is not picked up half-written.

## Use

```
make deploy    # ship the code to boris and (re)start nginx + the builder there
make site      # build site/ locally from /Volumes/scopessd (SCOPESSD=... to override)
make preview   # build locally, then serve it at http://localhost:8000
```

`site/` also works opened straight from disk (`open site/index.html`); there is no JavaScript.

Needs Python 3 and Pillow (the builder image has both). PyYAML is used if installed; otherwise
`minyaml.py` reads picks.yaml (block maps and lists, flow lists, comments; nothing fancier).

## Inputs (read only, never written)

Paths are set by flag or environment variable; the builder container uses the variables.

| Input | Flag | Variable | Default |
|---|---|---|---|
| finished JPGs | `--finished` | `GALLERY_FINISHED` | `<src>/Finished` |
| picks.yaml | `--picks` | `GALLERY_PICKS` | `<src>/Gallery/picks.yaml` |
| inventory.json | `--inventory` | `GALLERY_INVENTORY` | `<src>/Gallery/inventory.json` |
| output | `--out` | `GALLERY_OUT` | `./site` |
| `<src>` | `--src` | `GALLERY_SRC`, `SCOPESSD` | `/Volumes/scopessd` |

- `picks.yaml`: one entry per target. `natural`, `hoo`, `alt: [...]`, `inset: {natural, hoo}`
  are file names in `Finished/`. `sessions` are `"<scope>|<target>"` keys matched exactly against
  inventory.json. `goal_h` is the depth goal. `show: false` hides the target.
- `inventory.json`: written by `SirilWork/PI/py/inventory.py` on the processing VM. Sessions with 0
  stacked frames are left out of the tables and totals. Optional: when the file is missing the site
  is built without hours, goal bars or session tables (an empty file is still refused). See
  [PROCESSING.md](PROCESSING.md#2-inventoryjson) for getting it into the drop folder.
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

```
make deploy
```

copies `gallery.py`, `minyaml.py`, `watch.py`, `static/` and the `Dockerfile` to
`/mnt/user/appdata/astrogallery/app` on boris (Unraid, 192.168.1.3), plus `docker-compose.yml`,
`nginx.conf` and a `.env` holding `GALLERY_DROP`, then runs `docker compose up -d --build` there.
That starts two containers:

- `astrogallery-builder`: built from `app/`, mounts the drop folder read-only at `/data` and
  writes `site/`. `ssh root@192.168.1.3 docker logs -f astrogallery-builder` shows each build.
- `astrogallery`: `nginx:alpine` serving `site/` read-only on port 8088. Nginx Proxy Manager
  forwards `astro.home.vandekieft.net` -> `192.168.1.3:8088` (LAN only).

Rerun `make deploy` only when the code changes. Override the target with
`make deploy BORIS=user@host APPDATA=/path GALLERY_DROP=/mnt/user/<share>/Gallery`.

## Rebuild loop

After a processing session: copy the JPG into the drop folder's `Finished/`, refresh
`inventory.json` there, update picks.yaml if a new image replaces a pick. The builder does the rest.
