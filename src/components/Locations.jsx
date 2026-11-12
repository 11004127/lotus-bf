import { useEffect, useState } from 'react'
import { downloadCsv, fetchJson, queryUrl } from '../lib/api'
import { Pager, SortHeader } from './Table'
import { useSorted } from '../hooks/useSorted'

const COLUMNS = [
  { key: 'study_id', label: 'Study ID' },
  { key: 'x', label: 'X', numeric: true },
  { key: 'y', label: 'Y', numeric: true },
  { key: 'z', label: 'Z', numeric: true },
]
const PAGE_SIZE = 25

/** Coordinates reported by the matching studies. Clicking a row moves the brain viewer there. */
export function Locations ({ query, radius, onFocus }) {
  const [rows, setRows] = useState([])
  const [counts, setCounts] = useState({ loc: 0, study: 0 })
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [page, setPage] = useState(1)
  const { sorted, sortKey, sortDir, changeSort } = useSorted(rows, COLUMNS, 'study_id')

  useEffect(() => { setPage(1) }, [query, sortKey, sortDir])

  useEffect(() => {
    if (!query) { setRows([]); setErr(''); return }
    const ac = new AbortController()
    setLoading(true); setErr('')
    fetchJson(queryUrl(query, 'locations', { r: radius }), { signal: ac.signal })
      .then(d => {
        setRows(Array.isArray(d?.results) ? d.results : [])
        setCounts({ loc: d?.location_counts ?? 0, study: d?.study_counts ?? 0 })
      })
      .catch(e => { if (e.name !== 'AbortError') { setErr(e.message); setRows([]) } })
      .finally(() => { if (!ac.signal.aborted) setLoading(false) })
    return () => ac.abort()
  }, [query, radius])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const pageRows = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  if (!query) return <div className='empty'>Build a query above to list the reported coordinates.</div>

  return (
    <div className='tablebox'>
      <div className='row row--between tablebox__bar'>
        <span>{loading ? 'Searching…' : <><b>{counts.loc.toLocaleString()}</b> coordinates from <b>{counts.study.toLocaleString()}</b> {counts.study === 1 ? 'study' : 'studies'}</>}</span>
        <button type='button' className='btn btn--ghost btn--sm' disabled={!rows.length}
          onClick={() => downloadCsv('lotus_locations.csv', sorted, ['study_id', 'x', 'y', 'z'])}>CSV</button>
      </div>
      {err && <div className='alert alert--error'>Unable to fetch locations: {err}</div>}
      {loading && <div className='skeleton-list'>{Array.from({ length: 6 }).map((_, i) => <div key={i} className='skeleton' />)}</div>}
      {!loading && !err && (
        <>
          <div className='tablewrap'>
            <table className='table table--compact'>
              <thead><tr>{COLUMNS.map(c => <SortHeader key={c.key} col={c} sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />)}</tr></thead>
              <tbody>
                {pageRows.length === 0
                  ? <tr><td colSpan={4} className='muted'>No coordinates.</td></tr>
                  : pageRows.map((r, i) => (
                    <tr key={`${r.study_id}-${r.x}-${r.y}-${r.z}-${i}`} className='is-clickable'
                      onClick={() => onFocus?.([r.x, r.y, r.z])} title='Show this location in the brain viewer'>
                      <td>{r.study_id}</td><td>{r.x}</td><td>{r.y}</td><td>{r.z}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} totalPages={totalPages} setPage={setPage} />
        </>
      )}
    </div>
  )
}
