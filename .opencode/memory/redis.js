// Minimal Redis client over a raw TCP socket. Redis's RESP protocol is simple enough that a
// dependency isn't worth it: we send RESP arrays and parse RESP replies. One persistent
// connection, promise-queued commands (Redis is single-threaded and replies in order).
// Used for the hot short-term tier + the write-stream. If Redis is down, callers treat it as a
// soft failure (memory still works via SQLite) -- this client never throws on connect loss, it
// resolves commands with a rejected promise the caller can swallow.

import net from "node:net"

function encode(args) {
  let s = `*${args.length}\r\n`
  for (const a of args) {
    const v = String(a)
    s += `$${Buffer.byteLength(v)}\r\n${v}\r\n`
  }
  return s
}

// Parse one RESP reply from buf starting at pos. Returns [value, nextPos] or null if incomplete.
function parse(buf, pos) {
  if (pos >= buf.length) return null
  const type = String.fromCharCode(buf[pos])
  const nl = buf.indexOf("\r\n", pos)
  if (nl === -1) return null
  const line = buf.toString("utf8", pos + 1, nl)
  const after = nl + 2
  switch (type) {
    case "+": return [line, after]
    case "-": return [new Error(line), after]
    case ":": return [Number(line), after]
    case "$": {
      const len = Number(line)
      if (len === -1) return [null, after]
      if (after + len + 2 > buf.length) return null
      return [buf.toString("utf8", after, after + len), after + len + 2]
    }
    case "*": {
      const n = Number(line)
      if (n === -1) return [null, after]
      const arr = []
      let p = after
      for (let i = 0; i < n; i++) {
        const r = parse(buf, p)
        if (!r) return null
        arr.push(r[0]); p = r[1]
      }
      return [arr, p]
    }
    default: return [line, after]
  }
}

export function createRedis(url = "redis://127.0.0.1:6379") {
  const u = new URL(url)
  const host = u.hostname || "127.0.0.1"
  const port = Number(u.port || 6379)

  let sock = null
  let buf = Buffer.alloc(0)
  const waiters = []          // FIFO of {resolve,reject} for pending replies
  let connected = false

  function flush() {
    let r
    while ((r = parse(buf, 0))) {
      buf = buf.subarray(r[1])
      const w = waiters.shift()
      if (!w) continue
      if (r[0] instanceof Error) w.reject(r[0]); else w.resolve(r[0])
    }
  }

  function connect() {
    return new Promise((resolve) => {
      sock = net.connect({ host, port }, () => { connected = true; resolve(true) })
      sock.on("data", (d) => { buf = Buffer.concat([buf, d]); flush() })
      sock.on("error", () => { connected = false })
      sock.on("close", () => {
        connected = false
        while (waiters.length) waiters.shift().reject(new Error("redis connection closed"))
      })
    })
  }

  async function cmd(...args) {
    if (!connected) await connect()
    if (!connected) throw new Error("redis unavailable")
    return new Promise((resolve, reject) => {
      waiters.push({ resolve, reject })
      sock.write(encode(args))
    })
  }

  return {
    connect,
    cmd,
    get connected() { return connected },
    close() { try { sock?.end() } catch { /* ignore */ } connected = false },
  }
}
