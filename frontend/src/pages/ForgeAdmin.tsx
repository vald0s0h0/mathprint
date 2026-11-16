// Page admin « Forge » : comment la forge écrit ses exercices, du plus général au plus
// spécifique, avec les consignes et contextes lisibles d'un clic, et les commandes pour
// lancer chaque étape depuis Claude Code ou Codex. Les fichiers sont lus en direct par
// /api/admin/forge (lecture seule) : ils n'existent que sur le Mac.
import {
  ActionIcon, Alert, Badge, Box, Button, Checkbox, Code, CopyButton, Drawer, Group, Loader, Menu,
  NumberInput, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, TextInput, Title, Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { ArrowDown, ArrowRight, Check, Copy, FileText } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import MarkdownView from '../components/MarkdownView'

type Tone = 'know' | 'tool' | 'data'
type Doc = { label: string; path: string }
type CardDef = {
  cap: string; name: string; where?: string; size?: string; def?: string; tag?: string
  tone: Tone; dashed?: boolean; docs?: Doc[]; lessons?: boolean; change?: string; added?: string
}
type StepDef = { name: string; def: string; docs?: Doc[]; human?: boolean; added?: boolean }
type Flow = { title: string; lead: string; inputs: (CardDef | string)[]; steps: StepDef[]; outputs: CardDef[] }
type FileOut = { path: string; name: string; kind: 'markdown' | 'json' | 'yaml'; content: string; size: number; modified: string }
type Lesson = { path: string; subject: string; name: string; size: number; facts: number; verified: boolean; modified: string }

const TONES: Record<Tone, { bg: string; border: string }> = {
  know: { bg: 'var(--mantine-color-blue-light)', border: 'var(--mantine-color-blue-filled)' },
  tool: { bg: 'var(--mantine-color-orange-light)', border: 'var(--mantine-color-orange-filled)' },
  data: { bg: 'var(--mantine-color-gray-light)', border: 'var(--mantine-color-gray-6)' },
}

const F = (p: string) => `forge/${p}`
const SUBJECTS: [string, string][] = [['math', 'Mathématiques'], ['pc', 'Physique-chimie'], ['svt', 'SVT'],
  ['francais', 'Français'], ['anglais', 'Anglais'], ['hg', 'Histoire-géographie']]
const CI_LEVELS = ['6e', '5e', '4e', '3e', '2nde-A', '2nde-C', '1re-A1', '1re-A2', '1re-C', '1re-D', 'tle-A1', 'tle-A2', 'tle-C', 'tle-D']

const DOC = {
  consigneExercices: { label: 'CONSIGNE-EXERCICES.md', path: F('CONSIGNE-EXERCICES.md') },
  consigneLecon: { label: 'CONSIGNE-LECON.md', path: F('CONSIGNE-LECON.md') },
  exercice: { label: 'contexte/exercice.md', path: F('contexte/exercice.md') },
  formatsCommun: { label: 'formats/commun.md', path: F('contexte/formats/commun.md') },
  formatsCourants: { label: 'formats/courants.md', path: F('contexte/formats/courants.md') },
  geo: { label: 'formats/geo_construction.md', path: F('contexte/formats/geo_construction.md') },
  figures: { label: 'figures/commun.md', path: F('contexte/figures/commun.md') },
  exemples: { label: 'exemples/math.json', path: F('contexte/exemples/math.json') },
  verificateur: { label: 'prompts/verificateur.md', path: F('prompts/verificateur.md') },
  relecteur: { label: 'prompts/relecteur.md', path: F('prompts/relecteur.md') },
  eleve: { label: 'prompts/eleve.md', path: F('prompts/eleve.md') },
  correction: { label: 'prompts/correction.md', path: F('prompts/correction.md') },
}
const MATIERES: Doc[] = SUBJECTS.map(([id, label]) => ({ label: `${label} (${id}.md)`, path: F(`contexte/matieres/${id}.md`) }))
const ERREURS: Doc[] = SUBJECTS.map(([id, label]) => ({ label: `Erreurs types, ${label.toLowerCase()} (${id}.json)`, path: `erreurs/${id}.json` }))
const KITS: Doc[] = [['math', 'Mathématiques'], ['pc', 'Physique-chimie'], ['svt', 'SVT'], ['hg', 'Histoire-géographie']]
  .map(([id, label]) => ({ label: `${label} (${id}.md)`, path: F(`contexte/figures/${id}.md`) }))
const PAYS: Doc[] = [{ label: 'France (fr.md)', path: F('contexte/pays/fr.md') }, { label: "Côte d'Ivoire (ci.md)", path: F('contexte/pays/ci.md') }]
const PROGRAMMES: Doc[] = CI_LEVELS.map(level => ({ label: `Côte d'Ivoire, ${level}`, path: `programmes/ci/${level}.md` }))

const FLOWS: Flow[] = [
  {
    title: '1. Ouvrir une matière ou un pays',
    lead: "Une fois par matière et par pays. Seules les mathématiques sont ouvertes : la commande d'ouverture viendra avec la septième matière ou le troisième pays.",
    inputs: [
      { cap: 'Consigne', name: "Consigne d'ouverture", where: 'agents/forge/CONSIGNE-MATIERE.md', size: 'à écrire', tone: 'data', dashed: true,
        def: 'Quoi lire, quoi écrire, comment vérifier. Elle servira aussi à revoir une matière existante.' },
      { cap: 'Universel', name: 'Le bon exercice et les 9 archétypes', where: 'contexte/exercice.md + plan.ARCHETYPES', size: '~7 Ko', tone: 'know',
        docs: [DOC.exercice], def: 'Ce que la matière doit décliner. Les archétypes sont générés depuis le code, jamais recopiés.' },
      { cap: 'Programme officiel', name: 'La carte du parcours, de la 6e à la terminale', where: 'docs/programmes/<pays>/', size: '~15 Ko extraits', tone: 'data',
        docs: PROGRAMMES, def: 'Les titres des leçons et les verbes des habiletés. Les contenus détaillés servent à l\'étape 2.' },
      { cap: 'Sources', name: 'Manuels, annales, sujets d\'examen', where: 'PDF', size: 'facultatif', tone: 'data' },
    ],
    steps: [
      { name: 'Opus', def: 'Écrit le contexte matière et les erreurs types, ou le contexte pays.' },
      { name: 'Contrôle par script', def: 'Sections présentes, chaque archétype décliné, codes d\'erreur uniques. Prévu avec la commande.' },
      { name: 'Second avis, contexte neuf', def: 'Opus relit comme un inspecteur de la matière. Prévu avec la commande.', added: true },
      { name: 'Vous', def: 'Relecture complète, en commençant par ce que le second avis conteste.', human: true },
    ],
    outputs: [
      { cap: 'Contexte matière', name: 'Contexte matière, valable pour tous les pays', where: 'contexte/matieres/<m>.md', size: '4 à 10 Ko', tone: 'know', docs: MATIERES,
        def: 'Archétypes de la matière, contre-sens, règles valables dans toutes les leçons, charte d\'écriture.' },
      { cap: 'Exemples', name: 'Cartes validées de la matière', where: 'contexte/exemples/<m>.json', tone: 'know', docs: [DOC.exemples] },
      { cap: 'Contexte de format', name: 'Kit de figures de la matière', where: 'contexte/figures/<m>.md', tone: 'tool', docs: KITS },
      { cap: 'Erreurs types', name: 'Liste fermée des erreurs d\'élèves', where: 'backend/app/data/misconceptions/<m>.json', tone: 'know', docs: ERREURS },
      { cap: 'Contexte pays', name: 'Mots officiels, monnaie, lieux, registres d\'âge', where: 'contexte/pays/<pays>.md', size: '1 à 3 Ko', tone: 'know', docs: PAYS },
    ],
  },
  {
    title: "2. Écrire le contexte d'une leçon",
    lead: 'Commande forge lecon, une fois par leçon, écrite par Opus. Les exercices n\'ont pas d\'autre source de contenu : un fait faux ici donnerait des centaines d\'exercices faux.',
    inputs: [
      { cap: 'Consigne', name: 'Consigne de rédaction d\'une leçon', where: 'agents/forge/CONSIGNE-LECON.md', tone: 'data', docs: [DOC.consigneLecon],
        def: 'Un en-tête court, puis une section par compétence : faits numérotés, erreurs, supports, idées, attendus.' },
      { cap: 'Niveaux au-dessus', name: 'Universel, matière, pays, erreurs types', where: 'fichiers de l\'étape 1', size: '~20 Ko', tone: 'know',
        docs: [DOC.exercice, MATIERES[0], ...PAYS, ERREURS[0]], def: 'Lus pour savoir ce qui est déjà dit plus haut, et ne pas le répéter.' },
      { cap: 'Programme officiel', name: 'Les compétences de la leçon, mot pour mot', where: 'docs/programmes/<pays>/<niveau>.md', size: '~3 Ko', tone: 'data', docs: PROGRAMMES },
      { cap: 'Sources', name: 'Manuel, livre du professeur, annales', where: 'PDF, pages choisies', size: 'facultatif', tone: 'data' },
    ],
    steps: [
      { name: 'Opus', def: 'Écrit la leçon. Chaque fait porte un identifiant et dit s\'il est incertain.' },
      { name: 'Contrôle par script (run.py lecon)', def: 'Une section par compétence, extrait de 10 Ko au plus, idées valides, anti-copie, aucune règle déjà écrite plus haut.' },
      { name: 'Vérificateur de faits, contexte neuf', def: 'Opus juge chaque fait : juste, faux, hors programme ou ambigu. Seuls les faits modifiés sont rejugés.', docs: [DOC.verificateur], added: true },
      { name: 'Vous', def: 'Seulement les faits contestés et les faits incertains (--accepter pour maintenir).', human: true },
    ],
    outputs: [
      { cap: 'Contexte de leçon', name: 'Un fichier par leçon, idées comprises', where: 'data/forge/lecons/<m>/<pays>-<niveau>[-<série>]-<leçon>.md', tone: 'know', lessons: true,
        def: 'Lu par la forge en extrait : en-tête, section de la compétence, fin.' },
      { cap: 'Bilan', name: 'Faits contestés, faits incertains, codes à proposer', where: 'fin de session', tone: 'data' },
    ],
  },
  {
    title: '3. Écrire les exercices',
    lead: 'Une fois par compétence. L\'agent lit du plus général au plus spécifique, un ordre identique pour tous les runs d\'une matière, que le cache resert. Rien qui ne serve pas au lot.',
    inputs: [
      'Universel',
      { cap: 'Consigne', name: 'Procédure de la commande forge', where: 'agents/forge/CONSIGNE-EXERCICES.md', tone: 'data', docs: [DOC.consigneExercices] },
      { cap: 'Universel', name: 'Le bon exercice, familles et jumelles', where: 'contexte/exercice.md', tone: 'know', docs: [DOC.exercice] },
      { cap: 'Contexte de format', name: 'Socle commun et formats courants', where: 'contexte/formats/', tone: 'tool', docs: [DOC.formatsCommun, DOC.formatsCourants] },
      { cap: 'Contexte de format', name: 'Charte des figures et kit de la matière', where: 'contexte/figures/commun.md + <m>.md', tone: 'tool', docs: [DOC.figures, KITS[0]] },
      'Pays et matière',
      { cap: 'Matière', name: 'Contexte matière, exemples validés, erreurs types', where: 'matieres/<m>.md · exemples/<m>.json · misconceptions', tone: 'know',
        docs: [MATIERES[0], DOC.exemples, ERREURS[0]] },
      { cap: 'Pays', name: 'Contexte pays', where: 'contexte/pays/<pays>.md', tone: 'know', docs: PAYS },
      'Leçon et lot',
      { cap: 'Contexte de format', name: 'Géométrie à tracer', where: 'contexte/formats/geo_construction.md', tone: 'tool', dashed: true, docs: [DOC.geo],
        tag: 'seulement si la grille a des tracés' },
      { cap: 'Contexte de leçon', name: 'En-tête et section de la compétence', where: 'extrait de lecons/<m>/<tag>.md', tone: 'know', lessons: true },
      { cap: 'Demande', name: 'Grille, idées tirées, existant', where: 'slots/, existants.md (par run)', tone: 'data',
        def: 'Chaque emplacement porte son format, son niveau et son archétype avec sa définition.' },
    ],
    steps: [
      { name: 'Opus', def: 'Écrit les familles. Chaque famille cite les faits qu\'elle mobilise et le code d\'erreur de chaque piège.' },
      { name: 'Contrôles par script', def: 'Calculs, rendu PDF, géométrie, jumelles par SymPy, archétype, faits cités, anti-copie.' },
      { name: 'Lecteur sans contexte (goldfish)', def: 'Voit la carte rendue, la paraphrase et la résout.', docs: [DOC.eleve, DOC.correction] },
      { name: 'Relecteur pédagogique, contexte neuf', def: 'Opus : juste, utile, distracteurs, niveau, programme. Bloquée → réécrite une fois → écartée.', docs: [DOC.relecteur], added: true },
      { name: 'Vous, sur échantillon', def: 'La part fixée par l\'indice de confiance, plus tout ce qu\'un lecteur a signalé. Puis « Tout publier ».', human: true },
    ],
    outputs: [
      { cap: 'Familles', name: 'Énoncé à paramètres, deux dispositions', where: 'familles/<emplacement>.json', tone: 'data' },
      { cap: 'Cartes', name: 'Jumelles remplies par le script', where: 'manifeste.json du run', tone: 'data' },
      { cap: 'Banque', name: 'Exercices acceptés, avec faits cités et codes d\'erreur', where: 'onglet Exercices', tone: 'data', tag: '→ impression, correction, suivi' },
      { cap: 'Bilan', name: 'Écartés, signalés, erreurs proposées, tokens', where: 'run.py bilan', tone: 'data' },
    ],
  },
]

const LEVELS = [
  { title: 'Universel · toutes matières, tous pays', text: 'Le bon exercice, les 9 archétypes, familles et jumelles, formats, charte des figures.', who: 'Relu à chaque changement du moteur ou d\'un format.', docs: [DOC.exercice, DOC.formatsCommun, DOC.formatsCourants, DOC.figures] },
  { title: 'Pays et matière · deux axes indépendants', text: 'Pays : mots, monnaie, lieux, registres d\'âge. Matière : archétypes, contre-sens, règles de toutes les leçons, erreurs types.', who: 'Relu une fois par matière et par pays.', docs: [MATIERES[0], ...PAYS, ERREURS[0]] },
  { title: 'Leçon', text: 'Ce qui n\'est vrai que pour cette leçon : faits numérotés, limites, erreurs fréquentes, supports, idées.', who: 'Seulement les faits contestés et incertains.', lessons: true },
  { title: 'Lot · une compétence', text: 'La demande calculée par le script, puis les familles écrites par le modèle.', who: 'Échantillon fixé par l\'indice de confiance, jusqu\'à 5 %.' },
]

// ------------------------------------------------------------------ lecteur
function Viewer({ path, onClose }: { path: string | null; onClose: () => void }) {
  const [file, setFile] = useState<FileOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!path) return
    setFile(null); setError(null)
    api.get<FileOut>(`/api/admin/forge/file?path=${encodeURIComponent(path)}`)
      .then(setFile).catch(e => setError(e instanceof Error ? e.message : 'Lecture impossible.'))
  }, [path])
  const pretty = file?.kind === 'json' ? (() => { try { return JSON.stringify(JSON.parse(file.content), null, 2) } catch { return file.content } })() : ''
  return <Drawer opened={!!path} onClose={onClose} position="right" size="xl" padding="lg"
    title={<Group gap={8}><FileText size={16} /><Text fw={600}>{file?.name ?? path?.split('/').pop()}</Text></Group>}>
    {error && <Alert color="red">{error}</Alert>}
    {!file && !error && <Loader size="sm" />}
    {file && <Stack gap="md">
      <Text size="xs" c="dimmed">{path} · {(file.size / 1024).toFixed(1).replace('.', ',')} Ko · modifié le {new Date(file.modified).toLocaleString('fr-FR')}</Text>
      {file.kind === 'markdown' ? <MarkdownView source={file.content} />
        : <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{file.kind === 'json' ? pretty : file.content}</Code>}
    </Stack>}
  </Drawer>
}

function LessonList({ opened, onClose, onOpen }: { opened: boolean; onClose: () => void; onOpen: (path: string) => void }) {
  const [data, setData] = useState<{ available: boolean; lessons: Lesson[] } | null>(null)
  useEffect(() => { if (opened) api.get<{ available: boolean; lessons: Lesson[] }>('/api/admin/forge/lecons').then(setData).catch(() => setData({ available: false, lessons: [] })) }, [opened])
  return <Drawer opened={opened} onClose={onClose} position="right" size="lg" padding="lg" title={<Text fw={600}>Contextes de leçon</Text>}>
    {!data && <Loader size="sm" />}
    {data && !data.lessons.length && <Text size="sm" c="dimmed">Aucune leçon écrite sur cette machine.</Text>}
    {data && data.lessons.length > 0 && <Table highlightOnHover fz="sm">
      <Table.Thead><Table.Tr><Table.Th>Leçon</Table.Th><Table.Th>Faits</Table.Th><Table.Th>État</Table.Th></Table.Tr></Table.Thead>
      <Table.Tbody>{data.lessons.map(l => <Table.Tr key={l.path} style={{ cursor: 'pointer' }} onClick={() => onOpen(l.path)}>
        <Table.Td><Text size="sm" fw={500}>{l.name}</Text><Text size="xs" c="dimmed">{l.subject} · {(l.size / 1024).toFixed(1).replace('.', ',')} Ko</Text></Table.Td>
        <Table.Td>{l.facts}</Table.Td>
        <Table.Td>{l.facts === 0 ? <Badge color="red" variant="light">ancien format, à réécrire</Badge>
          : l.verified ? <Badge color="teal" variant="light">vérifiée</Badge> : <Badge color="yellow" variant="light">à vérifier</Badge>}</Table.Td>
      </Table.Tr>)}</Table.Tbody>
    </Table>}
  </Drawer>
}

// ------------------------------------------------------------------ cartes
function Openable({ docs, lessons, onOpen, onLessons, children }: {
  docs?: Doc[]; lessons?: boolean; onOpen: (p: string) => void; onLessons: () => void; children: ReactNode
}) {
  const style = { display: 'block', width: '100%', textAlign: 'left' as const }
  if (lessons) return <UnstyledButton style={style} onClick={onLessons} aria-label="Voir les leçons">{children}</UnstyledButton>
  if (!docs?.length) return <>{children}</>
  if (docs.length === 1) return <UnstyledButton style={style} onClick={() => onOpen(docs[0].path)} aria-label={`Lire ${docs[0].label}`}>{children}</UnstyledButton>
  return <Menu position="bottom-start" withinPortal shadow="md">
    <Menu.Target><UnstyledButton style={style} aria-label="Choisir un document">{children}</UnstyledButton></Menu.Target>
    <Menu.Dropdown style={{ maxHeight: 360, overflowY: 'auto' }}>
      {docs.map(d => <Menu.Item key={d.path} leftSection={<FileText size={14} />} onClick={() => onOpen(d.path)}>{d.label}</Menu.Item>)}
    </Menu.Dropdown>
  </Menu>
}

function Card({ card, onOpen, onLessons }: { card: CardDef; onOpen: (p: string) => void; onLessons: () => void }) {
  const tone = TONES[card.tone]
  const clickable = card.lessons || !!card.docs?.length
  const box = <Box p="xs" style={{
    background: tone.bg, border: `1.5px ${card.dashed ? 'dashed' : 'solid'} ${tone.border}`, borderRadius: 6,
    display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, transition: 'box-shadow .12s',
    boxShadow: clickable ? '0 0 0 0 transparent' : undefined,
  }} className={clickable ? 'forge-card-clickable' : undefined}>
    <Group justify="space-between" gap={6} wrap="nowrap">
      <Text size="9px" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: '.08em' }}>{card.cap}</Text>
      <Group gap={6} wrap="nowrap">
        {card.size && <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{card.size}</Text>}
        {clickable && <FileText size={13} color="var(--mantine-color-dimmed)" />}
      </Group>
    </Group>
    <Text size="sm" fw={600}>{card.name}</Text>
    {card.where && <Text size="xs" ff="monospace" c="dimmed" style={{ overflowWrap: 'anywhere' }}>{card.where}</Text>}
    {card.def && <Text size="xs">{card.def}</Text>}
    {card.tag && <Text size="xs" fw={600} c="blue">{card.tag}</Text>}
  </Box>
  return <Openable docs={card.docs} lessons={card.lessons} onOpen={onOpen} onLessons={onLessons}>{box}</Openable>
}

function StepBox({ step, onOpen }: { step: StepDef; onOpen: (p: string) => void }) {
  const box = <Box p="xs" style={{
    background: 'var(--mantine-color-body)', borderRadius: 6,
    border: step.human ? '1.5px dashed var(--mantine-color-text)' : step.added ? '2px solid var(--mantine-color-teal-filled)'
      : '1.5px solid var(--mantine-color-gray-6)',
  }} className={step.docs?.length ? 'forge-card-clickable' : undefined}>
    <Group justify="space-between" gap={6} wrap="nowrap">
      <Text size="sm" fw={600}>{step.name}</Text>
      {step.docs?.length ? <FileText size={13} color="var(--mantine-color-dimmed)" /> : null}
    </Group>
    <Text size="xs">{step.def}</Text>
  </Box>
  return <Openable docs={step.docs} onOpen={onOpen} onLessons={() => undefined}>{box}</Openable>
}

function FlowView({ flow, onOpen, onLessons }: { flow: Flow; onOpen: (p: string) => void; onLessons: () => void }) {
  const head = (t: string) => <Text size="xs" fw={700} tt="uppercase" c="dimmed" mb={6} style={{ letterSpacing: '.07em' }}>{t}</Text>
  return <Stack gap={8}>
    <Title order={3}>{flow.title}</Title>
    <Text size="sm" c="dimmed" maw="80ch">{flow.lead}</Text>
    <Box className="forge-flow">
      <Stack gap={8} style={{ minWidth: 0 }}>{head('Ce qui entre')}
        {flow.inputs.map((c, i) => typeof c === 'string'
          ? <Text key={i} size="10px" fw={700} tt="uppercase" c="blue" mt={4} style={{ letterSpacing: '.08em' }}>{c}</Text>
          : <Card key={i} card={c} onOpen={onOpen} onLessons={onLessons} />)}
      </Stack>
      <Box className="forge-arrow"><ArrowRight size={20} /></Box>
      <Stack gap={6} style={{ minWidth: 0 }}>{head('Traitement')}
        {flow.steps.map((s, i) => <Stack key={i} gap={6} align="stretch">
          {i > 0 && <Group justify="center"><ArrowDown size={14} /></Group>}
          <StepBox step={s} onOpen={onOpen} />
        </Stack>)}
      </Stack>
      <Box className="forge-arrow"><ArrowRight size={20} /></Box>
      <Stack gap={8} style={{ minWidth: 0 }}>{head('Ce qui sort')}
        {flow.outputs.map((c, i) => <Card key={i} card={c} onOpen={onOpen} onLessons={onLessons} />)}
      </Stack>
    </Box>
  </Stack>
}

// ------------------------------------------------------------------ commandes
function CommandLine({ label, command }: { label: string; command: string }) {
  return <Stack gap={4}>
    <Text size="xs" c="dimmed">{label}</Text>
    <Group gap={6} wrap="nowrap" align="flex-start">
      <Code block style={{ flex: 1, whiteSpace: 'pre-wrap', fontSize: 12.5 }}>{command}</Code>
      <CopyButton value={command}>{({ copied, copy }) => <Tooltip label={copied ? 'Copié' : 'Copier'}>
        <ActionIcon variant="subtle" onClick={copy} aria-label="Copier la commande">{copied ? <Check size={16} /> : <Copy size={16} />}</ActionIcon>
      </Tooltip>}</CopyButton>
    </Group>
  </Stack>
}

const LEVEL_OPTIONS = ['6e', '5e', '4e', '3e', '2nde', '1re', 'tle']

function Commands() {
  const [tool, setTool] = useState<'claude' | 'codex'>('claude')
  const [action, setAction] = useState('exercices')
  const [programme, setProgramme] = useState('fr')
  const [niveau, setNiveau] = useState('3e')
  const [serie, setSerie] = useState('')
  const [cible, setCible] = useState('C2.1')
  const [mode, setMode] = useState('amorce')
  const [count, setCount] = useState<number | string>(4)
  const [refaire, setRefaire] = useState(false)
  const [pilote, setPilote] = useState(false)
  const [sources, setSources] = useState('')
  const lycee = programme === 'ci' && ['2nde', '1re', 'tle'].includes(niveau)
  const opt = (flag: boolean, text: string) => flag ? ` ${text}` : ''
  const serieOpt = lycee && serie ? ` --serie ${serie}` : ''
  let prompt = ''
  if (action === 'lecon') {
    const lesson = cible.split('.')[0]
    prompt = `forge lecon ${programme} ${niveau} ${lesson}${serieOpt}${opt(refaire, '--refaire')}${sources ? ` --sources ${sources}` : ''}`
  } else if (action === 'exercices') {
    const extra = { amorce: '', suite: ' --suite', complement: ` --complement ${count}`, traces: ` --traces ${count}`, problemes: ' --problemes' }[mode]
    const target = mode === 'problemes' ? cible.split('.')[0] : cible
    prompt = `forge ${programme} ${niveau} ${target}${serieOpt}${extra}${opt(pilote, '--pilote')}`
  } else if (action === 'ecartes') prompt = `forge ecartes ${programme}-${niveau}-${cible.split('.')[0]}`
  else if (action === 'reprendre') prompt = 'forge reprendre'
  const terminal = tool === 'claude' ? `cd ~/MathPrint && claude --model opus "${prompt}"` : `cd ~/MathPrint && codex "${prompt}"`
  const lesson = cible.split('.')[0]
  const scripts: [string, string][] = [
    ['Contrôler une leçon', `backend/.venv/bin/python agents/forge/run.py lecon ${programme} ${niveau} ${lesson}${serieOpt}`],
    ['Faire vérifier ses faits (Opus)', `backend/.venv/bin/python agents/forge/run.py lecon ${programme} ${niveau} ${lesson}${serieOpt} --verifier`],
    ['Maintenir des faits contestés', `backend/.venv/bin/python agents/forge/run.py lecon ${programme} ${niveau} ${lesson}${serieOpt} --accepter F3 F7`],
    ['Bilan du dernier run, tokens compris', 'backend/.venv/bin/python agents/forge/run.py bilan'],
    ['Refus par cause et par format', 'backend/.venv/bin/python agents/forge/run.py qualite'],
    ['Confiance par format et archétype', 'backend/.venv/bin/python agents/forge/run.py confiance'],
    ['Leçons qui commencent bientôt', `backend/.venv/bin/python agents/forge/run.py calendrier ${programme}`],
    ['Compétences épuisées', 'backend/.venv/bin/python agents/forge/run.py epuisement'],
  ]
  return <Stack gap="md">
    <Title order={3}>Lancer la forge</Title>
    <Text size="sm" c="dimmed" maw="80ch">Claude Code et Codex lisent tous deux AGENTS.md à la racine du dépôt : la même phrase lance la même procédure. Dans une session déjà ouverte, tapez seulement la commande forge. Les relecteurs (goldfish, vérificateur, relecteur pédagogique) passent toujours par le CLI claude, quel que soit l'agent.</Text>
    <Group gap="md" align="flex-end" wrap="wrap">
      <SegmentedControl value={tool} onChange={v => setTool(v as 'claude' | 'codex')} data={[{ value: 'claude', label: 'Claude Code' }, { value: 'codex', label: 'Codex' }]} />
      <SegmentedControl value={action} onChange={setAction} data={[
        { value: 'matiere', label: 'Matière' }, { value: 'lecon', label: 'Leçon' }, { value: 'exercices', label: 'Exercices' },
        { value: 'ecartes', label: 'Écartés' }, { value: 'reprendre', label: 'Reprendre' }]} />
    </Group>
    {action === 'matiere' ? <Alert color="gray" title="Pas encore de commande">
      Seules les mathématiques sont ouvertes. La commande d'ouverture d'une matière (CONSIGNE-MATIERE.md) sera écrite avec la septième matière ou le troisième pays.
      En attendant, un contexte matière se lit et se corrige à la main : cliquez la carte « Contexte matière » plus haut.
    </Alert> : <>
      {action !== 'reprendre' && <Group gap="sm" align="flex-end" wrap="wrap">
        <Select label="Programme" w={150} value={programme} onChange={v => v && setProgramme(v)} data={[{ value: 'fr', label: 'France' }, { value: 'ci', label: "Côte d'Ivoire" }]} allowDeselect={false} />
        <Select label="Niveau" w={110} value={niveau} onChange={v => v && setNiveau(v)} data={LEVEL_OPTIONS} allowDeselect={false} />
        {lycee && <Select label="Série" w={100} value={serie} onChange={v => setSerie(v ?? '')} data={['A', 'A1', 'A2', 'C', 'D']} clearable />}
        <TextInput label={action === 'exercices' && mode !== 'problemes' ? 'Compétence ou leçon' : 'Leçon'} w={170} value={cible}
          onChange={e => setCible(e.currentTarget.value.trim())} placeholder={action === 'exercices' ? 'C2.1 ou C2' : 'C2'} />
        {action === 'exercices' && <Select label="Run" w={200} value={mode} onChange={v => v && setMode(v)} allowDeselect={false} data={[
          { value: 'amorce', label: 'Amorce (défaut)' }, { value: 'suite', label: 'Suite du socle' },
          { value: 'complement', label: 'Complément' }, { value: 'traces', label: 'Tracés seulement' }, { value: 'problemes', label: 'Problèmes de la leçon' }]} />}
        {action === 'exercices' && (mode === 'complement' || mode === 'traces') && <NumberInput label="Nombre" w={100} min={1} max={40} value={count} onChange={setCount} />}
        {action === 'exercices' && <Checkbox label="Pilote (tout relire)" checked={pilote} onChange={e => setPilote(e.currentTarget.checked)} mb={8} />}
        {action === 'lecon' && <Checkbox label="Réécrire (--refaire)" checked={refaire} onChange={e => setRefaire(e.currentTarget.checked)} mb={8} />}
        {action === 'lecon' && <TextInput label="Sources (facultatif)" w={260} value={sources} onChange={e => setSources(e.currentTarget.value)} placeholder="~/Manuels/maths-3e.pdf --pages 40-61" />}
      </Group>}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <CommandLine label="Dans une session ouverte à la racine du dépôt" command={prompt} />
        <CommandLine label="Depuis un terminal" command={terminal} />
      </SimpleGrid>
      {tool === 'codex' && <Alert color="yellow" variant="light">
        Codex n'est pas installé sur ce Mac pour l'instant. {action === 'lecon' && 'La consigne de leçon demande Opus : Codex s\'arrêtera et demandera confirmation avant d\'écrire.'}
      </Alert>}
      {action === 'exercices' && <Text size="xs" c="dimmed">Une leçon par session, trois sous-agents au plus en parallèle. La forge refuse une leçon absente, à l'ancien format ou dont les faits ne sont pas vérifiés : commencez alors par « Leçon ».</Text>}
    </>}
    <Title order={4} mt="sm">Scripts utiles, depuis la racine du dépôt</Title>
    <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
      {scripts.map(([label, command]) => <CommandLine key={label} label={label} command={command} />)}
    </SimpleGrid>
  </Stack>
}

// ------------------------------------------------------------------ page
export default function ForgeAdmin() {
  const [doc, setDoc] = useState<string | null>(null)
  const [lessons, setLessons] = useState(false)
  const [available, setAvailable] = useState<boolean | null>(null)
  useEffect(() => { api.get<{ available: boolean }>('/api/admin/forge/status').then(r => setAvailable(r.available)).catch(() => setAvailable(false)) }, [])
  const openDoc = (path: string) => { setLessons(false); setDoc(path) }
  return <Stack gap={40} p="md" maw={1180}>
    <style>{`
      .forge-flow { display: grid; grid-template-columns: minmax(0,1.25fr) 30px minmax(0,.7fr) 30px minmax(0,1fr); align-items: start; }
      .forge-arrow { display: flex; justify-content: center; padding-top: 28px; }
      .forge-card-clickable { cursor: pointer; }
      .forge-card-clickable:hover { box-shadow: 0 0 0 2px var(--mantine-color-blue-filled) !important; }
      @media (max-width: 900px) { .forge-flow { grid-template-columns: minmax(0,1fr); } .forge-arrow { transform: rotate(90deg); padding: 4px 0; } }
    `}</style>
    <Stack gap={8}>
      <Title order={2}>Forge</Title>
      <Text maw="80ch">Objectif : des exercices justes et de grande qualité pédagogique, publiés avec peu de relecture. Chaque règle vit au niveau le plus général où elle est vraie ; on relit en amont (matière, leçon), on fait confiance en aval grâce aux contrôles et à deux lecteurs indépendants. Cliquez une carte marquée <FileText size={12} /> pour lire le fichier.</Text>
      {available === false && <Alert color="yellow">Les fichiers de la forge ne sont pas sur cette machine : la forge tourne sur le Mac. Les diagrammes et les commandes restent lisibles.</Alert>}
    </Stack>

    <Stack gap={8}>
      <Title order={3}>Les niveaux, du plus général au plus spécifique</Title>
      <Box style={{ border: '1px solid var(--mantine-color-default-border)', borderRadius: 8, overflow: 'hidden' }}>
        {LEVELS.map((l, i) => <Group key={i} wrap="nowrap" align="stretch" gap={0}
          style={{ borderTop: i ? '1px solid var(--mantine-color-default-border)' : undefined }}>
          <Box style={{ flex: 1, minWidth: 0, padding: '10px 14px', paddingLeft: 14 + i * 14, display: 'flex', gap: 12 }}>
            <Box style={{ width: 5, borderRadius: 3, flex: 'none', background: ['var(--mantine-color-text)', 'var(--mantine-color-blue-filled)', 'var(--mantine-color-blue-4)', 'var(--mantine-color-gray-5)'][i] }} />
            <Stack gap={4} style={{ minWidth: 0 }}>
              <Text fw={600} size="sm">{l.title}</Text>
              <Text size="xs" c="dimmed">{l.text}</Text>
              <Group gap={6}>
                {l.docs?.map(d => <Button key={d.path} size="compact-xs" variant="light" leftSection={<FileText size={12} />} onClick={() => openDoc(d.path)}>{d.label}</Button>)}
                {l.lessons && <Button size="compact-xs" variant="light" leftSection={<FileText size={12} />} onClick={() => setLessons(true)}>Leçons écrites</Button>}
              </Group>
            </Stack>
          </Box>
          <Box visibleFrom="sm" style={{ width: 230, flex: 'none', padding: '10px 14px', background: 'var(--mantine-color-default-hover)' }}>
            <Text size="xs">{l.who}</Text>
          </Box>
        </Group>)}
      </Box>
    </Stack>

    <Group gap="lg" wrap="wrap">
      {([['data', 'consigne, programme, sources, demande'], ['know', 'contexte (savoir chargé)'], ['tool', 'contexte de format']] as [Tone, string][])
        .map(([tone, label]) => <Group key={tone} gap={6}><Box w={22} h={14} style={{ background: TONES[tone].bg, border: `1.5px solid ${TONES[tone].border}`, borderRadius: 3 }} /><Text size="xs" c="dimmed">{label}</Text></Group>)}
      <Group gap={6}><Box w={22} h={14} style={{ border: '1.5px dashed var(--mantine-color-text)', borderRadius: 3 }} /><Text size="xs" c="dimmed">à écrire, ou humain</Text></Group>
      <Group gap={6}><Box w={22} h={14} style={{ border: '2px solid var(--mantine-color-teal-filled)', borderRadius: 3 }} /><Text size="xs" c="dimmed">lecteur indépendant ajouté pour la qualité</Text></Group>
    </Group>

    {FLOWS.map(flow => <FlowView key={flow.title} flow={flow} onOpen={openDoc} onLessons={() => setLessons(true)} />)}

    <Stack gap={8}>
      <Title order={3}>Quand une carte est refusée, on corrige le bon niveau</Title>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
        {[['Pouce baissé, avec sa cause', 'Fait faux, règle de matière, format ou rendu, consigne mal suivie, pédagogie faible.'],
          ['Remonter au niveau', 'Fait faux → la leçon. Règle → le contexte matière. Rendu → le format ou un contrôle. Consigne → CONSIGNE-EXERCICES.'],
          ['Corriger une fois', 'Une règle vérifiable devient un contrôle par script ; sinon une phrase, au niveau trouvé, nulle part ailleurs.'],
          ['La confiance suit', 'Par format et par format × archétype. Un rendu fautif touche le format, le reste touche la case seule.']]
          .map(([t, d]) => <Box key={t} p="sm" style={{ border: '1px solid var(--mantine-color-default-border)', borderRadius: 6 }}>
            <Text fw={600} size="sm">{t}</Text><Text size="xs" c="dimmed">{d}</Text></Box>)}
      </SimpleGrid>
    </Stack>

    <Commands />

    <Viewer path={doc} onClose={() => setDoc(null)} />
    <LessonList opened={lessons} onClose={() => setLessons(false)} onOpen={openDoc} />
  </Stack>
}
