// fools-trick web/action layer: browser tools wrapping the user's Camofox
// (anti-detection Firefox) server. webfetch stays the fast path for a plain
// URL read; these are for interactive/JS-heavy/blocked sites. The server is
// started lazily and failures return an actionable error rather than hanging.

import { tool } from "@opencode-ai/plugin"

const BASE = process.env.CAMOFOX_URL || "http://localhost:9377"
const SERVER_DIR = process.env.CAMOFOX_DIR || `${process.env.HOME}/Source/camofox-browser`
const UA = { userId: "fools-trick", sessionKey: "orchestrator" }

async function alive() {
  try {
    const r = await fetch(`${BASE}/tabs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(UA),
      signal: AbortSignal.timeout(2500),
    })
    return r.ok ? await r.json() : null
  } catch {
    return null
  }
}

async function ensureTab($) {
  let tab = await alive()
  if (tab) return tab
  try {
    await $`bash -c 'cd ${SERVER_DIR} && node server.js >/tmp/fools-trick/camofox.log 2>&1 &'`
  } catch {
    /* fall through to the error below */
  }
  for (let i = 0; i < 8; i++) {
    await new Promise((r) => setTimeout(r, 750))
    tab = await alive()
    if (tab) return tab
  }
  throw new Error(
    `Camofox browser server is not reachable at ${BASE} and could not be ` +
    `started from ${SERVER_DIR}. Check /tmp/fools-trick/camofox.log, or run ` +
    `\`cd ${SERVER_DIR} && node server.js &\` by hand. For a simple URL read, ` +
    `use the built-in webfetch tool instead.`
  )
}

function tabId(tab) {
  return tab?.tabId ?? tab?.id ?? tab?.tab?.id
}

async function call(path, body, method = "POST") {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "GET" ? undefined : JSON.stringify({ ...UA, ...body }),
    signal: AbortSignal.timeout(30000),
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`camofox ${path} -> HTTP ${r.status}: ${text.slice(0, 300)}`)
  try { return JSON.parse(text) } catch { return { raw: text } }
}

export default async ({ $ }) => {
  return {
    tool: {
      browse_open: tool({
        description:
          "Open a URL in the Camofox anti-detection browser and return the page " +
          "as an accessibility snapshot with element refs. Use for interactive " +
          "or JS-heavy sites, or sites that block plain fetching. For a simple " +
          "static read, prefer the webfetch tool.",
        args: {
          url: tool.schema.string().describe("Absolute URL to open"),
        },
        async execute({ url }) {
          const tab = await ensureTab($)
          const id = tabId(tab)
          await call(`/tabs/${id}/navigate`, { url })
          const snap = await call(`/tabs/${id}/snapshot`, {}, "GET")
          const body = typeof snap === "string" ? snap : JSON.stringify(snap, null, 2)
          return {
            title: `browsed ${url}`,
            output: `tab=${id}\n${body.slice(0, 12000)}`,
            metadata: { tab: id, url },
          }
        },
      }),

      web_search: tool({
        description:
          "Search the web and return the results page (titles, snippets, links) " +
          "using the Camofox browser, which bypasses anti-bot blocking on search " +
          "engines. Use to find sources before reading them with browse_open or " +
          "webfetch.",
        args: {
          query: tool.schema.string().describe("Search query"),
        },
        async execute({ query }) {
          const tab = await ensureTab($)
          const id = tabId(tab)
          const url = `https://duckduckgo.com/html/?q=${encodeURIComponent(query)}`
          await call(`/tabs/${id}/navigate`, { url })
          const snap = await call(`/tabs/${id}/snapshot`, {}, "GET")
          const body = typeof snap === "string" ? snap : JSON.stringify(snap, null, 2)
          return {
            title: `search: ${query}`,
            output: body.slice(0, 12000),
            metadata: { tab: id, query },
          }
        },
      }),

      browse_click: tool({
        description:
          "Click an element in an open Camofox tab, by the ref from a snapshot " +
          "or a CSS selector. Returns the resulting page snapshot.",
        args: {
          tab: tool.schema.string().describe("Tab id from browse_open/web_search"),
          ref: tool.schema.string().optional().describe("Element ref from the snapshot"),
          selector: tool.schema.string().optional().describe("CSS selector, if no ref"),
        },
        async execute({ tab, ref, selector }) {
          await call(`/tabs/${tab}/click`, ref ? { ref } : { selector })
          const snap = await call(`/tabs/${tab}/snapshot`, {}, "GET")
          const body = typeof snap === "string" ? snap : JSON.stringify(snap, null, 2)
          return { title: `clicked in ${tab}`, output: body.slice(0, 12000), metadata: { tab } }
        },
      }),

      browse_type: tool({
        description:
          "Type text into an element in an open Camofox tab, by the ref from a " +
          "snapshot. Returns the resulting page snapshot.",
        args: {
          tab: tool.schema.string().describe("Tab id from browse_open/web_search"),
          ref: tool.schema.string().describe("Element ref from the snapshot"),
          text: tool.schema.string().describe("Text to type"),
        },
        async execute({ tab, ref, text }) {
          await call(`/tabs/${tab}/type`, { ref, text })
          const snap = await call(`/tabs/${tab}/snapshot`, {}, "GET")
          const body = typeof snap === "string" ? snap : JSON.stringify(snap, null, 2)
          return { title: `typed in ${tab}`, output: body.slice(0, 12000), metadata: { tab } }
        },
      }),
    },
  }
}
