import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// A real error boundary (class component). The previous version passed CSS strings
// to `style`, which itself throws in React, so any runtime error showed a blank page.
class ErrorBoundary extends React.Component {
  constructor (props) {
    super(props)
    this.state = { err: null }
  }

  static getDerivedStateFromError (err) { return { err } }

  componentDidCatch (err, info) { console.error('[LoTUS-BF]', err, info) }

  render () {
    if (!this.state.err) return this.props.children
    return (
      <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
        <h2>Something went wrong</h2>
        <pre style={{ whiteSpace: 'pre-wrap', background: '#f5f5f5', padding: 12, borderRadius: 8 }}>
          {String(this.state.err?.stack || this.state.err?.message || this.state.err)}
        </pre>
        <button onClick={() => window.location.reload()}>Reload</button>
      </div>
    )
  }
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
