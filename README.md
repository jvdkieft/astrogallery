# astrogallery

Static gallery of Joe's finished astrophotos. A builder container on boris watches the telescopes
share (`\\boris\Telescopes`, `/mnt/user/Telescopes` on boris) and rebuilds the site whenever
something in it changes. Nothing comes from scopessd any more.

```
Telescopes/Finished/*.jpg + Gallery/picks.yaml + Source Data/  --watch.py + gallery.py (boris)-->  site/  --nginx:alpine-->  :8088
```

The share keeps its usual layout; the gallery reads three things from it:

```
Telescopes/
  Finished/          finished JPGs, top level only (TIFs and subfolders are ignored)
  Gallery/
    picks.yaml       what is shown
    inventory.json   optional; only when present it replaces scanning Source Data
  Source Data/       Seestar and Dwarf captures; hours and sessions are counted from here
```

Drop a JPG in `Finished/`, point picks.yaml at it, and the live site updates within a couple of
minutes. New captures in `Source Data/` update the hours the same way. The builder polls every 60 s and builds once the inputs have stopped changing for one poll,
so a file still being copied is not picked up half-written.

## Use

```
make deploy    # ship the code to boris and (re)start nginx + the builder there
make site      # build site/ locally from the share at /Volumes/Telescopes (TELESCOPES=... to override)
make preview   # build locally, then serve it at http://localhost:8000
```

`site/` also works opened straight from disk (`open site/index.html`); there is no JavaScript.

On Windows: `python gallery.py --root Y:\` with the share mapped as Y:.

Needs Python 3 and Pillow (the builder image has both). PyYAML is used if installed; otherwise
`minyaml.py` reads picks.yaml (block maps and lists, flow lists, comments; nothing fancier).

## Inputs (read only, never written)

Paths are set by flag or environment variable; the builder container uses the variables.

| Input | Flag | Variable | Default |
|---|---|---|---|
| `<root>` (the share) | `--root` | `GALLERY_ROOT` | none; `/data` in the builder |
| finished JPGs | `--finished` | `GALLERY_FINISHED` | `<root>/Finished` |
| picks.yaml | `--picks` | `GALLERY_PICKS` | `<root>/Gallery/picks.yaml` |
| capture data | `--source` | `GALLERY_SOURCE` | `<root>/Source Data` |
| inventory.json | `--inventory` | `GALLERY_INVENTORY` | `<root>/Gallery/inventory.json` if it exists |
| output | `--out` | `GALLERY_OUT` | `./site` |

- `picks.yaml`: one entry per target. `natural`, `hoo`, `alt: [...]`, `inset: {natural, hoo}`
  are file names in `Finished/`. `sessions` are `"<scope>|<target>"` keys matched exactly against
  inventory.json. `goal_h` is the depth goal. `show: false` hides the target.
- Sessions: `inventory.py` (a port of `SirilWork/PI/py/inventory.py` that takes the folder as an
  argument) scans `Source Data/` on every build: Seestar `Stacked_*.fit` names and Dwarf
  `shotsInfo.json`. Sessions with 0 stacked frames are left out of the tables and totals. If
  `Gallery/inventory.json` exists it is used instead (an empty one is refused); delete it to go
  back to scanning. With neither, the site is built without hours, goal bars or session tables.
  `python3 inventory.py "<root>/Source Data"` prints what the scan finds.
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

copies `gallery.py`, `inventory.py`, `minyaml.py`, `watch.py`, `static/` and the `Dockerfile` to
`/mnt/user/appdata/astrogallery/app` on boris (Unraid, 192.168.1.3), plus `docker-compose.yml`,
`nginx.conf` and a `.env` holding `GALLERY_ROOT`, then runs `docker compose up -d --build` there.
That starts two containers:

- `astrogallery-builder`: built from `app/`, mounts the telescopes share read-only at `/data` and
  writes `site/`. `ssh root@192.168.1.3 docker logs -f astrogallery-builder` shows each build.
- `astrogallery`: `nginx:alpine` serving `site/` read-only on port 8088. Nginx Proxy Manager
  forwards `astro.home.vandekieft.net` -> `192.168.1.3:8088` (LAN only).

Rerun `make deploy` only when the code changes. Override the target with
`make deploy BORIS=user@host APPDATA=/path GALLERY_ROOT=/mnt/user/<share>`.

## Rebuild loop

After a processing session: copy the JPG into `Finished/` on the share and update
`Gallery/picks.yaml` if a new image replaces a pick. New capture data in `Source Data/` is counted
automatically. The builder does the rest.
