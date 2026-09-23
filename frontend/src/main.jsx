import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/mukta/latin-400.css'
import '@fontsource/mukta/latin-500.css'
import '@fontsource/mukta/latin-600.css'
import '@fontsource/mukta/latin-700.css'
import '@fontsource/mukta/latin-800.css'
import '@fontsource/mukta/devanagari-500.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
