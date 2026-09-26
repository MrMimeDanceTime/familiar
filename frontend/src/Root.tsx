import { useEffect, useState } from 'react'
import App from './App'
import { GradingPage } from './components/GradingPage'

// The app has one view; the grading page is a second one, reached at
// #grading so it needs no router.
export function Root() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onHash = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return hash === '#grading' ? <GradingPage /> : <App />
}
