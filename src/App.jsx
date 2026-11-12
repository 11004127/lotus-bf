import { useCallback, useRef, useState } from 'react'
import { Terms } from './components/Terms'
import { QueryBuilder } from './components/QueryBuilder'
import { Studies } from './components/Studies'
import { Locations } from './components/Locations'
import { NiiViewer } from './components/NiiViewer'
import { useUrlQueryState } from './hooks/useUrlQueryState'
import { appendToQuery } from './lib/api'
import './App.css'

const MIN_PX = 260

export default function App () {
  // query + radius live in the URL (?q=…&r=…) so a search can be bookmarked or shared
  const [query, setQuery] = useUrlQueryState('q')
  const [rText, setRText] = useUrlQueryState('r', '6')
  const radius = Math.max(1, Math.min(30, Number(rText) || 6))
  const [tab, setTab] = useState('studies')
  const [focus, setFocus] = useState(null) // MNI coordinate to show in the viewer

  const handlePickTerm = useCallback((term, op) => setQuery(q => appendToQuery(q, term, op)), [setQuery])
  const handleAddLocation = useCallback((coord) => setQuery(q => appendToQuery(q, coord, 'AND')), [setQuery])

  // --- resizable panes (desktop) ---
  const gridRef = useRef(null)
  const [sizes, setSizes] = useState([22, 44, 34])
  const startDrag = (which, e) => {
    e.preventDefault()
    const startX = e.clientX
    const total = gridRef.current.getBoundingClientRect().width
    const px = sizes.map(p => (p / 100) * total)
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      const a = which; const b = which + 1
      let pa = px[a] + dx; let pb = px[b] - dx
      if (pa < MIN_PX) { pb -= MIN_PX - pa; pa = MIN_PX }
      if (pb < MIN_PX) { pa -= MIN_PX - pb; pb = MIN_PX }
      const next = [...px]; next[a] = pa; next[b] = pb
      setSizes(next.map(v => (v / total) * 100))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.classList.remove('is-resizing')
    }
    document.body.classList.add('is-resizing')
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div className='app'>
      <header className='app__header'>
        <div>
          <h1 className='app__title'>LoTUS-BF</h1>
          <div className='app__subtitle'>Location-or-Term Unified Search for Brain Functions</div>
        </div>
      </header>

      <main className='app__grid' ref={gridRef}>
        <section className='card' style={{ flexBasis: `${sizes[0]}%` }} aria-label='Terms'>
          <div className='card__title'>Terms</div>
          <Terms onPickTerm={handlePickTerm} />
        </section>

        <div className='resizer' role='separator' aria-label='Resize left/middle' onMouseDown={(e) => startDrag(0, e)} />

        <section className='card card--stack' style={{ flexBasis: `${sizes[1]}%` }} aria-label='Query and results'>
          <QueryBuilder query={query} setQuery={setQuery} radius={radius} setRadius={setRText} />
          <div className='divider' />
          <div className='tabs' role='tablist'>
            <button type='button' role='tab' aria-selected={tab === 'studies'} className={`tabs__btn ${tab === 'studies' ? 'is-active' : ''}`} onClick={() => setTab('studies')}>Studies</button>
            <button type='button' role='tab' aria-selected={tab === 'locations'} className={`tabs__btn ${tab === 'locations' ? 'is-active' : ''}`} onClick={() => setTab('locations')}>Locations</button>
          </div>
          {tab === 'studies'
            ? <Studies query={query} radius={radius} />
            : <Locations query={query} radius={radius} onFocus={setFocus} />}
        </section>

        <div className='resizer' role='separator' aria-label='Resize middle/right' onMouseDown={(e) => startDrag(1, e)} />

        <section className='card' style={{ flexBasis: `${sizes[2]}%` }} aria-label='Brain map'>
          <NiiViewer query={query} radius={radius} focus={focus} onAddLocation={handleAddLocation} />
        </section>
      </main>

      <footer className='app__footer'>
        Data: Neurosynth-derived coordinates and abstracts · Maps are smoothed focus-count maps on the MNI152 2 mm template, for exploration only.
      </footer>
    </div>
  )
}
