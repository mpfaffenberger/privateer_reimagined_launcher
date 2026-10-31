// Download counts (#130): the Worker's logic without a runtime. Run: node --test worker/stats.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { collect, countsQuery, platform, recentDays, recentMarkers, renderPage, tally } from "./stats.js";

const ENV = { CF_API_TOKEN: "token", CF_ACCOUNT_ID: "acct", R2_BUCKET: "privateer-reimagined" };
const WIN = "blobs/aa/aa11.zst";
const MAC = "blobs/bb/bb22.zst";
const MARKERS = { format: 1, markers: {
  [WIN]: { channel: "stable", version: "0.1.26", published: "2026-10-31" },
  [MAC]: { channel: "stable-macos", version: "0.1.26-macos", published: "2026-10-31" },
} };
const NOW = Date.parse("2026-11-02T02:30:00Z");

const answer = (groups) => ({ data: { viewer: { accounts: [{ r2OperationsAdaptiveGroups:
  groups.map(([objectName, requests]) => ({ sum: { requests }, dimensions: { objectName } })) }] } } });

/** A fetch that serves the markers and answers GraphQL from `perDay` (day -> groups). */
function fakeFetch(perDay, markers = MARKERS) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url, init });
    if (url.endsWith("markers.json")) {
      return markers ? new Response(JSON.stringify(markers)) : new Response("", { status: 404 });
    }
    const { variables } = JSON.parse(init.body);
    return new Response(JSON.stringify(answer(perDay[variables.start.slice(0, 10)] ?? [])));
  };
  return { fn, calls };
}

test("a run reads the three complete UTC days before it", () => {
  assert.deepEqual(recentDays(NOW), ["2026-11-01", "2026-10-31", "2026-10-30"]);
});

test("the query asks for successful downloads of the marker files on one UTC day", () => {
  const { query, variables } = countsQuery(ENV, "2026-10-31", [WIN]);
  assert.deepEqual(variables, { account: "acct", bucket: "privateer-reimagined", keys: [WIN],
                                start: "2026-10-31T00:00:00Z", end: "2026-11-01T00:00:00Z" });
  for (const part of ['actionType: "GetObject"', 'actionStatus: "success"', "objectName_in: $keys",
                      "datetime_lt: $end"])
    assert.ok(query.includes(part), part);
  assert.doesNotMatch(query, /clientIP|userAgent|ip\b/i, "nothing about who is asked for");
});

test("each platform's downloads land on its own release; other files are ignored", async () => {
  const { fn, calls } = fakeFetch({ "2026-11-01": [[WIN, 40], [MAC, 7], ["blobs/cc/other", 999]],
                                    "2026-10-31": [[WIN, 12]] });
  const { markers, days } = await collect(ENV, NOW, fn);
  assert.equal(markers.length, 2);
  assert.deepEqual(days, [
    ["2026-11-01", { stable: { "0.1.26": 40 }, "stable-macos": { "0.1.26-macos": 7 } }],
    ["2026-10-31", { stable: { "0.1.26": 12 } }],
    ["2026-10-30", {}],
  ]);
  const graphql = calls.filter((c) => c.init.method === "POST");
  assert.equal(graphql.length, 3);
  assert.equal(graphql[0].init.headers.authorization, "Bearer token");
  assert.deepEqual(JSON.parse(graphql[0].init.body).variables.keys, [WIN, MAC]);
});

test("before any release has a marker, nothing is queried", async () => {
  for (const markers of [null, { format: 1, markers: {} }]) {
    const { fn, calls } = fakeFetch({}, markers);
    assert.deepEqual((await collect(ENV, NOW, fn)).days, []);
    assert.equal(calls.length, 1, "an empty objectName_in would mean every file");
  }
});

test("a GraphQL error fails the run instead of storing zeros", () => {
  assert.throws(() => tally([], { errors: [{ message: "not authorized" }] }), /not authorized/);
});

test("only the newest 100 releases are queried", () => {
  const many = { markers: Object.fromEntries(Array.from({ length: 130 }, (_, i) =>
    [`blobs/${i}`, { channel: "stable", version: `0.${i}`, published: "2026-10-01" }])) };
  const got = recentMarkers(many);
  assert.equal(got.length, 100);
  assert.equal(got.at(-1).version, "0.129");
});

test("platforms are named from the channel", () => {
  assert.deepEqual(["stable", "stable-macos", "beta-linux"].map(platform), ["Windows", "macOS", "Linux"]);
});

test("the page totals each release over the days, newest release first", () => {
  const html = renderPage({ markers: recentMarkers(MARKERS), days: [
    ["2026-10-31", { stable: { "0.1.26": 12 } }],
    ["2026-11-01", { stable: { "0.1.26": 40, "0.1.25": 3 }, "stable-macos": { "0.1.26-macos": 7 } }],
  ] });
  const rows = [...html.matchAll(/<tr><td>([^<]*)<\/td><td>([^<]*)<\/td><td>([^<]*)<\/td><td class="n">(\d+)<\/td><\/tr>/g)]
    .map((m) => m.slice(1));
  assert.deepEqual(rows.slice(0, 3), [
    ["0.1.26-macos", "macOS", "2026-10-31", "7"],
    ["0.1.26", "Windows", "2026-10-31", "52"],
    ["0.1.25", "Windows", "", "3"],
  ]);
  assert.deepEqual(rows.slice(3, 4), [["2026-11-01", "0.1.26", "Windows", "40"]], "by day, newest first");
  assert.doesNotMatch(html, /<script/i);
});

test("the page escapes what it shows and says when there's nothing yet", () => {
  assert.match(renderPage({}), /No counts yet/);
  const html = renderPage({ days: [["2026-11-01", { stable: { "<b>1</b>": 1 } }]] });
  assert.ok(html.includes("&#60;b&#62;1&#60;/b&#62;") && !html.includes("<b>1</b>"));
});
