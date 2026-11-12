import { useMemo, useState } from 'react'

/** Client-side sorting shared by the Studies and Locations tables. */
export function useSorted (rows, columns, initialKey, initialDir = 'asc') {
  const [sortKey, setSortKey] = useState(initialKey)
  const [sortDir, setSortDir] = useState(initialDir)
  const numeric = useMemo(() => new Set(columns.filter(c => c.numeric).map(c => c.key)), [columns])

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const A = a?.[sortKey]
      const B = b?.[sortKey]
      if (numeric.has(sortKey)) return ((Number(A) || 0) - (Number(B) || 0)) * dir
      return String(A ?? '').localeCompare(String(B ?? ''), 'en', { numeric: true }) * dir
    })
  }, [rows, sortKey, sortDir, numeric])

  const changeSort = (key) => {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir('asc') }
  }
  return { sorted, sortKey, sortDir, changeSort }
}
