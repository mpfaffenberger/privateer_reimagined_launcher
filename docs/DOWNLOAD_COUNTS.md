# Download counts (#130)

How many times each update is downloaded, per release and platform, per day. Nothing about who:
the launcher sends nothing new, nothing identifies a player, and nothing finer than a day is kept.

## What is counted

Every install or update of a release downloads that release's **game executable** exactly once
(`new_privateer.exe` on Windows, `new_privateer` on macOS). It's rebuilt every release, so it's new
in each one, and each platform has its own. Measured on 0.1.21 to 0.1.26: the executable was the
only file one platform had and the other didn't, in every release.

So a release's downloads = how many times its executable's blob was fetched. R2 already counts how
often each file in the bucket is read (the dataset behind the dashboard's R2 metrics, which has no
IP or user-agent field). Once a day the site Worker asks it for one number per executable.

The other requests the launcher makes don't measure downloads: it fetches the channel pointer and
the manifest every time it starts, whether or not it then downloads anything.

## How it works

1. **Publishing** (`prl publish`, `prl sign-publish`): just before moving the channel pointer, the
   publisher adds the release's executable to `stats/markers.json` on the CDN
   (`publisher/src/prl/stats.py`):
   `{"blobs/ab/ab12...zst": {"channel": "stable", "version": "0.1.27", "published": "2026-11-02"}}`.
2. **Daily, 02:30 UTC** (cron on the site Worker, `worker/`): reads `stats/markers.json`, asks
   Cloudflare's GraphQL API for the successful `GetObject` count of those files on each of the last
   three UTC days, and stores the result in a Durable Object (Cloudflare's storage for the Worker):
   `day:2026-11-01 -> {"stable": {"0.1.27": 40}, "stable-macos": {"0.1.27-macos": 7}}`. Re-reading
   three days means a missed run or late analytics fix themselves.
3. **Reading:** <https://privateer-reimagined.com/stats/> shows the totals per release, then the last
   30 days. Plain HTML, no scripts, `noindex`.

**Stored:** the counts above and the markers file. **Not stored, never asked for:** IPs, user
agents, times finer than a day, anything per request.

Counts start with the first release published after this change: older releases have no marker.

## Setup (Mike, once, about five minutes)

1. Merge the PR. Workers Builds deploys the Worker with the cron and the Durable Object; until the
   token exists, the daily run logs "no CF_API_TOKEN secret yet" and does nothing else.
2. **Create a read-only token:** dashboard > My Profile > API Tokens > Create Token > Custom token.
   - Permissions: **Account > Account Analytics > Read**. Nothing else.
   - Account resources: Include > your account.
   - TTL: leave it empty, so it never expires.
3. **Give it to the Worker:** Workers & Pages > `privateer-reimagined-launcher` > Settings >
   Variables and Secrets > Add > type **Secret**, name `CF_API_TOKEN`, value: the token. Deploys
   from Workers Builds keep it.
4. Publish a release, wait for the next 02:30 UTC, and open `/stats/`. The Worker's Logs (Workers &
   Pages > the Worker > Logs) show any error from the daily run.

To keep `/stats/` private instead of public: Zero Trust > Access > Applications > Add > Self-hosted,
domain `privateer-reimagined.com`, path `stats`, policy: allow your email. No code change.

## Limits

- **Downloads, not people.** A download that fails and is retried counts again; one player on two
  computers counts twice; a "Verify files" that re-downloads the executable counts once more.
- **Cache:** the CDN doesn't edge-cache today (`cf-cache-status: DYNAMIC`), so R2 sees every
  request. If a cache rule is ever added for `cdn.privateer-reimagined.com`, R2 would only see cache
  misses and these counts would drop: exclude `blobs/*` from that rule.
- **Analytics window:** Cloudflare answers for the last 31 days at most; each run reads three days,
  so the Worker has to run at least every third day (it runs daily).
- **Cost:** free tier. One cron run a day, three GraphQL queries, a few Durable Object writes; the
  site itself is still served as static assets without invoking the Worker.

## Rollback

- **Stop reading:** delete the `CF_API_TOKEN` secret. The daily run then does nothing; the page keeps
  what was counted.
- **Remove it all:** in `wrangler.jsonc` drop `main`, `run_worker_first`, `triggers`, `vars` and
  the `durable_objects` binding, and add a migration
  `{ "tag": "v2", "deleted_classes": ["DownloadCounts"] }` (Cloudflare refuses to drop the class
  otherwise; this also deletes the stored counts). Then delete `worker/`, `stats.record` in
  `finalize`, and the sentence in `site/privacy/index.html`. `stats/markers.json` on the CDN is
  harmless to leave or delete.
