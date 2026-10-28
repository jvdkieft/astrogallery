#!/usr/bin/env python3
"""Cloudflare edge stats for bortle9.space into InfluxDB (container astrogallery-cfstats on boris).

Every INTERVAL seconds, asks Cloudflare's GraphQL Analytics API for the zone's hourly totals of
the last HOURS hours (httpRequests1hGroups: the free plan has no per-minute data) and writes one
point per hour to InfluxDB 1.x. Writing an hour again replaces it (same series, same timestamp),
so the current hour fills in as it completes and nothing is counted twice.

Measurements (database $INFLUX_DB, default astrogallery):
  cf_hourly   fields requests, cached_requests, bytes, cached_bytes, threats, page_views, uniques
  cf_country  tag country (two-letter code); fields requests, bytes, threats
  cf_status   tag status (edge HTTP status); field requests
  cf_security one point per request Cloudflare blocked or challenged (firewallEventsAdaptive, last
              EVENT_HOURS hours): tags action, source (hot = hotlink, firewallCustom = custom rule,
              ratelimit, bic = browser integrity, ...), rule, country, asn; fields path, ua, ray.
              The point's time carries a few nanoseconds derived from the ray id, so two events in the
              same second stay apart and re-reading an event replaces it instead of adding it again.

Environment: CF_API_TOKEN (Zone / Analytics / Read), CF_ZONE_ID, INFLUX_URL, and optionally
INFLUX_DB, HOURS (26), EVENT_HOURS (2), INTERVAL (900). Standard library only.

  python3 cfstats.py            run forever
  python3 cfstats.py --print    one query, print the line protocol, write nothing
"""
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4/graphql"
QUERY = """query ($zone: String!, $since: Time!, $until: Time!) {
  viewer { zones(filter: {zoneTag: $zone}) {
    httpRequests1hGroups(limit: 100, filter: {datetime_geq: $since, datetime_lt: $until},
                         orderBy: [datetime_ASC]) {
      dimensions { datetime }
      uniq { uniques }
      sum {
        requests cachedRequests bytes cachedBytes threats pageViews
        countryMap { clientCountryName requests bytes threats }
        responseStatusMap { edgeResponseStatus requests }
      }
    }
  } }
}"""


def log(msg):
    print(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} cfstats: {msg}", file=sys.stderr, flush=True)


def fetch(token, zone, hours):
    now = dt.datetime.now(dt.timezone.utc)
    until = (now + dt.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    since = until - dt.timedelta(hours=hours + 1)
    body = json.dumps({"query": QUERY, "variables": {
        "zone": zone, "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "until": until.strftime("%Y-%m-%dT%H:%M:%SZ")}}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", str(e)) for e in data["errors"]))
    zones = (data.get("data") or {}).get("viewer", {}).get("zones") or []
    if not zones:
        raise RuntimeError(f"zone {zone} not visible to this token")
    return zones[0].get("httpRequests1hGroups") or []


EVENTS = """query ($zone: String!, $since: Time!, $until: Time!) {
  viewer { zones(filter: {zoneTag: $zone}) {
    firewallEventsAdaptive(limit: 5000, filter: {datetime_geq: $since, datetime_lt: $until},
                           orderBy: [datetime_DESC]) {
      datetime action source ruleId clientRequestPath clientCountryName clientAsn userAgent rayName
    }
  } }
}"""


def graphql(token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", str(e)) for e in data["errors"]))
    zones = (data.get("data") or {}).get("viewer", {}).get("zones") or []
    if not zones:
        raise RuntimeError("zone not visible to this token")
    return zones[0]


def fetch_events(token, zone, hours):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    since = now - dt.timedelta(hours=hours)
    z = graphql(token, EVENTS, {"zone": zone, "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "until": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return z.get("firewallEventsAdaptive") or []


def esc_field(v):
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def event_lines(events):
    out = []
    for e in events:
        t = dt.datetime.strptime(e["datetime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        ray = str(e.get("rayName") or "")
        ns = int(t.timestamp()) * 1_000_000_000 + (int(hashlib.sha1(ray.encode()).hexdigest()[:8], 16) % 1_000_000_000)
        tags = ",".join(f"{k}={esc_tag(v or '-')}" for k, v in (
            ("action", e.get("action")), ("source", e.get("source")), ("rule", e.get("ruleId")),
            ("country", e.get("clientCountryName")), ("asn", e.get("clientAsn"))))
        fields = ",".join(f"{k}={esc_field(v or '')}" for k, v in (
            ("path", e.get("clientRequestPath")), ("ua", e.get("userAgent")), ("ray", ray)))
        out.append(f"cf_security,{tags} {fields} {ns}")
    return out


def esc_tag(v):
    return str(v).replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def lines(groups):
    out = []
    for g in groups:
        ts = int(dt.datetime.strptime(g["dimensions"]["datetime"], "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=dt.timezone.utc).timestamp())
        s, u = g.get("sum") or {}, g.get("uniq") or {}
        f = dict(requests=s.get("requests", 0), cached_requests=s.get("cachedRequests", 0),
                 bytes=s.get("bytes", 0), cached_bytes=s.get("cachedBytes", 0),
                 threats=s.get("threats", 0), page_views=s.get("pageViews", 0),
                 uniques=u.get("uniques", 0))
        out.append("cf_hourly " + ",".join(f"{k}={int(v or 0)}i" for k, v in f.items()) + f" {ts}")
        for c in s.get("countryMap") or []:
            code = c.get("clientCountryName") or "XX"
            out.append(f"cf_country,country={esc_tag(code)} requests={int(c.get('requests') or 0)}i,"
                       f"bytes={int(c.get('bytes') or 0)}i,threats={int(c.get('threats') or 0)}i {ts}")
        for st in s.get("responseStatusMap") or []:
            out.append(f"cf_status,status={esc_tag(st.get('edgeResponseStatus'))} "
                       f"requests={int(st.get('requests') or 0)}i {ts}")
    return out


def write(influx, db, body):
    precision = "ns" if body.startswith("cf_security") else "s"
    url = influx.rstrip("/") + "/write?" + urllib.parse.urlencode({"db": db, "precision": precision})
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 204):
            raise RuntimeError(f"InfluxDB answered {r.status}")


def once(cfg, dry=False):
    groups = fetch(cfg["token"], cfg["zone"], cfg["hours"])
    body = "\n".join(lines(groups))
    try:
        events = fetch_events(cfg["token"], cfg["zone"], cfg["event_hours"])
        ev = "\n".join(event_lines(events))
    except (urllib.error.URLError, OSError, RuntimeError, ValueError, KeyError) as e:
        log(f"security events not read: {e}")
        events, ev = [], ""
    if dry:
        print(body)
        print(ev)
        return len(groups), len(events)
    if body:
        write(cfg["influx"], cfg["db"], body + "\n")
    if ev:
        write(cfg["influx"], cfg["db"], ev + "\n")
    return len(groups), len(events)


def main():
    env = os.environ.get
    cfg = dict(token=env("CF_API_TOKEN"), zone=env("CF_ZONE_ID"), influx=env("INFLUX_URL"),
               db=env("INFLUX_DB") or "astrogallery", hours=int(env("HOURS") or 26), event_hours=int(env("EVENT_HOURS") or 2),
               interval=int(env("INTERVAL") or 900))
    missing = [k for k in ("token", "zone") if not cfg[k]] + ([] if cfg["influx"] or "--print" in sys.argv else ["influx"])
    if missing:
        sys.exit(f"cfstats: missing {', '.join(missing)} (CF_API_TOKEN, CF_ZONE_ID, INFLUX_URL)")
    if "--print" in sys.argv:
        n, k = once(cfg, dry=True)
        log(f"{n} hours and {k} security events from Cloudflare (nothing written)")
        return
    while True:
        try:
            n, k = once(cfg)
            log(f"wrote {n} hours and {k} security events")
        except (urllib.error.URLError, OSError, RuntimeError, ValueError, KeyError) as e:
            log(f"failed, retrying next round: {e}")
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
