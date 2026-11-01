// The site Worker. Static assets serve the site exactly as before; this script runs only for
// /stats/ (assets.run_worker_first in wrangler.jsonc) and for the daily cron that collects
// download counts (#130, docs/DOWNLOAD_COUNTS.md). The logic lives in stats.js.
import { DurableObject } from "cloudflare:workers";
import { collect, renderPage, PAGE_HEADERS } from "./stats.js";

/** The counts: `day:<YYYY-MM-DD>` -> { channel: { version: downloads } }, plus the markers. */
export class DownloadCounts extends DurableObject {
  async save({ markers, days }) {
    await this.ctx.storage.put({ markers, ...Object.fromEntries(days.map(([day, c]) => [`day:${day}`, c])) });
  }

  async load() {
    const days = [...(await this.ctx.storage.list({ prefix: "day:" }))].map(([k, v]) => [k.slice(4), v]);
    return { markers: (await this.ctx.storage.get("markers")) ?? [], days };
  }
}

const counts = (env) => env.COUNTS.get(env.COUNTS.idFromName("downloads"));

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/stats") return Response.redirect(new URL("/stats/", request.url).href, 301);
    if (pathname !== "/stats/") return env.ASSETS.fetch(request);
    return new Response(renderPage(await counts(env).load()), { headers: PAGE_HEADERS });
  },

  async scheduled(controller, env) {
    if (!env.CF_API_TOKEN) {
      console.log("download counts: no CF_API_TOKEN secret yet (docs/DOWNLOAD_COUNTS.md)");
      return;
    }
    const result = await collect(env, controller.scheduledTime);
    if (result.days.length) await counts(env).save(result);
  },
};
