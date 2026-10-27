# Publishing the gallery on the internet

How to put the site on `bortle9.space` without exposing anything else at home. Two ways are
described; the first is the one to use.

```
visitor --HTTPS--> Cloudflare edge --tunnel (outbound from boris)--> cloudflared --> astrogallery:80
                                                                      (both on proxynet)
LAN:  https://astro.home.vandekieft.net --> Nginx Proxy Manager --> astrogallery:80   (unchanged)
```

What is already in place in this repo (see README "Facing the internet"): metadata-stripped
`full/` copies, the hardened `deploy/nginx.conf` (no version string, builder files 404,
`status.html` for private addresses only, `full/` throttled, CSP and the other headers,
`robots.txt`), a read-only nginx container.

## Before either route

1. Merge the PR and `make deploy` from the Mac. The first build after it re-renders every
   image once (`GEN_VERSION` 2); wait for `status.html` to show the new build.
2. Nginx Proxy Manager forwards `astro.home.vandekieft.net` to the container's own LAN address,
   `192.168.1.7` on `br0` (docker-compose.yml). A deploy from a checkout without that network
   (anything before the share-layout merge) drops the address and NPM answers **502**; that is
   what happened on 2026-10-27. Deploying from `main` puts it back. The tunnel is not affected:
   it reaches the container by name on `proxynet`.
3. On the LAN check the new config is live: `curl -sI https://astro.home.vandekieft.net/ | grep -i content-security`
   must print the CSP header, and `curl -s https://astro.home.vandekieft.net/nope | grep nginx`
   must show `nginx` without a version.
4. `bortle9.space` is registered at Cloudflare Registrar, in the same account as
   `vandekieft.net` (registered 2026-10-27; both zones share the nameserver pair
   linda/archer.ns.cloudflare.com). Accepted: passive-DNS sites index zones by nameserver
   pair, so the two names can be linked that way. Nothing on the site itself links them.
   WHOIS redaction is on by default.
5. Keep the GitHub repo private: its README names the share, boris and the LAN hostname, and
   the generated HTML contains strings unique enough to find the repo with code search.

## Route A: Cloudflare Tunnel (recommended)

No port is opened on the UDM; `cloudflared` on boris dials out to Cloudflare and only the
hostnames you list are published. The home IP never appears in DNS.

### 1. Create the tunnel

Cloudflare dashboard (the account that holds bortle9.space) → Zero Trust → Networks → Tunnels → Create a
tunnel → Cloudflared → name it `boris`. On the next screen pick Docker and copy the **token**
from the command shown (the long string after `--token`). Do not run that command; the
container is added to the compose file instead.

Under Public Hostname add:

| Subdomain | Domain | Service |
|---|---|---|
| (empty) | bortle9.space | `http://astrogallery:80` |
| www | bortle9.space | `http://astrogallery:80` |

`astrogallery` resolves because the connector joins `proxynet`, the same bridge NPM uses. Do
**not** point the service at NPM (`https://192.168.1.10`) with a Host override of
`astro.home.vandekieft.net`: NPM would answer every public request with
`X-Served-By: astro.home.vandekieft.net`.

### 2. Start the connector on boris

The connector is already described in `deploy/docker-compose.tunnel.yml` (image
`cloudflare/cloudflared`, container `astrogallery-tunnel`, on `proxynet`, read-only, no new
privileges). `deploy.sh` starts it whenever a `tunnel.env` exists next to the compose file on
boris, and leaves the tunnel alone when there is none. So, once, on boris:

```
ssh root@192.168.1.3
printf 'TUNNEL_TOKEN=eyJ...
' > /mnt/user/appdata/astrogallery/tunnel.env
chmod 600 /mnt/user/appdata/astrogallery/tunnel.env
```

then `make deploy` from the Mac (it prints "tunnel.env found: starting the Cloudflare Tunnel
connector too"). The token is never in the repo and `.env` is still rewritten by every
deploy without touching it. The tunnel shows **Healthy** in the dashboard within a minute;
one connector is enough for a static site. `ssh root@192.168.1.3 docker logs
astrogallery-tunnel` shows the connector registering.

**This is how it was actually done (2026-10-27):** the account already had a tunnel
`boris` (one `cloudflared` replica on boris, a container on `proxynet`, publishing
`mcp-tasks.vandekieft.net`). `bortle9.space` and `www.bortle9.space` were added to it as
published applications with service `http://astrogallery:80`; the DNS CNAMEs were created
by the dashboard. No second connector and no `tunnel.env`, so `deploy.sh` leaves the
tunnel alone; `deploy/docker-compose.tunnel.yml` stays as the fallback if that tunnel ever
goes away.

**Already running a tunnel for something else?** Reuse it instead of
making a second one; a tunnel can publish any number of hostnames, and `bortle9.space` is in
the same account, so it is offered in the hostname dropdown. In that tunnel add the two
public hostnames from step 1 and skip the connector here. The service URL depends on where
that connector runs: `http://astrogallery:80` if it is a container on `proxynet` on boris,
`http://192.168.1.3:8088` from anywhere else on the LAN. Both reach the same nginx, and nginx
takes the visitor's address from the `X-Forwarded-For` that cloudflared sets (the
connector's own LAN address is in the trusted range), so `status.html` stays LAN-only
either way.

### 3. Cloudflare settings for the zone (done 2026-10-27; here is what and why)

- SSL/TLS → Edge Certificates: **Always Use HTTPS** on (it is *off* on a new zone, so plain
  `http://` answered 200 until it was switched), **Minimum TLS 1.2** (default was 1.0), TLS
  1.3 on, **HSTS** on with max-age 6 months, includeSubDomains, no-sniff (the origin sends
  HSTS for a year too; harmless twice).
- Security → Security rules: a custom rule **"Block the builder status page"**,
  `(starts_with(http.request.uri.path, "/status"))` → Block. The origin gates `status.html`
  by address as well; this one does not depend on the tunnel's `X-Forwarded-For`. And a
  rate-limiting rule **"Throttle full-resolution downloads"**,
  `(starts_with(http.request.uri.path, "/full/"))`, more than 10 requests per 10 seconds
  per IP → Block for 10 seconds (the free plan allows one rule, 10-second windows only).
- Security → Settings: **Email Address Obfuscation off** (it is on by default and injects
  JavaScript), **Hotlink Protection on** (other sites cannot embed the images; direct links
  and search engines still work), Browser Integrity Check on (default). **Bot Fight Mode
  stays off**: it injects an inline `<script>` into every HTML page
  (`/cdn-cgi/challenge-platform/.../jsd/main.js`), which the site's
  `Content-Security-Policy: default-src 'none'` blocks, so it would only produce console
  errors and a crippled detection. The static site, the rate limit and the origin throttle
  are the bot defence. (Five minutes after switching it off the pages still carried the
  snippet; if `curl -A Mozilla https://bortle9.space/ | grep -c '<script'` is not 0 a day
  later, something else injects it. Once the hardened config is deployed the CSP blocks it
  either way, so the only symptom is a console message.)
- Speed → Settings: **Rocket Loader off** (default; it also injects JavaScript). Speed
  Brain, Early Hints and Cloudflare Fonts are on by default and are header-only or a
  no-op for a site without Google Fonts; leave them.
- Caching: **no cache rule needed**. Cloudflare caches `.jpg` by default and honours the
  origin's `Cache-Control: public, max-age=86400, must-revalidate`; checked from outside,
  the second fetch of a derivative and of a `full/` copy is `cf-cache-status: HIT`. HTML
  stays `DYNAMIC` (origin says `no-cache`), so a new image shows within minutes.
- `robots.txt`: Cloudflare's managed robots.txt (AI Crawl Control → Content Signals) is on
  for the zone and answers `/robots.txt` with its content-signal preamble; once the origin
  serves its own (the hardened config), Cloudflare prepends its block to the origin's.

### 4. Check from outside (phone on mobile data, or `curl` from anywhere)

```
curl -sI https://bortle9.space/ | grep -i "content-security\|x-served-by\|server:"
   # CSP present; no X-Served-By; Server: cloudflare
curl -s -o /dev/null -w "%{http_code}\n" https://bortle9.space/status.html      # 403
curl -s -o /dev/null -w "%{http_code}\n" https://bortle9.space/status           # 403
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Forwarded-For: 192.168.1.5" https://bortle9.space/status.html   # 403
curl -s https://bortle9.space/nope | grep -o "nginx[^<]*"                       # "nginx", no version
curl -s https://bortle9.space/robots.txt      # Cloudflare's content-signal block, then the origin's Disallow lines
dig +short bortle9.space     # Cloudflare addresses (104.x / 172.67.x), never the home IP
```

On the LAN `https://astro.home.vandekieft.net/status.html` still answers 200 through NPM.

### Rollback

Delete the public hostname in the tunnel (or `docker stop astrogallery-tunnel`). The site is off
the internet immediately; nothing else changes. To drop the connector for good, delete
`tunnel.env` on boris, `docker rm -f astrogallery-tunnel`, and delete the tunnel in the dashboard.

## Route B: port-forward to Nginx Proxy Manager (not recommended)

Works, but every host NPM knows becomes reachable by name from the internet, and the home IP
goes public. Only if the tunnel is unwanted:

1. UDM → Port Forwarding: 443 → 192.168.1.10:443 only (never 80 unless you want the
   HTTP→HTTPS redirect handled at home, and never 81, NPM's admin UI).
2. NPM: **Settings → Default Site → "No Response (444)"** so a request by IP gets nothing.
   An **Access List** (LAN only: 192.168.0.0/16) on *every* other proxy host (immich, unifi,
   tasks, ...). Add a proxy host `bortle9.space`, `www.bortle9.space` → `http://astrogallery:80`,
   with its own Let's Encrypt certificate requested by DNS challenge (Cloudflare API token,
   so no port 80 is needed). **Do not add bortle9.space as another name on the
   `*.home.vandekieft.net` certificate**: certificate transparency logs would tie the two
   names together permanently.
3. Cloudflare DNS: `A bortle9.space → <home IP>` proxied (orange cloud), and the same for `www`.
   SSL/TLS mode **Full (strict)**. Then UDM firewall: allow 443 only from
   [Cloudflare's published IP ranges](https://www.cloudflare.com/ips/), so nobody can reach
   NPM by IP and learn which certificate it serves. The home IP still changes with the ISP;
   a dynamic-DNS updater (NPM has none; the UDM can update Cloudflare via a DDNS entry with
   the Cloudflare provider) is needed.
4. `X-Served-By` will say `bortle9.space` (NPM echoes the request's host), which is fine.
5. Everything in Route A step 3 and the checks in step 4 apply unchanged.

## Traffic stats

Two sources, one InfluxDB database, one Grafana dashboard. Nothing runs in the visitor's
browser (the CSP would not let it anyway) and no visitor address is stored.

```
nginx (boris) --JSON log, one file a month--> Telegraf (boris) --> InfluxDB "astrogallery" (escher) --> Grafana (escher:3000)
Cloudflare GraphQL --cloudflare-exporter (boris)--> Telegraf ---^
```

- **Origin:** `deploy/nginx.conf` writes one JSON line per request to
  `/mnt/user/appdata/astrogallery/logs/access-<year>-<month>.json` (a few MB a month; delete
  old months by hand if ever needed). Per request: time, host, method, uri, status, `kind`
  (index / page / img / full / css / robots / status / other), `page` (the page or `full/` file
  when it was served with 200, else `-`), `country` (the two-letter code Cloudflare attaches;
  `-` from the LAN), `lan` (1 when the request came through Nginx Proxy Manager), bytes,
  request time, referer, user agent, Cloudflare ray id. **No IP address.**
  A full-res download counts when the whole file went out with status 200; a download manager
  fetching it in ranges (206) shows in the status chart but not in the download table.
- **Edge:** the `cloudflare_exporter` container asks Cloudflare's GraphQL API, once a minute,
  for one minute of zone totals (requests, cached requests, bytes, threats, by country and by
  status), five minutes behind real time. Those include the image requests Cloudflare answered
  from its cache, which never reach boris. Telegraf scrapes it at the same cadence, so the
  points in InfluxDB are per-minute counts and sum to the totals.
- **Telegraf** (`deploy/telegraf.conf`, container `astrogallery-stats`) tails the log and
  scrapes the exporter, and writes both to the `astrogallery` database on escher's
  InfluxDB 1.8 (`http://192.168.1.4:8086`, no authentication on the LAN). Measurement `access`
  for the origin (tags host, method, status, kind, page, country, lan, source=origin), and
  one measurement per exporter metric for the edge (`cloudflare_zone_requests_total`, ...,
  field `counter`, tags zone, country, status, source=edge).

### Setup (once)

1. Cloudflare dashboard → My Profile → API Tokens → Create Token → Custom: permission
   **Zone / Analytics / Read**, zone resource **Include / Specific zone / bortle9.space**.
   Nothing else. Copy the token.
2. On boris, next to the compose file:

   ```
   printf 'INFLUX_URL=http://192.168.1.4:8086\nCF_API_TOKEN=<the token>\n' > /mnt/user/appdata/astrogallery/stats.env
   chmod 600 /mnt/user/appdata/astrogallery/stats.env
   ```

3. `make deploy` from the Mac. It prints "stats.env found: starting the traffic stats", creates
   `./logs` (owned by nginx's uid 101) and starts `astrogallery-stats` and
   `astrogallery-cf-exporter`. The database is created by Telegraf if it does not exist.
4. Grafana (`http://escher.home.vandekieft.net:3000`): Connections → Data sources → Add →
   InfluxDB: query language InfluxQL, URL `http://192.168.1.4:8086`, database `astrogallery`,
   HTTP method GET, no auth. Save & test. Then Dashboards → New → Import → upload
   `deploy/grafana/astrogallery.json` and pick that data source.
5. Checks: `ssh root@192.168.1.3 docker logs astrogallery-stats` shows no `E!` lines;
   `ls -la /mnt/user/appdata/astrogallery/logs/` shows this month's file growing;
   `curl -s 'http://192.168.1.4:8086/query?db=astrogallery&q=SELECT+count(bytes)+FROM+access'`
   returns a number; the Cloudflare numbers in Grafana should match Analytics → Traffic in the
   Cloudflare dashboard for the same day (verify once; if they do not, the exporter's minute
   window and Telegraf's scrape have drifted apart).

### Turning it off

Delete `stats.env` on boris, `docker rm -f astrogallery-stats astrogallery-cf-exporter`, and
`make deploy`. The log keeps being written (it is cheap and local); drop the `./logs` line from
`deploy/docker-compose.yml` and the `access_log` line from `deploy/nginx.conf` to stop that too.

## What the site still reveals (accepted)

Northern-hemisphere targets, a Bortle 8.6 sky (it is in the site name), the nights the scopes were out and
the equipment and software in the file names. No name, no location, no time zone (the public
footer carries the date only; the build time with its zone is on the LAN-only status page).
