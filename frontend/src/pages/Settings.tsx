// Paramètres, en deux pages distinctes :
//   • scope="personal" (/parametres, roue crantée en haut à droite) : Mon compte,
//     Mes imprimantes (prof), Mes raccourcis, Calibration (édition autonome) ;
//   • scope="admin" (/admin/parametres, en bas de la barre latérale) : API,
//     Scans par mail, Pédagogie, Documents, Système, Journaux, Données.
// `?onglet=<valeur>` ouvre directement un onglet.
import {
  Accordion, ActionIcon, Alert, Badge, Box, Button, Card, Checkbox, ColorInput, FileButton,
  Group, Loader, Modal, NumberInput, PasswordInput, SimpleGrid, Stack, Switch, Table, Tabs, Text,
  TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Database, Download, FileText, FlaskConical, KeyRound, Keyboard, Mail, Printer,
  RefreshCw, Ruler, ScrollText, Save, SlidersHorizontal, Trash2, UserRound, Users,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { api, getToken } from '../api'
import CalibrationPanel from '../components/CalibrationPanel'
import MailIntakeSettings from '../components/MailIntakeSettings'
import OcrEngineSettings from '../components/OcrEngineSettings'
import PrinterSettings, { type PrintersInfo } from '../components/PrinterSettings'
import TeachingPreferences from '../components/TeachingPreferences'
import ShortcutsSettings from '../components/ShortcutsSettings'
import SettingsSection from '../components/SettingsSection'
import TemplateEditor from '../components/TemplateEditor'

type Me = {
  id: string; email: string; display_name: string; role: string
  service_mode: string | null; program_id: string | null; subject_id: string | null
  edition?: string
}

// Onglets de la page admin : un ancien lien /parametres?onglet=<admin> y est renvoyé.
export const ADMIN_TABS = ['api', 'mail', 'pedagogie', 'documents', 'systeme', 'journaux', 'donnees']
type Provider = { provider: string; secret_preview: string; active: boolean }
type Build = { sha: string; time: string }
type StudentRow = {
  id: string; name: string; class_name: string
  active: boolean; copy_count: number
}
type AssessmentRow = {
  id: string; title: string; type: string; status: string; class_name: string
  created_at: string; copy_count: number; scan_batch_count: number
}
type CorrectionRow = {
  id: string; assessment_title: string; class_name: string; status: string
  page_count: number; created_at: string
}
type OrphanRow = { label: string; count: number }
type Overview = {
  totals: { classes: number; students: number; assessments: number
    corrections: number; bank_exercises: number; orphans: number }
  classes: { id: string; name: string; grade_level: string; archived: boolean
    students: number; assessments: number; corrections: number }[]
  orphans: OrphanRow[]
}
type ClassDetail = { students: StudentRow[]; assessments: AssessmentRow[]
  corrections: CorrectionRow[] }
type DeleteKind = 'classes' | 'students' | 'assessments' | 'corrections'
type ScanOriginal = { name: string; filename: string; size: number; created: string; expires: string }
type SystemStatus = {
  version: string; build?: Build
  database: { ok: boolean; url_scheme: string }
  disk: { total_gb: number; free_gb: number; alert: boolean }
  last_backup: string | null
  indigo?: { published: number; in_bank: number; source: string; generated_at: string }
}
type LogEntry = { ts: string; method: string; path: string; error: string; traceback: string }

export default function SettingsPage({ scope = 'personal' }: { scope?: 'personal' | 'admin' }) {
  const adminPage = scope === 'admin'
  const [me, setMe] = useState<Me | null>(null)
  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [pwdLoading, setPwdLoading] = useState(false)
  const [providers, setProviders] = useState<Provider[]>([])
  const [system, setSystem] = useState<Record<string, any>>({})
  const [ocrThresholdPct, setOcrThresholdPct] = useState<number | string>(90)
  const [savingOcrThreshold, setSavingOcrThreshold] = useState(false)
  const [llmThresholdPct, setLlmThresholdPct] = useState<number | string>(90)
  const [savingLlmThreshold, setSavingLlmThreshold] = useState(false)
  // géométrie à tracer : seuil de la note automatique et part contrôlée
  const [traceThresholdPct, setTraceThresholdPct] = useState<number | string>(90)
  const [traceAuditPct, setTraceAuditPct] = useState<number | string>(20)
  const [savingTrace, setSavingTrace] = useState(false)
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [printers, setPrinters] = useState<PrintersInfo | null>(null)
  const [backups, setBackups] = useState<{ name: string; size: number }[]>([])
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [webBuild, setWebBuild] = useState<Build | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [classDetail, setClassDetail] = useState<Record<string, ClassDetail>>({})
  const [openClasses, setOpenClasses] = useState<string[]>([])
  const [confirmTarget, setConfirmTarget] = useState<{ kind: DeleteKind; id: string; label: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  // suppression d'une correction : retirer AUSSI du suivi des élèves ce que sa
  // validation y a propagé (preuves de compétence, courbe d'oubli, niveaux,
  // appréciations) — sinon ce suivi survit à la correction supprimée
  const [purgeTracking, setPurgeTracking] = useState(true)
  const [scanOriginals, setScanOriginals] = useState<ScanOriginal[]>([])
  const [purgeConfirmOpen, setPurgeConfirmOpen] = useState(false)
  const [purging, setPurging] = useState(false)
  const [orphansPurgeOpen, setOrphansPurgeOpen] = useState(false)
  const [purgingOrphans, setPurgingOrphans] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const defaultTab = adminPage ? 'api' : 'compte'
  const tab = searchParams.get('onglet') || defaultTab
  const isAdmin = me?.role === 'admin'

  async function refresh() {
    const account = await api.get<Me>('/api/auth/me'); setMe(account)
    if (account.role === 'admin' && adminPage) {
    api.get<Provider[]>('/api/settings/providers').then(setProviders)
    api.get<Record<string, any>>('/api/settings/system').then((values) => {
      setSystem(values)
      setOcrThresholdPct(Math.round(Number(values.ocr_confidence_threshold?.value ?? 0.9) * 100))
      setLlmThresholdPct(Math.round(Number(values.llm_confidence_threshold?.value ?? 0.9) * 100))
      setTraceThresholdPct(Math.round(Number(values.construction_confidence_threshold?.value ?? 0.9) * 100))
      setTraceAuditPct(Math.round(Number(values.construction_audit_share?.value ?? 0.2) * 100))
    })
    api.get<SystemStatus>('/api/system/status').then(setStatus)
    }
    if (!adminPage) api.get<PrintersInfo>('/api/printers').then(setPrinters)
    if (account.role === 'admin' && adminPage) api.get<{ name: string; size: number }[]>('/api/system/backups').then(setBackups)
    // build de l'interface web (image nginx) — absent en dev, servi en no-cache
    fetch('/build.json').then((r) => (r.ok ? r.json() : null)).then(setWebBuild)
      .catch(() => setWebBuild(null))
  }
  useEffect(() => { void refresh() }, [])
  // arrivée directe sur ?onglet=journaux : le chargement suit l'onglet ouvert
  useEffect(() => { if (tab === 'journaux') refreshLogs() }, [])
  useEffect(() => {
    if (adminPage) return
    const timer = window.setInterval(() => {
      api.get<PrintersInfo>('/api/printers').then(setPrinters).catch(() => {})
    }, 10_000)
    return () => window.clearInterval(timer)
  }, [])

  // onglet Données : réservé au rôle admin côté API — silencieux si 403.
  // Vue compactée : on ne tire que l'agrégat par classe ; le détail d'une classe
  // (élèves/sujets/corrections) se charge à l'ouverture (loadClassDetail).
  function refreshData(reopen: string[] = openClasses) {
    if (!adminPage) return
    api.get<Overview>('/api/data/overview')
      .then((o) => { setOverview(o); reopen.forEach(loadClassDetail) })
      .catch(() => {})
    api.get<{ retention_days: number; files: ScanOriginal[] }>('/api/data/scan-originals')
      .then((r) => setScanOriginals(r.files)).catch(() => {})
  }

  // scan original tel que reçu (copieur, mail, bac à sable), conservé 30 jours
  async function downloadScanOriginal(f: ScanOriginal) {
    const res = await fetch(`/api/data/scan-originals/${encodeURIComponent(f.name)}`,
      { headers: { Authorization: `Bearer ${getToken()}` } })
    if (!res.ok) {
      notifications.show({ color: 'red', message: 'Scan introuvable (purgé après 30 jours ?)' })
      return
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(await res.blob())
    a.download = f.filename
    a.click()
  }

  function loadClassDetail(classId: string) {
    Promise.all([
      api.get<StudentRow[]>(`/api/data/students?class_id=${classId}`),
      api.get<AssessmentRow[]>(`/api/data/assessments?class_id=${classId}`),
      api.get<CorrectionRow[]>(`/api/data/corrections?class_id=${classId}`),
    ]).then(([students, assessments, corrections]) =>
      setClassDetail((d) => ({ ...d, [classId]: { students, assessments, corrections } })))
      .catch(() => {})
  }

  async function purgeOrphans() {
    setPurgingOrphans(true)
    try {
      const r = await api.post<{ deleted: number }>('/api/data/orphans/purge')
      notifications.show({ color: 'green', message: `${r.deleted} ligne(s) orpheline(s) supprimée(s)` })
      setOrphansPurgeOpen(false)
      refreshData()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPurgingOrphans(false)
    }
  }
  useEffect(refreshData, [])

  async function purgeBank() {
    setPurging(true)
    try {
      const r = await api.post<{ exercises_deleted: number; extractions_reset: number }>(
        '/api/content/bank/purge')
      notifications.show({
        color: 'green',
        message: `Banque purgée : ${r.exercises_deleted} exercice(s) supprimé(s), `
          + `${r.extractions_reset} extraction(s) réinitialisée(s) — la prochaine `
          + 'génération repart de zéro.',
      })
      setPurgeConfirmOpen(false)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPurging(false)
    }
  }

  async function confirmDelete() {
    if (!confirmTarget) return
    setDeleting(true)
    try {
      const q = confirmTarget.kind === 'corrections' && purgeTracking ? '?purge_tracking=true' : ''
      await api.del(`/api/data/${confirmTarget.kind}/${confirmTarget.id}${q}`)
      notifications.show({ color: 'green', message: 'Supprimé définitivement' })
      setConfirmTarget(null)
      refreshData()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setDeleting(false)
    }
  }

  async function changePassword() {
    if (newPwd.length < 8) {
      notifications.show({ color: 'red', message: 'Nouveau mot de passe : 8 caractères minimum' })
      return
    }
    if (newPwd !== confirmPwd) {
      notifications.show({ color: 'red', message: 'Les mots de passe ne correspondent pas' })
      return
    }
    setPwdLoading(true)
    try {
      await api.post('/api/auth/me/password', { current_password: curPwd, new_password: newPwd })
      notifications.show({ color: 'green', message: 'Mot de passe mis à jour' })
      setCurPwd(''); setNewPwd(''); setConfirmPwd('')
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPwdLoading(false)
    }
  }

  async function save(provider: string) {
    const secret = edit[provider] || ''
    await api.post('/api/settings/providers', { provider, secret, active: true })
    notifications.show({ color: 'green', message: `${provider} enregistré` })
    refresh()
  }

  async function saveColor(key: string, value: string) {
    await api.post('/api/settings/system', { key, value: { value } })
    notifications.show({ color: 'green', message: 'Couleur enregistrée' })
    refresh()
  }

  async function saveAppreciationSynthesis(value: boolean) {
    await api.post('/api/settings/system', {
      key: 'appreciation_synthesis_enabled', value: { value },
    })
    notifications.show({ color: 'green', message: 'Réglage enregistré' })
    refresh()
  }

  async function saveOcrThreshold() {
    const pct = Math.max(1, Math.min(100, Number(ocrThresholdPct) || 90))
    setSavingOcrThreshold(true)
    try {
      await api.post('/api/settings/system', {
        key: 'ocr_confidence_threshold', value: { value: pct / 100 },
      })
      // Relire l'autorité serveur immédiatement : le champ affiche ainsi la
      // valeur réellement persistée, pas un état local optimiste.
      const values = await api.get<Record<string, any>>('/api/settings/system')
      const savedPct = Math.round(Number(values.ocr_confidence_threshold?.value ?? 0.9) * 100)
      setSystem(values); setOcrThresholdPct(savedPct)
      notifications.show({ color: 'green', message: `Seuil OCR/CV enregistré à ${savedPct} %` })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSavingOcrThreshold(false)
    }
  }

  async function saveLlmThreshold() {
    const pct = Math.max(1, Math.min(100, Number(llmThresholdPct) || 90))
    setSavingLlmThreshold(true)
    try {
      await api.post('/api/settings/system', {
        key: 'llm_confidence_threshold', value: { value: pct / 100 },
      })
      const values = await api.get<Record<string, any>>('/api/settings/system')
      const savedPct = Math.round(Number(values.llm_confidence_threshold?.value ?? 0.9) * 100)
      setSystem(values); setLlmThresholdPct(savedPct)
      notifications.show({ color: 'green', message: `Seuil DeepSeek enregistré à ${savedPct} %` })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSavingLlmThreshold(false)
    }
  }

  async function saveTraceSettings() {
    // 101 % : aucune note automatique (toute confiance est ≤ 100 %)
    const pct = Math.max(1, Math.min(101, Number(traceThresholdPct) || 90))
    const audit = Math.max(0, Math.min(100, Number(traceAuditPct) || 0))
    setSavingTrace(true)
    try {
      await api.post('/api/settings/system', {
        key: 'construction_confidence_threshold', value: { value: pct / 100 },
      })
      await api.post('/api/settings/system', {
        key: 'construction_audit_share', value: { value: audit / 100 },
      })
      const values = await api.get<Record<string, any>>('/api/settings/system')
      setSystem(values)
      setTraceThresholdPct(Math.round(Number(values.construction_confidence_threshold?.value ?? 0.9) * 100))
      setTraceAuditPct(Math.round(Number(values.construction_audit_share?.value ?? 0.2) * 100))
      notifications.show({ color: 'green', message: `Tracés : seuil ${pct} %, contrôle ${audit} %` })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSavingTrace(false)
    }
  }

  async function doBackup() {
    const r = await api.post<{ file: string }>('/api/system/backup')
    notifications.show({ color: 'green', message: `Sauvegarde : ${r.file}` })
    refresh()
  }

  async function refreshLogs() {
    setLogsLoading(true)
    try {
      const r = await api.get<{ entries: LogEntry[] }>('/api/system/logs?n=50')
      setLogs(r.entries)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setLogsLoading(false)
    }
  }

  async function clearLogs() {
    await api.post('/api/system/logs/clear')
    setLogs([])
    notifications.show({ color: 'green', message: 'Journal vidé' })
  }

  // anciens liens /parametres?onglet=<onglet admin> : renvoyés vers la page admin
  if (!adminPage && ADMIN_TABS.includes(tab)) return <Navigate to={`/admin/parametres?onglet=${tab}`} replace />
  if (!adminPage && tab === 'utilisateurs') return <Navigate to="/admin/utilisateurs" replace />

  const pp = adminPage ? { pl: 'xl', maw: 900 } as const : { pt: 'md' } as const

  return (
    <Stack>
      <div>
        <Title order={2}>{adminPage ? 'Paramètres admin' : 'Mes paramètres'}</Title>
        {adminPage && <Text size="sm" c="dimmed">Services, pédagogie, documents et maintenance de l'instance.</Text>}
      </div>
      <Tabs value={tab} keepMounted={false} orientation={adminPage ? 'vertical' : 'horizontal'}
        variant={adminPage ? 'pills' : 'default'}
        onChange={(v) => {
          const next = v ?? defaultTab
          setSearchParams(next === defaultTab ? {} : { onglet: next }, { replace: true })
          if (next === 'journaux') refreshLogs()
        }}>
        <Tabs.List miw={adminPage ? 190 : undefined} pr={adminPage ? 'md' : undefined}
          style={adminPage ? { gap: 4, alignSelf: 'flex-start' } : undefined}>
          {!adminPage && <>
            <Tabs.Tab value="compte" leftSection={<UserRound size={15} />}>Mon compte</Tabs.Tab>
            {me?.role === 'teacher' && <Tabs.Tab value="imprimantes" leftSection={<Printer size={15} />}>Mes imprimantes</Tabs.Tab>}
            <Tabs.Tab value="raccourcis" leftSection={<Keyboard size={15} />}>Raccourcis</Tabs.Tab>
            {me?.edition === 'autonome' && <Tabs.Tab value="calibration" leftSection={<Ruler size={15} />}>Calibration</Tabs.Tab>}
          </>}
          {adminPage && isAdmin && <>
            <Tabs.Tab value="api" leftSection={<KeyRound size={15} />}>API</Tabs.Tab>
            <Tabs.Tab value="mail" leftSection={<Mail size={15} />}>Scans par mail</Tabs.Tab>
            <Tabs.Tab value="pedagogie" leftSection={<SlidersHorizontal size={15} />}>Pédagogie</Tabs.Tab>
            <Tabs.Tab value="documents" leftSection={<FileText size={15} />}>Documents</Tabs.Tab>
            <Tabs.Tab value="systeme" leftSection={<Database size={15} />}>Système</Tabs.Tab>
            <Tabs.Tab value="journaux" leftSection={<ScrollText size={15} />}>Journaux</Tabs.Tab>
            <Tabs.Tab value="donnees" leftSection={<Trash2 size={15} />}>Données</Tabs.Tab>
          </>}
        </Tabs.List>

        {!adminPage && <Tabs.Panel value="compte" {...pp}>
          <Card withBorder maw={420}>
            {me && (
              <Group justify="space-between" mb="md">
                <div>
                  <Text fw={600}>{me.display_name}</Text>
                  <Text size="sm" c="dimmed">{me.email}</Text>
                </div>
                <Badge variant="light">{({ admin: 'Administrateur', teacher: 'Professeur', corrector: 'Atelier' } as Record<string, string>)[me.role] ?? me.role}</Badge>
              </Group>
            )}
            <Stack gap="xs">
              <Text fw={600} size="sm">Changer le mot de passe</Text>
              <PasswordInput label="Mot de passe actuel" value={curPwd}
                onChange={(e) => setCurPwd(e.target.value)} />
              <PasswordInput label="Nouveau mot de passe" value={newPwd}
                description="8 caractères minimum"
                onChange={(e) => setNewPwd(e.target.value)} />
              <PasswordInput label="Confirmer le nouveau mot de passe" value={confirmPwd}
                onChange={(e) => setConfirmPwd(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && changePassword()} />
              <Button size="xs" onClick={changePassword} loading={pwdLoading}
                leftSection={<Save size={14} />} disabled={!curPwd || !newPwd}>
                Mettre à jour le mot de passe
              </Button>
            </Stack>
          </Card>
          {me?.service_mode === 'autonomous' && (
            <TeachingPreferences programId={me.program_id} subjectId={me.subject_id} />
          )}
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="api" {...pp}>
          <Stack>
            <Text size="sm" c="dimmed">
              Sans clé, un service reste en mode simulé. Les clés sont chiffrées au repos
              et jamais renvoyées intégralement.
            </Text>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
              {(['mathpix', 'deepseek-flash', 'deepseek-pro', 'anthropic', 'mistral', 'gemini'] as const).map((p) => {
                const row = providers.find((x) => x.provider === p)
                const labels: Record<string, string> = {
                  'deepseek-flash': 'DeepSeek Flash',
                  'deepseek-pro': 'DeepSeek Pro',
                  mistral: 'Mistral OCR',
                  anthropic: 'Anthropic',
                  mathpix: 'Mathpix',
                  gemini: 'Gemini',
                }
                const usage: Record<string, string> = {
                  mistral: 'Extraction Sésamaths',
                  gemini: "Création d'exercices",
                }
                return (
                  <SettingsSection key={p} title={labels[p] ?? p} description={usage[p]}
                    badge={row?.active && row.secret_preview
                      ? <Badge variant="light" color="green">configuré {row.secret_preview}</Badge>
                      : <Badge variant="light" color="gray"
                          leftSection={<FlaskConical size={11} />}>simulé</Badge>}>
                    <Group align="flex-end" wrap="nowrap">
                      <TextInput style={{ flex: 1 }} label={p === 'mathpix' ? 'app_id:app_key' : 'Clé API'}
                        type="password" placeholder={row?.active ? 'Remplacer la clé…' : 'Coller la clé'}
                        onChange={(e) => setEdit({ ...edit, [p]: e.target.value })} />
                      <Button leftSection={<Save size={14} />} onClick={() => save(p)}>Enregistrer</Button>
                    </Group>
                  </SettingsSection>
                )
              })}
            </SimpleGrid>
          </Stack>
        </Tabs.Panel>}

        {!adminPage && <Tabs.Panel value="imprimantes" {...pp}>
          <PrinterSettings printers={printers} refresh={refresh} />
        </Tabs.Panel>}

        {!adminPage && <Tabs.Panel value="raccourcis" {...pp}>
          <ShortcutsSettings scope="personal" />
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="mail" {...pp}>
          <MailIntakeSettings />
        </Tabs.Panel>}

        {!adminPage && <Tabs.Panel value="calibration" {...pp}>
          <CalibrationPanel />
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="pedagogie" {...pp}>
          <Stack>
            <SettingsSection title="Lecture OCR et vision"
              description="Après la lecture des scans, « OCRiser » ne présente que les réponses dont la confiance OCR/CV est sous ce seuil, avant toute correction.">
              <Group align="flex-end">
                <NumberInput label="Seuil de confiance" suffix=" %" min={1} max={100}
                  step={1} w={190} value={ocrThresholdPct}
                  onChange={setOcrThresholdPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveOcrThreshold() }} />
                <Button leftSection={<Save size={14} />} loading={savingOcrThreshold}
                  onClick={saveOcrThreshold}>Enregistrer</Button>
              </Group>
            </SettingsSection>
            <OcrEngineSettings system={system} onSaved={refresh} />
            <SettingsSection title="Correction DeepSeek"
              description="Une réponse corrigée par le LLM n'est envoyée dans l'assistant de correction manuelle que si sa confiance est strictement inférieure à ce seuil. La valeur par défaut est 90 %.">
              <Group align="flex-end">
                <NumberInput label="Seuil de confiance LLM" suffix=" %" min={1} max={100}
                  step={1} w={210} value={llmThresholdPct}
                  onChange={setLlmThresholdPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveLlmThreshold() }} />
                <Button leftSection={<Save size={14} />} loading={savingLlmThreshold}
                  onClick={saveLlmThreshold}>Enregistrer</Button>
              </Group>
            </SettingsSection>
            <SettingsSection title="Géométrie à tracer"
              description="La lecture automatique note un tracé quand sa confiance atteint ce seuil ; en dessous, il part en correction manuelle (101 % : jamais de note automatique). Une part des tracés notés automatiquement passe quand même chez vous : vos notes servent à calibrer le seuil (script calibrate_construction). Mettez-la à 0 une fois le seuil établi.">
              <Group align="flex-end">
                <NumberInput label="Seuil de confiance" suffix=" %" min={1} max={101}
                  step={1} w={170} value={traceThresholdPct}
                  onChange={setTraceThresholdPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveTraceSettings() }} />
                <NumberInput label="Contrôle par échantillon" suffix=" %" min={0} max={100}
                  step={5} w={190} value={traceAuditPct}
                  onChange={setTraceAuditPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveTraceSettings() }} />
                <Button leftSection={<Save size={14} />} loading={savingTrace}
                  onClick={saveTraceSettings}>Enregistrer</Button>
              </Group>
            </SettingsSection>
            <ShortcutsSettings scope="default" />
            <SettingsSection title="Phrase encourageante (Claude Haiku)"
              description="Une phrase courte générée par IA dans la zone Appréciation des copies corrigées, en plus des progrès de compétences (toujours affichés, sans IA). Désactivée par défaut."
              action={<Switch size="md"
                checked={system.appreciation_synthesis_enabled?.value ?? false}
                onChange={(e) => saveAppreciationSynthesis(e.currentTarget.checked)} />} />
            <SettingsSection title="Révision et entraînement"
              description="Paramètres fixes du moteur de révision (lecture seule).">
              <Table verticalSpacing="xs" withRowBorders>
                <Table.Tbody>
                  <Table.Tr>
                    <Table.Td>Seuil de courbe d'oubli (probabilité de rappel)</Table.Td>
                    <Table.Td ta="right" fw={600}>{system.forgetting_threshold?.value ?? 0.8}</Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td>Variation automatique max du niveau (1-10) par cycle de révision</Table.Td>
                    <Table.Td ta="right" fw={600}>±1</Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td>Répartition entraînement</Table.Td>
                    <Table.Td ta="right" fw={600}>60 % consolidation / 30 % cible / 10 % défi</Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </SettingsSection>
          </Stack>
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="documents" {...pp}>
          <Stack>
            <TemplateEditor />
            <SettingsSection title="Couleurs techniques">
              <Group grow align="flex-start">
                <ColorInput size="xs" label="Zones de réponse élève (dropout)"
                  description="Supprimée avant OCR — garder un ton clair. Une plage de cette couleur est imprimée en bas de chaque page : le scan en mesure la teinte réelle pour mieux la supprimer."
                  value={system.dropout_color?.value ?? '#F5B7A8'}
                  onChangeEnd={(v) => saveColor('dropout_color', v)} />
                <ColorInput size="xs" label="Encre de correction (overlay)"
                  value={system.correction_color?.value ?? '#C62828'}
                  onChangeEnd={(v) => saveColor('correction_color', v)} />
              </Group>
              <Text size="xs" c="dimmed" mt="sm">
                Figé pour le repérage scanner : code DataMatrix de 20 chiffres signé (bas gauche),
                4 fiduciels AprilTag 11 mm (coins), marges 9 mm.
              </Text>
            </SettingsSection>
          </Stack>
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="systeme" {...pp}>
          <Stack>
            {status && (
              <SettingsSection title="État des services">
                <Group gap="xs">
                  <Badge variant="light" color={status.database.ok ? 'green' : 'red'}>
                    Base {status.database.url_scheme} {status.database.ok ? 'OK' : 'KO'}
                  </Badge>
                  <Badge variant="light" color={status.disk.alert ? 'red' : 'green'}>
                    Disque {status.disk.free_gb} / {status.disk.total_gb} Go libres
                  </Badge>
                  {status.indigo && (
                    <Badge variant="light"
                      color={status.indigo.in_bank > 0 && status.indigo.in_bank === status.indigo.published ? 'green' : 'orange'}>
                      Exercices publiés : {status.indigo.in_bank} en banque / {status.indigo.published}
                      {' '}({status.indigo.source === 'image' ? 'dépôt' : 'volume'}
                      {status.indigo.generated_at ? `, ${status.indigo.generated_at.slice(0, 16).replace('T', ' ')}` : ''})
                    </Badge>
                  )}
                </Group>
                <Text size="xs" c="dimmed" mt="sm">
                  Version {status.version}
                  {status.build?.sha && status.build.sha !== 'dev' &&
                    ` — API build ${status.build.sha}${status.build.time ? ` (${status.build.time})` : ''}`}
                  {webBuild?.sha && webBuild.sha !== 'dev' &&
                    ` · Web build ${webBuild.sha}`}
                  {status.build?.sha && webBuild?.sha && status.build.sha !== webBuild.sha &&
                    ' — ⚠ web et API sur des builds différents (mise à jour en cours ou incomplète)'}
                </Text>
              </SettingsSection>
            )}
            <SettingsSection title="Sauvegardes"
              description="Dump de la base dans /data/backups — rétention 30 fichiers."
              action={<Button size="xs" onClick={doBackup}>Sauvegarder maintenant</Button>}>
              {backups.length === 0
                ? <Text size="sm" c="dimmed">Aucune sauvegarde.</Text>
                : <Table verticalSpacing={4}>
                    <Table.Tbody>
                      {backups.slice(0, 5).map((b) => (
                        <Table.Tr key={b.name}>
                          <Table.Td><Text size="xs" ff="monospace">{b.name}</Text></Table.Td>
                          <Table.Td ta="right"><Text size="xs" c="dimmed">{(b.size / 1024).toFixed(0)} Ko</Text></Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>}
            </SettingsSection>
          </Stack>
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="journaux" {...pp}>
          <Stack>
            <SettingsSection title="Journal des erreurs serveur"
              description="Les 50 dernières erreurs 500 avec leur trace complète — pour diagnostiquer un bug (ex. « Valider la correction »). Reproduis l'erreur, puis actualise."
              action={<Group gap="xs">
                <Button size="xs" variant="light" leftSection={<RefreshCw size={14} />}
                  loading={logsLoading} onClick={refreshLogs}>Actualiser</Button>
                <Button size="xs" variant="subtle" color="red" leftSection={<Trash2 size={14} />}
                  onClick={clearLogs} disabled={logs.length === 0}>Vider</Button>
              </Group>} />
            {logs.length === 0 ? (
              <Text size="sm" c="dimmed" py="md">
                Aucune erreur enregistrée. Clique « Actualiser » après avoir reproduit le problème.
              </Text>
            ) : logs.map((l, i) => (
              <Card key={i} withBorder>
                <Group gap="xs" mb={4} wrap="nowrap">
                  <Badge color="red" variant="light">{l.method}</Badge>
                  <Text size="sm" ff="monospace" style={{ wordBreak: 'break-all' }}>{l.path}</Text>
                  <Text size="xs" c="dimmed" ml="auto" style={{ flexShrink: 0 }}>
                    {new Date(l.ts).toLocaleString('fr-FR')}
                  </Text>
                </Group>
                <Text size="sm" c="red" fw={600} mb={4} style={{ wordBreak: 'break-word' }}>
                  {l.error}
                </Text>
                <Text component="pre" size="xs" ff="monospace" c="dimmed"
                  style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0,
                    maxHeight: 260, overflow: 'auto' }}>
                  {l.traceback}
                </Text>
              </Card>
            ))}
          </Stack>
        </Tabs.Panel>}

        {adminPage && isAdmin && <Tabs.Panel value="donnees" {...pp}>
          <Stack>
            <Alert color="red" variant="light" icon={<AlertTriangle size={16} />}>
              Suppression définitive et irréversible — aucune corbeille. Supprimer une classe
              ou un sujet emporte tout ce qui en dépend (élèves, copies, corrections, scans,
              overlays, PDF/images sur le disque) : aucune donnée orpheline n'est laissée.
            </Alert>

            {overview && (
              <SimpleGrid cols={{ base: 3, sm: 6 }} spacing="xs">
                {[
                  ['Classes', overview.totals.classes],
                  ['Élèves', overview.totals.students],
                  ['Sujets', overview.totals.assessments],
                  ['Corrections', overview.totals.corrections],
                  ['Banque', overview.totals.bank_exercises],
                  ['Orphelins', overview.totals.orphans],
                ].map(([label, n]) => (
                  <Card key={label as string} withBorder padding="xs"
                    style={label === 'Orphelins' && (n as number) > 0
                      ? { borderColor: 'var(--mantine-color-orange-5)' } : undefined}>
                    <Text size="xl" fw={700} lh={1.1}
                      c={label === 'Orphelins' && (n as number) > 0 ? 'orange' : undefined}>{n}</Text>
                    <Text size="xs" c="dimmed">{label}</Text>
                  </Card>
                ))}
              </SimpleGrid>
            )}

            <SettingsSection title="Scans originaux"
              description="Chaque fichier déposé (bac à sable, dépôt ciblé, mail) est conservé tel quel pendant 30 jours, même si sa correction est supprimée — puis effacé automatiquement : ce sont des copies d'élèves.">
              {scanOriginals.length === 0 ? (
                <Text size="sm" c="dimmed">Aucun scan conservé.</Text>
              ) : (
                <Table>
                  <Table.Tbody>
                    {scanOriginals.map((f) => (
                      <Table.Tr key={f.name}>
                        <Table.Td>{f.filename}</Table.Td>
                        <Table.Td w={90}>{(f.size / 1e6).toFixed(1)} Mo</Table.Td>
                        <Table.Td w={150}>{new Date(f.created).toLocaleString('fr-FR')}</Table.Td>
                        <Table.Td w={170}><Text size="xs" c="dimmed">
                          effacé le {new Date(f.expires).toLocaleDateString('fr-FR')}</Text></Table.Td>
                        <Table.Td w={36}>
                          <ActionIcon variant="subtle" aria-label="Télécharger le scan original"
                            onClick={() => downloadScanOriginal(f)}><Download size={14} /></ActionIcon>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </SettingsSection>

            <SettingsSection title="Données orphelines"
              tone={overview && overview.totals.orphans > 0 ? 'warning' : 'default'}
              action={overview && overview.totals.orphans > 0 ? (
                <Button color="orange" variant="outline" size="xs"
                  leftSection={<Trash2 size={14} />} onClick={() => setOrphansPurgeOpen(true)}>
                  Nettoyer ({overview.totals.orphans})
                </Button>) : undefined}>
              <div>
                  {overview && overview.orphans.length === 0 ? (
                    <Text size="sm" c="dimmed">Aucune donnée orpheline — la base est propre.</Text>
                  ) : (
                    <Stack gap={2}>
                      {overview?.orphans.map((o) => (
                        <Text key={o.label} size="xs" c="dimmed">
                          <b>{o.count}</b> — {o.label}
                        </Text>
                      ))}
                    </Stack>
                  )}
              </div>
            </SettingsSection>

            <SettingsSection title="Banque d'exercices" tone="danger"
              description="Supprime TOUS les exercices de la banque (quelle que soit leur source) ainsi que l'état d'extraction Sésamaths déjà en cache — pour repartir d'une banque vide et propre si des exercices étranges ou répétés s'y sont accumulés. La prochaine génération réextrait tout depuis le manuel."
              action={<Button color="red" variant="outline" size="xs" leftSection={<Trash2 size={14} />}
                onClick={() => setPurgeConfirmOpen(true)}>
                Purger toute la banque
              </Button>} />

            <Text fw={650} mt="xs">Par classe</Text>
            <Accordion multiple variant="separated" value={openClasses}
              onChange={(v) => {
                const opened = v as string[]
                opened.filter((id) => !classDetail[id]).forEach(loadClassDetail)
                setOpenClasses(opened)
              }}>
              {overview?.classes.map((c) => {
                const d = classDetail[c.id]
                return (
                  <Accordion.Item key={c.id} value={c.id}>
                    <Accordion.Control>
                      <Group gap={8} wrap="nowrap">
                        <Text fw={600} size="sm">{c.name}</Text>
                        <Badge size="xs" variant="light">{c.grade_level}</Badge>
                        {c.archived && <Badge size="xs" color="gray">archivée</Badge>}
                        <Text size="xs" c="dimmed">
                          {c.students} élève(s) · {c.assessments} sujet(s) · {c.corrections} correction(s)
                        </Text>
                      </Group>
                    </Accordion.Control>
                    <Accordion.Panel>
                      <Group justify="flex-end" mb="xs">
                        <Button color="red" variant="light" size="compact-xs"
                          leftSection={<Trash2 size={13} />}
                          onClick={() => setConfirmTarget({
                            kind: 'classes', id: c.id,
                            label: `la classe « ${c.name} » (${c.students} élève(s), ${c.assessments} sujet(s), et toutes leurs corrections)`,
                          })}>
                          Supprimer la classe entière
                        </Button>
                      </Group>
                      {!d ? <Group justify="center" p="md"><Loader size="sm" /></Group> : (
                        <Stack gap="md">
                          <div>
                            <Text size="xs" fw={600} c="dimmed" mb={4}>Élèves ({d.students.length})</Text>
                            <Table>
                              <Table.Tbody>
                                {d.students.map((s) => (
                                  <Table.Tr key={s.id}>
                                    <Table.Td>{s.name}</Table.Td>
                                    <Table.Td w={70}>{s.copy_count} copie(s)</Table.Td>
                                    <Table.Td w={80}>{s.active
                                      ? <Badge size="xs" color="green" variant="light">actif</Badge>
                                      : <Badge size="xs" color="gray" variant="light">inactif</Badge>}</Table.Td>
                                    <Table.Td w={36}>
                                      <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                                        kind: 'students', id: s.id,
                                        label: `l'élève « ${s.name} » (${s.copy_count} copie(s))`,
                                      })}><Trash2 size={14} /></ActionIcon>
                                    </Table.Td>
                                  </Table.Tr>
                                ))}
                                {d.students.length === 0 && <Table.Tr><Table.Td><Text size="xs" c="dimmed">Aucun élève.</Text></Table.Td></Table.Tr>}
                              </Table.Tbody>
                            </Table>
                          </div>
                          <div>
                            <Text size="xs" fw={600} c="dimmed" mb={4}>Sujets ({d.assessments.length})</Text>
                            <Table>
                              <Table.Tbody>
                                {d.assessments.map((a) => (
                                  <Table.Tr key={a.id}>
                                    <Table.Td>{a.title}</Table.Td>
                                    <Table.Td w={90}><Badge size="xs" variant="light">{a.status}</Badge></Table.Td>
                                    <Table.Td w={130}>{a.copy_count} copie(s) · {a.scan_batch_count} corr.</Table.Td>
                                    <Table.Td w={36}>
                                      <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                                        kind: 'assessments', id: a.id,
                                        label: `le sujet « ${a.title} » (${a.copy_count} copie(s), ${a.scan_batch_count} correction(s), scans et overlays)`,
                                      })}><Trash2 size={14} /></ActionIcon>
                                    </Table.Td>
                                  </Table.Tr>
                                ))}
                                {d.assessments.length === 0 && <Table.Tr><Table.Td><Text size="xs" c="dimmed">Aucun sujet.</Text></Table.Td></Table.Tr>}
                              </Table.Tbody>
                            </Table>
                          </div>
                          <div>
                            <Text size="xs" fw={600} c="dimmed" mb={4}>Corrections ({d.corrections.length})</Text>
                            <Table>
                              <Table.Tbody>
                                {d.corrections.map((b) => (
                                  <Table.Tr key={b.id}>
                                    <Table.Td>{b.assessment_title}</Table.Td>
                                    <Table.Td w={90}><Badge size="xs" variant="light">{b.status}</Badge></Table.Td>
                                    <Table.Td w={80}>{b.page_count} page(s)</Table.Td>
                                    <Table.Td w={36}>
                                      <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                                        kind: 'corrections', id: b.id,
                                        label: `la correction du sujet « ${b.assessment_title} » (${b.page_count} page(s) scannée(s) : scans, images recadrées, notes et copies corrigées)`,
                                      })}><Trash2 size={14} /></ActionIcon>
                                    </Table.Td>
                                  </Table.Tr>
                                ))}
                                {d.corrections.length === 0 && <Table.Tr><Table.Td><Text size="xs" c="dimmed">Aucune correction.</Text></Table.Td></Table.Tr>}
                              </Table.Tbody>
                            </Table>
                          </div>
                        </Stack>
                      )}
                    </Accordion.Panel>
                  </Accordion.Item>
                )
              })}
            </Accordion>
          </Stack>
        </Tabs.Panel>}

      </Tabs>

      <Modal opened={!!confirmTarget} onClose={() => setConfirmTarget(null)}
        title={<Text fw={650}>Confirmer la suppression</Text>}>
        <Stack>
          <Text size="sm">Supprimer définitivement {confirmTarget?.label} ?</Text>
          {confirmTarget?.kind === 'corrections' && (
            <Checkbox checked={purgeTracking}
              onChange={(e) => setPurgeTracking(e.currentTarget.checked)}
              label="Supprimer aussi les données de suivi des élèves issues de cette correction"
              description="Preuves de compétence, courbe d'oubli, niveaux calculés et appréciations : les élèves reviennent à leur suivi d'avant cette correction." />
          )}
          <Text size="xs" c="dimmed">Cette action est irréversible, y compris les fichiers stockés.</Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setConfirmTarget(null)}>Annuler</Button>
            <Button color="red" loading={deleting} onClick={confirmDelete}>Supprimer définitivement</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={purgeConfirmOpen} onClose={() => setPurgeConfirmOpen(false)}
        title={<Text fw={650}>Confirmer la purge de la banque</Text>}>
        <Stack>
          <Text size="sm">
            Supprimer définitivement TOUS les exercices de la banque (toutes sources) et
            réinitialiser l'état d'extraction Sésamaths ?
          </Text>
          <Text size="xs" c="dimmed">
            Cette action est irréversible. La prochaine génération réextrait tout depuis le
            manuel — les premières copies après la purge seront plus lentes à générer.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setPurgeConfirmOpen(false)}>Annuler</Button>
            <Button color="red" loading={purging} onClick={purgeBank}>Purger toute la banque</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={orphansPurgeOpen} onClose={() => setOrphansPurgeOpen(false)}
        title={<Text fw={650}>Nettoyer les données orphelines</Text>}>
        <Stack>
          <Text size="sm">
            Supprimer définitivement toutes les lignes pointant vers un parent disparu
            (et les fichiers orphelins sur le disque) ?
          </Text>
          <Text size="xs" c="dimmed">
            N'affecte que des restes incohérents — jamais une donnée encore rattachée à une
            classe, un élève ou un sujet existant.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setOrphansPurgeOpen(false)}>Annuler</Button>
            <Button color="orange" loading={purgingOrphans} onClick={purgeOrphans}>
              Nettoyer
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
