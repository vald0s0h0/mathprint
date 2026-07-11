import { AppShell, Group, NavLink, Text, Title } from '@mantine/core'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { getToken, setToken } from './api'
import Competencies from './pages/Competencies'
import Corrections from './pages/Corrections'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import SettingsPage from './pages/Settings'
import Students from './pages/Students'
import Subjects from './pages/Subjects'

const NAV = [
  { to: '/', label: 'Dashboard' },
  { to: '/sujets', label: 'Sujets' },
  { to: '/corrections', label: 'Corrections' },
  { to: '/eleves', label: 'Élèves' },
  { to: '/competences', label: 'Compétences' },
  { to: '/parametres', label: 'Paramètres' },
]

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const authed = !!getToken()

  if (!authed && location.pathname !== '/login') return <Navigate to="/login" />
  if (location.pathname === '/login') return <Login />

  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 200, breakpoint: 'sm' }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>MathPrint</Title>
          <Text size="sm" c="dimmed" style={{ cursor: 'pointer' }}
            onClick={() => { setToken(null); navigate('/login') }}>
            Déconnexion
          </Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {NAV.map((n) => (
          <NavLink key={n.to} label={n.label} active={location.pathname === n.to}
            onClick={() => navigate(n.to)} />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sujets" element={<Subjects />} />
          <Route path="/corrections" element={<Corrections />} />
          <Route path="/eleves" element={<Students />} />
          <Route path="/competences" element={<Competencies />} />
          <Route path="/parametres" element={<SettingsPage />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
