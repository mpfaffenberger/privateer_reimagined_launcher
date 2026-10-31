// Download counts (#130): what the site Worker does once a day and on /stats/. No Cloudflare
// APIs in here, so `node --test worker/stats.test.mjs` checks it all. docs/DOWNLOAD_COUNTS.md
// explains the method; in short: every install or update of a release downloads that release's
// game executable once, publishing lists it in stats/markers.json, and R2 already counts how
// often each file was fetched. We read those totals per day. Nothing about who.

export const MARKERS_URL = "https://cdn.privateer-reimagined.com/stats/markers.json";
export const GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql";
export const DAYS_BACK = 3;     // each run re-reads the last three days: a missed run or late data heals
const MAX_MARKERS = 100;        // the newest releases; superseded ones stop being downloaded
const BY_DAY_SHOWN = 30;
const DAY_MS = 86_400_000;

export const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);

/** The last `n` complete UTC days before `nowMs`, newest first. */
export function recentDays(nowMs, n = DAYS_BACK) {
  const today = Date.parse(isoDay(nowMs));
  return Array.from({ length: n }, (_, i) => isoDay(today - (i + 1) * DAY_MS));
}

/** stats/markers.json -> [{ key, channel, version, published }], newest last. */
export function recentMarkers(doc) {
  return Object.entries(doc?.markers ?? {}).slice(-MAX_MARKERS)
    .map(([key, m]) => ({ key, channel: m.channel, version: m.version, published: m.published }));
}

// R2's per-file operation totals (the dataset behind the dashboard's R2 metrics). It has no IP
// or user-agent field to ask for, and we only ask for one number per file per day.
const QUERY = `query DownloadCounts($account: string!, $bucket: string!, $start: Time!, $end: Time!, $keys: [string!]!) {
  viewer { accounts(filter: { accountTag: $account }) {
    r2OperationsAdaptiveGroups(limit: 1000, filter: {
      bucketName: $bucket, actionType: "GetObject", actionStatus: "success",
      datetime_geq: $start, datetime_lt: $end, objectName_in: $keys
    }) { sum { requests } dimensions { objectName } }
  } }
}`;

export function countsQuery(env, day, keys) {
  return {
    query: QUERY,
    variables: { account: env.CF_ACCOUNT_ID, bucket: env.R2_BUCKET, keys,
                 start: `${day}T00:00:00Z`, end: `${isoDay(Date.parse(day) + DAY_MS)}T00:00:00Z` },
  };
}

/** A GraphQL answer -> { channel: { version: downloads } }. Files that aren't markers are ignored. */
export function tally(markers, answer) {
  if (answer.errors?.length) throw new Error(`GraphQL: ${answer.errors.map((e) => e.message).join("; ")}`);
  const groups = answer.data?.viewer?.accounts?.[0]?.r2OperationsAdaptiveGroups ?? [];
  const byKey = new Map(markers.map((m) => [m.key, m]));
  const counts = {};
  for (const g of groups) {
    const m = byKey.get(g.dimensions.objectName);
    if (!m) continue;
    const channel = (counts[m.channel] ??= {});
    channel[m.version] = (channel[m.version] ?? 0) + g.sum.requests;
  }
  return counts;
}

/** One daily run: { markers, days: [[day, counts]] } for the last DAYS_BACK days. */
export async function collect(env, nowMs, fetchFn = fetch) {
  const res = await fetchFn(env.MARKERS_URL ?? MARKERS_URL,
                            { headers: { "user-agent": "privateer-reimagined-stats" } });
  if (res.status === 404) return { markers: [], days: [] };   // no release published since #130
  if (!res.ok) throw new Error(`stats/markers.json: HTTP ${res.status}`);
  const markers = recentMarkers(await res.json());
  if (!markers.length) return { markers, days: [] };          // an empty filter would mean every file
  const keys = markers.map((m) => m.key);
  const days = [];
  for (const day of recentDays(nowMs)) {
    const answer = await fetchFn(env.GRAPHQL_URL ?? GRAPHQL_URL, {
      method: "POST",
      headers: { authorization: `Bearer ${env.CF_API_TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify(countsQuery(env, day, keys)),
    });
    if (!answer.ok) throw new Error(`GraphQL: HTTP ${answer.status}`);
    days.push([day, tally(markers, await answer.json())]);
  }
  return { markers, days };
}

// ---- /stats/ ------------------------------------------------------------------------------

const PLATFORMS = { macos: "macOS", linux: "Linux", windows: "Windows" };
export const platform = (channel) => PLATFORMS[channel.split("-").pop()] ?? "Windows";

const escape = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const row = (cells) => `<tr>${cells.map(([v, cls]) => `<td${cls ? ` class="${cls}"` : ""}>${escape(v)}</td>`).join("")}</tr>`;

export const PAGE_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "public, max-age=0, must-revalidate, no-transform",
  "content-security-policy": "default-src 'self'; style-src 'self'; script-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

/** The /stats/ page: totals per release, then the last 30 days. Plain HTML, no scripts. */
export function renderPage({ markers = [], days = [] }) {
  const published = new Map(markers.map((m) => [`${m.channel}\n${m.version}`, m.published]));
  const totals = new Map();
  const daily = [];
  for (const [day, counts] of [...days].sort(([a], [b]) => b.localeCompare(a))) {
    for (const [channel, versions] of Object.entries(counts)) {
      for (const [version, n] of Object.entries(versions)) {
        const id = `${channel}\n${version}`;
        totals.set(id, (totals.get(id) ?? 0) + n);
        if (n > 0) daily.push({ day, channel, version, n });
      }
    }
  }
  const releases = [...totals].map(([id, n]) => {
    const [channel, version] = id.split("\n");
    return { channel, version, n, published: published.get(id) ?? "" };
  }).sort((a, b) => b.published.localeCompare(a.published) || b.version.localeCompare(a.version, "en", { numeric: true }));
  const newest = daily[0]?.day ?? "1970-01-01";
  const cutoff = isoDay(Date.parse(newest) - (BY_DAY_SHOWN - 1) * DAY_MS);
  const body = releases.length === 0 ? "<p>No counts yet: they start with the first release published after this page went up.</p>" : `
    <table>
      <thead><tr><th>Release</th><th>Platform</th><th>Published</th><th class="n">Downloads</th></tr></thead>
      <tbody>${releases.map((r) => row([[r.version], [platform(r.channel)], [r.published], [r.n, "n"]])).join("")}</tbody>
    </table>
    <h2>The last ${BY_DAY_SHOWN} days</h2>
    <table>
      <thead><tr><th>Day</th><th>Release</th><th>Platform</th><th class="n">Downloads</th></tr></thead>
      <tbody>${daily.filter((d) => d.day >= cutoff).map((d) => row([[d.day], [d.version], [platform(d.channel)], [d.n, "n"]])).join("")}</tbody>
    </table>`;
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloads | Privateer Reimagined</title>
  <meta name="robots" content="noindex">
  <meta name="theme-color" content="#0f141b">
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="page stats">
    <p class="eyebrow"><a href="/">Privateer Reimagined</a></p>
    <h1>Downloads</h1>
    <p class="lede">How many times each update was downloaded.</p>
    <p>Counted once a day from how often each release's game file was fetched from our download
      server. Nothing about who: see <a href="/privacy/">Privacy</a>.</p>
    ${body}
    <p class="updated">Days are UTC. A download that's retried, or a second computer, counts again.</p>
  </main>
</body>
</html>
`;
}
