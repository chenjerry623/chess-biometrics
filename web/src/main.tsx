import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './styles/global.css'
import App from './App.tsx'
import { PlayersPage } from './PlayersPage.tsx'
import { PlayerProfile } from './PlayerProfile.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/players" element={<PlayersPage />} />
        <Route path="/players/:username" element={<PlayerProfile />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
