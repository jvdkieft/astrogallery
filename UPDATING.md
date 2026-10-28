# Updating the gallery

The site is https://bortle9.space on the internet and https://astro.home.vandekieft.net on the LAN
(also http://192.168.1.7; `192.168.1.3:8088` only answers from boris itself). Boris rebuilds
it by itself from the telescopes share: it checks every 60 seconds and rebuilds once the share has
stopped changing. You never build or upload the site yourself. Updating the gallery means putting
the right files on the share.

| Machine | Share | Repo checkout |
|---|---|---|
| Mac | `/Volumes/Telescopes` (`smb://boris/Telescopes`) | `~/GitHub/astrogallery` (the only machine with SSH to boris) |
| PC | `Y:\` (`\\boris\Telescopes`) | none needed; no Python installed |
| boris | `/mnt/user/Telescopes` | `/mnt/user/appdata/astrogallery/app` (written by `make deploy`) |

## After an imaging night: the usual update

1. **Process on scopessd** as usual. Export the finished JPG at full resolution into
   `Finished/` on the SSD, named per the convention below.
2. **Copy it to the share.**
   - From the Mac, with scopessd plugged in:
     ```
     cd ~/GitHub/astrogallery
     make archive-check   # what the share is missing (Seestar S30 Pro, Dwarf 3, NINA, Finished, nina_reject.json)
     make archive         # copy it; only adds or replaces empty/older copies, never deletes
     ```
   - From the PC (scopessd is `E:\`): copy the new JPG from `E:\Finished` to `Y:\Finished`, and the
     night's capture folders from `E:\Seestar S30 Pro`, `E:\Dwarf 3` and `E:\NINA` to the same places under `Y:\`.
     Run `make archive-check` on the Mac later to catch anything you missed.
3. **Wait a couple of minutes**, then open https://bortle9.space/status.html (the "build status" link in
   every page's footer). Check that the build time is recent, there are no
   warnings, nothing is listed under "Not published", and the target shows the new image and hours.
4. Open the target's page and look at it.

That's all. A version with more nights replaces the older one on its own, a new object gets its
own card, and new capture data updates the hours. Nothing has to be edited.

## Naming the JPG

The file name is what places the image, so it has to follow the convention exactly:

```
<Cat#> <Name> <D3|S30P|S30P+D3> [mosaic] <PixInsight|Siril> [tags] [HOO] [Astro] <YYYYMMDD[+MMDD...]> [wide].jpg
```

Examples:

```
NGC 281 Pacman D3 PixInsight 20260911+1002+1003 wide.jpg
NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg
```

- List every night in the stack. The date is the local evening the session started.
- Emission nebulae get two files: a natural one and a `HOO` one with otherwise the same name.
  Galaxies, clusters and reflection nebulae get natural only.
- Put it in the top level of `Finished/`. Subfolders and TIFs are ignored.
- Give a new version a new name (more nights, a new process). Overwriting a file also works.
- Names without `PixInsight` or `Siril` (quick app exports) are only used when a target has
  nothing else.

The main image for each target is the one with the most nights, then the latest night, then
PixInsight over Siril. [PROCESSING.md](PROCESSING.md#how-the-automatic-picks-work-autopickspy)
has the full rules.

## When something looks wrong

| You see | Why | Fix |
|---|---|---|
| The new image isn't on the site | The build hasn't run yet, or the name couldn't be read | Check status.html: a recent build time, and whether the file is under "Not published". Rename it to the convention. |
| A warning about an unreadable JPG | The copy was cut short or is 0 bytes | Copy the file again. The old image stays on the site until then. |
| Hours are too low, or a session-key warning | That night's capture data isn't on the share yet | `make archive` from the Mac, or copy the `Seestar S30 Pro`/`Dwarf 3`/`NINA` folders. |
| The wrong image is the main one | The automatic choice doesn't match what you want | Add an override in `Gallery/picks.yaml` (below). |
| A new object lands under "Other" with a raw name | It isn't in the catalogue table in `autopicks.py` | Give it `name`, `kind` and `goal_h` in picks.yaml, or add it to the catalogue (a code change). |
| status.html says the build failed | The end of the log is in build-log.html (home network only: https://astro.home.vandekieft.net/build-log.html) | The site stays as it was. Fix the input; the next change triggers a new build. |

## Overriding a pick: Gallery/picks.yaml

`Gallery/picks.yaml` on the share only corrects the automatic choices. Without an entry, a target
is fully automatic. An entry only changes the fields it sets:

```yaml
targets:
  - id: ngc281                    # the page name, ngc281.html
    natural: NGC 281 Pacman D3 PixInsight 20260911+1002+1003 wide.jpg
    hoo: NGC 281 Pacman D3 PixInsight HOO 20260911+1002+1003 wide.jpg
  - id: ngc6888                   # a new object not in the catalogue yet
    match: NGC 6888               # the automatic target this entry corrects
    name: NGC 6888 Crescent Nebula  # card and page title
    kind: emission nebula         # decides the group on the index
    goal_h: 3                     # depth goal in hours
  - id: moon
    show: false                   # hide a target
```

- Pinning `natural` or `hoo` stops that target from updating by itself when you add a better
  version. Remove the pin once the automatic choice is right again.
- File names must match exactly, including case, spaces and `+`.
- Keep it plain YAML: block lists and maps, `[a, b]` lists and `#` comments only.
- A backup of the old full file is next to it as `picks.yaml.bak-20261012`.

To check a draft before saving it to the share, from the Mac:

```
cd ~/GitHub/astrogallery
python3 autopicks.py --root /Volumes/Telescopes          # what the automatic picks choose
python3 gallery.py --root /Volumes/Telescopes --picks ~/picks-draft.yaml --out /tmp/site
open /tmp/site/index.html
```

`python3 autopicks.py --root /Volumes/Telescopes --slim` prints the current picks.yaml with every
field the automatic picks already produce left out.

## Changing the code

Only needed for things the share can't do: the page layout, the catalogue table, the pick rules.
On the Mac:

```
cd ~/GitHub/astrogallery
git switch -c my-change
# edit gallery.py, autopicks.py, static/style.css, ...
make test        # autopicks self-test and a compile check (GitHub Actions runs it too)
make preview     # build from the share and serve it at http://localhost:8000
```

Merge the change into `main`, then ship it from the Mac:

```
git switch main && git pull
make deploy
```

`make deploy` copies the code to boris and rebuilds the two containers there
(`astrogallery-builder` and the nginx `astrogallery`). The builder then rebuilds the site, and
status.html shows the deployed commit. Run it yourself: it needs SSH to boris, which Claude's
sessions on the Mac are not allowed to use. Deploy from `main` so boris runs what's in the repo.

To watch the builder directly: `ssh root@192.168.1.3 docker logs -f astrogallery-builder`.

[README.md](README.md) has the full reference: inputs, flags, environment variables and output files.
