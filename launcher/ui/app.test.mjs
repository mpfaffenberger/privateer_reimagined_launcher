// Launcher UI tests (#88): app.js runs in a vm with its Tauri calls stubbed and a fake DOM, so the
// UI's own logic is checked without a window. Run: node --test launcher/ui/app.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SRC = readFileSync(new URL("./app.js", import.meta.url), "utf8");

// The parts of a DOM element app.js touches; click() runs its click listeners.
function element() {
  const listeners = {};
  return {
    hidden: false, disabled: false, textContent: "", innerHTML: "", title: "", className: "",
    style: {},
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
    click() { return Promise.all((listeners.click || []).map((fn) => fn())); },
    append() {},
  };
}

// A launcher with Privateer found and 0.1.8 installed and current, unless `answers` (command ->
// value, or a function of the args; a throw is a failed call) says otherwise.
const DEFAULTS = {
  get_status: { installed_version: "0.1.8", install_dir: "C:\\Games\\PR", cdn: "test",
                owned: { store: "gog", path: "C:\\GOG\\Privateer" } },
  release_hint: "pointer-1",
  check_update: { published: true, up_to_date: true, version: "0.1.8", installed_version: "0.1.8" },
  news_page: null,
  check_launcher_update: null,
};

const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

async function boot(answers = {}) {
  const all = { ...DEFAULTS, ...answers };
  const calls = [];
  const invoke = async (cmd, args) => {
    calls.push(cmd);
    return typeof all[cmd] === "function" ? all[cmd](args) : all[cmd];
  };
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement: () => element(),
    createTextNode: (text) => text,
  };
  const window = { __TAURI__: { core: { invoke }, event: { listen: async () => {} },
                                dialog: { open: async () => null }, opener: { openUrl: async () => {} } } };
  const ctx = vm.createContext({ window, document, setInterval: () => 0, Date, console });
  vm.runInContext(SRC, ctx);
  await settle();                       // start-up: status, ownership, the first check
  return { $: (id) => document.getElementById(id), calls };
}

test("idle with Privateer found, the status line offers Check for updates", async () => {
  const { $ } = await boot();
  assert.equal($("check-now").hidden, false);
  assert.equal($("check-now").textContent, "Check for updates");
});

test("a click checks everything at once, inside the ten minutes", async () => {
  const { $, calls } = await boot();
  calls.length = 0;                     // start-up just checked: no timed recheck is due
  await $("check-now").click();
  await settle();
  for (const cmd of ["release_hint", "check_update", "news_page", "check_launcher_update"])
    assert.ok(calls.includes(cmd), `${cmd} runs`);
  assert.ok(calls.indexOf("release_hint") < calls.indexOf("check_update"),
            "the pointer is probed before the signed check, as at start-up");
});

test("after a failed check it reads Try again", async () => {
  const { $ } = await boot({ check_update: () => { throw new Error("offline"); } });
  assert.equal($("check-now").hidden, false);
  assert.equal($("check-now").textContent, "Try again");
});

test("hidden while a check runs, back when it's done", async () => {
  let finish;
  const { $ } = await boot();
  const pending = boot({ check_update: () => new Promise((resolve) => { finish = resolve; }) });
  const busy = await pending;
  assert.equal(busy.$("check-now").hidden, true, "busy: nothing to click");
  finish(DEFAULTS.check_update);
  await settle();
  assert.equal(busy.$("check-now").hidden, false);
  assert.equal($("check-now").hidden, false);
});

test("hidden when Privateer isn't found", async () => {
  const { $ } = await boot({ get_status: { ...DEFAULTS.get_status, owned: null } });
  assert.equal($("check-now").hidden, true);
});
