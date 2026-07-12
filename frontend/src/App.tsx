import {
  ActionIcon, AppShell, Badge, Group, NavLink, SegmentedControl, Text, Title,
  Tooltip, useComputedColorScheme, useMantineColorScheme,
} from '@mantine/core'
import {
  FlaskConical, GraduationCap, LayoutDashboard, Library, LogOut, Moon, ScanLine,
  Settings as SettingsIcon, Sun, Target, Users, FileText,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, getToken, setToken } from './api'
import Competencies from './pages/Competencies'
import Corrections from './pages/Corrections'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import SettingsPage from './pages/Settings'
import Setup from './pages/Setup'
import Students from './pages/Students'
import Bank from './pages/Bank'
import Subjects from './pages/Subjects'
import { CYCLES, useAppState, type Cycle } from './state/AppState'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/sujets', label: 'Sujets', icon: FileText },
  { to: '/banque', label: 'Banque', icon: Library },
  { to: '/corrections', label: 'Corrections', icon: ScanLine },
  { to: '/eleves', label: 'Élèves', icon: Users },
  { to: '/competences', label: 'Compétences', icon: Target },
  { to: '/parametres', label: 'Paramètres', icon: SettingsIcon },
]

function ThemeToggle() {
  const { setColorScheme } = useMantineColorScheme()
  const computed = useComputedColorScheme('light')
  return (
    <Tooltip label={computed === 'dark' ? 'Mode clair' : 'Mode sombre'}>
      <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Basculer le thème"
        onClick={() => setColorScheme(computed === 'dark' ? 'light' : 'dark')}>
        {computed === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
      </ActionIcon>
    </Tooltip>
  )
}

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const authed = !!getToken()
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const { cycle, setCycle, mockMode, refreshSystem } = useAppState()

  useEffect(() => {
    api.get<{ needs_setup: boolean }>('/api/setup/status')
      .then((r) => setNeedsSetup(r.needs_setup))
      .catch(() => setNeedsSetup(false))
  }, [])

  useEffect(() => { if (authed) refreshSystem() }, [authed, refreshSystem])

  if (needsSetup === null) return null
  if (needsSetup) return <Setup onDone={() => setNeedsSetup(false)} />

  if (!authed && location.pathname !== '/login') return <Navigate to="/login" />
  if (location.pathname === '/login') return <Login />

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 216, breakpoint: 'sm' }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap={8} wrap="nowrap" style={{ cursor: 'pointer' }} onClick={() => navigate('/')}>
            <GraduationCap size={24} strokeWidth={2.2} />
            <Title order={3} fw={700}>MathPrint</Title>
          </Group>

          {/* Filtre CYCLE global : le professeur travaille par cycle ; ce
              choix filtre classes, sujets, corrections et compétences. */}
          <SegmentedControl size="sm" radius="md" value={cycle}
            onChange={(v) => setCycle(v as Cycle)}
            data={[...CYCLES.map((c) => ({ value: c, label: c })),
                   { value: 'all', label: 'Tout' }]} />

          <Group gap="xs" wrap="nowrap">
            {mockMode && (
              <Tooltip label="Mode démonstration actif — désactivable dans Paramètres → Système">
                <Badge variant="light" color="grape" leftSection={<FlaskConical size={12} />}>
                  démo
                </Badge>
              </Tooltip>
            )}
            <ThemeToggle />
            <Tooltip label="Déconnexion">
              <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Déconnexion"
                onClick={() => { setToken(null); navigate('/login') }}>
                <LogOut size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        {NAV.map((n) => (
          <NavLink key={n.to} label={n.label} active={location.pathname === n.to}
            leftSection={<n.icon size={17} strokeWidth={1.9} />}
            style={{ borderRadius: 8 }} fw={500} mb={2}
            onClick={() => navigate(n.to)} />
        ))}
        <Text size="xs" c="dimmed" mt="auto" px="sm" pb={4}>MathPrint v0.9</Text>
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sujets" element={<Subjects />} />
          <Route path="/banque" element={<Bank />} />
          <Route path="/corrections" element={<Corrections />} />
          <Route path="/eleves" element={<Students />} />
          <Route path="/competences" element={<Competencies />} />
          <Route path="/parametres" element={<SettingsPage />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
