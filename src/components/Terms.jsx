import { useEffect, useMemo, useState } from 'react'
import { API_BASE, fetchJson, formatTerm } from '../lib/api'

const MAX_SHOWN = 500

/**
 * Term browser.
 * - click a term → add it to the query with the chosen operator (AND / OR / NOT)
 * - multi-word terms are quoted automatically ("default mode")
 * - ⓘ shows related terms (GET /terms/<term>, Jaccard similarity)
 */
export function Terms ({ onPickTerm }) {
  const [terms, setTerms] = useState([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [op, setOp] = useState('AND')
  const [focus, setFocus] = useState('') // term whose related terms are shown
  const [related, setRelated] = useState({ loading: false, err: '', rows: [] })

  useEffect(() => {
    const ac = new AbortController()
    setLoading(true); setErr('')
    fetchJson(`${API_BASE}/terms`, { signal: ac.signal })
      .then(d => setTerms(Array.isArray(d?.terms) ? d.terms : []))
      .catch(e => { if (e.name !== 'AbortError') setErr(`Failed to fetch terms: ${e.message}`) })
      .finally(() => { if (!ac.signal.aborted) setLoading(false) })
    return () => ac.abort()
  }, [])

  useEffect(() => {
    if (!focus) return
    const ac = new AbortController()
    setRelated({ loading: true, err: '', rows: [] })
    fetchJson(`${API_BASE}/terms/${encodeURIComponent(focus)}?limit=12`, { signal: ac.signal })
      .then(d => setRelated({ loading: false, err: '', rows: d?.related || [] }))
      .catch(e => { if (e.name !== 'AbortError') setRelated({ loading: false, err: e.message, rows: [] }) })
    return () => ac.abort()
  }, [focus])

  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase()
    if (!s) return terms
    // prefix matches first, then substring matches
    const pre = [], sub = []
    for (const t of terms) {
      const l = t.toLowerCase()
      if (l.startsWith(s)) pre.push(t)
      else if (l.includes(s)) sub.push(t)
    }
    return pre.concat(sub)
  }, [terms, search])

  const pick = (t) => onPickTerm?.(formatTerm(t), op)

  return (
    <div className='terms'>
      <div className='row'>
        <input
          type='search'
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder='Search terms…'
          className='input'
          aria-label='Search terms'
        />
        <button type='button' onClick={() => setSearch('')} className='btn btn--ghost btn--sm' disabled={!search}>Clear</button>
      </div>

      <div className='row row--between terms__meta'>
        <span className='muted'>
          {loading ? 'Loading…' : `${Math.min(filtered.length, MAX_SHOWN)} of ${filtered.length} shown`}
        </span>
        <div className='seg' role='radiogroup' aria-label='Operator used when adding a term'>
          <span className='muted'>add as</span>
          {['AND', 'OR', 'NOT'].map(o => (
            <button key={o} type='button' role='radio' aria-checked={op === o}
              className={`seg__btn ${op === o ? 'is-active' : ''}`} onClick={() => setOp(o)}>{o}</button>
          ))}
        </div>
      </div>

      {focus && (
        <div className='related'>
          <div className='row row--between'>
            <b>Related to “{focus}”</b>
            <button type='button' className='btn btn--ghost btn--sm' onClick={() => setFocus('')} aria-label='Close related terms'>✕</button>
          </div>
          {related.loading && <div className='muted'>Loading…</div>}
          {related.err && <div className='text-error'>{related.err}</div>}
          {!related.loading && !related.err && related.rows.length === 0 && <div className='muted'>No related terms.</div>}
          <div className='row row--wrap'>
            {related.rows.map(r => (
              <button key={r.term} type='button' className='btn btn--chip btn--ghost' onClick={() => pick(r.term)}
                title={`co-occurs in ${r.co_count} studies · Jaccard ${Number(r.jaccard).toFixed(3)}`}>
                {r.term} <span className='muted'>{Number(r.jaccard).toFixed(2)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && <div className='skeleton-list'>{Array.from({ length: 8 }).map((_, i) => <div key={i} className='skeleton' />)}</div>}
      {err && <div className='alert alert--error'>{err}</div>}

      {!loading && !err && (
        filtered.length === 0
          ? <div className='muted terms__empty'>No terms found</div>
          : (
            <ul className='terms__ul'>
              {filtered.slice(0, MAX_SHOWN).map(t => (
                <li key={t} className='terms__li'>
                  <button type='button' className='terms__name' title={`Add “${t}” to the query (${op})`} onClick={() => pick(t)}>{t}</button>
                  <button type='button' className='terms__info' title={`Show terms related to “${t}”`} aria-label={`Related terms for ${t}`}
                    onClick={() => setFocus(t)}>ⓘ</button>
                </li>
              ))}
            </ul>
            )
      )}
    </div>
  )
}
