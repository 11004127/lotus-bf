// src/hooks/useUrlQueryState.js
import { useCallback, useEffect, useMemo, useState } from 'react'

/**
 * Sync a string state with a URL query param (default: "q").
 * - Keeps other params intact
 * - Updates via history.replaceState (no page reload)
 * - Setter accepts value or updater function
 */
export function useUrlQueryState(param = 'q', initial = '') {
  // read once on mount
  const readFromUrl = useCallback(() => {
    const sp = new URLSearchParams(window.location.search)
    return sp.get(param) ?? initial
  }, [param, initial])

  const [value, setValue] = useState(readFromUrl)

  // keep internal state in sync if user uses back/forward
  useEffect(() => {
    const onPop = () => setValue(readFromUrl())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [readFromUrl])

  // stable setter: updates state + URL
  const setAndSync = useCallback((next) => {
    setValue(prev => {
      const v = typeof next === 'function' ? next(prev) : next
      const url = new URL(window.location.href)
      const sp = url.searchParams
      if (v == null || v === '') sp.delete(param)
      else sp.set(param, String(v))
      // preserve hash/pathname; only replace search
      const newUrl = `${url.pathname}?${sp.toString()}${url.hash}`
      window.history.replaceState(null, '', newUrl)
      return v
    })
  }, [param])

  // memoized tuple (not strictly needed, but tidy)
  return useMemo(() => [value, setAndSync], [value, setAndSync])
}

export default useUrlQueryState

