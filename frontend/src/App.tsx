import {
  ActionIcon, AppShell, Badge, Box, Divider, Group, Loader, Menu, NavLink, Progress,
  SegmentedControl, Select, Text, Title, Tooltip, useComputedColorScheme, useMantineColorScheme,
} from '@mantine/core'
import {
  BookOpenCheck, ClipboardList, GraduationCap, LayoutDashboard, Library, LogOut,
  Building2, Moon, ScanLine, Settings as SettingsIcon, ShieldUser, Sun, Target, Users, FileText,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, getToken, setToken } from './api'
import { APP_VERSION } from './version'
import Competencies from './pages/Competencies'
import Corrections from './pages/Corrections'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import SettingsPage from './pages/Settings'
import Setup from './pages/Setup'
import Students from './pages/Students'
import Bank from './pages/Bank'
import Exercices from './pages/Exercices'
import Subjects from './pages/Subjects'
import Grades from './pages/Grades'
import Establishments from './pages/Establishments'
import Register from './pages/Register'
import ScanProblemsModal from './components/ScanProblemsModal'
import { CYCLES, useAppState, type Cycle } from './state/AppState'

type NavItem = { to: string; label: string; icon: typeof LayoutDashboard; admin?: boolean; operator?: boolean }

// Navigation du quotidien, en haut de la barre latérale.
const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/sujets', label: 'Sujets', icon: FileText },
  { to: '/banque', label: 'Banque', icon: Library },
  { to: '/corrections', label: 'Corrections', icon: ScanLine },
  { to: '/eleves', label: 'Élèves', icon: Users },
  { to: '/notes', label: 'Notes', icon: ClipboardList },
  { to: '/competences', label: 'Compétences', icon: Target },
]

// Zone « Admin », en bas de la barre latérale. `admin: true` => réservé au
// rôle admin (absent des builds utilisateur/correcteur). Les utilisateurs se
// gèrent désormais dans Paramètres > Utilisateurs.
const NAV_BOTTOM: NavItem[] = [
  { to: '/etablissements', label: 'Établissements', icon: Building2, operator: true },
  { to: '/exercices', label: 'Exercices', icon: BookOpenCheck, admin: true },
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
  const [role, setRole] = useState<string>('')
  const { cycle, setCycle, activeJobs, subject, setSubject } = useAppState()
  const isAdmin = role === 'admin'
  const isOperator = isAdmin || role === 'corrector'
  const [subjects, setSubjects] = useState<{ id: string; name: string }[]>([])
  const navBottom = NAV_BOTTOM.filter((n) => (!n.admin || isAdmin) && (!n.operator || isOperator))

  useEffect(() => {
    api.get<{ needs_setup: boolean }>('/api/setup/status')
      .then((r) => setNeedsSetup(r.needs_setup))
      .catch(() => setNeedsSetup(false))
  }, [])

  useEffect(() => {
    let active = true
    if (!authed) { setRole(''); setSubjects([]); return }
    api.get<{ role: string }>('/api/auth/me').then((m) => { if (active) setRole(m.role) }).catch(() => {})
    api.get<{ subjects: { id: string; name: string }[] }>('/api/operations/catalog').then(c => { if (active) setSubjects(c.subjects) }).catch(() => {})
    return () => { active = false }
  }, [authed])

  function renderNavLink(n: NavItem) {
    return (
      <NavLink key={n.to} label={n.label} active={location.pathname === n.to}
        leftSection={<n.icon size={17} strokeWidth={1.9} />}
        rightSection={n.admin ? (
          <Tooltip label="Réservé aux administrateurs" position="right">
            <ShieldUser size={14} color="var(--mantine-color-gray-6)"
              aria-label="Réservé aux administrateurs" />
          </Tooltip>
        ) : undefined}
        style={{ borderRadius: 8 }} fw={500} mb={2}
        onClick={() => navigate(n.to)} />
    )
  }

  if (needsSetup === null) return null
  if (needsSetup) return <Setup onDone={() => setNeedsSetup(false)} />

  if (location.pathname === '/inscription') return <Register />
  if (!authed && location.pathname !== '/login') return <Navigate to="/login" />
  if (location.pathname === '/login') return <Login />
  if (!role) return <Loader m="xl" />

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 216, breakpoint: 'sm' }} padding="lg">
      <ScanProblemsModal />
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap={8} wrap="nowrap" style={{ cursor: 'pointer' }} onClick={() => navigate('/')}>
            <GraduationCap size={24} strokeWidth={2.2} />
            <Title order={3} fw={700}>Kiro & Nina</Title>
          </Group>

          {/* Filtre CYCLE global : le professeur travaille par cycle ; ce
              choix filtre classes, sujets, corrections et compétences. */}
          <Group gap="xs" wrap="nowrap">
            <Select aria-label="Cycle" size="xs" w={150} value={cycle} onChange={v => setCycle((v || 'all') as Cycle)}
              data={[{ value: 'all', label: 'Tous les cycles' }, ...CYCLES.map(c => ({ value: c, label: ({ '2nde': 'Seconde', '1re': 'Première', 'tle': 'Terminale' } as Record<string, string>)[c] || c }))]} />
            <Select aria-label="Matière" size="xs" w={180} value={subject} onChange={v => setSubject(v || 'math')}
              data={subjects.map(s => ({ value: s.id, label: s.name }))} />
          </Group>

          <Group gap="xs" wrap="nowrap">
            {activeJobs.length > 0 && (
              <Menu shadow="md" width={280} position="bottom-end">
                <Menu.Target>
                  <Badge variant="light" color="orange" style={{ cursor: 'pointer' }}
                    leftSection={<Loader size={11} color="orange" />}>
                    {activeJobs.length} sujet(s) en cours
                  </Badge>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>Génération en file de fond</Menu.Label>
                  {activeJobs.map((j) => (
                    <div key={j.assessment_id} style={{ padding: '4px 12px' }}>
                      <Text size="xs" fw={600} lineClamp={1}>{j.title} — {j.class_name}</Text>
                      <Progress value={j.progress} size={4} mt={2}
                        color={j.status === 'running' ? 'orange' : 'gray'} />
                    </div>
                  ))}
                </Menu.Dropdown>
              </Menu>
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
        {NAV.map(renderNavLink)}
        <Box mt="auto">
          <Divider mb={6} />
          {isOperator && (
            <Text size="10px" fw={700} c="dimmed" tt="uppercase" px="sm" mb={4}
              style={{ letterSpacing: 0.6 }}>{isAdmin ? 'Admin' : 'Opérations'}</Text>
          )}
          {navBottom.map(renderNavLink)}
          <Text size="xs" c="dimmed" px="sm" pt={4} pb={4}>Kiro & Nina v{APP_VERSION}</Text>
        </Box>
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={role === 'corrector' ? <Navigate to="/etablissements" replace /> : <Dashboard />} />
          {isOperator && <Route path="/etablissements" element={<Establishments />} />}
          <Route path="/sujets" element={<Subjects />} />
          <Route path="/banque" element={<Bank />} />
          {isAdmin && <Route path="/exercices" element={<Exercices />} />}
          {/* ancien onglet, conservé pour les liens existants */}
          <Route path="/utilisateurs" element={<Navigate to="/parametres?onglet=utilisateurs" replace />} />
          <Route path="/corrections" element={<Corrections />} />
          <Route path="/eleves" element={<Students />} />
          <Route path="/notes" element={<Grades />} />
          <Route path="/competences" element={<Competencies />} />
          <Route path="/parametres" element={<SettingsPage />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
