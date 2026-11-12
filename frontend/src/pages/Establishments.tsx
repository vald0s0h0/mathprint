import { Alert, Badge, Button, Card, Checkbox, Group, Modal, MultiSelect, Select, SimpleGrid, Stack, Table, Tabs, Text, Textarea, TextInput, Title, UnstyledButton } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { AlertTriangle, Building2, Check, Copy, Plus, RefreshCw, Truck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import ClassEditor, { type Catalog, type ClassRow, type SchoolRow, type TeacherRow, type Teaching, type YearRow, options, showError } from '../components/ClassEditor'
import PrintButton from '../components/PrintButton'
import PdfPreviewModal from '../components/PdfPreview'
import { useAppState } from '../state/AppState'
import SubjectWizard from './subjects/SubjectWizard'
import styles from './Establishments.module.css'

type Work = { id: string; title: string; class_id: string; class_name: string; school_id: string; school_name: string; teacher_name: string; subject_name: string; stage: string; status: string; duplex: boolean; service_active: boolean; service_mode: string; copy_count: number; absent_count: number; batch_id: string | null; error: string | null; delivery_due_at: string | null; note: string; subjects_delivered_at: string | null; at_school: boolean; days_at_school: number | null; collection_overdue: boolean }
type Dashboard = { counts: Record<string, number>; assessments: Work[]; lots_at_schools: number; overdue_collection_count: number }
const stages: Record<string, { label: string; color: string }> = {
  prepare: { label: 'À préparer', color: 'gray' }, generating: { label: 'En génération', color: 'blue' },
  print: { label: 'Sujets à imprimer', color: 'indigo' }, deliver: { label: 'À livrer', color: 'cyan' },
  collect: { label: 'À récupérer', color: 'yellow' }, scan: { label: 'À scanner', color: 'orange' },
  correct: { label: 'À corriger', color: 'grape' }, overlay: { label: 'Overlays à imprimer', color: 'violet' },
  return: { label: 'À restituer', color: 'teal' }, complete: { label: 'Terminé', color: 'green' }, blocked: { label: 'À débloquer', color: 'red' },
}
const emptySchool = { name: '', country_id: 'CI', program_ids: ['ci'], city: '', address: '', contact: '', notes: '', service_active: true }
const emptyTeacher = { name: '', school_ids: [] as string[], email: '', phone: '', active: true }

// Deux pages sur le même écran :
//   • mode="atelier" (/atelier) : suivi logistique des sujets, de la préparation
//     à la restitution (admin et rôle Atelier) ;
//   • mode="admin" (/admin/etablissements) : établissements, professeurs,
//     classes et groupes, années, enseignements (administrateur seul).
export default function Establishments({ mode }: { mode: 'atelier' | 'admin' }) {
  const atelier = mode === 'atelier'
  const navigate = useNavigate()
  // L'établissement filtré se choisit dans la barre du haut (HeaderFilters).
  const { school: schoolId, setSchool: setSchoolId, setSchools: shareSchools } = useAppState()
  const [catalog, setCatalog] = useState<Catalog | null>(null); const [schools, setSchools] = useState<SchoolRow[]>([])
  const [teachers, setTeachers] = useState<TeacherRow[]>([]); const [classes, setClasses] = useState<ClassRow[]>([])
  const [years, setYears] = useState<YearRow[]>([]); const [role, setRole] = useState('')
  const [stage, setStage] = useState<string | null>(null); const [search, setSearch] = useState(''); const [tab, setTab] = useState<string | null>(atelier ? 'work' : 'schools')
  const [dashboard, setDashboard] = useState<Dashboard>({ counts: {}, assessments: [], lots_at_schools: 0, overdue_collection_count: 0 }); const [error, setError] = useState('')
  const [schoolModal, setSchoolModal] = useState(false); const [schoolEdit, setSchoolEdit] = useState<string | null>(null); const [schoolForm, setSchoolForm] = useState(emptySchool)
  const [teacherModal, setTeacherModal] = useState(false); const [teacherEdit, setTeacherEdit] = useState<string | null>(null); const [teacherForm, setTeacherForm] = useState(emptyTeacher)
  const [classModal, setClassModal] = useState(false); const [classEdit, setClassEdit] = useState<ClassRow | null>(null)
  const [wizard, setWizard] = useState(false); const [wizardClass, setWizardClass] = useState<ClassRow | null>(null)
  const [inviteUrl, setInviteUrl] = useState(''); const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false); const [yearLabel, setYearLabel] = useState('')
  const [rollClass, setRollClass] = useState<ClassRow | null>(null); const [rollYear, setRollYear] = useState<string | null>(null)
  const [rollName, setRollName] = useState(''); const [rollGrade, setRollGrade] = useState('6e'); const [rollSeries, setRollSeries] = useState<string | null>(null)
  const [assignClass, setAssignClass] = useState<ClassRow | null>(null); const [teachings, setTeachings] = useState<Teaching[]>([])
  const [assignSubject, setAssignSubject] = useState('math'); const [assignTeacher, setAssignTeacher] = useState<string | null>(null)
  const [groupTeaching, setGroupTeaching] = useState<string | null>(null); const [groupPath, setGroupPath] = useState<string | null>(null)
  const [groupName, setGroupName] = useState(''); const [groupStudents, setGroupStudents] = useState<string[]>([]); const [students, setStudents] = useState<{ id: string; name: string }[]>([])
  const [noteWork, setNoteWork] = useState<Work | null>(null); const [note, setNote] = useState(''); const [due, setDue] = useState('')
  const [newSubjectName, setNewSubjectName] = useState(''); const [newSubjectId, setNewSubjectId] = useState('')
  const [newPathName, setNewPathName] = useState(''); const [newPathProgram, setNewPathProgram] = useState('ci'); const [newPathGrades, setNewPathGrades] = useState<string[]>([])

  const refreshLists = useCallback(async () => {
    const [c, s, t, cl, y, me] = await Promise.all([api.get<Catalog>('/api/operations/catalog'),
      api.get<SchoolRow[]>('/api/operations/schools'), api.get<TeacherRow[]>('/api/operations/teachers'),
      api.get<ClassRow[]>('/api/classes?include_archived=true'), api.get<YearRow[]>('/api/years'), api.get<{ role: string }>('/api/auth/me')])
    setCatalog(c); setSchools(s); shareSchools(s); setTeachers(t); setClasses(cl); setYears(y); setRole(me.role)
  }, [shareSchools])
  const refreshWork = useCallback(async () => {
    if (!atelier) return
    try {
      const q = new URLSearchParams(); if (schoolId) q.set('school_id', schoolId)
      setDashboard(await api.get<Dashboard>(`/api/operations/dashboard?${q}`)); setError('')
    } catch (e) { setError((e as Error).message) }
  }, [schoolId, atelier])
  useEffect(() => { refreshLists().catch(showError) }, [refreshLists])
  useEffect(() => {
    if (!atelier) return
    void refreshWork(); const timer = setInterval(refreshWork, 5000); return () => clearInterval(timer)
  }, [refreshWork, atelier])
  async function act(fn: () => Promise<unknown>, after?: () => void) {
    setBusy(true)
    try { await fn(); await refreshLists(); await refreshWork(); after?.() }
    catch (e) { showError(e) } finally { setBusy(false) }
  }
  const school = schools.find(s => s.id === schoolId)
  // Une classe sans établissement est celle d'un professeur autonome : pas supervisée ici.
  const visibleClasses = classes.filter(c => c.school_id && (!schoolId || c.school_id === schoolId) && !c.archived)
  const work = dashboard.assessments.filter(a => (!stage || (stage === 'collect' ? a.at_school : a.stage === stage)) && `${a.title} ${a.class_name} ${a.teacher_name} ${a.school_name}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => stage === 'collect' ? (b.days_at_school ?? -1) - (a.days_at_school ?? -1) : 0)
  function newSubject(c?: ClassRow) { setWizardClass(c || null); setWizard(true) }
  function editSchool(s?: SchoolRow) { setSchoolEdit(s?.id || null); setSchoolForm(s ? { ...s } : { ...emptySchool }); setSchoolModal(true) }
  function editTeacher(t?: TeacherRow) { setTeacherEdit(t?.id || null); setTeacherForm(t ? { ...t } : { ...emptyTeacher, school_ids: schoolId ? [schoolId] : [] }); setTeacherModal(true) }
  async function openAssignments(c: ClassRow) {
    setAssignClass(c); setTeachings(await api.get<Teaching[]>(`/api/operations/classes/${c.id}/teachings`))
    setStudents(await api.get<{ id: string; name: string }[]>(`/api/classes/${c.id}/students`))
    setAssignTeacher(null); setGroupTeaching(null); setGroupPath(null); setGroupStudents([]); setGroupName('')
  }
  function tracking(a: Work, values: object) { return act(() => api.patch(`/api/operations/assessments/${a.id}`, values)) }

  return <Stack gap="lg">
    {atelier
      ? <Group justify="flex-end"><Button variant="subtle" leftSection={<RefreshCw size={15} />} onClick={() => { void refreshWork(); refreshLists().catch(showError) }}>Actualiser</Button>
        <Button leftSection={<Plus size={16} />} onClick={() => newSubject()} disabled={!visibleClasses.some(c => !c.archived) || school?.service_active === false}>Créer un sujet</Button></Group>
      : <Group justify="space-between"><div><Group gap="sm"><Building2 size={26} /><Title order={2}>Établissements</Title></Group>
        <Text c="dimmed" size="sm" mt={4}>Établissements, professeurs, classes et années scolaires du centre.</Text></div>
        <Button variant="subtle" leftSection={<RefreshCw size={15} />} onClick={() => { refreshLists().catch(showError) }}>Actualiser</Button></Group>}
    {error && <Alert color="red">{error}</Alert>}
    {school && <Group align="end">
      <Badge color={school.service_active ? 'green' : 'orange'}>{school.service_active ? 'Service actif' : 'Service suspendu'}</Badge>
      {!atelier && <Button size="xs" variant="light" onClick={() => editSchool(school)}>Modifier l’établissement</Button>}
    </Group>}
    {school?.service_active === false && <Alert color="orange">Service suspendu : les nouvelles générations et impressions sont bloquées. Les données et corrections restent accessibles.</Alert>}
    <Tabs value={tab} onChange={setTab}>
      {!atelier && <Tabs.List><Tabs.Tab value="schools">Établissements</Tabs.Tab>
        <Tabs.Tab value="teachers">Professeurs</Tabs.Tab><Tabs.Tab value="classes">Classes et groupes</Tabs.Tab><Tabs.Tab value="years">Années scolaires</Tabs.Tab>
        {role === 'admin' && <Tabs.Tab value="catalog">Enseignements</Tabs.Tab>}</Tabs.List>}
      <Tabs.Panel value="work" pt={0}><Stack>
        <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing="sm" role="group" aria-label="Étapes du suivi">
          {['all', 'prepare', 'print', 'deliver', 'collect', 'correct', 'return'].map(key => {
            const selected = key === 'all' ? stage === null : stage === key
            const count = key === 'all' ? dashboard.assessments.length : key === 'collect' ? dashboard.lots_at_schools : dashboard.counts[key] || 0
            return <UnstyledButton key={key} className={styles.stageCard} data-selected={selected || undefined} aria-pressed={selected}
              onClick={() => setStage(key === 'all' ? null : key)}>
              <Text size="xs" c={selected ? 'indigo' : 'dimmed'} fw={selected ? 700 : 400}>{key === 'all' ? 'Tous les lots' : stages[key].label}</Text>
              <Text size="xl" fw={750}>{count}</Text>
              {key === 'collect' && <Text size="xs" c="dimmed">Lots dans les établissements</Text>}
              {key === 'collect' && dashboard.overdue_collection_count > 0 && <Text size="xs" c="red" fw={600}>{dashboard.overdue_collection_count} depuis plus de 7 jours</Text>}
            </UnstyledButton>
          })}
        </SimpleGrid>
        {dashboard.overdue_collection_count > 0 && <Alert color="red" icon={<AlertTriangle size={18} />} title="Récupération des copies à prévoir" role="status">
          <Group justify="space-between" align="center">
            <Text size="sm">{dashboard.overdue_collection_count} lot{dashboard.overdue_collection_count > 1 ? 's sont présents' : ' est présent'} dans les établissements depuis plus d’une semaine après la livraison des sujets.</Text>
            <Button size="xs" variant="light" color="red" onClick={() => { setStage('collect'); setSearch('') }}>Voir les lots à récupérer</Button>
          </Group>
        </Alert>}
        <Group><Select placeholder="Toutes les étapes" clearable data={Object.entries(stages).map(([value, s]) => ({ value, label: `${s.label} (${value === 'collect' ? dashboard.lots_at_schools : dashboard.counts[value] || 0})` }))} value={stage} onChange={setStage} w={260} />
          <TextInput placeholder="Rechercher un sujet, une classe, un professeur…" value={search} onChange={e => setSearch(e.currentTarget.value)} style={{ flex: 1 }} />
          <Button variant="light" onClick={() => navigate('/atelier/corrections')}>Déposer les scans</Button></Group>
        {!work.length && <Card withBorder p="xl"><Text c="dimmed" ta="center">{dashboard.assessments.length ? 'Aucun lot ne correspond aux filtres sélectionnés.' : 'Aucun sujet dans cette sélection. Créez un établissement, un professeur et une classe pour commencer.'}</Text></Card>}
        {work.map(a => <Card withBorder key={a.id} p="md"><Group justify="space-between" align="start" wrap="wrap">
          <Stack gap={4} style={{ flex: 1, minWidth: 240 }}><Group><Text fw={700}>{a.title}</Text><Badge color={stages[a.stage].color} variant="light">{stages[a.stage].label}</Badge></Group>
            <Text size="sm">{a.school_name} · {a.class_name} · {a.subject_name}</Text>
            <Text size="xs" c="dimmed">{a.teacher_name} · {a.copy_count} copies{a.absent_count > 0 ? ` · ${a.absent_count} absents` : ''}</Text>
            {a.at_school && a.subjects_delivered_at && <Group gap="xs">
              <Text size="xs" c={a.collection_overdue ? 'red' : 'dimmed'} fw={a.collection_overdue ? 600 : 400}>
                Dans l’établissement depuis {a.days_at_school} jour{a.days_at_school === 1 ? '' : 's'} · livré le {new Date(a.subjects_delivered_at).toLocaleDateString('fr-FR')}
              </Text>
              {a.collection_overdue && <Badge size="xs" color="red" variant="light">Plus d’une semaine</Badge>}
            </Group>}
            {a.delivery_due_at && <Text size="xs" c={new Date(a.delivery_due_at) < new Date() && a.stage !== 'complete' ? 'red' : 'dimmed'}>Livraison prévue le {new Date(a.delivery_due_at).toLocaleDateString('fr-FR')}</Text>}
            {a.note && <Text size="sm">{a.note}</Text>}{a.error && <Alert color="red" p="xs">{a.error}</Alert>}
          </Stack>
          <Group gap="xs">
            {['print', 'deliver', 'collect', 'scan', 'correct', 'overlay', 'return', 'complete'].includes(a.stage) && <Button size="xs" variant="subtle" onClick={() => setPreview(a.id)}>Aperçu</Button>}
            {a.service_active && ['print', 'deliver'].includes(a.stage) && <PrintButton assessmentId={a.id} file="subject_batch.pdf" label={a.service_mode === 'accompanied' ? 'Imprimer sujets + intercalaire' : 'Imprimer les sujets'} assessmentDuplex={a.duplex} />}
            {a.stage === 'deliver' && <Button size="xs" leftSection={<Truck size={14} />} loading={busy} onClick={() => tracking(a, { subjects_delivered: true })}>Sujets livrés</Button>}
            {a.at_school && <Button size="xs" loading={busy} onClick={() => tracking(a, { originals_received: true })}>Copies récupérées</Button>}
            {['scan', 'correct', 'blocked'].includes(a.stage) && <Button size="xs" variant="light" onClick={() => navigate(`/atelier/corrections?assessment=${a.id}`)}>Ouvrir la correction</Button>}
            {a.stage === 'prepare' && <Button size="xs" variant="light" onClick={() => navigate('/atelier/sujets')}>Ouvrir les sujets</Button>}
            {a.service_active && a.stage === 'overlay' && <PrintButton assessmentId={a.id} file="correction_overlay.pdf" label="Imprimer les overlays" />}
            {a.stage === 'return' && <Button size="xs" color="teal" leftSection={<Check size={14} />} loading={busy} onClick={() => tracking(a, { returned: true })}>Copies restituées</Button>}
            <Button size="xs" variant="subtle" color="gray" onClick={() => { setNoteWork(a); setNote(a.note); setDue(a.delivery_due_at?.slice(0, 10) || '') }}>Suivi</Button>
          </Group></Group></Card>)}
      </Stack></Tabs.Panel>
      <Tabs.Panel value="schools" pt="lg"><Stack><Group justify="space-between"><Text c="dimmed">Pays d’implantation et programmes enseignés sont indépendants.</Text><Button onClick={() => editSchool()}>Ajouter un établissement</Button></Group>
        <SimpleGrid cols={{ base: 1, md: 2 }}>{schools.map(s => <Card key={s.id} withBorder><Group justify="space-between"><Text fw={700}>{s.name}</Text><Badge color={s.service_active ? 'green' : 'orange'}>{s.service_active ? 'Actif' : 'Suspendu'}</Badge></Group>
          <Text size="sm" c="dimmed" my="sm">{catalog?.countries.find(c => c.id === s.country_id)?.name} · {s.city} · {s.class_count} classes</Text>
          <Text size="xs" mb="sm">{s.program_ids.map(p => catalog?.programs.find(c => c.id === p)?.name).join(' · ')}</Text>
          <Group><Button size="xs" variant="light" onClick={() => { setSchoolId(s.id); navigate('/atelier') }}>Voir le suivi</Button><Button size="xs" variant="subtle" onClick={() => editSchool(s)}>Modifier</Button></Group></Card>)}</SimpleGrid>
      </Stack></Tabs.Panel>
      <Tabs.Panel value="teachers" pt="lg"><Stack><Group justify="space-between"><Text c="dimmed">Professeurs accompagnés. Le service fonctionne même sans compte de connexion.</Text><Button onClick={() => editTeacher()}>Ajouter un professeur</Button></Group>
        <Table.ScrollContainer minWidth={750}><Table striped><Table.Thead><Table.Tr>{['Professeur', 'Accès', 'Établissements', 'Actions'].map(x => <Table.Th key={x}>{x}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>
          {teachers.filter(t => !schoolId || t.school_ids.includes(schoolId)).map(t => <Table.Tr key={t.id}><Table.Td><Text fw={600} size="sm">{t.name}</Text><Text size="xs" c="dimmed">{t.email || t.phone}</Text></Table.Td>
            <Table.Td>{!t.active ? 'Suspendu' : t.has_account ? 'Activé' : 'Sans compte'}</Table.Td>
            <Table.Td>{t.school_ids.map(id => schools.find(s => s.id === id)?.name).filter(Boolean).join(', ')}</Table.Td>
            <Table.Td><Group gap="xs"><Button size="xs" variant="subtle" onClick={() => editTeacher(t)}>Modifier</Button>{!t.has_account && t.active && <Button size="xs" variant="light" onClick={() => act(async () => { const r = await api.post<{ path: string }>(`/api/operations/teachers/${t.id}/invitation`); setInviteUrl(window.location.origin + r.path) })}>Lien d’inscription</Button>}</Group></Table.Td></Table.Tr>)}
        </Table.Tbody></Table></Table.ScrollContainer></Stack></Tabs.Panel>
      <Tabs.Panel value="classes" pt="lg"><Stack><Group justify="space-between"><Text c="dimmed">Un professeur par matière. Les groupes permettent de distinguer les enseignements du lycée.</Text><Button onClick={() => { setClassEdit(null); setClassModal(true) }}>Nouvelle classe</Button></Group>
        {visibleClasses.map(c => <Card withBorder key={c.id}><Group justify="space-between"><div><Text fw={700}>{c.name} {c.archived && <Badge color="gray">Archivée</Badge>}</Text><Text size="sm" c="dimmed">{c.school_name} · {c.school_year} · {c.student_count} élèves · {catalog?.programs.find(p => p.id === c.program_id)?.name}{c.series_id ? ` · ${catalog?.paths.find(p => p.id === c.series_id)?.name}` : ''}</Text></div>
          <Group><Button size="xs" onClick={() => newSubject(c)} disabled={c.archived}>Créer un sujet</Button><Button size="xs" variant="light" onClick={() => openAssignments(c).catch(showError)}>Professeurs et groupes</Button>
            <Button size="xs" variant="subtle" onClick={() => { setClassEdit(c); setClassModal(true) }}>Modifier</Button><Button size="xs" variant="subtle" onClick={() => navigate(`/admin/eleves?class=${c.id}`)}>Élèves</Button>
            <Button size="xs" variant="subtle" onClick={() => { setRollClass(c); setRollName(c.name); setRollGrade(c.grade_level); setRollSeries(c.series_id); setRollYear(null) }}>Reprendre la liste</Button></Group></Group></Card>)}
      </Stack></Tabs.Panel>
      <Tabs.Panel value="years" pt="lg"><Stack><Group><TextInput label="Nouvelle année scolaire" placeholder="2027-2028" value={yearLabel} onChange={e => setYearLabel(e.currentTarget.value)} /><Button mt={24} loading={busy} disabled={!yearLabel.trim()} onClick={() => act(() => api.post('/api/operations/years', { label: yearLabel }), () => setYearLabel(''))}>Créer</Button></Group>
        <Alert>Archiver une année conserve les sujets et résultats. La reprise d’une liste crée de nouvelles fiches élèves sans transférer leurs niveaux.</Alert>
        {years.map(y => <Card withBorder key={y.id}><Group justify="space-between"><Text>{y.label} · {y.active ? 'Active' : 'Archivée'}</Text>{y.active && <Button color="orange" variant="light" size="xs" onClick={() => { if (window.confirm(`Archiver ${y.label} et ses classes ? Les résultats seront conservés.`)) void act(() => api.post(`/api/operations/years/${y.id}/archive`)) }}>Archiver l’année</Button>}</Group></Card>)}
      </Stack></Tabs.Panel>
      {role === 'admin' && <Tabs.Panel value="catalog" pt="lg"><Stack><Text c="dimmed">La structure est prête ; les compétences, manuels et exercices seront ajoutés séparément.</Text>
        <Card withBorder><Title order={4}>Matières</Title><Text my="sm">{catalog?.subjects.map(s => s.name).join(' · ')}</Text><Group align="end"><TextInput label="Identifiant stable" placeholder="physique" value={newSubjectId} onChange={e => setNewSubjectId(e.currentTarget.value)} /><TextInput label="Nom" value={newSubjectName} onChange={e => setNewSubjectName(e.currentTarget.value)} /><Button onClick={() => act(() => api.post('/api/operations/subjects', { id: newSubjectId, name: newSubjectName }))}>Ajouter</Button></Group></Card>
        <Card withBorder><Title order={4}>Séries</Title><Text size="sm" c="dimmed" my="sm">Les séries proposées dépendent du programme et du cycle. La liste technique initiale est issue de l’annuaire ministériel 2020-2021 et peut être complétée.</Text><Group align="end"><Select label="Programme" data={options(catalog?.programs || [])} value={newPathProgram} onChange={v => setNewPathProgram(v || 'ci')} /><TextInput label="Série" value={newPathName} onChange={e => setNewPathName(e.currentTarget.value)} /><MultiSelect label="Cycles" data={options(catalog?.grades.slice(4) || [])} value={newPathGrades} onChange={setNewPathGrades} /><Button onClick={() => act(() => api.post('/api/operations/paths', { program_id: newPathProgram, name: newPathName, grades: newPathGrades }))}>Ajouter</Button></Group></Card>
      </Stack></Tabs.Panel>}
    </Tabs>
    <ClassEditor supervised opened={classModal} onClose={() => setClassModal(false)} onSaved={() => { refreshLists().catch(showError) }} schoolId={schoolId || undefined} editing={classEdit} />
    <SubjectWizard opened={wizard} classes={(wizardClass ? [wizardClass] : visibleClasses).filter(c => !c.archived)} onClose={() => setWizard(false)} onCreated={() => { setWizard(false); void refreshWork() }} />
    <PdfPreviewModal assessmentId={preview} opened={!!preview} onClose={() => setPreview(null)} />
    <Modal opened={schoolModal} onClose={() => setSchoolModal(false)} title={schoolEdit ? 'Modifier l’établissement' : 'Nouvel établissement'} size="lg"><Stack>
      <TextInput label="Nom" value={schoolForm.name} onChange={e => setSchoolForm({ ...schoolForm, name: e.currentTarget.value })} />
      <Select label="Pays" data={options(catalog?.countries || [])} value={schoolForm.country_id} onChange={v => setSchoolForm({ ...schoolForm, country_id: v || 'CI' })} />
      <MultiSelect label="Programmes enseignés" data={options(catalog?.programs || [])} value={schoolForm.program_ids} onChange={v => setSchoolForm({ ...schoolForm, program_ids: v })} />
      {(['city', 'address', 'contact'] as const).map((k, i) => <TextInput key={k} label={['Ville', 'Adresse de livraison', 'Contact'][i]} value={schoolForm[k]} onChange={e => setSchoolForm({ ...schoolForm, [k]: e.currentTarget.value })} />)}
      <Textarea label="Notes" value={schoolForm.notes} onChange={e => setSchoolForm({ ...schoolForm, notes: e.currentTarget.value })} />
      <Checkbox label="Service actif" checked={schoolForm.service_active} onChange={e => setSchoolForm({ ...schoolForm, service_active: e.currentTarget.checked })} />
      <Button loading={busy} disabled={!schoolForm.name.trim()} onClick={() => act(() => schoolEdit ? api.put(`/api/operations/schools/${schoolEdit}`, schoolForm) : api.post('/api/operations/schools', schoolForm), () => setSchoolModal(false))}>Enregistrer</Button>
    </Stack></Modal>
    <Modal opened={teacherModal} onClose={() => setTeacherModal(false)} title={teacherEdit ? 'Modifier le professeur' : 'Nouveau professeur'}><Stack>
      <TextInput label="Nom affiché et imprimé" value={teacherForm.name} onChange={e => setTeacherForm({ ...teacherForm, name: e.currentTarget.value })} />
      <MultiSelect label="Établissements" searchable data={options(schools)} value={teacherForm.school_ids} onChange={v => setTeacherForm({ ...teacherForm, school_ids: v })} />
      <TextInput label="E-mail de contact (facultatif)" value={teacherForm.email} onChange={e => setTeacherForm({ ...teacherForm, email: e.currentTarget.value })} />
      <TextInput label="Téléphone (facultatif)" value={teacherForm.phone} onChange={e => setTeacherForm({ ...teacherForm, phone: e.currentTarget.value })} />
      <Checkbox label="Professeur actif" checked={teacherForm.active} onChange={e => setTeacherForm({ ...teacherForm, active: e.currentTarget.checked })} />
      <Button loading={busy} disabled={!teacherForm.name.trim()} onClick={() => act(() => teacherEdit ? api.put(`/api/operations/teachers/${teacherEdit}`, teacherForm) : api.post('/api/operations/teachers', teacherForm), () => setTeacherModal(false))}>Enregistrer</Button>
    </Stack></Modal>
    <Modal opened={!!inviteUrl} onClose={() => setInviteUrl('')} title="Lien personnel d’inscription"><Stack><Text size="sm">À transmettre manuellement au professeur. Valable 14 jours, une seule fois. Un nouveau lien invalide le précédent.</Text>
      <TextInput readOnly value={inviteUrl} onFocus={e => e.currentTarget.select()} /><Button leftSection={<Copy size={15} />} onClick={() => navigator.clipboard.writeText(inviteUrl).then(() => notifications.show({ message: 'Lien copié' })).catch(showError)}>Copier le lien</Button></Stack></Modal>
    <Modal opened={!!noteWork} onClose={() => setNoteWork(null)} title="Suivi du sujet"><Stack><TextInput type="date" label="Livraison prévue" value={due} onChange={e => setDue(e.currentTarget.value)} /><Textarea label="Note opérationnelle" value={note} onChange={e => setNote(e.currentTarget.value)} /><Button loading={busy} onClick={() => noteWork && act(() => api.patch(`/api/operations/assessments/${noteWork.id}`, { note, delivery_due_at: due ? `${due}T12:00:00Z` : null }), () => setNoteWork(null))}>Enregistrer</Button></Stack></Modal>
    <Modal opened={!!rollClass} onClose={() => setRollClass(null)} title="Reprendre la liste pour une nouvelle année"><Stack><Text size="sm">Les élèves sont recopiés sans leurs niveaux. La classe d’origine reste conservée.</Text>
      <Select label="Année cible" data={years.filter(y => y.active && y.id !== rollClass?.school_year_id).map(y => ({ value: y.id, label: y.label }))} value={rollYear} onChange={setRollYear} />
      <TextInput label="Nom de la nouvelle classe" value={rollName} onChange={e => setRollName(e.currentTarget.value)} /><Select label="Cycle" data={options(catalog?.grades || [])} value={rollGrade} onChange={v => { setRollGrade(v || '6e'); setRollSeries(null) }} />
      <Select label="Série" clearable data={options(catalog?.paths.filter(p => p.kind === 'series' && p.program_id === rollClass?.program_id && p.grades.includes(rollGrade)) || [])} value={rollSeries} onChange={setRollSeries} />
      <Button loading={busy} disabled={!rollYear || !rollName.trim()} onClick={() => rollClass && act(() => api.post(`/api/operations/classes/${rollClass.id}/rollover`, { year_id: rollYear, name: rollName, grade_level: rollGrade, series_id: rollSeries }), () => setRollClass(null))}>Créer la nouvelle classe</Button>
    </Stack></Modal>
    <Modal opened={!!assignClass} onClose={() => setAssignClass(null)} title={`${assignClass?.name} · Enseignements`} size="lg"><Stack>
      {teachings.map(t => <Card withBorder key={t.id}><Text fw={600}>{t.subject_name} · {teachers.find(p => p.id === t.teacher_id)?.name}</Text>{t.groups.map(g => <Text key={g.id} size="sm">{g.name} · {g.student_ids.length} élèves</Text>)}</Card>)}
      <Group grow><Select label="Matière" data={options(catalog?.subjects || [])} value={assignSubject} onChange={v => setAssignSubject(v || 'math')} /><Select label="Professeur" data={options(teachers.filter(t => t.active && t.school_ids.includes(assignClass?.school_id || '')))} value={assignTeacher} onChange={setAssignTeacher} /></Group>
      <Button disabled={!assignTeacher} loading={busy} onClick={() => assignClass && act(() => api.put(`/api/operations/classes/${assignClass.id}/teaching`, { subject_id: assignSubject, teacher_id: assignTeacher }), () => { void openAssignments(assignClass) })}>Affecter le professeur à cette matière</Button>
      {catalog?.paths.some(p => p.kind === 'option' && p.program_id === assignClass?.program_id && p.grades.includes(assignClass.grade_level)) && <>
        <Title order={5}>Créer un groupe d’enseignement</Title><Select label="Matière du groupe" data={teachings.map(t => ({ value: t.id, label: t.subject_name }))} value={groupTeaching} onChange={setGroupTeaching} />
        <Select label="Enseignement" data={options(catalog.paths.filter(p => p.kind === 'option' && p.program_id === assignClass?.program_id && p.grades.includes(assignClass.grade_level)))} value={groupPath} onChange={setGroupPath} />
        <TextInput label="Nom du groupe" placeholder="Ex. Maths expertes" value={groupName} onChange={e => setGroupName(e.currentTarget.value)} /><MultiSelect label="Élèves" searchable data={options(students)} value={groupStudents} onChange={setGroupStudents} />
        <Button disabled={!groupTeaching || !groupPath || !groupName || !groupStudents.length} loading={busy} onClick={() => assignClass && act(() => api.post('/api/operations/groups', { teaching_id: groupTeaching, path_id: groupPath, name: groupName, student_ids: groupStudents }), () => { void openAssignments(assignClass) })}>Créer le groupe</Button>
      </>}
    </Stack></Modal>
  </Stack>
}
