# Getting a finished image into the gallery

For whoever processes images (usually a Claude session working from `SirilWork/PI/README.md`).
The gallery is built from three things in the drop folder on boris's telescopes share
(`/mnt/user/telescopes/Gallery`, `\\boris\telescopes\Gallery` over SMB; the share path is an
assumption, see `GALLERY_DROP` in the README). You change those three things and the builder
container on boris rebuilds the site by itself within a couple of minutes.

| Step | What | Where (in the drop folder) |
|---|---|---|
| 1 | Copy the finished JPG | `Finished/` |
| 2 | Refresh the inventory | `inventory.json` |
| 3 | Point picks.yaml at the new image | `picks.yaml` |
| 4 | Check the build | `ssh root@192.168.1.3 docker logs --tail 20 astrogallery-builder` |

The live gallery is http://192.168.1.3:8088 (nginx container `astrogallery` on boris).

## 1. Finished/

- Copy to the top level of the drop folder's `Finished/`. The generator does not look in subfolders.
  TIFs can stay on scopessd; only the JPG is needed here.
- Naming, from `README - Portfolio.md`:
  `<Cat#> <Name> <D3|S30P|S30P+D3> [mosaic] <PixInsight|Siril> [HOO] <YYYYMMDD[+MMDD...]> [wide].jpg`,
  e.g. `NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg`. Date = local evening the
  session started.
- Emission nebulae get two files, a natural one and a `HOO` one. Galaxies, clusters and reflection
  nebulae get natural only.
- The gallery publishes the JPG only. It makes 600 px and 2560 px copies and offers the JPG itself
  as the full-resolution download, so export the JPG at full resolution, sRGB (or with an embedded
  ICC profile; it is converted). TIFs are never published.
- Prefer a new file name for a new version (more nights, new process) over overwriting the old one.
  Overwriting also works: the generator rebuilds any image whose mtime or size changed.
- The generator never writes to the drop folder (it is mounted read-only).

## 2. inventory.json

Hours, nights, scopes and filters on the site all come from `inventory.json`, never from file names.
Rerun it after new data lands or after stacking:

```
python3 ~/lib/inventory.py        # or SirilWork/PI/py/inventory.py
```

It scans `~/mnt/scopessd/Source Data/` and writes `~/mnt/scopessd/Gallery/inventory.json`, so run it
on the processing VM where scopessd is mounted at `~/mnt/scopessd`. Then copy that file into the drop
folder, overwriting the old one. (If the VM can mount the telescopes share, copying it there as part
of the same command saves the step.) On Joe's Mac `~/mnt/scopessd` does not exist and it would write
an empty list; `gallery.py` refuses to build from an empty inventory.

If `inventory.json` is missing from the drop folder the site still builds and shows the images, just
without hours, goal bars and session tables.

It prints one line per session. The second and third columns (`scope`, `target`) are what picks.yaml
`sessions` keys must match, e.g. `D3   NGC 281 ...` -> `"D3|NGC 281"`, and Seestar mosaics come out as
`S30P|mosaic_IC 1805`. Sessions with 0 stacked frames are ignored by the gallery.

## 3. picks.yaml

`Gallery/picks.yaml` decides what is shown. Joe owns it; keep edits small and say what you changed.
Entries marked `# check` are guesses waiting for Joe.

```yaml
targets:
  - id: ngc281                        # unique, lowercase; becomes the page name ngc281.html
    name: NGC 281 Pacman              # card and page title
    kind: emission nebula             # also decides the group (see below); "mosaic" adds a badge
    goal_h: 5                         # depth goal in hours (README - Portfolio.md); omit for none
    sessions: ["D3|NGC 281"]          # "<scope>|<target>" from inventory.json, exact match; summed
    natural: NGC 281 Pacman D3 PixInsight 20260911+1002+1003 wide.jpg      # file name in Finished/
    hoo: NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg      # optional
    alt: [NGC 281 Pacman D3 Siril hand 20260911 wide.jpg]                  # optional, shown as "Other versions"
    inset:                            # optional detail image, its own HOO/Natural toggle
      natural: IC 1848 Soul D3 PixInsight 20261003+1004 wide.jpg
      hoo: IC 1848 Soul D3 PixInsight HOO 20261003+1004 wide.jpg
    show: false                       # optional; hides the target from the gallery
```

Rules the generator applies:

- At least one of `natural` / `hoo` must exist or the target is skipped. When both exist the page
  opens on HOO and the card thumbnail is the HOO one.
- File names are exact (case, spaces, `+`). Don't quote them unless they contain ` #` or start with
  `[`, `"` or `'`.
- `sessions: []` (Moon, Sun) shows no hours, no goal bar and no session table.
- Group by `kind`, first match wins: `galaxy` -> Galaxies, `emission` -> Emission nebulae,
  `reflection` -> Reflection nebulae, `supernova` -> Supernova remnants, `planetary` -> Planetary
  nebulae, `solar system`/`moon`/`sun`/`planet` -> Solar system, else Other.
- Cards keep picks.yaml order within each group.

Common edits:

- **New version of an existing target** (more nights): replace the `natural`/`hoo` file names with
  the new ones. Move the old one into `alt` only if it is still worth seeing.
- **New target**: add an entry with a new `id`; add every scope/target key that counts toward it
  to `sessions` (both scopes, mosaic and single-panel).
- **New data, same image**: nothing to change in picks.yaml; rerunning the inventory updates the hours.

Keep picks.yaml plain YAML: block lists and maps, `[a, b]` lists, `#` comments. PyYAML is not
installed on the Mac or in the builder, and the fallback reader handles nothing fancier (no anchors, no multi-line
strings).

## 4. Check the build

There is nothing to run: the builder on boris notices the change and rebuilds within a couple of
minutes. Check what it said:

```
ssh root@192.168.1.3 docker logs --tail 20 astrogallery-builder
```

`warning: missing in Finished/: ...` means a picks.yaml file name is wrong, and
`warning: <id>: session key ... not in inventory.json` means a `sessions` key doesn't match.
Spot-check the hours it prints. A line `watch: build FAILED` means the site was left as it was;
fix the input and the next change triggers another build.

Without SSH to boris, just open the target page on http://192.168.1.3:8088 and check it.

To try a change before it goes live, build locally against a copy of the drop folder:
`python3 gallery.py --src <copy> --finished <copy>/Finished --picks <copy>/picks.yaml
--inventory <copy>/inventory.json`, then `make preview`-style serve `site/`.

Code changes to the generator still need `make deploy` from Joe's Mac, with Joe's say-so.

## Checklist

- [ ] JPG in the drop folder's `Finished/`, named per the convention, HOO + natural for emission targets
- [ ] `inventory.py` rerun on the VM and `inventory.json` copied into the drop folder
- [ ] picks.yaml points at the new files; `sessions` keys cover all the data
- [ ] builder log shows no warnings and the right hours, and the page looks right on http://192.168.1.3:8088
