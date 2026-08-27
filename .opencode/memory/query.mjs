// CLI to query the durable episode store from outside the plugin (e.g. the Python benchmark's
// eviction-verification gate). Read-only.
//
//   node query.mjs count   <thread>            -> number of episodes stored for a thread
//   node query.mjs search  <thread> <query>    -> JSON array of matching episodes
//   node query.mjs has      <thread> <needle>  -> "yes"/"no": is <needle> present in any episode
//
// Uses MEMORY_DB from the environment (same path the plugin writes to), so it sees exactly what
// the live session persisted.
import { open, search, recent } from "./store.js"

const DB = process.env.MEMORY_DB || `${process.env.HOME}/.local/share/fools-trick/memory.db`
const [, , cmd, thread, ...rest] = process.argv
await open(DB)

if (cmd === "count") {
  console.log(recent({ thread, k: 100000 }).length)
} else if (cmd === "search") {
  console.log(JSON.stringify(search({ thread, query: rest.join(" "), k: 20 })))
} else if (cmd === "has") {
  const needle = rest.join(" ")
  const hits = recent({ thread, k: 100000 }).filter((e) => e.content.includes(needle))
  console.log(hits.length ? "yes" : "no")
} else {
  console.error("usage: query.mjs {count|search|has} <thread> [query|needle]")
  process.exit(2)
}
