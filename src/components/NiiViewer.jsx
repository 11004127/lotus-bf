import { useEffect, useMemo, useRef, useState } from 'react'
import * as nifti from 'nifti-reader-js'
import { formatCoord, queryUrl } from '../lib/api'

// Background template (served from /public with the Vite base path, e.g. /lotus-bf/static/…)
const MNI_BG_URL = `${import.meta.env.BASE_URL}static/mni_2mm.nii.gz`
const SCALE = 4 // on-screen pixels per voxel (canvas is drawn at this resolution → crisp crosshair)

// MNI152 2 mm affine: x = 90 - 2i, y = 2j - 126, z = 2k - 72  (i,j,k = voxel indices)
const isStd = (dims) => dims[0] === 91 && dims[1] === 109 && dims[2] === 91
function vox2mni ([i, j, k], dims) {
  if (isStd(dims)) return [90 - 2 * i, 2 * j - 126, 2 * k - 72]
  return [-(i - (dims[0] >> 1)), j - (dims[1] >> 1), k - (dims[2] >> 1)]
}
function mni2vox ([x, y, z], dims) {
  const c = (v, n) => Math.max(0, Math.min(n - 1, Math.round(v)))
  if (isStd(dims)) return [c((90 - x) / 2, dims[0]), c((y + 126) / 2, dims[1]), c((z + 72) / 2, dims[2])]
  return [c(-x + (dims[0] >> 1), dims[0]), c(y + (dims[1] >> 1), dims[1]), c(z + (dims[2] >> 1), dims[2])]
}

// ---------- NIfTI loading ----------
function asTypedArray (header, buffer) {
  const T = nifti.NIFTI1
  switch (header.datatypeCode) {
    case T.TYPE_INT8: return new Int8Array(buffer)
    case T.TYPE_UINT8: return new Uint8Array(buffer)
    case T.TYPE_INT16: return new Int16Array(buffer)
    case T.TYPE_UINT16: return new Uint16Array(buffer)
    case T.TYPE_INT32: return new Int32Array(buffer)
    case T.TYPE_UINT32: return new Uint32Array(buffer)
    case T.TYPE_FLOAT64: return new Float64Array(buffer)
    default: return new Float32Array(buffer)
  }
}
async function loadNifti (url, signal) {
  const res = await fetch(url, { signal })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { msg = (await res.json()).error || msg } catch { /* not JSON */ }
    throw new Error(msg)
  }
  let ab = await res.arrayBuffer()
  if (nifti.isCompressed(ab)) ab = nifti.decompress(ab)
  if (!nifti.isNIFTI(ab)) throw new Error('response is not a NIfTI file')
  const header = nifti.readHeader(ab)
  const raw = asTypedArray(header, nifti.readImage(header, ab))
  const slope = header.scl_slope || 1
  const inter = header.scl_inter || 0
  const data = new Float32Array(raw.length)
  let min = Infinity; let max = -Infinity
  for (let i = 0; i < raw.length; i++) {
    const v = raw[i] * slope + inter
    data[i] = v
    if (v < min) min = v
    if (v > max) max = v
  }
  const dims = [header.dims[1] | 0, header.dims[2] | 0, header.dims[3] | 0]
  if (!dims[0] || !dims[1] || !dims[2]) throw new Error('invalid NIfTI dimensions')
  return {
    data, dims, min, max,
    studies: Number(res.headers.get('X-LoTUS-Studies') ?? NaN),
    foci: Number(res.headers.get('X-LoTUS-Foci') ?? NaN),
  }
}

/** percentile over the *positive* voxels only (a sparse map is mostly zeros) */
function positivePercentile (data, p) {
  const vals = []
  const step = Math.max(1, Math.floor(data.length / 300000))
  for (let i = 0; i < data.length; i += step) if (data[i] > 0) vals.push(data[i])
  if (!vals.length) return Infinity
  vals.sort((a, b) => a - b)
  return vals[Math.min(vals.length - 1, Math.floor((p / 100) * (vals.length - 1)))]
}

/** "hot" colormap, starting at dark red so the lowest supra-threshold voxels stay visible */
function hot (t) {
  const u = 0.3 + 0.7 * Math.max(0, Math.min(1, t))
  const c = (v) => Math.round(255 * Math.max(0, Math.min(1, v)))
  return [c(u * 3), c(u * 3 - 1), c(u * 3 - 2)]
}

export function NiiViewer ({ query, radius, focus, onAddLocation }) {
  const [bg, setBg] = useState(null)
  const [map, setMap] = useState(null)
  const [errBG, setErrBG] = useState('')
  const [errMap, setErrMap] = useState('')
  const [loadingMap, setLoadingMap] = useState(false)

  const [fwhm, setFwhm] = useState(10)
  const [kernel, setKernel] = useState('gauss')
  const [alpha, setAlpha] = useState(0.7)
  const [thrMode, setThrMode] = useState('pctl')
  const [pctl, setPctl] = useState(90)
  const [thrValue, setThrValue] = useState(0)

  const [cursor, setCursor] = useState([45, 63, 36]) // voxel indices (≈ MNI [0,0,0])
  const [coordText, setCoordText] = useState(['0', '0', '0'])

  const refs = { z: useRef(null), y: useRef(null), x: useRef(null) }
  const dims = bg?.dims || map?.dims || [91, 109, 91]

  const mapUrl = useMemo(() => (query ? queryUrl(query, 'nii', { fwhm, kernel, r: radius }) : ''), [query, fwhm, kernel, radius])

  // background template (once)
  useEffect(() => {
    const ac = new AbortController()
    loadNifti(MNI_BG_URL, ac.signal)
      .then(v => { setBg(v); setCursor(mni2vox([0, 0, 0], v.dims)) })
      .catch(e => { if (e.name !== 'AbortError') setErrBG(e.message) })
    return () => ac.abort()
  }, [])

  // meta-analytic map; stale requests are aborted when the query changes
  useEffect(() => {
    if (!mapUrl) { setMap(null); setErrMap(''); return }
    const ac = new AbortController()
    setLoadingMap(true); setErrMap('')
    loadNifti(mapUrl, ac.signal)
      .then(v => setMap(v))
      .catch(e => { if (e.name !== 'AbortError') { setErrMap(e.message); setMap(null) } })
      .finally(() => { if (!ac.signal.aborted) setLoadingMap(false) })
    return () => ac.abort()
  }, [mapUrl])

  // external focus (e.g. click on a row in the Locations table)
  useEffect(() => {
    if (focus) setCursor(mni2vox(focus, dims))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus])

  const mni = vox2mni(cursor, dims)
  useEffect(() => { setCoordText(mni.map(String)) }, [mni[0], mni[1], mni[2]]) // eslint-disable-line react-hooks/exhaustive-deps

  const threshold = useMemo(() => {
    if (!map) return Infinity
    if (thrMode === 'value') return Number(thrValue) || 0
    return positivePercentile(map.data, Math.max(0, Math.min(99.9, Number(pctl) || 0)))
  }, [map, thrMode, pctl, thrValue])

  // ---------- drawing ----------
  useEffect(() => {
    const [nx, ny, nz] = dims
    const [ci, cj, ck] = cursor
    const bgOK = bg && bg.dims.join() === dims.join()
    const mapOK = map && map.dims.join() === dims.join()
    const bgRange = bgOK ? (bg.max - bg.min) || 1 : 1
    const mapMax = mapOK ? map.max : 0
    const span = mapMax > threshold ? mapMax - threshold : 1

    // axis: which index is fixed; (w,h) = native slice size; vox(c,r) → linear index
    const views = {
      z: { w: nx, h: ny, vox: (c, r) => (nx - 1 - c) + (ny - 1 - r) * nx + ck * nx * ny, cx: nx - 1 - ci, cy: ny - 1 - cj },
      y: { w: nx, h: nz, vox: (c, r) => (nx - 1 - c) + cj * nx + (nz - 1 - r) * nx * ny, cx: nx - 1 - ci, cy: nz - 1 - ck },
      x: { w: ny, h: nz, vox: (c, r) => ci + c * nx + (nz - 1 - r) * nx * ny, cx: cj, cy: nz - 1 - ck },
    }
    for (const [axis, v] of Object.entries(views)) {
      const canvas = refs[axis].current
      if (!canvas) continue
      const off = document.createElement('canvas')
      off.width = v.w; off.height = v.h
      const octx = off.getContext('2d')
      const img = octx.createImageData(v.w, v.h)
      let p = 0
      for (let r = 0; r < v.h; r++) {
        for (let c = 0; c < v.w; c++) {
          const idx = v.vox(c, r)
          let R = 0; let G = 0; let B = 0
          if (bgOK) {
            const g = Math.max(0, Math.min(255, ((bg.data[idx] - bg.min) / bgRange) * 255 * 1.15))
            R = G = B = g
          }
          if (mapOK) {
            const mv = map.data[idx]
            if (mv > 0 && mv >= threshold) {
              const [hr, hg, hb] = hot((mv - threshold) / span)
              R = (1 - alpha) * R + alpha * hr
              G = (1 - alpha) * G + alpha * hg
              B = (1 - alpha) * B + alpha * hb
            }
          }
          img.data[p++] = R; img.data[p++] = G; img.data[p++] = B; img.data[p++] = 255
        }
      }
      octx.putImageData(img, 0, 0)

      canvas.width = v.w * SCALE; canvas.height = v.h * SCALE
      const ctx = canvas.getContext('2d')
      ctx.imageSmoothingEnabled = true
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(off, 0, 0, canvas.width, canvas.height)
      // crosshair
      const X = (v.cx + 0.5) * SCALE; const Y = (v.cy + 0.5) * SCALE
      ctx.strokeStyle = 'rgba(0,255,120,0.85)'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(X, 0); ctx.lineTo(X, canvas.height); ctx.moveTo(0, Y); ctx.lineTo(canvas.width, Y); ctx.stroke()
      // orientation labels
      ctx.fillStyle = 'rgba(255,255,255,0.8)'; ctx.font = `${3 * SCALE}px system-ui, sans-serif`
      const [lab1, lab2] = axis === 'x' ? ['P', 'A'] : ['L', 'R']
      ctx.fillText(lab1, 4, canvas.height - 6)
      ctx.fillText(lab2, canvas.width - 4 - ctx.measureText(lab2).width, canvas.height - 6)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bg, map, cursor, alpha, threshold, dims.join()])

  function onCanvasClick (e, axis) {
    const canvas = e.currentTarget
    const rect = canvas.getBoundingClientRect()
    // the canvas is letter-boxed inside a square box (object-fit: contain)
    const s = Math.min(rect.width / canvas.width, rect.height / canvas.height)
    const ox = rect.left + (rect.width - canvas.width * s) / 2
    const oy = rect.top + (rect.height - canvas.height * s) / 2
    const c = Math.floor((e.clientX - ox) / s / SCALE)
    const r = Math.floor((e.clientY - oy) / s / SCALE)
    if (c < 0 || r < 0 || c >= canvas.width / SCALE || r >= canvas.height / SCALE) return
    const [nx, ny, nz] = dims
    setCursor(([i, j, k]) => {
      if (axis === 'z') return [nx - 1 - c, ny - 1 - r, k]
      if (axis === 'y') return [nx - 1 - c, j, nz - 1 - r]
      return [i, c, nz - 1 - r]
    })
  }

  function commitCoords () {
    const vals = coordText.map(s => parseFloat(s))
    if (vals.some(Number.isNaN)) { setCoordText(mni.map(String)); return }
    setCursor(mni2vox(vals, dims))
  }

  const linear = cursor[0] + cursor[1] * dims[0] + cursor[2] * dims[0] * dims[1]
  const valueHere = map && map.dims.join() === dims.join() ? map.data[linear] : null
  const thrShown = Number.isFinite(threshold) ? threshold : null

  const views = [
    { key: 'y', name: 'Coronal', fixed: `y = ${mni[1]}` },
    { key: 'x', name: 'Sagittal', fixed: `x = ${mni[0]}` },
    { key: 'z', name: 'Axial', fixed: `z = ${mni[2]}` },
  ]

  return (
    <div className='viewer'>
      <div className='row row--between'>
        <div className='card__title'>Brain map</div>
        {query && <a href={mapUrl} className='btn btn--ghost btn--sm' download>Download .nii.gz</a>}
      </div>

      <div className='viewer__summary muted'>
        {!query && 'Run a query to draw its meta-analytic map on the MNI152 template.'}
        {query && loadingMap && 'Generating map…'}
        {query && !loadingMap && map && (
          Number.isFinite(map.studies)
            ? <>Map from <b>{map.studies}</b> {map.studies === 1 ? 'study' : 'studies'} · <b>{map.foci}</b> {map.foci === 1 ? 'focus' : 'foci'} · peak ≈ {map.max.toFixed(2)} foci</>
            : <>peak {map.max.toFixed(2)}</>
        )}
      </div>

      {(errBG || errMap) && (
        <div className='alert alert--warn'>
          {errBG && <div>Template: {errBG}</div>}
          {errMap && <div>Map: {errMap}</div>}
        </div>
      )}

      <div className={`viewer__grid ${loadingMap ? 'is-loading' : ''}`}>
        {views.map(v => (
          <figure key={v.key} className='viewer__slice'>
            <canvas ref={refs[v.key]} onClick={(e) => onCanvasClick(e, v.key)} aria-label={`${v.name} slice, click to move the cursor`} />
            <figcaption>{v.name} <span className='muted'>{v.fixed}</span></figcaption>
          </figure>
        ))}
      </div>

      <div className='viewer__cursor'>
        <form className='row' onSubmit={(e) => { e.preventDefault(); commitCoords() }}>
          {['X', 'Y', 'Z'].map((lab, n) => (
            <label key={lab} className='coord'>
              {lab}
              <input className='input input--xs' inputMode='decimal' value={coordText[n]} aria-label={`${lab} (mm)`}
                onChange={e => setCoordText(t => t.map((s, m) => (m === n ? e.target.value : s)))}
                onBlur={commitCoords} />
            </label>
          ))}
          <button type='submit' className='visually-hidden'>Go</button>
        </form>
        <div className='muted'>value {valueHere == null ? '—' : valueHere.toFixed(3)}</div>
        <button type='button' className='btn btn--primary btn--sm' onClick={() => onAddLocation?.(formatCoord(mni))}
          title='Add this coordinate to the query'>
          + {formatCoord(mni)} to query
        </button>
      </div>

      {map && thrShown != null && map.max > thrShown && (
        <div className='colorbar'>
          <span>{thrShown.toFixed(2)}</span>
          <div className='colorbar__bar' />
          <span>{map.max.toFixed(2)}</span>
        </div>
      )}

      <details className='panel' open>
        <summary>Display &amp; map settings</summary>
        <div className='settings'>
          <label>Threshold
            <select className='input input--sm' value={thrMode} onChange={e => setThrMode(e.target.value)}>
              <option value='pctl'>Percentile of non-zero voxels</option>
              <option value='value'>Absolute value</option>
            </select>
          </label>
          {thrMode === 'pctl'
            ? <label>Percentile <input className='input input--sm' type='number' min={0} max={99.9} step={1} value={pctl} onChange={e => setPctl(e.target.value)} /></label>
            : <label>Value <input className='input input--sm' type='number' min={0} step={0.01} value={thrValue} onChange={e => setThrValue(e.target.value)} /></label>}
          <label>Overlay opacity <input type='range' min={0} max={1} step={0.05} value={alpha} onChange={e => setAlpha(Number(e.target.value))} /></label>
          <label>Smoothing FWHM (mm) <input className='input input--sm' type='number' min={0} max={40} step={1} value={fwhm} onChange={e => setFwhm(Math.max(0, Number(e.target.value) || 0))} /></label>
          <label>Kernel
            <select className='input input--sm' value={kernel} onChange={e => setKernel(e.target.value)}>
              <option value='gauss'>Gaussian</option>
              <option value='uniform'>Uniform (box)</option>
            </select>
          </label>
        </div>
      </details>
    </div>
  )
}
