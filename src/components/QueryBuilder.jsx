import { useEffect, useRef, useState } from 'react'
import { fetchJson, queryUrl } from '../lib/api'

const EXAMPLES = [
  'emotion',
  '(pain OR emotion) AND memory',
  '[-22,-4,-18] NOT emotion',
  '"default mode" OR [0,-52,26]',
  'reward AND [-2,50,-6]',
]

/**
 * Query editor.
 * - Typing edits a local draft; the search only runs on Enter / Search
 *   (before: every keystroke fired a studies query AND a NIfTI map request).
 * - While typing, the draft is checked by GET /query/<q>/parse (no DB access)
 *   and the interpretation (with explicit parentheses) or the syntax error is shown.
 */
export function QueryBuilder ({ query, setQuery, radius, setRadius }) {
  const [draft, setDraft] = useState(query || '')
  const [parsed, setParsed] = useState(null) // { interpreted } | { error }
  const [showHelp, setShowHelp] = useState(false)
  const inputRef = useRef(null)

  // keep the draft in sync when the committed query changes elsewhere (term click, back button…)
  useEffect(() => { setDraft(query || '') }, [query])

  // live syntax check (debounced)
  useEffect(() => {
    const q = draft.trim()
    if (!q) { setParsed(null); return }
    const ac = new AbortController()
    const t = setTimeout(async () => {
      try {
        const d = await fetchJson(queryUrl(q, 'parse', { r: radius }), { signal: ac.signal })
        setParsed({ interpreted: d.interpreted, terms: d.terms?.length || 0, locs: d.locations?.length || 0 })
      } catch (e) {
        if (e.name === 'AbortError') return
        // 404 = an older backend without /parse → stay silent
        setParsed(e.status === 404 ? null : { error: e.message })
      }
    }, 300)
    return () => { clearTimeout(t); ac.abort() }
  }, [draft, radius])

  const commit = (value = draft) => setQuery(value.trim())

  const insert = (token) => {
    const el = inputRef.current
    const start = el?.selectionStart ?? draft.length
    const end = el?.selectionEnd ?? draft.length
    const before = draft.slice(0, start).replace(/\s+$/, '')
    const after = draft.slice(end).replace(/^\s+/, '')
    const pad = (s) => (s ? ' ' : '')
    const next = `${before}${pad(before)}${token}${pad(after)}${after}`
    setDraft(next)
    requestAnimationFrame(() => {
      if (!el) return
      const pos = (before + pad(before) + token).length + 1
      el.focus(); el.setSelectionRange(pos, pos)
    })
  }

  const dirty = draft.trim() !== (query || '').trim()

  return (
    <div className='qb'>
      <div className='row row--between'>
        <div className='card__title'>Query Builder</div>
        <button type='button' className='btn btn--ghost btn--sm' onClick={() => setShowHelp(s => !s)} aria-expanded={showHelp}>
          {showHelp ? 'Hide syntax help' : 'Syntax help'}
        </button>
      </div>

      <form className='qb__form' onSubmit={(e) => { e.preventDefault(); commit() }}>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder='e.g. [-22,-4,-18] NOT emotion'
          className={`input qb__input ${parsed?.error ? 'input--error' : ''}`}
          aria-label='Query'
          spellCheck={false}
        />
        <button type='submit' className='btn btn--primary' disabled={!draft.trim()}>
          Search
        </button>
      </form>

      <div className='qb__status' aria-live='polite'>
        {parsed?.error && <span className='text-error'>⚠ {parsed.error}</span>}
        {parsed?.interpreted && (
          <span className='muted'>
            Interpreted as <code>{parsed.interpreted}</code>
            {dirty && <span className='badge'>press Enter to search</span>}
          </span>
        )}
      </div>

      <div className='row row--wrap'>
        {['AND', 'OR', 'NOT', '(', ')'].map(op => (
          <button key={op} type='button' className='btn btn--chip' onClick={() => insert(op)}>{op}</button>
        ))}
        <button type='button' className='btn btn--chip btn--danger' onClick={() => { setDraft(''); setQuery('') }}>Reset</button>
        <label className='qb__radius' title='Radius (mm) used to match [x,y,z] locations'>
          radius
          <input
            type='number' min={1} max={30} step={1}
            value={radius}
            onChange={(e) => setRadius(e.target.value)}
            className='input input--xs'
          /> mm
        </label>
      </div>

      {showHelp && (
        <div className='help'>
          <ul>
            <li><b>Terms</b>: <code>emotion</code>, phrases in quotes <code>"working memory"</code>, prefix <code>emot*</code></li>
            <li><b>Locations</b>: MNI coordinates <code>[x,y,z]</code>, matched within the radius above</li>
            <li><b>Operators</b>: <code>AND</code>, <code>OR</code>, <code>NOT</code>, parentheses. NOT &gt; AND &gt; OR; a space means AND.</li>
            <li>Terms and locations can be mixed in one expression.</li>
          </ul>
          <div className='row row--wrap'>
            {EXAMPLES.map(ex => (
              <button key={ex} type='button' className='btn btn--chip btn--ghost' onClick={() => { setDraft(ex); commit(ex) }}>{ex}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
