// Banque d'exercices, organisée par compétence.
// La banque grandit à la demande (cycles réellement enseignés) ; cette page
// donne la couverture, l'aperçu fidèle (KaTeX + figures identiques au PDF),
// le retrait d'un contenu douteux. Toute création reste hors de cet onglet.
import {
  ActionIcon, Badge, Box, Button, Checkbox, Collapse, Group, Loader, Paper, ScrollArea,
  SegmentedControl, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { ChevronDown, ChevronUp, Library } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from '../components/CompetencyHierarchy'
import ExercisePrintPreview from '../components/ExercisePrintPreview'
import GradeSelectionRequired from '../components/GradeSelectionRequired'
import MathText from '../components/MathText'
import { useAppState } from '../state/AppState'

type Summary = {
  competency_id: string; code: string; short_id: string; label: string; grade_level: string
  order_index: number
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
  by_level: Record<string, number>; total: number; problems: number
}
type Exercise = {
  id: string; competency_id: string; level: number; variant: number
  statement: string; correction: string; response_type: string
  choices: string[]; source: string; kind: string
  quality: Record<string, number>; figure: Record<string, any> | null
  bareme_points: number; figure_url: string | null
  calculator: string
  expected: Record<string, any>; grading: Record<string, any>
  row_labels: string[] | null; col_labels: string[] | null; lines: number | null
  correction_solution: string; correction_guide: string
  // extraction brute dont provient cette ligne : shape variable selon la
  // source. Seule source="sesamaths" porte des blocs OCR Mistral affichables
  // (title/text/table/list/equation/image/...) ; les autres sources (indigo,
  // gemini, mathalea) stockent ici des métadonnées de forme différente ou
  // rien — ne JAMAIS supposer `.blocks` présent sans vérifier.
  raw: Record<string, any> | null
}

const RESPONSE_LABELS: Record<string, string> = {
  short_text: 'réponse courte', multiline_text: 'raisonnement rédigé',
  qcm_single: 'QCM', qcm_multiple: 'QCM multiple',
  checkbox_grid: 'grille cochée', multi_blank: 'cases à trous',
  table_fill: 'tableau à remplir', matching: 'points à relier',
  manual_drawing: 'tracé / dessin (correction manuelle)',
  composite: 'types mixtes',
}

/** Barème en écriture française : « 1,5 » et non « 1.5 », entier sans décimale. */
// 3 décimales (pas du barème = 0,125), zéros inutiles retirés — cf. Exercices.tsx
const formatPoints = (v: number) =>
  (Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace('.', ','))

function QualityBadge({ quality }: { quality: Record<string, number> }) {
  const vals = Object.values(quality || {})
  if (!vals.length) return null
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length
  const color = avg >= 4.5 ? 'teal' : avg >= 3.5 ? 'yellow' : 'red'
  return (
    <Tooltip label={Object.entries(quality).map(([k, v]) => `${k} : ${v}/5`).join(' · ')}>
      <Badge size="xs" variant="light" color={color}>qualité {avg.toFixed(1)}</Badge>
    </Tooltip>
  )
}

function ExerciseCard({ ex, showCorrection, showGuide }: {
  ex: Exercise; showCorrection: boolean; showGuide: boolean
}) {
  const [showRaw, setShowRaw] = useState(false)
  // seule la source Sésamaths porte des blocs OCR affichables ici ; les
  // autres sources (indigo, gemini...) ont un raw_extract_json de forme
  // différente (ou absente) — vérifier la forme, pas juste la présence, sinon
  // .map() plante et fait disparaître toute la page (cf. incident du 31/07).
  const rawBlocks = Array.isArray(ex.raw?.blocks) ? ex.raw!.blocks : null
  return (
    <Box>
      <ExercisePrintPreview exercise={ex} color={ex.kind === 'probleme' ? 'orange' : 'indigo'}
        showCorrection={showCorrection} showGuide={showGuide}
        badges={<Group gap={6}>
          <Badge size="xs" variant="filled" color="indigo">Niv. {ex.level}</Badge>
          {ex.kind === 'probleme' && <Badge size="xs" variant="light" color="orange">problème</Badge>}
          <Badge size="xs" variant="light" color="gray">{RESPONSE_LABELS[ex.response_type] ?? ex.response_type}</Badge>
          {/* barème d'effort : ce que l'exercice VAUT, résolu côté API (repli
              déterministe compris) — même information que dans l'onglet Exercices. */}
          {ex.bareme_points > 0 && (
            <Tooltip label="Barème : ce que l'exercice vaut (effort demandé), utilisé pour la note">
              <Badge size="xs" variant="light" color="teal">{formatPoints(ex.bareme_points)} pt</Badge>
            </Tooltip>
          )}
          <QualityBadge quality={ex.quality} />
        </Group>}
        actions={<Group gap={4} wrap="nowrap">
          {rawBlocks && (
            <Tooltip label={showRaw ? 'Masquer le texte original' : 'Voir le texte original extrait du manuel'}>
              <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => setShowRaw((v) => !v)}>
                {showRaw ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </ActionIcon>
            </Tooltip>
          )}
        </Group>}
        beforeFrame={rawBlocks && (
        <Collapse in={showRaw}>
          <Paper bg="var(--mantine-color-default-hover)" p={8} radius="sm" mb={8}
            style={{ borderLeft: '3px solid var(--mantine-color-gray-5)' }}>
            <Text size="xs" c="dimmed" fw={600} mb={4}>Blocs OCR d'origine (Mistral)</Text>
            <Stack gap={4}>
              {rawBlocks.map((b) => (
                <Group key={b.i} gap={6} wrap="nowrap" align="flex-start">
                  <Badge size="xs" variant="outline" color="gray" style={{ flexShrink: 0 }}>
                    {b.type} p.{b.page}
                  </Badge>
                  {b.type === 'table'
                    ? <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
                        {b.content}
                      </Text>
                    : <MathText text={b.content} size="sm" />}
                </Group>
              ))}
            </Stack>
          </Paper>
        </Collapse>
      )} />
    </Box>
  )
}

export default function Bank() {
  const { cycle } = useAppState()
  const [summary, setSummary] = useState<Summary[] | null>(null)
  const [selected, setSelected] = useState<Summary | null>(null)
  const [exercises, setExercises] = useState<Exercise[] | null>(null)
  const [levelFilter, setLevelFilter] = useState('all')
  const [showCorrections, setShowCorrections] = useState(false)
  const [showGuides, setShowGuides] = useState(false)

  const isAll = cycle === 'all'
  useEffect(() => {
    setSelected(null)
    setExercises(null)
    setLevelFilter('all')
    setShowCorrections(false)
    setShowGuides(false)
    if (isAll) {
      setSummary(null)
      return
    }
    setSummary(null)
    let current = true
    api.get<Summary[]>(`/api/content/summary?grade_level=${cycle}`)
      .then((rows) => { if (current) setSummary(rows) })
    return () => { current = false }
  }, [cycle, isAll])

  const loadDetail = useCallback((s: Summary) => {
    setSelected(s)
    setExercises(null)
    setShowCorrections(false)
    setShowGuides(false)
    api.get<Exercise[]>(`/api/content/exercises?competency_id=${s.competency_id}`).then(setExercises)
  }, [])

  const rows = summary ?? []

  // Même contrat hiérarchique que les onglets Exercices et Compétences.
  const domainGroups = useMemo(() => {
    const byDomain = new Map<string, { name: string; chapters: Map<string, { name: string; comps: Summary[] }> }>()
    for (const s of rows) {
      const domainKey = `${s.grade_level}\0${s.domain_code}`
      let d = byDomain.get(domainKey)
      if (!d) { d = { name: s.domain_name, chapters: new Map() }; byDomain.set(domainKey, d) }
      let c = d.chapters.get(s.chapter_code)
      if (!c) { c = { name: s.chapter_name, comps: [] }; d.chapters.set(s.chapter_code, c) }
      c.comps.push(s)
    }
    return Array.from(byDomain.entries()).map(([domainKey, domain]) => {
      const [gradeLevel, domainCode] = domainKey.split('\0')
      const key = `${gradeLevel}/${domainCode || domain.name}`
      return {
        key,
        code: domainCode,
        name: domain.name,
        chapters: Array.from(domain.chapters.entries()).map(([chapterCode, chapter]) => ({
          key: `${key}/${chapterCode || chapter.name}`,
          code: chapterCode,
          name: chapter.name,
          rows: chapter.comps,
        })),
      }
    })
  }, [rows])

  const columns = useMemo<CompetencyHierarchyColumn<Summary>[]>(() => {
    const result: CompetencyHierarchyColumn<Summary>[] = [{
      key: 'exercises', label: 'Exercices', width: 84, align: 'center',
      render: (competency) => <Text size="xs">{competency.total}</Text>,
    }]
    if (!selected) result.push({
      key: 'levels', label: 'Niv. 1–5', width: 102, align: 'center',
      render: (competency) => (
        <Group gap={3} justify="center" wrap="nowrap">
          {[1, 2, 3, 4, 5].map((level) => (
            <Tooltip key={level}
              label={`Niveau ${level} : ${competency.by_level[String(level)]} exercice(s)`}>
              <Box w={9} h={9} style={{
                borderRadius: 2,
                background: competency.by_level[String(level)] > 0
                  ? 'var(--mantine-color-blue-5)'
                  : 'var(--mantine-color-gray-3)',
              }} />
            </Tooltip>
          ))}
        </Group>
      ),
    })
    return result
  }, [selected])

  const shownExercises = useMemo(
    () => (exercises ?? []).filter((e) => levelFilter === 'all' || e.level === Number(levelFilter)),
    [exercises, levelFilter])

  if (isAll) return <GradeSelectionRequired title="Banque de contenus" />

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Title order={2}><Group gap={8}><Library size={22} /> Banque de contenus</Group></Title>
          <Text c="dimmed" size="sm">
            Consultez les exercices disponibles par domaine, chapitre et compétence.
          </Text>
        </Box>
      </Group>

      <Group align="flex-start" gap="md" wrap="nowrap">
        <Paper withBorder radius="md" p="xs" style={{
          flex: 1, minWidth: 0,
        }}>
          {summary === null ? <Loader size="sm" m="md" /> : rows.length === 0 ? (
            <Text c="dimmed" size="sm" p="md">
              Aucune compétence disponible pour ce cycle.
            </Text>
          ) : (
            <ScrollArea.Autosize mah="70vh">
              <CompetencyHierarchy domains={domainGroups} columns={columns}
                showColumnHeaders={false}
                selectedKey={selected?.competency_id}
                getRowKey={(competency) => competency.competency_id}
                getShortId={(competency) => competency.short_id || competency.code}
                getLabel={(competency) => competency.label}
                onRowClick={loadDetail}
                chapterAside={(chapter) => {
                  const problems = chapter.rows.reduce((total, competency) => total + competency.problems, 0)
                  return (
                    <Tooltip label="Problèmes transverses du chapitre, comptés une seule fois">
                      <Group gap={5} wrap="nowrap">
                        <Text size="xs" c="dimmed">Problèmes</Text>
                        <Text size="xs" fw={700}>{problems || '—'}</Text>
                      </Group>
                    </Tooltip>
                  )
                }} />
            </ScrollArea.Autosize>
          )}
        </Paper>

        {selected && (
          <Paper withBorder radius="md" p="md" style={{
            flex: '0 0 372px', width: 372, minWidth: 372,
          }}>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Box>
                <Text fw={600}>{selected.short_id || selected.code} — {selected.label}</Text>
                <Text size="xs" c="dimmed">{selected.grade_level} · {selected.domain_name} · {selected.chapter_name}</Text>
              </Box>
              <Button size="compact-xs" variant="subtle" onClick={() => setSelected(null)}>Fermer</Button>
            </Group>
            <Group justify="space-between" mb="xs" align="flex-end" wrap="wrap">
              <Group gap="md">
                <Checkbox size="xs" checked={showCorrections}
                  onChange={(e) => setShowCorrections(e.currentTarget.checked)}
                  label="Afficher les corrections" />
                <Checkbox size="xs" checked={showGuides}
                  onChange={(e) => setShowGuides(e.currentTarget.checked)}
                  label="Afficher les guides élèves" />
              </Group>
              <SegmentedControl size="xs" value={levelFilter} onChange={setLevelFilter}
                data={[{ value: 'all', label: 'Tous' },
                  ...[1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: `Niv. ${l}` }))]} />
            </Group>
                {exercises === null ? <Loader size="sm" /> : (
                  <ScrollArea.Autosize mah="58vh">
                    <Stack gap="xs">
                      {shownExercises.map((ex) => (
                        <ExerciseCard key={ex.id} ex={ex}
                          showCorrection={showCorrections} showGuide={showGuides} />
                      ))}
                      {shownExercises.length === 0 && (
                        <Text c="dimmed" size="sm">Aucun exercice disponible pour ce filtre.</Text>
                      )}
                    </Stack>
                  </ScrollArea.Autosize>
                )}
          </Paper>
        )}
      </Group>
    </Stack>
  )
}
