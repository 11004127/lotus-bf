export function SortHeader ({ col, sortKey, sortDir, onSort }) {
  const active = sortKey === col.key
  return (
    <th aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type='button' className='th-btn' onClick={() => onSort(col.key)}>
        {col.label}<span className='th-arrow'>{active ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}</span>
      </button>
    </th>
  )
}

export function Pager ({ page, totalPages, setPage }) {
  if (totalPages <= 1) return null
  return (
    <div className='row row--between pager'>
      <span className='muted'>Page <b>{page}</b> / <b>{totalPages}</b></span>
      <div className='row'>
        <button type='button' className='btn btn--ghost btn--sm' disabled={page <= 1} onClick={() => setPage(1)} aria-label='First page'>⏮</button>
        <button type='button' className='btn btn--ghost btn--sm' disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
        <button type='button' className='btn btn--ghost btn--sm' disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
        <button type='button' className='btn btn--ghost btn--sm' disabled={page >= totalPages} onClick={() => setPage(totalPages)} aria-label='Last page'>⏭</button>
      </div>
    </div>
  )
}
