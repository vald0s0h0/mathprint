// Assistant « Créer un sujet » — UN seul assistant, deux façons de composer.
//
//   1. Classe        — la classe, puis le mode :
//                      • automatique : une copie PERSONNALISÉE par élève, choisie
//                        sur son historique (lacunes, courbe de l'oubli, niveau).
//                        Ouvert dès un sujet corrigé, avec un avertissement sous
//                        le seuil conseillé ;
//                      • manuel : le professeur choisit les exercices lui-même.
//   2. Sujet         — titre, type, base de notes, pages, guides : en dégradé selon
//                      le niveau, pour tous les élèves, ou aucun ;
//   3. Compétences   — le périmètre (+ l'option Problèmes en automatique) ;
//   4. Variantes     — manuel seulement : sujet unique mélangé, aléatoires ou par niveau ;
//   5. Exercices     — manuel seulement : un clic par carte, le moteur met en page ;
//   6. Génération    — récapitulatif et mise en file.
//
// Les deux modes partagent tout le reste (worker de fond, correction) : seul le
// plan enregistré diffère (POST /auto-plan ou /manual-plan).
import {
  Alert, Badge, Box, Button, Card, Checkbox, Collapse, Divider, Group, Modal, Progress, ScrollArea,
  Select, SegmentedControl, SimpleGrid, Slider, Stack, Stepper, Switch, Text,
  TextInput, ThemeIcon, Title, Tooltip, UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, Copy as CopyIcon, Dices, Layers,
  Lightbulb, ListOrdered, Lock, PencilRuler, Puzzle, Rocket, Shuffle,
  Sparkles, TrendingUp, Users, Wand2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../../api'
import { type Teaching } from '../../components/ClassEditor'
import { useAppState } from '../../state/AppState'
import CompetencyMatrixStep, { type Matrix } from './CompetencyMatrixStep'
import { DEFAULT_GUIDE_REACH, guideRatio } from './guideCurve'
import ExercisePicker, {
  formatDuration, packCard, selectionSummary, sideLabel, type Metrics, type PoolItem,
} from './ExercisePicker'

export type WizardClass = {
  id: string; name: string; grade_level: string; student_count?: number; school_name?: string
}
type Eligibility = {
  corrected: number; required: number; recommended: number; eligible: boolean; students: number
  levels: { level: number; count: number }[]
}
type Pool = {
  exercises: PoolItem[]; problems: PoolItem[]
  chapters: { code: string; name: string }[]; metrics: Metrics
}
type Variant = { key: string; label: string; selection: string[] }
type Mode = 'auto' | 'manual'
type StepKey = 'classe' | 'sujet' | 'competences' | 'variantes' | 'page' | 'generation'

const STEPS: Record<Mode, { key: StepKey; label: string; desc: string }[]> = {
  auto: [
    { key: 'classe', label: 'Classe', desc: 'Et le mode' },
    { key: 'sujet', label: 'Sujet', desc: 'Titre, notes, guides' },
    { key: 'competences', label: 'Compétences', desc: 'Le périmètre' },
    { key: 'generation', label: 'Génération', desc: 'Vérifier et lancer' },
  ],
  manual: [
    { key: 'classe', label: 'Classe', desc: 'Et le mode' },
    { key: 'sujet', label: 'Sujet', desc: 'Titre, notes, guides' },
    { key: 'competences', label: 'Compétences', desc: 'Le périmètre' },
    { key: 'variantes', label: 'Variantes', desc: 'Un ou plusieurs sujets' },
    { key: 'page', label: 'Exercices', desc: 'Un clic par carte' },
    { key: 'generation', label: 'Génération', desc: 'Vérifier et lancer' },
  ],
}

const NOTE_BASES = ['5', '10', '20']
const LEVEL_KEYS = ['facile', 'moyen', 'difficile'] as const
const LEVEL_LABELS: Record<string, string> = {
  facile: 'Facile', moyen: 'Moyen', difficile: 'Difficile',
}
const MAX_VARIANTS = 6
// élèves qui reçoivent des problèmes (student_history.PROBLEM_PLAN)
const PROBLEM_MIN_LEVEL = 7

/** Décline une variante : chaque carte est remplacée par un exercice équivalent tiré
 *  au hasard (même compétence, ou même chapitre pour un problème, même difficulté,
 *  hauteur la plus proche). Sans équivalent, la carte d'origine est conservée. */
function deriveSelection(base: string[], items: PoolItem[], guidesOn: boolean): { selection: string[]; kept: number } {
  const byId = new Map(items.map((it) => [it.id, it]))
  const baseIds = new Set(base)
  const taken = new Set<string>()
  const heightOf = (it: PoolItem) => packCard(it, guidesOn).height
  let kept = 0
  const selection = base.map((id) => {
    const src = byId.get(id)
    if (!src) return id
    const same = (it: PoolItem) => (src.kind === 'probleme'
      ? it.kind === 'probleme' && it.chapter_code === src.chapter_code
      : it.kind !== 'probleme' && it.competency_id === src.competency_id)
    const free = items.filter((it) => !baseIds.has(it.id) && !taken.has(it.id) && same(it))
    const level = free.filter((it) => it.difficulty === src.difficulty)
    const candidates = (level.length ? level : free)
      .map((it) => ({ it, gap: Math.abs(heightOf(it) - heightOf(src)) }))
      .sort((a, b) => a.gap - b.gap).slice(0, 3)
    if (!candidates.length) { kept += 1; taken.add(id); return id }
    const chosen = candidates[Math.floor(Math.random() * candidates.length)].it
    taken.add(chosen.id)
    return chosen.id
  })
  return { selection, kept }
}

// ------------------------------------------------------------ petits blocs

function StepIntro({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div>
      <Title order={3}>{title}</Title>
      {children && <Text size="sm" c="dimmed" mt={4}>{children}</Text>}
    </div>
  )
}

function ChoiceCard({ selected, disabled, icon: Icon, title, desc, onClick, children, color = 'indigo' }: {
  selected: boolean; disabled?: boolean; icon: typeof Layers; title: string
  desc: ReactNode; onClick: () => void; children?: ReactNode; color?: string
}) {
  return (
    <UnstyledButton onClick={disabled ? undefined : onClick} disabled={disabled}
      aria-pressed={selected}
      style={{
        flex: 1, minWidth: 220, textAlign: 'left', borderRadius: 'var(--mantine-radius-md)',
        padding: 'var(--mantine-spacing-md)',
        border: `${selected ? 2 : 1}px solid ${selected
          ? `var(--mantine-color-${color}-filled)` : 'var(--mantine-color-default-border)'}`,
        background: selected ? `var(--mantine-color-${color}-light)` : 'var(--mantine-color-body)',
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.6 : 1,
        transition: 'border-color 120ms, background 120ms',
      }}>
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <ThemeIcon size={36} radius="md" variant={selected ? 'filled' : 'light'}
          color={disabled ? 'gray' : color}>
          {disabled ? <Lock size={18} /> : <Icon size={18} />}
        </ThemeIcon>
        <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
          <Group justify="space-between" wrap="nowrap">
            <Text fw={650}>{title}</Text>
            {selected && <Check size={16} color={`var(--mantine-color-${color}-filled)`} />}
          </Group>
          <Text size="sm" c="dimmed">{desc}</Text>
          {children}
        </Stack>
      </Group>
    </UnstyledButton>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>{label}</Text>
      {children}
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Stack>
  )
}

/** Le dégradé des guides, niveau par niveau, avec l'effectif de la classe
 *  sous chaque barre : le professeur voit QUI recevra combien d'aide. */
function GuideGradient({ reach, levels }: { reach: number; levels: Eligibility['levels'] }) {
  const byLevel = new Map(levels.map((l) => [l.level, l.count]))
  return (
    <Group gap={6} align="flex-end" wrap="nowrap" style={{ height: 150 }}>
      {Array.from({ length: 10 }, (_, i) => i + 1).map((lvl) => {
        const r = guideRatio(lvl, reach)
        const n = byLevel.get(lvl) ?? 0
        return (
          <Stack key={lvl} gap={2} align="center" style={{ flex: 1, minWidth: 0 }}>
            <Text fz={10} c="dimmed">{Math.round(r * 100)}%</Text>
            <Box style={{
              width: '100%', height: 90, display: 'flex', alignItems: 'flex-end',
              background: 'var(--mantine-color-default-hover)', borderRadius: 4,
            }}>
              <Box style={{
                width: '100%', height: `${Math.max(2, r * 100)}%`, borderRadius: 4,
                background: 'var(--mantine-color-yellow-filled)', transition: 'height 150ms',
              }} />
            </Box>
            <Text fz={11} fw={500}>{lvl}</Text>
            <Text fz={10} c={n ? undefined : 'dimmed'}>{n ? `${n} él.` : '—'}</Text>
          </Stack>
        )
      })}
    </Group>
  )
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <Group justify="space-between" wrap="nowrap" align="flex-start" py={6}
      style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}>
      <Text size="sm" c="dimmed" style={{ flexShrink: 0 }}>{k}</Text>
      <Text size="sm" fw={550} ta="right">{v}</Text>
    </Group>
  )
}

// ------------------------------------------------------------ l'assistant

export default function SubjectWizard({ opened, classes, onClose, onCreated }: {
  opened: boolean; classes: WizardClass[]; onClose: () => void; onCreated: () => void
}) {
  const { subject: defaultSubject } = useAppState()
  const [subject, setSubject] = useState(defaultSubject)
  const [groupId, setGroupId] = useState<string | null>(null)
  const [teachings, setTeachings] = useState<Teaching[]>([])
  const [step, setStep] = useState(0)
  // 1. classe et mode
  const [classId, setClassId] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('manual')
  const [eligibility, setEligibility] = useState<Record<string, Eligibility>>({})
  // 2. sujet
  const [title, setTitle] = useState('')
  const [type, setType] = useState('control')
  const [noteBase, setNoteBase] = useState('20')
  const [pages, setPages] = useState(1)
  const [guidesOn, setGuidesOn] = useState(true)
  const [guidesForAll, setGuidesForAll] = useState(false)
  const [guideReach, setGuideReach] = useState(DEFAULT_GUIDE_REACH)
  // 3. compétences
  const [competencyIds, setCompetencyIds] = useState<string[]>([])
  const [matrix, setMatrix] = useState<Matrix | null>(null)
  const [problems, setProblems] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  // 4. variantes (manuel)
  const [variantKind, setVariantKind] = useState('none')
  const [variants, setVariants] = useState<Variant[]>(
    [{ key: 'A', label: 'Sujet unique', selection: [] }])
  const [current, setCurrent] = useState(0)
  // 5. exercices (manuel)
  const [pool, setPool] = useState<Pool | null>(null)
  const [loadingPool, setLoadingPool] = useState(false)
  // 6. génération
  const [busy, setBusy] = useState(false)

  const steps = STEPS[mode]
  const stepKey = steps[Math.min(step, steps.length - 1)].key
  const cls = classes.find((c) => c.id === classId)
  const grade = cls?.grade_level
  const elig = classId ? eligibility[classId] : undefined

  const reset = useCallback(() => {
    setStep(0); setGroupId(null); setClassId(null); setMode('manual'); setTitle(''); setType('control')
    setNoteBase('20'); setPages(1); setGuidesOn(true); setGuidesForAll(false); setGuideReach(DEFAULT_GUIDE_REACH)
    setCompetencyIds([]); setProblems(false); setVariantKind('none'); setCurrent(0)
    setPool(null)
    setVariants([{ key: 'A', label: 'Sujet unique', selection: [] }])
  }, [])

  function close() { reset(); onClose() }
  useEffect(() => { if (opened && classes.length === 1) setClassId(classes[0].id) }, [opened, classes.length])
  useEffect(() => {
    let active = true
    setGroupId(null); setCompetencyIds([]); setTeachings([])
    if (classId) api.get<Teaching[]>(`/api/operations/classes/${classId}/teachings`).then(rows => {
      if (!active) return
      setTeachings(rows); setSubject(rows.some(t => t.subject_id === defaultSubject) ? defaultSubject : rows[0]?.subject_id || defaultSubject)
    }).catch(e => notifications.show({ color: 'red', message: e.message }))
    return () => { active = false }
  }, [classId, defaultSubject])
  useEffect(() => { setCompetencyIds([]); setPool(null); setMatrix(null) }, [subject, groupId])

  // éligibilité à la création automatique, pour toutes les classes proposées
  const classKey = classes.map((c) => c.id).join(',')
  useEffect(() => {
    if (!opened) return
    let active = true
    setEligibility({})
    for (const c of classes) {
      if (groupId && c.id !== classId) continue
      api.get<Eligibility>(`/api/assessments/auto-eligibility?class_id=${c.id}&subject_id=${subject}${groupId ? `&group_id=${groupId}` : ''}`)
        .then((e) => { if (active) setEligibility((m) => ({ ...m, [c.id]: e })) })
        .catch(() => { /* la classe reste simplement en mode manuel */ })
    }
    return () => { active = false }
    // la liste est recréée à chaque rafraîchissement de la page : seuls les
    // identifiants comptent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, classKey, subject, groupId, classId])

  // une classe sans aucune correction ne peut pas rester en automatique
  useEffect(() => {
    if (mode === 'auto' && elig && !elig.eligible) setMode('manual')
  }, [mode, elig])

  // ---------------------------------------------------------- variantes

  function applyVariantKind(kind: string, count?: number) {
    setVariantKind(kind)
    setCurrent(0)
    setVariants((vs) => {
      const base = vs[0]?.selection ?? []
      if (kind === 'none') return [{ key: 'A', label: 'Sujet unique', selection: base }]
      if (kind === 'level') {
        return LEVEL_KEYS.map((k, i) => ({
          key: k, label: LEVEL_LABELS[k], selection: vs[i]?.selection ?? [...base],
        }))
      }
      const n = Math.max(2, Math.min(MAX_VARIANTS, count ?? (variantKind === 'anticheat' ? vs.length : 2)))
      return Array.from({ length: n }, (_, i) => ({
        key: String.fromCharCode(65 + i), label: `Variante ${String.fromCharCode(65 + i)}`,
        selection: vs[i]?.selection ?? [],
      }))
    })
  }

  function copyInto(target: number, from: number) {
    setVariants((vs) => vs.map((v, i) => (i === target
      ? { ...v, selection: [...vs[from].selection] } : v)))
  }

  function deriveInto(target: number, from: number) {
    if (!pool) return
    const { selection, kept } = deriveSelection(
      variants[from].selection, [...pool.exercises, ...pool.problems], guidesOn)
    setVariants((vs) => vs.map((v, i) => (i === target ? { ...v, selection } : v)))
    notifications.show({
      color: kept ? 'orange' : 'blue',
      message: kept
        ? `Variante déclinée — ${kept} carte(s) sans équivalent en banque, conservée(s) telle(s) quelle(s).`
        : 'Variante déclinée : chaque exercice a été remplacé par un équivalent.',
    })
  }

  // le pool ne dépend que des compétences (et du nombre de pages, pour la
  // géométrie des colonnes) : chargé à l'entrée dans le choix des exercices.
  useEffect(() => {
    if (!opened || mode !== 'manual' || stepKey !== 'page' || !competencyIds.length) return
    setLoadingPool(true)
    api.get<Pool>(`/api/assessments/manual/pool?competency_ids=${competencyIds.join(',')}`
      + `&pages=${pages}`)
      .then((p) => {
        setPool(p)
        // Décocher une compétence retire ses exercices du pool : les cartes
        // déjà choisies qui en venaient doivent disparaître, sinon la
        // génération échouerait.
        const known = new Set([...p.exercises, ...p.problems].map((e) => e.id))
        const dropped = variants.reduce((n, v) => n + v.selection.filter((id) => !known.has(id)).length, 0)
        if (dropped) {
          setVariants((vs) => vs.map((v) => ({ ...v, selection: v.selection.filter((id) => known.has(id)) })))
          notifications.show({
            color: 'orange',
            message: `${dropped} exercice(s) retiré(s) : leur compétence n'est plus cochée.`,
          })
        }
      })
      .catch((e) => notifications.show({ color: 'red', message: (e as Error).message }))
      .finally(() => setLoadingPool(false))
  }, [opened, mode, stepKey, competencyIds, pages])

  // ------------------------------------------------------------ dérivés

  const placed = variants.reduce((n, v) => n + v.selection.length, 0)
  const emptyVariants = variants.filter((v) => v.selection.length === 0)
  const summaries = useMemo(() => {
    if (!pool) return []
    const byId = new Map(([...pool.exercises, ...pool.problems]).map((e) => [e.id, e]))
    return variants.map((v) => ({
      variant: v, summary: selectionSummary(v.selection, byId, pool.metrics, guidesOn, pages),
    }))
  }, [pool, variants, guidesOn, pages])
  const overfull = summaries.filter((s) => !s.summary.fits).map((s) => s.variant.label)

  // chapitres touchés par la sélection : problèmes disponibles, titre proposé
  const touched = useMemo(() => {
    const sel = new Set(competencyIds)
    const chapters: { code: string; name: string; problems: number }[] = []
    for (const d of matrix?.domains ?? []) {
      for (const ch of d.chapters) {
        if (ch.competencies.some((c) => sel.has(c.id))) {
          chapters.push({ code: ch.code, name: ch.name, problems: ch.problem_count })
        }
      }
    }
    return chapters
  }, [matrix, competencyIds])
  const problemsAvailable = touched.reduce((n, ch) => n + ch.problems, 0)
  const autoTitle = touched.length
    ? touched.slice(0, 2).map((ch) => ch.name).join(' · ') + (touched.length > 2 ? '…' : '')
    : 'Sans titre'
  const finalTitle = title.trim() || autoTitle

  const levelCount = (pred: (l: number) => boolean) =>
    (elig?.levels ?? []).filter((l) => pred(l.level)).reduce((n, l) => n + l.count, 0)
  const strongStudents = levelCount((l) => l >= PROBLEM_MIN_LEVEL)

  // --------------------------------------------------------- navigation

  const blocker: string | null = (() => {
    switch (stepKey) {
      case 'classe':
        if (!classId) return 'Choisissez une classe'
        if (!teachings.some(t => t.subject_id === subject)) return 'Aucun enseignement affecté'
        if (!groupId && !teachings.find(t => t.subject_id === subject)?.framework_id) return 'Choisissez un groupe d’enseignement'
        if (mode === 'auto' && !elig?.eligible) return 'Création automatique indisponible pour cette classe'
        return null
      case 'competences':
        return competencyIds.length ? null : 'Cochez au moins une compétence'
      case 'page':
        if (!placed) return 'Choisissez au moins un exercice'
        return overfull.length ? `Trop de cartes : ${overfull.join(', ')}` : null
      default:
        return null
    }
  })()

  function next() {
    if (!blocker && step < steps.length - 1) setStep(step + 1)
  }

  async function suggest() {
    if (!classId) return
    setSuggesting(true)
    try {
      const s = await api.get<{ competency_ids: string[]; reason: string }>(
        `/api/assessments/suggested-competencies?class_id=${classId}&subject_id=${subject}${groupId ? `&group_id=${groupId}` : ''}`)
      if (s.competency_ids.length) setCompetencyIds(s.competency_ids)
      notifications.show({ color: s.competency_ids.length ? 'blue' : 'gray', message: s.reason })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSuggesting(false)
    }
  }

  const guidesPlan = !guidesOn ? 'none' : guidesForAll ? 'include' : 'auto'

  async function createAndGenerate() {
    setBusy(true)
    try {
      const created = await api.post<{ id: string }>('/api/assessments', {
        class_id: classId, subject_id: subject, group_id: groupId, type, title: finalTitle, pages, note_base: Number(noteBase),
      })
      if (mode === 'auto') {
        await api.post(`/api/assessments/${created.id}/auto-plan`, {
          competency_ids: competencyIds, guides_reach_pct: guideReach, problems, guides: guidesPlan,
        })
      } else {
        await api.post(`/api/assessments/${created.id}/manual-plan`, {
          competency_ids: competencyIds, variant_kind: variantKind, composition: 'engine',
          guides: guidesPlan, guides_reach_pct: guideReach,
          variants: variants.map((v) => ({
            key: v.key, label: v.label,
            items: v.selection.map((exercise_id, rank) => ({ exercise_id, rank })),
          })),
        })
      }
      await api.post(`/api/assessments/${created.id}/generate`, { font_size: 10 })
      notifications.show({
        color: 'blue',
        message: mode === 'auto'
          ? `« ${finalTitle} » en file : une copie personnalisée par élève`
          : `« ${finalTitle} » en file de génération`,
      })
      onCreated()
      close()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------- écrans

  const stepClasse = (
    <Stack gap="xl" maw={980} mx="auto" w="100%" py="lg">
      <Stack gap="sm">
        <StepIntro title="Pour quelle classe ?" />
        {classes.length === 0 ? (
          <Alert color="orange" icon={<AlertTriangle size={16} />}>
            Aucune classe pour ce cycle — créez-en une dans l'onglet Élèves.
          </Alert>
        ) : (
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="sm">
            {classes.map((c) => {
              const e = eligibility[c.id]
              const on = c.id === classId
              return (
                <UnstyledButton key={c.id} onClick={() => setClassId(c.id)} aria-pressed={on}
                  style={{
                    padding: 'var(--mantine-spacing-sm) var(--mantine-spacing-md)',
                    borderRadius: 'var(--mantine-radius-md)',
                    border: `${on ? 2 : 1}px solid ${on ? 'var(--mantine-color-indigo-filled)'
                      : 'var(--mantine-color-default-border)'}`,
                    background: on ? 'var(--mantine-color-indigo-light)' : 'var(--mantine-color-body)',
                  }}>
                  <Group justify="space-between" wrap="nowrap">
                    <Text fw={700} size="lg">{c.name}</Text>
                    <Badge variant="light" size="sm" style={{ flexShrink: 0 }}>{({ '2nde': 'Seconde', '1re': 'Première', 'tle': 'Terminale' } as Record<string, string>)[c.grade_level] || c.grade_level}</Badge>
                  </Group>
                  {c.school_name && <Text size="xs" c="dimmed" mt={4}>{c.school_name}</Text>}
                  <Group gap={10} mt={4}>
                    <Group gap={4}>
                      <Users size={13} opacity={0.6} />
                      <Text size="xs" c="dimmed">{c.student_count ?? e?.students ?? '…'} élèves</Text>
                    </Group>
                    <Text size="xs" c="dimmed">
                      {e ? `${e.corrected} sujet${e.corrected > 1 ? 's' : ''} corrigé${e.corrected > 1 ? 's' : ''}` : ''}
                    </Text>
                  </Group>
                </UnstyledButton>
              )
            })}
          </SimpleGrid>
        )}
      </Stack>

      <Stack gap="sm">
        <StepIntro title="Comment composer le sujet ?" />
        {classId && <Group grow>
          {teachings.length > 1 && <Select label="Matière" data={teachings.map(t => ({ value: t.subject_id, label: t.subject_name }))} value={subject} onChange={v => { setSubject(v || 'math'); setGroupId(null) }} />}
          {!!teachings.find(t => t.subject_id === subject)?.groups.length && <Select label="Groupe d’enseignement" placeholder="Classe entière" clearable
            data={(teachings.find(t => t.subject_id === subject)?.groups || []).map(g => ({ value: g.id, label: g.name }))} value={groupId} onChange={setGroupId} />}
        </Group>}
        <Group align="stretch" gap="sm">
          <ChoiceCard selected={mode === 'auto'} disabled={!classId || !elig?.eligible}
            icon={Wand2} title="Création automatique" color="violet"
            onClick={() => setMode('auto')}
            desc="Une copie personnalisée pour chaque élève : la plateforme choisit les exercices d'après ses lacunes, la courbe de l'oubli et son niveau.">
            <Stack gap={4} mt={4}>
              <Group gap={6}><TrendingUp size={13} opacity={0.7} />
                <Text size="xs">Difficulté et guides dosés selon le niveau 1 à 10</Text></Group>
              <Group gap={6}><Puzzle size={13} opacity={0.7} />
                <Text size="xs">Problèmes pour les élèves forts (option)</Text></Group>
              <Group gap={6}><ListOrdered size={13} opacity={0.7} />
                <Text size="xs">Du plus simple au plus difficile, problèmes en dernier</Text></Group>
            </Stack>
            {classId && elig && elig.corrected < elig.recommended && (
              <Stack gap={4} mt="xs">
                <Progress value={(elig.corrected / elig.recommended) * 100} size="sm" color={elig.eligible ? 'orange' : 'violet'} />
                <Group gap={6} wrap="nowrap" align="flex-start">
                  {elig.eligible && <AlertTriangle size={13} color="var(--mantine-color-orange-filled)" style={{ flexShrink: 0, marginTop: 2 }} />}
                  <Text size="xs" c="dimmed">
                    {elig.eligible
                      ? `${elig.corrected} évaluation${elig.corrected > 1 ? 's' : ''} corrigée${elig.corrected > 1 ? 's' : ''} sur les ${elig.recommended} conseillées : c'est possible, mais le niveau de chaque élève sera moins précis.`
                      : `Il faut au moins une évaluation corrigée. ${elig.recommended} sont conseillées pour connaître précisément le niveau de chaque élève.`}
                  </Text>
                </Group>
              </Stack>
            )}
            {!classId && <Text size="xs" c="dimmed" mt="xs">Choisissez d'abord une classe.</Text>}
          </ChoiceCard>
          <ChoiceCard selected={mode === 'manual'} icon={PencilRuler} title="Création manuelle"
            onClick={() => setMode('manual')}
            desc="Vous cliquez sur les exercices qui vous intéressent, la mise en page se fait toute seule — un sujet commun, décliné si besoin en variantes.">
            <Stack gap={4} mt={4}>
              <Group gap={6}><Shuffle size={13} opacity={0.7} />
                <Text size="xs">Variantes aléatoires ou par niveau</Text></Group>
              <Group gap={6}><Layers size={13} opacity={0.7} />
                <Text size="xs">Remplissage des pages et durée en direct</Text></Group>
            </Stack>
          </ChoiceCard>
        </Group>
      </Stack>
    </Stack>
  )

  const stepSujet = (
    <Stack gap="lg" maw={860} mx="auto" w="100%" py="lg">
      <StepIntro title="Le sujet">
        {mode === 'auto'
          ? 'Ces réglages valent pour toutes les copies ; leur contenu, lui, sera propre à chaque élève.'
          : 'Ces réglages valent pour toute la classe et toutes les variantes.'}
      </StepIntro>
      <TextInput label="Titre" placeholder={`ex. ${touched.length ? autoTitle : 'Fractions — semaine 12'}`}
        description="Laissé vide, le titre reprend les chapitres cochés."
        value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg">
        <Field label="Type">
          <SegmentedControl value={type} onChange={setType} data={[
            { value: 'training', label: 'Entraînement' },
            { value: 'control', label: 'Contrôle noté' },
          ]} />
        </Field>
        <Field label="Base de notes" hint={type === 'control'
          ? 'Le résultat est ramené à cette base à la correction.'
          : 'Sert au suivi, sans être imprimé sur la copie.'}>
          <SegmentedControl value={noteBase} onChange={setNoteBase}
            data={NOTE_BASES.map((b) => ({ value: b, label: `/${b}` }))} />
        </Field>
        <Field label="Pages" hint={pages === 1 ? 'Recto seul' : pages === 2 ? 'Recto/verso'
          : `${Math.ceil(pages / 2)} feuilles recto/verso`}>
          <SegmentedControl value={String(pages)} onChange={(v) => setPages(Number(v))}
            data={['1', '2', '3', '4', '5', '6']} />
        </Field>
      </SimpleGrid>
      <Divider />
      <Card withBorder padding="md">
        <Stack gap="sm">
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <Group gap="sm" wrap="nowrap" align="flex-start">
              <ThemeIcon variant="light" color="yellow" size={34}><Lightbulb size={18} /></ThemeIcon>
              <div>
                <Text fw={600} size="sm">Inclure les guides pour les élèves en difficulté</Text>
                <Text size="xs" c="dimmed">
                  Encadrés d'aide (fond jaune) imprimés progressivement selon le niveau de chaque
                  élève : plus il est en difficulté, plus ses exercices sont guidés.
                </Text>
              </div>
            </Group>
            <Switch size="md" color="yellow" checked={guidesOn} aria-label="Inclure les guides"
              onChange={(e) => setGuidesOn(e.currentTarget.checked)} />
          </Group>
          <Collapse in={guidesOn}>
            <Checkbox color="yellow" mt="xs" checked={guidesForAll}
              onChange={(e) => setGuidesForAll(e.currentTarget.checked)}
              label="Donner les guides à tous les élèves"
              description="Tous les encadrés d'aide sont imprimés sur toutes les copies, quel que soit le niveau." />
            <Collapse in={!guidesForAll}>
              <Group gap="md" align="center" wrap="nowrap" mt="md">
                <Text size="sm" w={150} style={{ flexShrink: 0 }}>Jusqu'à quel niveau aider</Text>
                <Slider style={{ flex: 1 }} color="yellow" value={guideReach}
                  onChange={setGuideReach} step={5} min={0} max={100}
                  label={(v) => `${v} %`}
                  marks={[0, 25, 50, 75, 100].map((value) => ({ value, label: `${value} %` }))} />
                <Badge size="lg" variant="light" color="yellow" w={70}>{guideReach} %</Badge>
              </Group>
              <Box mt="lg">
                <GuideGradient reach={guideReach} levels={elig?.levels ?? []} />
                <Text size="xs" c="dimmed" ta="center" mt={4}>
                  Part d'exercices guidés par niveau (1 → 10), et nombre d'élèves de la classe à ce niveau
                </Text>
              </Box>
            </Collapse>
          </Collapse>
        </Stack>
      </Card>
    </Stack>
  )

  const stepCompetences = (
    <Stack gap="sm" maw={1180} mx="auto" w="100%" pt="lg" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" align="flex-end" wrap="nowrap">
        <StepIntro title="Les compétences">
          {mode === 'auto'
            ? "Le périmètre du sujet : chaque élève aura au moins un exercice par compétence cochée, puis davantage sur ses lacunes."
            : "Les exercices proposés à l'étape suivante viendront de ces compétences ; les problèmes, de leurs chapitres."}
        </StepIntro>
        <Group gap="xs" wrap="nowrap">
          <Badge size="lg" variant={competencyIds.length ? 'filled' : 'light'}>
            {competencyIds.length} cochée{competencyIds.length > 1 ? 's' : ''}
          </Badge>
          <Tooltip label="Cocher les compétences à revoir d'après la courbe de l'oubli des élèves de la classe">
            <Button size="xs" variant="light" leftSection={<Sparkles size={14} />}
              loading={suggesting} onClick={suggest}>Suggérer</Button>
          </Tooltip>
          <Button size="xs" variant="subtle" color="gray" disabled={!competencyIds.length}
            onClick={() => setCompetencyIds([])}>Tout décocher</Button>
        </Group>
      </Group>
      {mode === 'auto' && (
        <Card withBorder padding="sm"
          style={problems ? { borderColor: 'var(--mantine-color-orange-filled)' } : undefined}>
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <Group gap="sm" wrap="nowrap" align="flex-start">
              <ThemeIcon variant="light" color="orange" size={34}><Puzzle size={18} /></ThemeIcon>
              <div>
                <Text fw={600} size="sm">Ajouter des problèmes</Text>
                <Text size="xs" c="dimmed">
                  Réservés aux élèves de niveau {PROBLEM_MIN_LEVEL} et plus : un mélange
                  exercices/problèmes aux niveaux 7-8, beaucoup de problèmes, surtout
                  difficiles, aux niveaux 9-10. Toujours imprimés après les exercices.
                </Text>
                {competencyIds.length > 0 && (
                  <Group gap={6} mt={6}>
                    <Badge size="sm" variant="light" color={problemsAvailable ? 'orange' : 'gray'}>
                      {problemsAvailable} problème{problemsAvailable > 1 ? 's' : ''} dans les chapitres cochés
                    </Badge>
                    <Badge size="sm" variant="light" color={strongStudents ? 'orange' : 'gray'}>
                      {strongStudents} élève{strongStudents > 1 ? 's' : ''} concerné{strongStudents > 1 ? 's' : ''}
                    </Badge>
                  </Group>
                )}
              </div>
            </Group>
            <Switch size="md" color="orange" checked={problems}
              onChange={(e) => setProblems(e.currentTarget.checked)} />
          </Group>
        </Card>
      )}
      <CompetencyMatrixStep gradeLevel={grade} classId={classId} subjectId={subject} groupId={groupId} selected={competencyIds}
        onChange={setCompetencyIds} onMatrix={setMatrix} />
    </Stack>
  )

  const bands = {
    facile: levelCount((l) => l <= 4), moyen: levelCount((l) => l >= 5 && l <= 7),
    difficile: levelCount((l) => l >= 8),
  }
  const stepVariantes = (
    <Stack gap="lg" maw={980} mx="auto" w="100%" py="lg">
      <StepIntro title="Un ou plusieurs sujets ?">
        Les variantes sont des sujets équivalents composés séparément à l'étape suivante.
      </StepIntro>
      <Group align="stretch" gap="sm">
        <ChoiceCard selected={variantKind === 'none'} icon={Layers} title="Un seul sujet"
          onClick={() => applyVariantKind('none')}
          desc="Toute la classe a les mêmes exercices, mais chaque copie est mélangée pour éviter la triche." />
        <ChoiceCard selected={variantKind === 'anticheat'} icon={Dices} title="Variantes aléatoires"
          onClick={() => applyVariantKind('anticheat')}
          desc="Plusieurs sujets équivalents, répartis au hasard entre les élèves, en nombre égal." />
        <ChoiceCard selected={variantKind === 'level'} icon={TrendingUp} title="Variantes par niveau"
          onClick={() => applyVariantKind('level')}
          desc="Trois sujets — facile, moyen, difficile — attribués d'après le niveau de chaque élève." />
      </Group>
      {variantKind === 'none' && (
        <Card withBorder padding="md">
          <Stack gap={6}>
            <Group gap={8} wrap="nowrap"><Shuffle size={15} opacity={0.7} />
              <Text size="sm">QCM, lignes de tableau et points à relier changent d'ordre sur chaque copie.</Text></Group>
            <Group gap={8} wrap="nowrap"><ListOrdered size={15} opacity={0.7} />
              <Text size="sm">Les cartes changent aussi de place : facile, puis moyen, puis difficile, à une carte près.</Text></Group>
            <Group gap={8} wrap="nowrap"><Puzzle size={15} opacity={0.7} />
              <Text size="sm">Les problèmes restent toujours à la fin.</Text></Group>
          </Stack>
        </Card>
      )}
      {variantKind === 'anticheat' && (
        <Card withBorder padding="md">
          <Group justify="space-between">
            <div>
              <Text size="sm" fw={600}>Nombre de variantes</Text>
              <Text size="xs" c="dimmed">
                Environ {cls?.student_count ? Math.ceil(cls.student_count / variants.length) : '…'} élèves
                par variante. À l'étape suivante, « Décliner » compose une variante
                à partir d'une autre avec des exercices équivalents.
              </Text>
            </div>
            <SegmentedControl value={String(variants.length)}
              onChange={(v) => applyVariantKind('anticheat', Number(v))}
              data={['2', '3', '4', '5', '6']} />
          </Group>
        </Card>
      )}
      {variantKind === 'level' && (
        <SimpleGrid cols={3} spacing="sm">
          {([['facile', '1 à 4', 'green'], ['moyen', '5 à 7', 'yellow'], ['difficile', '8 à 10', 'red']] as const)
            .map(([k, range, color]) => (
              <Card key={k} withBorder padding="sm">
                <Group justify="space-between">
                  <Badge color={color} variant="light">{LEVEL_LABELS[k]}</Badge>
                  <Text size="xs" c="dimmed">niveaux {range}</Text>
                </Group>
                <Text size="xl" fw={700} mt={6}>{bands[k]}</Text>
                <Text size="xs" c="dimmed">élève{bands[k] > 1 ? 's' : ''} de la classe</Text>
              </Card>
            ))}
        </SimpleGrid>
      )}
    </Stack>
  )

  const variantHint = variantKind === 'level'
    ? `Variante « ${LEVEL_LABELS[variants[current]?.key] ?? ''} » : pour les élèves de ce niveau.`
    : variantKind === 'anticheat'
      ? `${variants[current]?.label} : un élève sur ${variants.length}, tiré au hasard.`
      : 'Mêmes cartes pour toute la classe, mélangées sur chaque copie.'
  const stepPage = (
    <Stack gap="xs" pt="sm" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" wrap="nowrap">
        <Group gap={6} wrap="nowrap">
          {variants.map((v, i) => (
            <Button key={v.key} size="compact-sm"
              variant={i === current ? 'filled' : 'default'}
              color={variantKind === 'level'
                ? (['green', 'yellow', 'red'][i] ?? 'indigo') : 'indigo'}
              onClick={() => setCurrent(i)}
              rightSection={<Badge size="xs" variant="white" color="gray">{v.selection.length}</Badge>}>
              {v.label}
            </Button>
          ))}
          {variantKind !== 'none' && current > 0 && (
            <>
              <Divider orientation="vertical" />
              <Tooltip label={`Reprendre les cartes de « ${variants[current - 1].label} »`}>
                <Button size="compact-sm" variant="subtle" leftSection={<CopyIcon size={13} />}
                  onClick={() => copyInto(current, current - 1)}>
                  Recopier « {variants[current - 1].label} »
                </Button>
              </Tooltip>
              {variantKind === 'anticheat' && (
                <Tooltip label={`Chaque carte de « ${variants[0].label} » remplacée par un équivalent tiré au hasard (même compétence, même difficulté)`}>
                  <Button size="compact-sm" variant="light" leftSection={<Dices size={13} />}
                    disabled={!pool || variants[0].selection.length === 0}
                    onClick={() => deriveInto(current, 0)}>
                    Décliner « {variants[0].label} »
                  </Button>
                </Tooltip>
              )}
            </>
          )}
        </Group>
        <Text size="xs" c="dimmed">{variantHint}</Text>
      </Group>
      {loadingPool && !pool && <Text size="sm" c="dimmed">Chargement des exercices…</Text>}
      {pool && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <ExercisePicker
            pool={pool.exercises} problems={pool.problems}
            metrics={pool.metrics} guidesOn={guidesOn} pages={pages}
            selection={variants[current]?.selection ?? []}
            onChange={(selection) => setVariants((vs) =>
              vs.map((v, i) => (i === current ? { ...v, selection } : v)))} />
        </div>
      )}
    </Stack>
  )

  const guideSummary = !guidesOn ? 'Non inclus'
    : guidesForAll ? 'Pour tous les élèves' : `Dégradé selon le niveau — réglage ${guideReach} %`
  const firstSummary = summaries[0]?.summary
  const emptyPages = summaries.flatMap(({ variant, summary }) => (summary.fits && summary.count
    ? summary.pages.map((part, p) => (part === 0
      ? `${variantKind === 'none' ? '' : `${variant.label} : `}${sideLabel(p, pages)}` : null)).filter((x): x is string => !!x)
    : []))
  const stepGeneration = (
    <Stack gap="md" maw={640} mx="auto" w="100%" py="lg">
      <StepIntro title="Vérifier et lancer">
        La génération tourne en file de fond : la fenêtre se ferme aussitôt, le sujet
        apparaît dans la liste dès qu'il est prêt.
      </StepIntro>
      <Card withBorder padding="md">
        <Row k="Classe" v={cls ? `${cls.name} (${cls.grade_level})` : '—'} />
        <Row k="Mode" v={mode === 'auto'
          ? `Automatique — une copie personnalisée par élève (${elig?.students ?? cls?.student_count ?? '…'})`
          : 'Manuel'} />
        <Row k="Titre" v={finalTitle} />
        <Row k="Type" v={`${type === 'control' ? 'Contrôle noté' : 'Entraînement'} /${noteBase}`} />
        <Row k="Pages" v={`${pages} page${pages > 1 ? 's' : ''}`} />
        <Row k="Guides" v={guideSummary} />
        <Row k="Compétences" v={`${competencyIds.length} · ${touched.map((ch) => ch.code).join(', ')}`} />
        {mode === 'auto' ? (
          <>
            <Row k="Problèmes" v={problems
              ? `Oui — pour ${strongStudents} élève${strongStudents > 1 ? 's' : ''} de niveau ${PROBLEM_MIN_LEVEL}+`
              : 'Non'} />
            <Row k="Ordre des exercices" v="Du plus simple au plus difficile, problèmes en dernier" />
          </>
        ) : (
          <>
            <Row k="Variantes" v={variantKind === 'none' ? 'Sujet unique, mélangé sur chaque copie'
              : `${variants.length} · ${variantKind === 'level' ? 'par niveau' : 'aléatoires'}`} />
            <Row k="Exercices choisis" v={`${placed} au total`} />
            {variantKind === 'none' && firstSummary && (
              <Row k="Remplissage et durée"
                v={`${Math.round(firstSummary.fill * 100)} % · ≈ ${formatDuration(firstSummary.seconds)}`} />
            )}
          </>
        )}
      </Card>
      {mode === 'auto' && elig && elig.corrected < elig.recommended && (
        <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
          {elig.corrected} évaluation{elig.corrected > 1 ? 's' : ''} corrigée{elig.corrected > 1 ? 's' : ''} sur
          les {elig.recommended} conseillées : le niveau de chaque élève, et donc ses exercices, seront moins précis.
        </Alert>
      )}
      {mode === 'auto' && problems && problemsAvailable === 0 && (
        <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
          Aucun problème publié dans les chapitres cochés : les copies n'en contiendront pas.
        </Alert>
      )}
      {mode === 'auto' && problems && problemsAvailable > 0 && strongStudents === 0 && (
        <Alert color="blue" p="xs">
          Aucun élève de niveau {PROBLEM_MIN_LEVEL} ou plus dans la classe : aucun problème ne sera imprimé.
        </Alert>
      )}
      {mode === 'manual' && emptyVariants.length > 0 && (
        <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
          {emptyVariants.map((v) => v.label).join(', ')} : aucune carte choisie. Ces
          variantes ne seront pas générées — revenez à l'étape précédente pour les composer.
        </Alert>
      )}
      {mode === 'manual' && overfull.length > 0 && (
        <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
          Trop de cartes pour {pages === 1 ? 'la page' : `les ${pages} pages`} : {overfull.join(', ')}.
          Revenez à l'étape précédente pour en retirer.
        </Alert>
      )}
      {mode === 'manual' && overfull.length === 0 && emptyPages.length > 0 && (
        <Alert color="blue" p="xs">
          Sans aucun exercice : {emptyPages.join(', ')}. Ces faces seront imprimées vides.
        </Alert>
      )}
      <Button size="md" leftSection={<Rocket size={18} />} onClick={createAndGenerate}
        loading={busy} disabled={mode === 'manual' && (placed === 0 || overfull.length > 0)}
        color={mode === 'auto' ? 'violet' : 'indigo'}>
        {mode === 'auto' ? `Générer ${elig?.students ?? ''} copies personnalisées` : 'Générer le sujet'}
      </Button>
    </Stack>
  )

  const screens: Record<StepKey, ReactNode> = {
    classe: stepClasse, sujet: stepSujet, competences: stepCompetences,
    variantes: stepVariantes, page: stepPage, generation: stepGeneration,
  }
  // la matrice et le mur de cartes gèrent eux-mêmes leur défilement
  const fullHeight = stepKey === 'competences' || stepKey === 'page'

  return (
    <Modal opened={opened} onClose={close} size="calc(100vw - 3rem)" centered
      title={<Group gap="xs">
        <Text fw={700}>Créer un sujet</Text>
        {cls && <Badge variant="light">{cls.name}</Badge>}
        {step > 0 && (
          <Badge variant="dot" color={mode === 'auto' ? 'violet' : 'indigo'}>
            {mode === 'auto' ? 'automatique' : 'manuel'}
          </Badge>
        )}
      </Group>}
      styles={{
        content: { height: 'calc(100vh - 3rem)', display: 'flex', flexDirection: 'column' },
        body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', paddingBottom: 0 },
      }}>
      <Stepper active={step} onStepClick={(i) => i < step && setStep(i)}
        allowNextStepsSelect={false} size="sm" color={mode === 'auto' ? 'violet' : 'indigo'}>
        {steps.map((s) => <Stepper.Step key={s.key} label={s.label} description={s.desc} />)}
      </Stepper>

      <Box style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {fullHeight ? screens[stepKey] : (
          <ScrollArea style={{ flex: 1 }} type="auto">{screens[stepKey]}</ScrollArea>
        )}
      </Box>

      {/* navigation, toujours au même endroit */}
      <Group justify="space-between" py="sm" mt="xs" wrap="nowrap"
        style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}>
        <Button variant="default" leftSection={<ArrowLeft size={16} />}
          onClick={() => (step === 0 ? close() : setStep(step - 1))}>
          {step === 0 ? 'Annuler' : 'Retour'}
        </Button>
        <Text size="xs" c="dimmed">Étape {step + 1} sur {steps.length}</Text>
        {stepKey !== 'generation' ? (
          <Tooltip label={blocker} disabled={!blocker}>
            <Button rightSection={<ArrowRight size={16} />} onClick={next}
              disabled={!!blocker} color={mode === 'auto' ? 'violet' : 'indigo'}>
              {stepKey === 'variantes' ? 'Choisir les exercices' : 'Continuer'}
            </Button>
          </Tooltip>
        ) : <Box w={120} />}
      </Group>
    </Modal>
  )
}

