# Getting a finished image into the gallery

For whoever processes images (usually a Claude session working from `SirilWork/PI/README.md`).
The gallery is built from boris's telescopes share (`\\boris\Telescopes` over SMB,
`/mnt/user/Telescopes` on boris). You change things there and the builder container on boris
rebuilds the site by itself within a couple of minutes. scopessd is not involved, and in the
normal case nothing has to be edited: the targets come from the file names in `Finished/` and the
hours from the capture data.

| Step | What | Where (on the share) |
|---|---|---|
| 1 | Copy the finished JPG, named per the convention | `Finished/` |
| 2 | Make sure the capture data is there | `Source Data/` and `NINA/` (hours are counted from them) |
| 3 | Only if the automatic choice is wrong: add an override | `Gallery/picks.yaml` |
| 4 | Check the build | `ssh root@192.168.1.3 docker logs --tail 30 astrogallery-builder` |

The live gallery is http://192.168.1.3:8088 (nginx container `astrogallery` on boris).

## 1. Finished/

- Copy to the top level of the share's `Finished/`. The generator does not look in subfolders
  and ignores TIFs; only the JPG is published.
- The name is what places the image, so follow `README - Portfolio.md` exactly:
  `<Cat#> <Name> <D3|S30P|S30P+D3> [mosaic] <PixInsight|Siril> [tags] [HOO] [Astro] <YYYYMMDD[+MMDD...]> [wide].jpg`,
  e.g. `NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg`. Date = local evening the
  session started; list every night in the stack.
- Emission nebulae get two files, a natural one and a `HOO` one with otherwise the same name.
  Galaxies, clusters and reflection nebulae get natural only.
- Export the JPG at full resolution, sRGB (or with an embedded ICC profile; it is converted).
- Use a new file name for a new version (more nights, new process) rather than overwriting.
  Overwriting also works: the generator rebuilds any image whose mtime or size changed.
- The generator never writes to the share (it is mounted read-only).

### How the automatic picks work (autopicks.py)

- Files are grouped into targets by the leading catalogue number: `M27 Dumbbell ...` and
  `M27 D3 ...` are both M 27; `NGC7380` = `NGC 7380`; `IC 1805-1848 Heart and Soul` and
  `IC 1805 Heart` are both IC 1805. Names with no number go by their first word (`Veil ...`,
  `Moon ...`).
- Main image: most nights, then the latest night, then PixInsight over Siril, then not `wide`,
  then the fewest extra tags (`hand`, `BXT+NXT+SXT`, `v2`, ...). Its `HOO` twin becomes the HOO
  rendering. So a new `... 20261003+1005+1007+1008.jpg` replaces the `+1007` version by itself.
- Detail inset: a target marked as part of another in `CATALOGUE` (IC 1848 Soul in the Heart,
  IC 410 Tadpoles in the IC 405 mosaic, C 4 in NGC 7023), or, for a Seestar mosaic, the best
  Dwarf 3 image of the same object (NGC 1499).
- Other versions: the `wide`/crop twin of the main image, and the best version of every other
  night set, scope and rendering. Versions whose nights are all inside the main image's are left
  out, and so are other processes of exactly the same nights.
- Sessions: every capture session whose target is one of the target's catalogue numbers or
  aliases (C 23 = NGC 891, C 30 = NGC 7331, mosaic_NGC 1893 = IC 405, ...), from the scopes in its
  file names. The Moon and Sun get none.
- Name, kind (which decides the group on the page) and page id come from the `CATALOGUE` table in
  autopicks.py; an object not in it gets its name from the file name and kind `other`. Add a line
  there (or a picks.yaml entry) for a new object. Goal: from the kind, per the depth goals in
  `README - Portfolio.md` (mosaic 5 h, galaxy 4, emission/reflection/SNR 3, planetary 2, cluster 1.5).
- Files without `PixInsight` or `Siril` in the name (quick app exports like `M27 D3 20260911.jpeg`)
  are used only when a target has nothing else, and a target made only of those is not shown.

To see what it picks (read only):

```
python3 autopicks.py --root /mnt/user/Telescopes          # or Y:\ on Windows, /Volumes/Telescopes on the Mac
python3 autopicks.py --selftest                           # the rules, checked against real file names
```

## 2. Source Data and NINA (hours and sessions)

Hours, nights, scopes and filters on the site all come from the capture data on the share, never
from file names. The builder scans it on every build with `inventory.py` (same rules as
`SirilWork/PI/py/inventory.py`):

- Seestar app: `Source Data/Seestar S30 Pro/<target>/Stacked_*.fit` (largest stack per night).
- Seestar under NINA: `NINA/<target>/<date>/LIGHT/NNNN.fits`, every sub except those listed in
  `SirilWork/PI/nina_reject.json` (or `NINA/nina_reject.json`), grouped by FILTER and EXPTIME.
- Dwarf 3: `Source Data/Dwarf 3/Astronomy/DWARF_RAW_*/shotsInfo.json` (`shotsStacked`).

New data only has to be copied to the share; there is nothing to rerun. To see what the scan finds
(one line per session):

```
python3 inventory.py "/mnt/user/Telescopes/Source Data"      # NINA/ and the reject list next to it are found too
```

The second and third columns (`scope`, `target`) are the `sessions` keys picks.yaml uses, e.g.
`D3   NGC 281 ...` -> `"D3|NGC 281"`; Seestar mosaics come out as `S30P|mosaic_IC 1805`. Sessions with 0
stacked frames are ignored.

If `Gallery/inventory.json` exists on the share it is used instead of the scan (the build log says
`sessions: N from .../inventory.json`). That is only a stopgap while the share's capture data is
incomplete; delete or rename it to go back to scanning. `gallery.py --no-inventory` (or
`GALLERY_INVENTORY=none`) scans even when it exists, to compare.

## 3. picks.yaml (overrides, optional)

`Gallery/picks.yaml` on the share only corrects the automatic picks. Joe owns it; keep edits small
and say what you changed. Without the file every target is automatic.

```yaml
targets:
  - id: ngc281                        # page name ngc281.html; matches the automatic target with this id
    match: NGC 281                    # optional: the automatic target to use when id differs (catalogue key or id)
    name: NGC 281 Pacman              # card and page title
    kind: emission nebula             # decides the group (see below); "mosaic" adds a badge
    goal_h: 5                         # depth goal in hours
    sessions: ["D3|NGC 281"]          # "<scope>|<target>" capture-session keys, exact match; summed
    natural: NGC 281 Pacman D3 PixInsight 20260911+1002+1003 wide.jpg      # file name in Finished/
    hoo: NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg
    alt: [NGC 281 Pacman D3 Siril hand 20260911 wide.jpg]                  # "Other versions"
    inset:                            # detail image with its own HOO/Natural toggle
      natural: IC 1848 Soul D3 PixInsight 20261003+1004 wide.jpg
      hoo: IC 1848 Soul D3 PixInsight HOO 20261003+1004 wide.jpg
    show: false                       # hides the target
```

Rules:

- Every field an entry sets wins, even an empty one (`hoo:` with no value = no HOO image,
  `alt: []` = no other versions). Fields it leaves out come from the automatic target.
- An entry takes over the automatic target with the same `id` (or `match`), or the one holding its
  `natural`/`hoo` file. Any other automatic target holding a file the entry uses is not shown
  separately (e.g. the C 33 image used as the Veil inset).
- Pinning `natural`/`hoo` stops those from updating by themselves. Leave them out unless the
  automatic choice is wrong.
- Automatic targets with no entry are added after the picks.yaml ones.
- At least one of `natural`/`hoo` must exist or the target is skipped. With both, the page opens
  on HOO and the card thumbnail is the HOO one.
- File names are exact (case, spaces, `+`). Don't quote them unless they contain ` #` or start with
  `[`, `"` or `'`.
- Group by `kind`, first match wins: `galaxy` -> Galaxies, `emission` -> Emission nebulae,
  `reflection` -> Reflection nebulae, `supernova` -> Supernova remnants, `planetary` -> Planetary
  nebulae, `solar system`/`moon`/`sun`/`planet` -> Solar system, else Other.

`python3 autopicks.py --root <share> --slim` prints the current picks.yaml with every field the
automatic picks already produce left out: the same site, but new versions are picked up by
themselves. Review it before it replaces the share's copy.

Keep picks.yaml plain YAML: block lists and maps, `[a, b]` lists, `#` comments. PyYAML is not
installed on the Mac or in the builder, and the fallback reader handles nothing fancier (no
anchors, no multi-line strings).

## 4. Check the build

There is nothing to run: the builder on boris notices the change and rebuilds within a couple of
minutes. Check what it said:

```
ssh root@192.168.1.3 docker logs --tail 30 astrogallery-builder
```

`picks: N from picks.yaml, M new from Finished/ names` says how many targets came from where.
`warning: missing in Finished/: ...` means a picks.yaml file name is wrong, and
`warning: <id>: session key ... not in the capture sessions` means a `sessions` key doesn't match,
or that night's data is not on the share yet. Spot-check the hours it prints. A line
`watch: build FAILED` means the site was left as it was; fix the input and the next change
triggers another build.

Without SSH to boris, just open the target page on http://192.168.1.3:8088 and check it.

To try a change before it goes live, build locally from the share (read only):
`python3 gallery.py --root <share> [--picks <draft picks.yaml>] --out /tmp/site`, then open
`/tmp/site/index.html`.

Code changes to the generator still need `make deploy` from Joe's Mac, with Joe's say-so.

## Checklist

- [ ] JPG in the share's `Finished/`, named per the convention, HOO + natural for emission targets
- [ ] capture data for every night on the share (`Source Data/`, `NINA/`)
- [ ] builder log shows no warnings and the right hours, and the page looks right on http://192.168.1.3:8088
- [ ] only if something is wrong: a small override in picks.yaml
