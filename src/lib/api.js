// Shared helpers for talking to the LoTUS-BF backend.

// Optional override at build time: VITE_API_BASE=http://host:5000 npm run build
export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

/** Build `/query/<q>/<endpoint>?params` (the query is URL-encoded once). */
export function queryUrl (query, endpoint, params = {}) {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const qs = sp.toString()
  return `${API_BASE}/query/${encodeURIComponent(query)}/${endpoint}${qs ? `?${qs}` : ''}`
}

/** fetch JSON and surface the backend's {error} message on failure. */
export async function fetchJson (url, opts = {}) {
  const res = await fetch(url, opts)
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const msg = data?.error || `HTTP ${res.status}`
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return data
}

/** Quote a term if it contains whitespace, so "working memory" stays one phrase. */
export function formatTerm (t) {
  const s = String(t).trim()
  return /\s/.test(s) ? `"${s.replace(/"/g, '')}"` : s
}

/** Append a token to a query with the chosen operator (AND is implicit). */
export function appendToQuery (q, token, op = 'AND') {
  const base = (q || '').trim()
  if (!base) return op === 'NOT' ? `NOT ${token}` : token
  if (op === 'OR') return `${base} OR ${token}`
  // AND binds tighter than OR, so "A OR B" + C must become "(A OR B) C", not "A OR (B AND C)"
  const left = /\bOR\b/i.test(base) ? `(${base})` : base
  return op === 'NOT' ? `${left} NOT ${token}` : `${left} ${token}`
}

export function formatCoord ([x, y, z]) {
  return `[${Math.round(x)},${Math.round(y)},${Math.round(z)}]`
}

/** Download an array of objects as a CSV file. */
export function downloadCsv (filename, rows, columns) {
  const esc = (v) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [columns.join(','), ...rows.map(r => columns.map(c => esc(r[c])).join(','))]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
