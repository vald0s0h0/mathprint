import {
  ActionIcon, AppShell, Badge, Box, Divider, Group, Loader, Menu, NavLink, Progress,
  Text, Title, Tooltip, useComputedColorScheme, useMantineColorScheme,
} from '@mantine/core'
import {
  BookOpenCheck, ClipboardList, Factory, GraduationCap, LayoutDashboard, Library, LogOut,
  Building2, Moon, ScanLine, ScanText, Settings as SettingsIcon, SlidersHorizontal, Sun, Target, Users, UserCog, FileText,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, getToken, setToken, zoneOf } from './api'
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
import Atelier from './pages/Atelier'
import OcrReport from './pages/OcrReport'
import AtelierPrinters from './pages/AtelierPrinters'
import CalibrationPanel from './components/CalibrationPanel'
import UsersAdmin from './pages/UsersAdmin'
import Register from './pages/Register'
import HeaderFilters from './components/HeaderFilters'
import ScanProblemsModal from './components/ScanProblemsModal'
import { useAppState, type Teaching } from './state/AppState'

type NavItem = { to: string; label: string; icon: typeof LayoutDashboard; centre?: boolean }

// Onglets utilisateur, en haut de la barre latérale (prof, et admin qui teste
// la plateforme en prof : l'API filtre alors sur SON profil, zone « user »).
const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/sujets', label: 'Sujets', icon: FileText },
  { to: '/banque', label: 'Banque', icon: Library },
  { to: '/corrections', label: 'Corrections', icon: ScanLine },
  { to: '/eleves', label: 'Élèves', icon: Users },
  { to: '/notes', label: 'Notes', icon: ClipboardList },
  { to: '/competences', label: 'Compétences', icon: Target },
]

// En bas de la barre latérale : l'Atelier (admin et rôle Atelier, édition
// centre), puis, sous un séparateur, l'administration (admin seul).
const NAV_ATELIER: NavItem = { to: '/atelier', label: 'Atelier', icon: Factory, centre: true }
const NAV_ADMIN: NavItem[] = [
  { to: '/admin/etablissements', label: 'Établissements', icon: Building2, centre: true },
  { to: '/admin/utilisateurs', label: 'Utilisateurs', icon: UserCog },
  { to: '/exercices', label: 'Exercices', icon: BookOpenCheck },
  { to: '/admin/lecture-ocr', label: 'Lecture OCR', icon: ScanText },
  { to: '/admin/parametres', label: 'Paramètres admin', icon: SlidersHorizontal },
]

const ZONE_BADGE = {
  atelier: { label: 'Atelier', color: 'orange' },
  admin: { label: 'Admin', color: 'red' },
} as const

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
  const [edition, setEdition] = useState<string>('centre')
  const { activeJobs, setTeaching } = useAppState()
  const centre = edition !== 'autonome'
  const isAdmin = role === 'admin'
  const isAtelierRole = role === 'corrector'
  // L'Atelier n'existe que dans l'édition centre (Mac) ; sur l'instance des
  // profs autonomes (NAS), un compte Atelier n'a rien à faire.
  const hasAtelier = centre && (isAdmin || isAtelierRole)
  const hasUserTabs = !isAtelierRole
  const navAdmin = isAdmin ? NAV_ADMIN.filter((n) => !n.centre || centre) : []
  const zone = zoneOf(location.pathname)

  useEffect(() => {
    api.get<{ needs_setup: boolean }>('/api/setup/status')
      .then((r) => setNeedsSetup(r.needs_setup))
      .catch(() => setNeedsSetup(false))
  }, [])

  useEffect(() => {
    let active = true
    if (!authed) { setRole(''); return }
    // Relu à chaque onglet : une classe créée peut ajouter un cycle au filtre.
    api.get<{ role: string; teaching: Teaching; service_mode: string | null; program_id: string | null; edition?: string }>('/api/auth/me').then((m) => {
      if (!active) return
      setRole(m.role); setTeaching(m.teaching); setEdition(m.edition || 'centre')
      // Professeur autonome qui arrive : son premier geste est de dire ce qu'il enseigne.
      if (m.service_mode === 'autonomous' && !m.program_id && location.pathname === '/') navigate('/parametres', { replace: true })
    }).catch(() => {})
    return () => { active = false }
  }, [authed, location.pathname, navigate, setTeaching])

  function renderNavLink(n: NavItem) {
    const active = n.to === '/atelier' ? zone === 'atelier' : location.pathname === n.to
    return (
      <NavLink key={n.to} label={n.label} active={active}
        leftSection={<n.icon size={17} strokeWidth={1.9} />}
        style={{ borderRadius: 8 }} fw={500} mb={2}
        onClick={() => navigate(n.to)} />
    )
  }

  function sectionLabel(label: string) {
    return (
      <Text size="10px" fw={700} c="dimmed" tt="uppercase" px="sm" mb={4}
        style={{ letterSpacing: 0.6 }}>{label}</Text>
    )
  }

  const atelier = (page: React.ReactNode) => <Atelier>{page}</Atelier>

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
            {zone !== 'user' && (
              <Badge variant="light" color={ZONE_BADGE[zone].color}>{ZONE_BADGE[zone].label}</Badge>
            )}
          </Group>

          <HeaderFilters />

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
            <Tooltip label="Mes paramètres">
              <ActionIcon variant={location.pathname === '/parametres' ? 'light' : 'subtle'} color="gray"
                size="lg" aria-label="Mes paramètres" onClick={() => navigate('/parametres')}>
                <SettingsIcon size={18} />
              </ActionIcon>
            </Tooltip>
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
        {hasUserTabs && NAV.map(renderNavLink)}
        <Box mt="auto">
          {hasAtelier && <>
            <Divider mb={6} />
            {renderNavLink(NAV_ATELIER)}
          </>}
          {navAdmin.length > 0 && <>
            <Divider my={6} />
            {sectionLabel('Admin')}
            {navAdmin.map(renderNavLink)}
          </>}
          <Text size="xs" c="dimmed" px="sm" pt={4} pb={4}>Kiro & Nina v{APP_VERSION}</Text>
        </Box>
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          {/* onglets utilisateur */}
          {hasUserTabs && <>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sujets" element={<Subjects />} />
            <Route path="/banque" element={<Bank />} />
            <Route path="/corrections" element={<Corrections />} />
            <Route path="/eleves" element={<Students />} />
            <Route path="/notes" element={<Grades />} />
            <Route path="/competences" element={<Competencies />} />
          </>}
          <Route path="/parametres" element={<SettingsPage scope="personal" />} />

          {/* Atelier */}
          {hasAtelier && <>
            <Route path="/atelier" element={atelier(<Establishments mode="atelier" />)} />
            <Route path="/atelier/corrections" element={atelier(<Corrections />)} />
            <Route path="/atelier/sujets" element={atelier(<Subjects />)} />
            <Route path="/atelier/imprimantes" element={atelier(<AtelierPrinters />)} />
            <Route path="/atelier/calibration" element={atelier(<CalibrationPanel />)} />
          </>}

          {/* administration */}
          {isAdmin && <>
            {centre && <Route path="/admin/etablissements" element={<Establishments mode="admin" />} />}
            {centre && <Route path="/admin/eleves" element={<Students />} />}
            <Route path="/admin/utilisateurs" element={<UsersAdmin centre={centre} />} />
            <Route path="/exercices" element={<Exercices />} />
            <Route path="/admin/lecture-ocr" element={<OcrReport />} />
            <Route path="/admin/parametres" element={<SettingsPage scope="admin" />} />
          </>}

          {/* anciens liens */}
          <Route path="/etablissements" element={<Navigate to={hasAtelier ? '/atelier' : '/'} replace />} />
          <Route path="/utilisateurs" element={<Navigate to="/admin/utilisateurs" replace />} />
          <Route path="*" element={<Navigate to={hasUserTabs ? '/' : '/atelier'} replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
