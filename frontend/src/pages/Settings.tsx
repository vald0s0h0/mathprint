// Paramètres (§9.6) : Mon compte, API, Imprimantes, Calibration, Pédagogie,
// Documents (éditeur de templates), Système, Données.
import {
  Accordion, ActionIcon, Alert, Badge, Box, Button, Card, ColorInput, FileButton,
  Group, Loader, Modal, NumberInput, PasswordInput, SimpleGrid, Stack, Switch, Table, Tabs, Text,
  TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Database, FileText, FlaskConical, KeyRound, Mail, Printer,
  RefreshCw, Ruler, ScrollText, Save, SlidersHorizontal, Trash2, UserRound,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, getToken } from '../api'
import MailIntakeSettings from '../components/MailIntakeSettings'
import PrinterSettings, { type PrintersInfo } from '../components/PrinterSettings'
import TemplateEditor from '../components/TemplateEditor'

type Me = { id: string; email: string; display_name: string; role: string }
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
type SystemStatus = {
  version: string; build?: Build
  database: { ok: boolean; url_scheme: string }
  mathalea: { status?: string; mathaleaVersion?: string; exercises?: number }
  disk: { total_gb: number; free_gb: number; alert: boolean }
  last_backup: string | null
}
type LogEntry = { ts: string; method: string; path: string; error: string; traceback: string }

export default function SettingsPage() {
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
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [printers, setPrinters] = useState<PrintersInfo | null>(null)
  const [backups, setBackups] = useState<{ name: string; size: number }[]>([])
  const [calibrations, setCalibrations] = useState<any[]>([])
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [webBuild, setWebBuild] = useState<Build | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [classDetail, setClassDetail] = useState<Record<string, ClassDetail>>({})
  const [openClasses, setOpenClasses] = useState<string[]>([])
  const [confirmTarget, setConfirmTarget] = useState<{ kind: DeleteKind; id: string; label: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [purgeConfirmOpen, setPurgeConfirmOpen] = useState(false)
  const [purging, setPurging] = useState(false)
  const [orphansPurgeOpen, setOrphansPurgeOpen] = useState(false)
  const [purgingOrphans, setPurgingOrphans] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logsLoading, setLogsLoading] = useState(false)

  function refresh() {
    api.get<Me>('/api/auth/me').then(setMe)
    api.get<Provider[]>('/api/settings/providers').then(setProviders)
    api.get<Record<string, any>>('/api/settings/system').then((values) => {
      setSystem(values)
      setOcrThresholdPct(Math.round(Number(values.ocr_confidence_threshold?.value ?? 0.9) * 100))
      setLlmThresholdPct(Math.round(Number(values.llm_confidence_threshold?.value ?? 0.9) * 100))
    })
    api.get<SystemStatus>('/api/system/status').then(setStatus)
    api.get<PrintersInfo>('/api/printers').then(setPrinters)
    api.get<{ name: string; size: number }[]>('/api/system/backups').then(setBackups)
    api.get<any[]>('/api/system/calibration/profiles').then(setCalibrations)
    // build de l'interface web (image nginx) — absent en dev, servi en no-cache
    fetch('/build.json').then((r) => (r.ok ? r.json() : null)).then(setWebBuild)
      .catch(() => setWebBuild(null))
  }
  useEffect(refresh, [])
  useEffect(() => {
    const timer = window.setInterval(() => {
      api.get<PrintersInfo>('/api/printers').then(setPrinters).catch(() => {})
    }, 10_000)
    return () => window.clearInterval(timer)
  }, [])

  // onglet Données : réservé au rôle admin côté API — silencieux si 403.
  // Vue compactée : on ne tire que l'agrégat par classe ; le détail d'une classe
  // (élèves/sujets/corrections) se charge à l'ouverture (loadClassDetail).
  function refreshData(reopen: string[] = openClasses) {
    api.get<Overview>('/api/data/overview')
      .then((o) => { setOverview(o); reopen.forEach(loadClassDetail) })
      .catch(() => {})
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
      await api.del(`/api/data/${confirmTarget.kind}/${confirmTarget.id}`)
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

  async function saveShortcut(field: string, value: string) {
    const key = (value || '').trim().slice(0, 1).toLowerCase()
    if (!key) return
    const cur = system.correction_shortcuts ?? {}
    await api.post('/api/settings/system', {
      key: 'correction_shortcuts', value: { ...cur, [field]: key },
    })
    notifications.show({ color: 'green', message: 'Raccourci enregistré' })
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

  async function syncMathalea() {
    try {
      const r = await api.post<{ created: number; updated: number; competency_mapped: number }>(
        '/api/assessments/exercises/sync-mathalea')
      notifications.show({
        color: 'green',
        message: `MathALÉA : ${r.created} créés, ${r.updated} mis à jour, ${r.competency_mapped} rattachés aux compétences`,
      })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
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

  async function downloadCalibrationPage() {
    const res = await fetch('/api/system/calibration/page', {
      method: 'POST', headers: { Authorization: `Bearer ${getToken()}` },
    })
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'calibration_page.pdf'
    a.click()
  }

  async function uploadCalibrationScan(file: File | null) {
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await api.post<any>('/api/system/calibration/measure', fd)
      notifications.show({
        color: r.verdict === 'ok' ? 'green' : 'orange',
        message: `Échelle ${r.scale_x}×${r.scale_y}, rotation ${r.rotation_deg}° — ${r.verdict}`,
      })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  return (
    <Stack>
      <Title order={2}>Paramètres</Title>
      <Tabs defaultValue="compte" keepMounted={false}
        onChange={(v) => { if (v === 'journaux') refreshLogs() }}>
        <Tabs.List>
          <Tabs.Tab value="compte" leftSection={<UserRound size={15} />}>Mon compte</Tabs.Tab>
          <Tabs.Tab value="api" leftSection={<KeyRound size={15} />}>API</Tabs.Tab>
          <Tabs.Tab value="imprimantes" leftSection={<Printer size={15} />}>Imprimantes</Tabs.Tab>
          <Tabs.Tab value="mail" leftSection={<Mail size={15} />}>Scans par mail</Tabs.Tab>
          <Tabs.Tab value="calibration" leftSection={<Ruler size={15} />}>Calibration</Tabs.Tab>
          <Tabs.Tab value="pedagogie" leftSection={<SlidersHorizontal size={15} />}>Pédagogie</Tabs.Tab>
          <Tabs.Tab value="documents" leftSection={<FileText size={15} />}>Documents</Tabs.Tab>
          <Tabs.Tab value="systeme" leftSection={<Database size={15} />}>Système</Tabs.Tab>
          <Tabs.Tab value="journaux" leftSection={<ScrollText size={15} />}>Journaux</Tabs.Tab>
          <Tabs.Tab value="donnees" leftSection={<Trash2 size={15} />}>Données</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="compte" pt="md">
          <Card withBorder maw={420}>
            {me && (
              <Group justify="space-between" mb="md">
                <div>
                  <Text fw={600}>{me.display_name}</Text>
                  <Text size="sm" c="dimmed">{me.email}</Text>
                </div>
                <Badge variant="light">{me.role}</Badge>
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
        </Tabs.Panel>

        <Tabs.Panel value="api" pt="md">
          <Stack>
            <Text size="sm" c="dimmed">
              Sans clé, un service reste en mode simulé. Les clés sont chiffrées au repos
              et jamais renvoyées intégralement.
            </Text>
            {(['mathpix', 'deepseek-flash', 'deepseek-pro', 'anthropic', 'mistral', 'gemini'] as const).map((p) => {
              const row = providers.find((x) => x.provider === p)
              const labels: Record<string, string> = {
                'deepseek-flash': 'DeepSeek Flash',
                'deepseek-pro': 'DeepSeek Pro',
                mistral: 'Mistral OCR (extraction Sésamaths)',
                gemini: 'Gemini (création d\'exercices)',
              }
              return (
                <Card key={p} withBorder>
                  <Group justify="space-between">
                    <Group>
                      <Text fw={600}>{labels[p] ?? p}</Text>
                      {row?.active && row.secret_preview
                        ? <Badge variant="light" color="green">configuré {row.secret_preview}</Badge>
                        : <Badge variant="light" color="gray"
                            leftSection={<FlaskConical size={11} />}>simulé</Badge>}
                    </Group>
                  </Group>
                  <TextInput mt="sm" label={p === 'mathpix' ? 'app_id:app_key' : 'Clé API'} type="password"
                    onChange={(e) => setEdit({ ...edit, [p]: e.target.value })} />
                  <Button size="xs" mt="sm" onClick={() => save(p)}>Enregistrer</Button>
                </Card>
              )
            })}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="imprimantes" pt="md">
          <PrinterSettings printers={printers} refresh={refresh} />
        </Tabs.Panel>

        <Tabs.Panel value="mail" pt="md">
          <MailIntakeSettings />
        </Tabs.Panel>

        <Tabs.Panel value="calibration" pt="md">
          <Card withBorder>
            <Text fw={600}>Assistant de calibration imprimante/scanner</Text>
            <Text size="sm" c="dimmed" mt="xs">
              1. Télécharger la page test → 2. L'imprimer à 100 % → 3. La scanner →
              4. Déposer le scan : offsets, échelle et rotation sont mesurés sur les 4 marqueurs.
            </Text>
            <Group mt="sm">
              <Button size="xs" onClick={downloadCalibrationPage}>Télécharger la page test</Button>
              <FileButton onChange={uploadCalibrationScan} accept="application/pdf,image/*">
                {(props) => <Button size="xs" variant="light" {...props}>Déposer le scan de la page test</Button>}
              </FileButton>
            </Group>
            {calibrations.length > 0 && (
              <Table mt="md" striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Imprimante</Table.Th><Table.Th>Échelle X/Y</Table.Th>
                    <Table.Th>Rotation</Table.Th><Table.Th>Offset (mm)</Table.Th><Table.Th>Validé</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {calibrations.map((c) => (
                    <Table.Tr key={c.id}>
                      <Table.Td>{c.printer || '—'}</Table.Td>
                      <Table.Td>{c.scale_x} / {c.scale_y}</Table.Td>
                      <Table.Td>{c.rotation_deg}°</Table.Td>
                      <Table.Td>{c.offset_x_mm} / {c.offset_y_mm}</Table.Td>
                      <Table.Td><Text size="xs">{c.validated_at?.slice(0, 16)}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="pedagogie" pt="md">
          <Stack maw={640}>
            <Card withBorder>
              <Text fw={600} mb={4}>Lecture OCR et vision</Text>
              <Text size="xs" c="dimmed" mb="sm">
                Après la lecture des scans, « OCRiser » ne présente que les réponses
                dont la confiance OCR/CV est sous ce seuil, avant toute correction.
              </Text>
              <Group align="flex-end">
                <NumberInput label="Seuil de confiance" suffix=" %" min={1} max={100}
                  step={1} w={190} value={ocrThresholdPct}
                  onChange={setOcrThresholdPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveOcrThreshold() }} />
                <Button leftSection={<Save size={14} />} loading={savingOcrThreshold}
                  onClick={saveOcrThreshold}>Enregistrer</Button>
              </Group>
            </Card>
            <Card withBorder>
              <Text fw={600} mb={4}>Correction DeepSeek</Text>
              <Text size="xs" c="dimmed" mb="sm">
                Une réponse corrigée par le LLM n'est envoyée dans l'assistant de
                correction manuelle que si sa confiance est strictement inférieure
                à ce seuil. La valeur par défaut est 90 %.
              </Text>
              <Group align="flex-end">
                <NumberInput label="Seuil de confiance LLM" suffix=" %" min={1} max={100}
                  step={1} w={210} value={llmThresholdPct}
                  onChange={setLlmThresholdPct}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveLlmThreshold() }} />
                <Button leftSection={<Save size={14} />} loading={savingLlmThreshold}
                  onClick={saveLlmThreshold}>Enregistrer</Button>
              </Group>
            </Card>
            <Card withBorder>
              <Text fw={600} mb={4}>Raccourcis de correction manuelle</Text>
              <Text size="xs" c="dimmed" mb="sm">
                Une touche attribue une fraction des points de l'exercice dans la
                modale « Corriger manuellement » (les touches s'affichent sur les
                boutons). Une seule lettre par action.
              </Text>
              <Group grow>
                <TextInput label="Tous les points" maxLength={1}
                  key={`f-${system.correction_shortcuts?.full ?? 'f'}`}
                  defaultValue={system.correction_shortcuts?.full ?? 'f'}
                  onBlur={(e) => saveShortcut('full', e.currentTarget.value)} />
                <TextInput label="2⁄3 des points" maxLength={1}
                  key={`d-${system.correction_shortcuts?.two_thirds ?? 'd'}`}
                  defaultValue={system.correction_shortcuts?.two_thirds ?? 'd'}
                  onBlur={(e) => saveShortcut('two_thirds', e.currentTarget.value)} />
                <TextInput label="1⁄3 des points" maxLength={1}
                  key={`s-${system.correction_shortcuts?.one_third ?? 's'}`}
                  defaultValue={system.correction_shortcuts?.one_third ?? 's'}
                  onBlur={(e) => saveShortcut('one_third', e.currentTarget.value)} />
                <TextInput label="0 point" maxLength={1}
                  key={`q-${system.correction_shortcuts?.zero ?? 'q'}`}
                  defaultValue={system.correction_shortcuts?.zero ?? 'q'}
                  onBlur={(e) => saveShortcut('zero', e.currentTarget.value)} />
              </Group>
            </Card>
            <Card withBorder>
              <Group justify="space-between" align="flex-start">
                <Box>
                  <Text fw={600} mb={4}>Phrase encourageante (Claude Haiku)</Text>
                  <Text size="xs" c="dimmed" maw={440}>
                    Une phrase courte générée par IA dans la zone Appréciation des copies
                    corrigées, en plus des progrès de compétences (toujours affichés,
                    sans IA). Désactivée par défaut.
                  </Text>
                </Box>
                <Switch
                  checked={system.appreciation_synthesis_enabled?.value ?? false}
                  onChange={(e) => saveAppreciationSynthesis(e.currentTarget.checked)} />
              </Group>
            </Card>
            <Card withBorder>
              <Table>
                <Table.Tbody>
                  <Table.Tr>
                    <Table.Td>Seuil de courbe d'oubli (probabilité de rappel)</Table.Td>
                    <Table.Td>{system.forgetting_threshold?.value ?? 0.8}</Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td>Variation automatique max du niveau (1-10) par cycle de révision</Table.Td>
                    <Table.Td>±1</Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td>Répartition entraînement</Table.Td>
                    <Table.Td>60 % consolidation / 30 % cible / 10 % défi</Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="documents" pt="md">
          <Stack>
            <TemplateEditor />
            <Card withBorder maw={640}>
              <Text fw={600} mb="xs">Couleurs techniques</Text>
              <Group grow>
                <ColorInput size="xs" label="Zones de réponse élève (dropout)"
                  description="Supprimée avant OCR — garder un ton clair"
                  value={system.dropout_color?.value ?? '#F5B7A8'}
                  onChangeEnd={(v) => saveColor('dropout_color', v)} />
                <ColorInput size="xs" label="Encre de correction (overlay)"
                  value={system.correction_color?.value ?? '#C62828'}
                  onChangeEnd={(v) => saveColor('correction_color', v)} />
              </Group>
              <Text size="xs" c="dimmed" mt="sm">
                Figé pour le repérage scanner : QR 24 mm signé HMAC (haut droit),
                3 fiduciels AprilTag 11 mm (coins), marges 9 mm.
              </Text>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="systeme" pt="md">
          <Stack>
            {status && (
              <Card withBorder>
                <Text fw={600} mb="xs">État des services</Text>
                <Group gap="lg">
                  <Badge variant="light" color={status.database.ok ? 'green' : 'red'}>
                    Base {status.database.url_scheme} {status.database.ok ? 'OK' : 'KO'}
                  </Badge>
                  <Badge variant="light" color={status.mathalea.status === 'ok' ? 'green' : 'red'}>
                    MathALÉA {status.mathalea.status === 'ok'
                      ? `v${status.mathalea.mathaleaVersion} (${status.mathalea.exercises} exos)`
                      : 'injoignable'}
                  </Badge>
                  <Badge variant="light" color={status.disk.alert ? 'red' : 'green'}>
                    Disque {status.disk.free_gb} / {status.disk.total_gb} Go libres
                  </Badge>
                </Group>
                <Group mt="sm">
                  <Button size="xs" variant="light" onClick={syncMathalea}>
                    Synchroniser le catalogue MathALÉA
                  </Button>
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
              </Card>
            )}
            <Card withBorder>
              <Group justify="space-between">
                <div>
                  <Text fw={600}>Sauvegardes</Text>
                  <Text size="sm" c="dimmed">
                    Dump de la base dans /data/backups — rétention 30 fichiers.
                  </Text>
                </div>
                <Button size="xs" onClick={doBackup}>Sauvegarder maintenant</Button>
              </Group>
              {backups.slice(0, 5).map((b) => (
                <Group key={b.name} gap="xs" py={1}>
                  <Text size="xs" ff="monospace">{b.name}</Text>
                  <Text size="xs" c="dimmed">{(b.size / 1024).toFixed(0)} Ko</Text>
                </Group>
              ))}
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="journaux" pt="md">
          <Stack>
            <Group justify="space-between">
              <div>
                <Text fw={600}>Journal des erreurs serveur</Text>
                <Text size="sm" c="dimmed">
                  Les 50 dernières erreurs 500 avec leur trace complète — pour diagnostiquer
                  un bug (ex. « Valider la correction »). Reproduis l'erreur, puis actualise.
                </Text>
              </div>
              <Group gap="xs">
                <Button size="xs" variant="light" leftSection={<RefreshCw size={14} />}
                  loading={logsLoading} onClick={refreshLogs}>Actualiser</Button>
                <Button size="xs" variant="subtle" color="red" leftSection={<Trash2 size={14} />}
                  onClick={clearLogs} disabled={logs.length === 0}>Vider</Button>
              </Group>
            </Group>
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
        </Tabs.Panel>

        <Tabs.Panel value="donnees" pt="md">
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

            <Card withBorder style={overview && overview.totals.orphans > 0
              ? { borderColor: 'var(--mantine-color-orange-5)' } : undefined}>
              <Group justify="space-between" align="flex-start">
                <div>
                  <Text fw={600} mb={2}>Données orphelines</Text>
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
                {overview && overview.totals.orphans > 0 && (
                  <Button color="orange" variant="outline" size="xs"
                    leftSection={<Trash2 size={14} />} onClick={() => setOrphansPurgeOpen(true)}>
                    Nettoyer ({overview.totals.orphans})
                  </Button>
                )}
              </Group>
            </Card>

            <Card withBorder style={{ borderColor: 'var(--mantine-color-red-6)' }}>
              <Text fw={600} mb={2}>Banque d'exercices</Text>
              <Text size="sm" c="dimmed" mb="sm">
                Supprime TOUS les exercices de la banque (quelle que soit leur source) ainsi
                que l'état d'extraction Sésamaths déjà en cache — pour repartir d'une banque
                vide et propre si des exercices étranges ou répétés s'y sont accumulés. La
                prochaine génération réextrait tout depuis le manuel.
              </Text>
              <Button color="red" variant="outline" size="xs" leftSection={<Trash2 size={14} />}
                onClick={() => setPurgeConfirmOpen(true)}>
                Purger toute la banque
              </Button>
            </Card>

            <Text fw={600} size="sm" mt="xs">Par classe</Text>
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
        </Tabs.Panel>
      </Tabs>

      <Modal opened={!!confirmTarget} onClose={() => setConfirmTarget(null)}
        title={<Text fw={650}>Confirmer la suppression</Text>}>
        <Stack>
          <Text size="sm">Supprimer définitivement {confirmTarget?.label} ?</Text>
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
