import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import Admin from './pages/Admin.jsx'
import Chat from './pages/Chat.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Chat />} />
      <Route path="/admin" element={<Navigate to="/admin/" replace />} />
      <Route path="/admin/" element={<Admin />} />
    </Routes>
  )
}
