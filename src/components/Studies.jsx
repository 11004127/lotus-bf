import { useEffect, useMemo, useState } from 'react'
import { downloadCsv, fetchJson, queryUrl } from '../lib/api'
import { Pager, SortHeader } from './Table'
import { useSorted } from '../hooks/useSorted'

const COLUMNS = [
  { key: 'year', label: 'Year', numeric: true },
  { key: 'journal', label: 'Journal' },
  { key: 'title', label: 'Title' },
  { key: 'authors', label: 'Authors' },
]
const PAGE_SIZE = 20

export function Studies ({ query, radius }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(1)
  const { sorted, sortKey, sortDir, changeSort } = useSorted(rows, COLUMNS, 'year', 'desc')

  useEffect(() => { setPage(1); setFilter('') }, [query])
  useEffect(() => { setPage(1) }, [filter, sortKey, sortDir])

  useEffect(() => {
    if (!query) { setRows([]); setErr(''); return }
    const ac = new AbortController()
    setLoading(true); setErr('')
    fetchJson(queryUrl(query, 'studies', { r: radius }), { signal: ac.signal })
      .then(d => setRows(Array.isArray(d?.results) ? d.results : []))
      .catch(e => { if (e.name !== 'AbortError') { setErr(e.message); setRows([]) } })
      .finally(() => { if (!ac.signal.aborted) setLoading(false) })
    return () => ac.abort()
  }, [query, radius])

  const visible = useMemo(() => {
    const s = filter.trim().toLowerCase()
    if (!s) return sorted
    return sorted.filter(r => ['title', 'authors', 'journal', 'year', 'study_id']
      .some(k => String(r[k] ?? '').toLowerCase().includes(s)))
  }, [sorted, filter])

  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE))
  const pageRows = visible.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  if (!query) return <div className='empty'>Build a query above (or click a term) to see matching studies.</div>

  return (
    <div className='tablebox'>
      <div className='row row--between tablebox__bar'>
        <span>
          {loading ? 'Searching…' : <><b>{rows.length.toLocaleString()}</b> {rows.length === 1 ? 'study' : 'studies'}{filter && <> · <b>{visible.length}</b> after filter</>}</>}
        </span>
        <div className='row'>
          <input className='input input--sm' type='search' placeholder='Filter results…' value={filter}
            onChange={e => setFilter(e.target.value)} aria-label='Filter studies' />
          <button type='button' className='btn btn--ghost btn--sm' disabled={!visible.length}
            onClick={() => downloadCsv('lotus_studies.csv', visible, ['study_id', 'year', 'journal', 'title', 'authors'])}>
            CSV
          </button>
        </div>
      </div>

      {err && <div className='alert alert--error'>Unable to fetch studies: {err}</div>}
      {loading && <div className='skeleton-list'>{Array.from({ length: 6 }).map((_, i) => <div key={i} className='skeleton skeleton--tall' />)}</div>}

      {!loading && !err && (
        <>
          <div className='tablewrap'>
            <table className='table'>
              <thead>
                <tr>{COLUMNS.map(c => <SortHeader key={c.key} col={c} sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />)}</tr>
              </thead>
              <tbody>
                {pageRows.length === 0
                  ? <tr><td colSpan={4} className='muted'>No studies match this query.</td></tr>
                  : pageRows.map(r => (
                    <tr key={r.study_id}>
                      <td className='nowrap'>{r.year ?? ''}</td>
                      <td>{r.journal || ''}</td>
                      <td className='table__title'>
                        <a href={`https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(r.study_id)}/`} target='_blank' rel='noreferrer' title='Open in PubMed'>
                          {r.title || '(untitled)'}
                        </a>
                      </td>
                      <td className='table__authors' title={r.authors}>{r.authors || ''}</td>
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
