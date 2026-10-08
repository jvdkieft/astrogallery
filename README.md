# astrogallery

Static gallery of Joe's finished astrophotos. A builder container on boris watches the telescopes
share (`\\boris\Telescopes`, `/mnt/user/Telescopes` on boris) and rebuilds the site whenever
something in it changes. Nothing comes from scopessd any more.

```
Telescopes/Finished/*.jpg + Source Data/ + NINA/ (+ Gallery/picks.yaml)  --watch.py + gallery.py (boris)-->  site/  --nginx:alpine-->  :8088
```

The share keeps its usual layout; the gallery reads these from it:

```
Telescopes/
  Finished/          finished JPGs, top level only (TIFs and subfolders are ignored); their
                     names decide the targets and images (autopicks.py)
  Source Data/       Seestar and Dwarf captures; hours and sessions are counted from here
  NINA/              Seestar subs captured with NINA; counted too
  SirilWork/PI/nina_reject.json   NINA subs left out of the count (or NINA/nina_reject.json)
  Gallery/
    picks.yaml       optional overrides of the automatic picks
    inventory.json   optional; only when present it replaces scanning Source Data and NINA
```

Drop a correctly named JPG in `Finished/` and the live site updates within a couple of minutes:
a new object gets its own card, a version with more nights replaces the older one. New captures in
`Source Data/` or `NINA/` update the hours the same way. Nothing has to be edited. The builder polls every 60 s and builds once the inputs have stopped changing for one poll,
so a file still being copied is not picked up half-written.

## Use

```
make deploy    # ship the code to boris and (re)start nginx + the builder there
make site      # build site/ locally from the share at /Volumes/Telescopes (TELESCOPES=... to override)
make preview   # build locally, then serve it at http://localhost:8000
```

```
make archive-check   # what the share is missing from the SSD (Source Data, NINA, Finished, nina_reject.json)
make archive         # copy it: adds files, replaces empty or older copies, never deletes (archive.py)
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
| NINA subs | `--nina` | `GALLERY_NINA` | `<root>/NINA` |
| NINA rejects | | `GALLERY_NINA_REJECT` | `<root>/SirilWork/PI/nina_reject.json`, else `<root>/NINA/nina_reject.json` |
| inventory.json | `--inventory`, `--no-inventory` | `GALLERY_INVENTORY` (`none` = scan) | `<root>/Gallery/inventory.json` if it exists |
| output | `--out` | `GALLERY_OUT` | `./site` |

- Targets: `autopicks.py` groups the `Finished/` JPGs by catalogue number and picks the main
  image (most nights, latest, PixInsight), its HOO twin, a detail inset and other versions, and
  matches the capture sessions; name, kind and page id come from its `CATALOGUE` table. See
  [PROCESSING.md](PROCESSING.md#how-the-automatic-picks-work-autopickspy).
  `python3 autopicks.py --root <share>` prints the result; `--selftest` checks the rules.
- `picks.yaml` (optional): one entry per target to override. `natural`, `hoo`, `alt: [...]`,
  `inset: {natural, hoo}` are file names in `Finished/`. `sessions` are `"<scope>|<target>"` keys.
  `goal_h` is the depth goal. `show: false` hides the target. Every field set wins; the rest is
  automatic. `python3 autopicks.py --root <share> --slim` prints it with the automatic fields left out.
- Sessions: `inventory.py` (a port of `SirilWork/PI/py/inventory.py` that takes the folders as
  arguments) scans `Source Data/` and `NINA/` on every build: Seestar `Stacked_*.fit` names, NINA
  subs (FITS headers, minus the reject list) and Dwarf `shotsInfo.json`. Sessions with 0 stacked
  frames are left out of the tables and totals. If `Gallery/inventory.json` exists it is used
  instead (an empty one is refused); delete it to go back to scanning. With neither, the site is
  built without hours, goal bars or session tables. `python3 inventory.py "<root>/Source Data"`
  prints what the scan finds.
- `Finished/*.jpg`. TIFs are never published.

See [PROCESSING.md](PROCESSING.md) for getting a newly processed image into the gallery.

Missing files and session keys that are not in the inventory are reported as warnings; a target
with neither a natural nor a HOO image is skipped. Files whose names do not follow the naming
are ignored by the automatic picks (picks.yaml can still use them).

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

copies `gallery.py`, `autopicks.py`, `inventory.py`, `minyaml.py`, `watch.py`, `static/` and the `Dockerfile` to
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

After a processing session: copy the JPG into `Finished/` on the share. New capture data in
`Source Data/` and `NINA/` is counted automatically. The builder does the rest. Edit
`Gallery/picks.yaml` only to correct an automatic choice; a pinned `natural`/`hoo` there stops
that target from updating by itself.
