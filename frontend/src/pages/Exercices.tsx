// Onglet Exercices (Indigo) — ADMIN uniquement.
// Copie/adaptation d'exercices d'un manuel réel. Vue par défaut : tableau de
// TOUTES les compétences de la classe (toggle 6/5/4/3) avec brouillon/validé/
// publié. Clic sur une compétence → ses exercices, un par carte (largeur d'une
// demi-colonne A4) : extrait manuel → tags/badges → énoncé → guide → corrigé →
// actions. « Modifier » ouvre une modale d'édition complète.
import {
  ActionIcon, Alert, Badge, Box, Button, Card, Checkbox, Group,
  Loader, Modal, NumberInput, Paper, Progress, ScrollArea, SegmentedControl, Select,
  Stack, Table, TagsInput, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, BookOpen, Calculator, Check, CheckCircle2, CheckSquare, ChevronLeft,
  ImageOff, ImagePlus, Minus, Pencil, Plus, RefreshCw, RotateCcw, Slash, Sparkles,
  Trash2, UploadCloud, Wand2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import AuthImg from '../components/AuthImg'
import MathText from '../components/MathText'
import { useAppState } from '../state/AppState'

// largeur d'une demi-colonne d'A4 (≈ 86 mm) : l'aperçu ressemble à l'impression
const CARD_W = 340

// ------------------------------------------------------------------- types
type Comp = {
  id: string; code: string; short_id: string; label: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
}
type Manuals = { grade_level: string; manuals: { eleve: ManualInfo; prof: ManualInfo } }
type ManualInfo = { available: boolean; pages: number }
type SummaryRow = {
  competency_id: string; short_id: string; label: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
  draft: number; validated: number; published: number; done: boolean
}
type Extraction = {
  id: string; status: string; progress: number; progress_message: string
  error_message: string; stats: Record<string, any>; created_at: string
}
type Exercise = {
  id: string; ref: string; competency_id: string; competency_short_id: string
  source_page: number; source_number: string; order_index: number
  badge_type: string; difficulty: number; calculator: string
  title: string; tags: string[]; has_figure: boolean; figure_required: boolean
  statement: string; response_type: string; expected: Record<string, any>; choices: string[]
  adapted: boolean
  row_labels: string[] | null; col_labels: string[] | null; lines: number | null
  bareme_points: number; correction_solution: string; correction_guide: string
  status: string; crop_url: string | null; figure_url: string | null
  // provenance brute : raw_ocr.pipeline vaut "cli-exos" pour la pipeline CLI
  // (agents/cli-exos, abonnement Claude) — sinon c'est l'extraction Indigo (API).
  raw_ocr: Record<string, any> | null
}

// --- palette de badges PROPRE (≠ manuel) : on re-colore à notre façon
const BADGE_COLOR: Record<string, string> = {
  exercice: 'indigo', flash: 'yellow', expert: 'grape', enigme: 'pink', probleme: 'orange',
}
const PROBLEME_COLOR: Record<number, string> = { 2: 'green', 3: 'orange', 4: 'red' }
// difficulté = 3 niveaux, UNIQUEMENT pour les problèmes (2/3/4 = facile/moyen/difficile)
const DIFF_LABEL: Record<number, string> = { 2: 'Facile', 3: 'Moyen', 4: 'Difficile' }
const DIFF_OPTS = [
  { value: '2', label: 'Facile' }, { value: '3', label: 'Moyen' }, { value: '4', label: 'Difficile' },
]
const isProbleme = (ex: { badge_type: string }) => ex.badge_type === 'probleme' || ex.badge_type === 'enigme'
const BADGE_LABEL: Record<string, string> = {
  exercice: 'Exercice', flash: 'Flash', expert: 'Expert', enigme: 'Énigme', probleme: 'Problème',
}
// Listés dans l'ORDRE DE PRIORITÉ imposé au générateur (cf.
// prompts/indigo/generation.txt) : cocher > relier > écrire court > tableau >
// cases dans le texte > rédiger > tracer.
const RESPONSE_TYPES = [
  { value: 'qcm_single', label: 'QCM (une réponse)' },
  { value: 'qcm_multiple', label: 'QCM (plusieurs)' },
  { value: 'checkbox_grid', label: 'Grille cochée (Vrai/Faux…)' },
  { value: 'matching', label: 'Points à relier' },
  { value: 'short_text', label: 'Réponse courte' },
  { value: 'table_fill', label: 'Tableau à remplir' },
  { value: 'multi_blank', label: 'Cases à trous' },
  { value: 'multiline_text', label: 'Raisonnement rédigé' },
  { value: 'manual_drawing', label: 'Tracé (correction manuelle)' },
  { value: 'composite', label: 'Composite (types mixtes)' },
]
const CALC_OPTS = [
  { value: 'autorisee', label: 'Autorisée' },
  { value: 'necessaire', label: 'Nécessaire' },
  { value: 'interdite', label: 'Interdite' },
]

function badgeColor(ex: { badge_type: string; difficulty: number }) {
  if (ex.badge_type === 'probleme') return PROBLEME_COLOR[ex.difficulty] ?? 'gray'
  return BADGE_COLOR[ex.badge_type] ?? 'gray'
}
const rtLabel = (v: string) => RESPONSE_TYPES.find((r) => r.value === v)?.label ?? v
/** Barème en écriture française : « 1,5 » et non « 1.5 », entier sans décimale. */
// pas du barème = 0,125 : `toFixed(1)` afficherait « 0,1 » pour un huitième de
// point. On garde 3 décimales et on retire les zéros inutiles (1,5 pas 1,500).
export const formatPoints = (v: number) =>
  (Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace('.', ','))

function CalcIcon({ mode, size = 18 }: { mode: string; size?: number }) {
  if (mode === 'autorisee') return null
  const necessaire = mode === 'necessaire'
  return (
    <Tooltip label={necessaire ? 'Calculatrice nécessaire' : 'Calculatrice interdite'}>
      <Box style={{ position: 'relative', width: size, height: size }}>
        <Calculator size={size} color={necessaire ? 'var(--mantine-color-blue-6)' : 'var(--mantine-color-red-6)'} />
        {!necessaire && <Slash size={size} color="var(--mantine-color-red-6)" style={{ position: 'absolute', left: 0, top: 0 }} />}
      </Box>
    </Tooltip>
  )
}

// ----------------------------------------------------- aperçu de l'énoncé
function ResponseZone({ ex }: { ex: Exercise }) {
  const rt = ex.response_type
  const inlineBlank = (ex.statement || '').includes('{{blank}}')
  if (rt === 'qcm_single' || rt === 'qcm_multiple') {
    // 1 à 3 colonnes selon le nombre de propositions et leur longueur (comme à
    // l'impression, pdgen._qcm_layout) — évite de laisser trop de blanc.
    const n = ex.choices.length
    const maxLen = Math.max(0, ...ex.choices.map((c) => c.replace(/\$/g, '').length))
    // MÊME règle qu'à l'impression (backend pdfgen._qcm_ncols_cap) : colonnes
    // RÉSERVÉES aux réponses courtes (type chiffres) et nombreuses ; dès qu'une
    // proposition est longue (phrase), on reste en liste (1 colonne).
    const ncols = (maxLen > 16 || n < 4) ? 1 : (maxLen <= 6 && n >= 6 ? 3 : 2)
    return (
      <Box mt={6} style={{ columnCount: ncols, columnGap: 14 }}>
        {ex.choices.map((c, i) => (
          <Group key={i} gap={8} wrap="nowrap" align="center" mb={4} style={{ breakInside: 'avoid' }}>
            <Box style={{ width: 14, height: 14, border: '1.5px solid #888', borderRadius: 3, flex: '0 0 auto' }} />
            <MathText text={c} size="sm" />
          </Group>
        ))}
      </Box>
    )
  }
  if (rt === 'checkbox_grid') {
    // grille cochée : lignes = sous-questions, colonnes = options (Vrai/Faux…),
    // une case à cocher par option. Vue élève : les bonnes réponses sont masquées.
    const cols: string[] = ex.expected?.cols ?? []
    const rows: { label: string; correct: number }[] = ex.expected?.rows ?? []
    return (
      <Table withTableBorder withColumnBorders mt={8} styles={{ td: { padding: 4 }, th: { padding: 4 } }}>
        <Table.Thead><Table.Tr>
          <Table.Th />
          {cols.map((c, i) => <Table.Th key={i} ta="center"><MathText text={c} size="xs" /></Table.Th>)}
        </Table.Tr></Table.Thead>
        <Table.Tbody>
          {rows.map((r, ri) => (
            <Table.Tr key={ri}>
              <Table.Td><MathText text={r.label} size="sm" /></Table.Td>
              {cols.map((_c, ci) => (
                <Table.Td key={ci} ta="center">
                  <Box style={{ display: 'inline-block', width: 13, height: 13, border: '1.5px solid #888', borderRadius: 3 }} />
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )
  }
  if (rt === 'matching') {
    // deux colonnes reliées par l'élève : on montre les pastilles de départ et
    // d'arrivée, jamais les paires attendues (vue élève).
    const left: string[] = ex.expected?.left ?? []
    const right: string[] = ex.expected?.right ?? []
    const dot = { width: 7, height: 7, borderRadius: 7, background: '#888', flex: '0 0 auto' }
    return (
      <Group mt={8} align="flex-start" justify="space-between" wrap="nowrap" gap={24}>
        <Box style={{ flex: 1 }}>
          {left.map((l, i) => (
            <Group key={i} gap={8} wrap="nowrap" mb={6}>
              <MathText text={l} size="sm" /><Box style={dot} />
            </Group>
          ))}
        </Box>
        <Box style={{ flex: 1 }}>
          {right.map((r, i) => (
            <Group key={i} gap={8} wrap="nowrap" mb={6}>
              <Box style={dot} /><MathText text={r} size="sm" />
            </Group>
          ))}
        </Box>
      </Group>
    )
  }
  if (rt === 'manual_drawing')
    // cadre libre : l'élève trace/complète, la correction est manuelle
    return (
      <Box mt={8} style={{ height: 90, border: '1px dashed var(--mantine-color-gray-5)',
        borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Text size="xs" c="dimmed">Cadre de tracé (correction manuelle)</Text>
      </Box>
    )
  if (rt === 'short_text' && !inlineBlank)
    return <Box mt={8} style={{ height: 26, border: '1px solid var(--mantine-color-gray-5)', borderRadius: 3 }} />
  if (rt === 'multiline_text') {
    // nombre EXACT de lignes tel qu'il sera imprimé (backend grading.lines,
    // dimensionné sur le corrigé par services.indigo_fields), un peu plus aérées
    const n = Math.max(3, Math.min(12, ex.lines ?? 5))
    return (
      <Box mt={8}>
        {Array.from({ length: n }, (_, i) => (
          <Box key={i} style={{ height: 22, borderBottom: '1px dashed var(--mantine-color-gray-5)' }} />
        ))}
      </Box>
    )
  }
  if (rt === 'table_fill') {
    const cells: any[][] = ex.expected?.cells ?? []
    return (
      <Table withTableBorder withColumnBorders mt={8} styles={{ td: { padding: 4 } }}>
        {ex.col_labels && (
          <Table.Thead><Table.Tr>
            {ex.row_labels && <Table.Th />}
            {ex.col_labels.map((c, i) => <Table.Th key={i}><MathText text={c} size="xs" /></Table.Th>)}
          </Table.Tr></Table.Thead>
        )}
        <Table.Tbody>
          {cells.map((row, r) => (
            <Table.Tr key={r}>
              {ex.row_labels && <Table.Td><MathText text={ex.row_labels[r] ?? ''} size="xs" /></Table.Td>}
              {row.map((cell, c) => (
                <Table.Td key={c} ta="center" style={{ minWidth: 42, height: 22, background: cell?.given ? 'var(--mantine-color-gray-1)' : undefined }}>
                  {cell?.given
                    ? <MathText text={String(cell.value ?? '')} size="xs" />
                    // case à cocher : « coche si vrai » (évite d'écrire oui/non → crédits Mathpix)
                    : (cell?.check !== undefined || cell?.type === 'check')
                      ? <Box style={{ display: 'inline-block', width: 13, height: 13, border: '1.5px solid #888', borderRadius: 3 }} />
                      : null}
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )
  }
  return null
}

// Étiquette de sous-question en tête de ligne : lettre a-h OU nombre 1-2 chiffres,
// suivie d'un point/parenthèse (cf. backend statement.subquestion_label).
const SUBLABEL_RE = /^([a-h]|\d{1,2})[.)]\s+/
const BULLET_RE = /^[•–—-]\s+/

/** Corps d'un texte mis en lignes : chaque « a. »/« 1. » en tête de ligne
 *  devient une PASTILLE colorée (couleur de l'exercice), chaque puce « • » un
 *  point coloré — jamais un « - » (confusion signe moins). Le reste passe par
 *  MathText (formules + espaces insécables). Sert à l'énoncé, au guide et au
 *  corrigé pour une mise en page cohérente avec l'impression. */
function RichBody({ text, color, size }: { text: string; color: string; size?: string }) {
  const lines = (text || '').split('\n')
  // La taille de police n'augmente QUE pour une ligne portant une case à
  // remplir — comme à l'impression (pdfgen blank_fs). Toutes les autres lignes
  // gardent la taille de base : sinon l'aperçu paraît incohérent. Les TROIS
  // variantes de case (standard, pleine largeur, mini) grandissent leur ligne
  // de la même façon — même règle qu'à l'impression (backend has_answer_field) :
  // une phrase ne mélange jamais deux tailles de police selon sa case.
  const fzOf = (ln: string) => (/\{\{(blank(_right)?|mini)\}\}/.test(ln) ? '1.12em' : size)
  return (
    <Box fz={size}>
      {lines.map((ln, i) => {
        const fz = fzOf(ln)
        const lab = ln.match(SUBLABEL_RE)
        if (lab) {
          return (
            <Group key={i} gap={6} align="flex-start" wrap="nowrap" mt={i ? 4 : 0}>
              <Badge color={color} radius="sm" size="sm" variant="filled" style={{ flex: '0 0 auto', marginTop: 2 }}>
                {lab[1]}
              </Badge>
              <Box style={{ flex: 1, minWidth: 0 }}><MathText text={ln.slice(lab[0].length)} size={fz} /></Box>
            </Group>
          )
        }
        const bul = ln.match(BULLET_RE)
        if (bul) {
          return (
            <Group key={i} gap={6} align="flex-start" wrap="nowrap" mt={i ? 3 : 0}>
              <Text component="span" fw={900} style={{ flex: '0 0 auto', lineHeight: 1.35, color: `var(--mantine-color-${color}-6)` }}>•</Text>
              <Box style={{ flex: 1, minWidth: 0 }}><MathText text={ln.slice(bul[0].length)} size={fz} /></Box>
            </Group>
          )
        }
        return <Box key={i} mt={i ? 2 : 0}><MathText text={ln} size={fz} /></Box>
      })}
    </Box>
  )
}

/** Guide d'auto-correction = rappel de leçon : présenté dans un bloc enfant type
 *  « citation » (alinéa, filet coloré) avec une icône livre, taille de police
 *  par défaut (pas d'agrandissement), gras possible. Reste court (cf. prompt). */
function GuideBlock({ text, color }: { text: string; color: string }) {
  return (
    <Box style={{
      borderLeft: `3px solid var(--mantine-color-${color}-4)`,
      background: 'var(--mantine-color-gray-0)', borderRadius: 4, padding: '6px 8px',
    }}>
      <Group gap={6} align="flex-start" wrap="nowrap">
        <BookOpen size={15} color={`var(--mantine-color-${color}-6)`} style={{ flex: '0 0 auto', marginTop: 2 }} />
        <Box style={{ flex: 1, minWidth: 0 }}><RichBody text={text} color={color} size="sm" /></Box>
      </Group>
    </Box>
  )
}

// Marqueur de PLACEMENT de l'image (cf. backend statement.py) : coupe l'énoncé
// à cet endroit pour insérer la figure EXACTEMENT là (aperçu = feuille imprimée).
const FIGURE_TOKEN = '{{figure}}'
const stripFigureToken = (s: string) =>
  (s || '').replace(/^[ \t]*\{\{figure\}\}[ \t]*\n?/gm, '').replace(/\{\{figure\}\}/g, '').trim()

function StatementPreview({ ex, color }: { ex: Exercise; color: string }) {
  const figImg = ex.figure_url
    ? <AuthImg src={ex.figure_url} alt="figure" style={{ maxWidth: '100%', marginTop: 6, marginBottom: 6, display: 'block' }} />
    : null
  // exercice COMPOSITE : contexte commun, puis chaque sous-question (a./b./c.)
  // avec SON propre format de réponse — rendu comme une carte unifiée à l'impression.
  if (ex.response_type === 'composite') {
    const parts: any[] = ex.expected?.parts ?? []
    return (
      <Box>
        <RichBody text={stripFigureToken(ex.statement)} color={color} />
        {figImg}
        <Stack gap={8} mt={8}>
          {parts.map((p, i) => {
            const g = p.grading ?? {}
            const partEx = { ...ex, response_type: p.response_type, statement: p.statement || '',
              expected: p.expected ?? {}, choices: g.choices ?? [],
              col_labels: g.col_labels ?? null, row_labels: g.row_labels ?? null,
              lines: g.lines ?? null } as Exercise
            return (
              <Group key={i} gap={6} align="flex-start" wrap="nowrap">
                <Badge color={color} radius="sm" size="sm" variant="filled" style={{ flex: '0 0 auto', marginTop: 2 }}>
                  {String.fromCharCode(97 + i)}
                </Badge>
                <Box style={{ flex: 1, minWidth: 0 }}>
                  <RichBody text={p.statement || ''} color={color} />
                  <ResponseZone ex={partEx} />
                </Box>
              </Group>
            )
          })}
        </Stack>
      </Box>
    )
  }
  // l'image se place AU marqueur {{figure}} (comme à l'impression) ; sans
  // marqueur (ou sans image), elle reste après l'énoncé (comportement d'avant).
  if (figImg && (ex.statement || '').includes(FIGURE_TOKEN)) {
    const idx = ex.statement.indexOf(FIGURE_TOKEN)
    const before = ex.statement.slice(0, idx).replace(/\n+$/, '')
    const after = ex.statement.slice(idx + FIGURE_TOKEN.length).replace(/^\n+/, '')
    return (
      <Box>
        {before.trim() && <RichBody text={before} color={color} />}
        {figImg}
        {after.trim() && <RichBody text={after} color={color} />}
        <ResponseZone ex={ex} />
      </Box>
    )
  }
  return (
    <Box>
      <RichBody text={stripFigureToken(ex.statement)} color={color} />
      {figImg}
      <ResponseZone ex={ex} />
    </Box>
  )
}

// ----------------------------------------------------- tags + badges (item 2)
function BadgeRow({ ex }: { ex: Exercise }) {
  const isProb = isProbleme(ex)
  return (
    <Group gap={6} wrap="wrap" align="center">
      <Badge color={badgeColor(ex)} radius="sm" size="sm">{ex.ref}</Badge>
      <Badge color={badgeColor(ex)} variant="light" size="sm">
        {BADGE_LABEL[ex.badge_type] ?? 'Exercice'}{isProb && ex.title ? ` · ${ex.title}` : ''}
        {/* la difficulté (3 niveaux) n'est affichée QUE pour les problèmes */}
        {isProb ? ` · ${DIFF_LABEL[ex.difficulty] ?? 'Moyen'}` : ''}
      </Badge>
      <CalcIcon mode={ex.calculator} size={16} />
      <Badge variant="outline" color="gray" size="xs">{rtLabel(ex.response_type)}</Badge>
      {/* barème : ce que l'exercice VAUT (multiple de 0,125, jusqu'à 5). Affiché
          ici comme dans la Banque — c'est LE barème qui pilote la note finale. */}
      {ex.bareme_points > 0 && (
        <Tooltip label="Barème : ce que l'exercice vaut (réflexion × complexité), utilisé pour la note">
          <Badge variant="light" color="teal" size="xs">{formatPoints(ex.bareme_points)} pt</Badge>
        </Tooltip>
      )}
      {/* provenance : distingue la pipeline CLI (abonnement Claude) de l'extraction
          Indigo (API). Purement informatif — le CRUD/validation/publication est commun. */}
      {ex.raw_ocr?.pipeline === 'cli-exos' && (
        <Tooltip label="Produit par la pipeline cli-exos (CLI Claude, abonnement — sans API)">
          <Badge color="cyan" variant="light" size="xs">CLI</Badge>
        </Tooltip>
      )}
      {/* échec SILENCIEUX de l'adaptation LLM : l'exercice est un repli OCR brut
          (ni QCM, ni cases par sous-question, guide/corrigé « à compléter »).
          Ce n'est pas une mauvaise génération mais une adaptation qui n'a rien
          produit (clé Anthropic absente, budget atteint, erreur API). */}
      {ex.adapted === false && (
        <Tooltip label="Adaptation LLM échouée : exercice en repli OCR brut (non adapté). Vérifie la clé Anthropic (Paramètres → Fournisseurs) et la page Coûts, puis relance l'extraction.">
          <Badge color="red" variant="filled" size="xs" leftSection={<AlertTriangle size={11} />}>Non adapté</Badge>
        </Tooltip>
      )}
      {/* couche « besoin de figure » (indice textuel + Claude) : signale un
          énoncé potentiellement incompréhensible sans image, même quand aucun
          repli n'a pu être rattaché (placeholder sans géométrie) */}
      {ex.figure_required && !ex.has_figure && (
        <Tooltip label="Cet exercice semble dépendre d'un schéma/image du manuel, mais aucune image n'a pu être rattachée — vérifie l'énoncé.">
          <Badge color="red" variant="light" size="xs" leftSection={<ImageOff size={11} />}>Image manquante</Badge>
        </Tooltip>
      )}
      {/* les tags de compétence ne sont pas des tags de difficulté : conservés en info admin */}
      {ex.tags?.map((t) => <Badge key={t} variant="dot" color="gray" size="xs">{t}</Badge>)}
      {ex.status === 'validated'
        ? <Badge color="green" size="xs" leftSection={<Check size={11} />}>Validé</Badge>
        : <Badge color="orange" variant="light" size="xs">Brouillon</Badge>}
    </Group>
  )
}

// ----------------------------------------------------- carte exercice (colonne)
function ExerciseCard({ ex, onEdit, onChange, onDelete, selectable, selected, onToggleSelect }: {
  ex: Exercise; onEdit: (e: Exercise) => void
  onChange: (e: Exercise) => void; onDelete: (id: string) => void
  selectable?: boolean; selected?: boolean; onToggleSelect?: (id: string, v: boolean) => void
}) {
  const validate = async () => {
    const updated = await api.post<Exercise>(`/api/indigo/exercises/${ex.id}/validate`)
    onChange(updated)
    notifications.show({ color: 'green', message: `${ex.ref} validé` })
  }
  const color = badgeColor(ex)
  // Exercice VALIDÉ = travail terminé : la carte est grisée et atténuée pour que
  // les brouillons restants ressortent d'un coup d'œil. Elle reste entièrement
  // lisible et actionnable (on doit pouvoir la relire, la modifier, l'invalider).
  const done = ex.status === 'validated'
  return (
    <Card withBorder radius="md" p="sm"
      style={{
        width: CARD_W,
        outline: selected ? '2px solid var(--mantine-color-blue-5)' : undefined,
        background: done ? 'var(--mantine-color-gray-1)' : undefined,
        borderColor: done ? 'var(--mantine-color-gray-4)' : undefined,
        opacity: done ? 0.72 : undefined,
      }}>
      {/* sélection (mode « régénérer ») */}
      {selectable && (
        <Checkbox mb={8} checked={!!selected} label={<Text size="xs" fw={600}>Sélectionner</Text>}
          onChange={(e) => onToggleSelect?.(ex.id, e.currentTarget.checked)} />
      )}
      {/* 1 — extrait du manuel (image de référence, non éditable) */}
      {ex.crop_url && (
        <Box mb={8}>
          <Text size="10px" c="dimmed" mb={2}>Extrait du manuel</Text>
          <AuthImg src={ex.crop_url} alt="extrait" style={{ maxWidth: '100%', display: 'block', border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4 }} />
        </Box>
      )}
      {/* 2 — tags + badges */}
      <BadgeRow ex={ex} />
      {/* 3 — énoncé (avec figure + zone de réponse) */}
      <Paper withBorder p="xs" radius="sm" mt={8}>
        <StatementPreview ex={ex} color={color} />
      </Paper>
      {/* 4 — guide d'auto-correction (élève) = rappel de leçon en bloc citation */}
      {ex.correction_guide && (
        <Box mt={8}>
          <Text size="10px" fw={700} c="dimmed" mb={2}>Guide (élève)</Text>
          <GuideBlock text={ex.correction_guide} color={color} />
        </Box>
      )}
      {/* 5 — corrigé (prof) */}
      {ex.correction_solution && (
        <Box mt={6}>
          <Text size="10px" fw={700} c="dimmed">Corrigé (prof)</Text>
          <RichBody text={ex.correction_solution} color={color} size="sm" />
        </Box>
      )}
      {/* 6 — actions */}
      <Group justify="space-between" mt={10}>
        <ActionIcon color="red" variant="subtle" onClick={() => onDelete(ex.id)}><Trash2 size={16} /></ActionIcon>
        <Group gap={6}>
          <Button size="xs" variant="light" leftSection={<Pencil size={14} />} onClick={() => onEdit(ex)}>Modifier</Button>
          {ex.status !== 'validated' &&
            <Button size="xs" color="green" leftSection={<Check size={14} />} onClick={validate}>Valider</Button>}
        </Group>
      </Group>
    </Card>
  )
}

// ----------------------------------------------------- modale d'édition
function EditModal({ ex, onClose, onSaved, onChange }: {
  ex: Exercise | null; onClose: () => void; onSaved: (e: Exercise) => void
  onChange: (e: Exercise) => void   // répercute sur la carte SANS fermer la modale
}) {
  const [form, setForm] = useState<Exercise | null>(ex)
  const [figV, setFigV] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setForm(ex); setFigV(0) }, [ex])
  if (!form) return null
  const isProb = form.badge_type === 'probleme' || form.badge_type === 'enigme'
  const correct: number[] = form.expected?.correct ?? []

  const nudge = async (edge: 'left' | 'top' | 'right' | 'bottom', dir: 1 | -1) => {
    const body = { left: 0, top: 0, right: 0, bottom: 0, [edge]: 14 * dir }
    const updated = await api.post<Exercise>(`/api/indigo/exercises/${form.id}/figure`, body)
    setForm(updated); setFigV((v) => v + 1)      // reloadKey => AuthImg re-fetch instantané
  }

  const removeFigure = async () => {
    const updated = await api.del<Exercise>(`/api/indigo/exercises/${form.id}/figure`)
    setForm(updated)
    onChange(updated)  // répercute tout de suite sur la carte (plus de « image indisponible »), modale ouverte
    notifications.show({ color: 'gray', message: 'Image de l\'énoncé supprimée' })
  }

  const addFigure = async () => {
    try {
      const updated = await api.post<Exercise>(`/api/indigo/exercises/${form.id}/figure/add`)
      setForm(updated); setFigV((v) => v + 1); onChange(updated)
      notifications.show({ color: 'green', message: 'Image ajoutée depuis l\'extrait du manuel — ajuste le cadrage' })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
  }

  const buildPatch = () => {
    const p: any = {
      statement: form.statement, response_type: form.response_type,
      correction_solution: form.correction_solution, correction_guide: form.correction_guide,
      badge_type: form.badge_type, difficulty: form.difficulty, calculator: form.calculator,
      title: form.title, tags: form.tags, bareme_points: form.bareme_points, expected: form.expected,
    }
    // le barème est REPORTÉ dans chaque grading_json réécrit : l'omettre le
    // faisait disparaître de l'exercice à la première édition, qui repartait
    // alors sur le repli déterministe (cf. 4 QCM de la banque sans barème).
    // max_score = une unité par CASE (chaque case cochée/laissée vide à raison
    // rapporte sa part, cf. grading.qcm_credit).
    if (form.response_type.startsWith('qcm'))
      p.grading_json = { comparator: 'qcm', max_score: form.choices.length, negative: 0,
        choices: form.choices, bareme_points: form.bareme_points }
    if (form.response_type === 'checkbox_grid') {
      const cols = form.expected?.cols ?? []
      const rows = form.expected?.rows ?? []
      p.expected = { type: 'grid', cols, rows }
      p.grading_json = { comparator: 'grid', max_score: rows.length, cols, rows,
        bareme_points: form.bareme_points }
    }
    return p
  }

  const save = async (thenValidate: boolean) => {
    setBusy(true)
    try {
      let updated = await api.patch<Exercise>(`/api/indigo/exercises/${form.id}`, buildPatch())
      if (thenValidate) updated = await api.post<Exercise>(`/api/indigo/exercises/${form.id}/validate`)
      onSaved(updated)
      notifications.show({ color: 'green', message: thenValidate ? `${form.ref} validé` : 'Enregistré (brouillon)' })
      onClose()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setBusy(false) }
  }

  return (
    <Modal opened={!!ex} onClose={onClose} size="lg" title={<Text fw={700}>Modifier {form.ref}</Text>}>
      <Stack gap="sm">
        <Group grow>
          <Select label="Type de réponse" data={RESPONSE_TYPES} value={form.response_type}
            onChange={(v) => setForm({ ...form, response_type: v || 'short_text' })} />
          <Select label="Badge" data={Object.keys(BADGE_LABEL).map((k) => ({ value: k, label: BADGE_LABEL[k] }))}
            value={form.badge_type} onChange={(v) => setForm({ ...form, badge_type: v || 'exercice' })} />
          {/* difficulté = 3 niveaux, UNIQUEMENT pour les problèmes/énigmes */}
          {isProb && (
            <Select label="Difficulté" data={DIFF_OPTS} value={String(form.difficulty)}
              onChange={(v) => setForm({ ...form, difficulty: Number(v) || 3 })} />
          )}
        </Group>
        <Group grow align="flex-end">
          <Box>
            <Text size="xs" fw={600} mb={4}>Calculatrice</Text>
            <SegmentedControl size="xs" fullWidth data={CALC_OPTS} value={form.calculator}
              onChange={(v) => setForm({ ...form, calculator: v })} />
          </Box>
          <NumberInput label="Barème (points)" min={0.125} max={5} step={0.125} decimalScale={3}
            value={form.bareme_points}
            onChange={(v) => setForm({ ...form, bareme_points: Number(v) || 1 })} />
        </Group>
        {isProb && (
          <Group grow>
            <TextInput label="Titre (problème/énigme)" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.currentTarget.value })} />
            <TagsInput label="Tags (non imprimés sur le sujet)" value={form.tags}
              onChange={(v) => setForm({ ...form, tags: v })} />
          </Group>
        )}
        <Textarea label="Énoncé (LaTeX $...$, {{blank}} = case à remplir, « • » pour une puce — jamais « - »)"
          autosize minRows={3}
          value={form.statement} onChange={(e) => setForm({ ...form, statement: e.currentTarget.value })} />
        {/* aperçu live : ce que verra l'élève (pastilles a./b./1., puces, cases) */}
        <Paper withBorder p="xs" radius="sm" bg="var(--mantine-color-gray-0)">
          <Text size="10px" c="dimmed" mb={2}>Aperçu</Text>
          <StatementPreview ex={form} color={badgeColor(form)} />
        </Paper>

        {form.response_type.startsWith('qcm') && (
          <Box>
            <Text size="xs" fw={600} mb={4}>Choix (coche les bonnes réponses)</Text>
            <Stack gap={4}>
              {form.choices.map((c, i) => (
                <Group key={i} gap={6} wrap="nowrap">
                  <Checkbox checked={correct.includes(i)} onChange={(e) => {
                    const set = new Set(correct)
                    e.currentTarget.checked ? set.add(i) : set.delete(i)
                    setForm({ ...form, expected: { type: 'choice', correct: Array.from(set).sort((a, b) => a - b) } })
                  }} />
                  <TextInput size="xs" style={{ flex: 1 }} value={c} onChange={(e) => {
                    const ch = [...form.choices]; ch[i] = e.currentTarget.value; setForm({ ...form, choices: ch })
                  }} />
                  <ActionIcon size="sm" color="red" variant="subtle"
                    onClick={() => setForm({ ...form, choices: form.choices.filter((_, j) => j !== i) })}><Minus size={12} /></ActionIcon>
                </Group>
              ))}
              <Button size="xs" variant="light" leftSection={<Plus size={12} />}
                onClick={() => setForm({ ...form, choices: [...form.choices, ''] })}>Ajouter un choix</Button>
            </Stack>
          </Box>
        )}

        {form.response_type === 'checkbox_grid' && (() => {
          const cols: string[] = form.expected?.cols ?? ['Vrai', 'Faux']
          const rows: { label: string; correct: number }[] = form.expected?.rows ?? []
          const setGrid = (next: { cols?: string[]; rows?: { label: string; correct: number }[] }) =>
            setForm({ ...form, expected: { type: 'grid', cols: next.cols ?? cols, rows: next.rows ?? rows } })
          return (
            <Box>
              <Text size="xs" fw={600} mb={4}>Colonnes (options — 2 à 4, ex. Vrai / Faux)</Text>
              <Group gap={6} mb={8}>
                {cols.map((c, i) => (
                  <Group key={i} gap={2} wrap="nowrap">
                    <TextInput size="xs" w={104} value={c} onChange={(e) => {
                      const cc = [...cols]; cc[i] = e.currentTarget.value; setGrid({ cols: cc })
                    }} />
                    {cols.length > 2 && (
                      <ActionIcon size="sm" color="red" variant="subtle" onClick={() => {
                        const cc = cols.filter((_, j) => j !== i)
                        const rr = rows.map((r) => ({ ...r, correct: Math.min(r.correct, cc.length - 1) }))
                        setGrid({ cols: cc, rows: rr })
                      }}><Minus size={12} /></ActionIcon>
                    )}
                  </Group>
                ))}
                {cols.length < 4 && (
                  <Button size="compact-xs" variant="light" leftSection={<Plus size={12} />}
                    onClick={() => setGrid({ cols: [...cols, ''] })}>Colonne</Button>
                )}
              </Group>
              <Text size="xs" fw={600} mb={4}>Lignes (sous-questions — coche la bonne colonne)</Text>
              <Stack gap={4}>
                {rows.map((r, i) => (
                  <Group key={i} gap={6} wrap="nowrap" align="center">
                    <TextInput size="xs" style={{ flex: 1 }} placeholder="énoncé de la sous-question (LaTeX $...$)"
                      value={r.label} onChange={(e) => {
                        const rr = [...rows]; rr[i] = { ...r, label: e.currentTarget.value }; setGrid({ rows: rr })
                      }} />
                    <Select size="xs" w={104} allowDeselect={false}
                      data={cols.map((c, j) => ({ value: String(j), label: c || `Col ${j + 1}` }))}
                      value={String(r.correct)} onChange={(v) => {
                        const rr = [...rows]; rr[i] = { ...r, correct: Number(v) || 0 }; setGrid({ rows: rr })
                      }} />
                    <ActionIcon size="sm" color="red" variant="subtle"
                      onClick={() => setGrid({ rows: rows.filter((_, j) => j !== i) })}><Minus size={12} /></ActionIcon>
                  </Group>
                ))}
                <Button size="xs" variant="light" leftSection={<Plus size={12} />}
                  onClick={() => setGrid({ rows: [...rows, { label: '', correct: 0 }] })}>Ajouter une ligne</Button>
              </Stack>
            </Box>
          )
        })()}

        {/* ajouter une image quand le LLM n'en a rattaché aucune : amorcée depuis
            l'extrait du manuel, puis affinée avec les boutons de cadrage ci-dessous */}
        {!form.has_figure && (
          <Button size="xs" variant="light" leftSection={<ImagePlus size={14} />}
            onClick={addFigure} style={{ alignSelf: 'flex-start' }}>
            Ajouter une image (depuis l'extrait du manuel)
          </Button>
        )}

        {/* figure (schéma) : SEUL crop éditable — ajuste les bords si Mistral l'a mal cadrée.
            Le bouton « Supprimer l'image » est TOUJOURS proposé dès qu'une figure est
            attachée (même si son image est indisponible/mal détectée) : supprimer, c'est
            dire « pas d'image pour cet énoncé » (retire l'insertion fausse). */}
        {form.has_figure && (
          <Box>
            <Group justify="space-between" mb={4}>
              <Text size="xs" fw={600}>Figure de l'énoncé — ajuste le cadrage</Text>
              <Button size="compact-xs" variant="subtle" color="red"
                leftSection={<ImageOff size={12} />} onClick={removeFigure}>Supprimer l'image</Button>
            </Group>
            {!form.figure_url && (
              <Text size="xs" c="dimmed">Image absente ou indisponible — « Supprimer l'image » retire la référence à une figure pour cet énoncé.</Text>
            )}
            {form.figure_url && (
            <Box style={{ position: 'relative', display: 'inline-block', border: '1px solid var(--mantine-color-gray-3)' }}>
              <AuthImg src={form.figure_url} reloadKey={figV} alt="figure" style={{ maxWidth: 260, display: 'block' }} />
              {(['top', 'bottom', 'left', 'right'] as const).map((edge) => {
                const pos: any = {
                  top: { top: 2, left: '50%', transform: 'translateX(-50%)' },
                  bottom: { bottom: 2, left: '50%', transform: 'translateX(-50%)' },
                  left: { left: 2, top: '50%', transform: 'translateY(-50%)', flexDirection: 'column' },
                  right: { right: 2, top: '50%', transform: 'translateY(-50%)', flexDirection: 'column' },
                }[edge]
                return (
                  <Group key={edge} gap={2} style={{ position: 'absolute', ...pos }}>
                    <ActionIcon size="xs" variant="filled" color="dark" onClick={() => nudge(edge, 1)}><Plus size={11} /></ActionIcon>
                    <ActionIcon size="xs" variant="filled" color="gray" onClick={() => nudge(edge, -1)}><Minus size={11} /></ActionIcon>
                  </Group>
                )
              })}
            </Box>
            )}
          </Box>
        )}

        <Textarea label="Guide d'auto-correction (élève)" autosize minRows={1}
          value={form.correction_guide} onChange={(e) => setForm({ ...form, correction_guide: e.currentTarget.value })} />
        <Textarea label="Corrigé (prof)" autosize minRows={1}
          value={form.correction_solution} onChange={(e) => setForm({ ...form, correction_solution: e.currentTarget.value })} />

        <Group justify="flex-end" mt="xs">
          <Button variant="default" onClick={onClose}>Annuler</Button>
          <Button variant="light" loading={busy} onClick={() => save(false)}>Enregistrer</Button>
          <Button color="green" loading={busy} leftSection={<Check size={16} />} onClick={() => save(true)}>Valider</Button>
        </Group>
      </Stack>
    </Modal>
  )
}

// ----------------------------------------------------- assistant d'extraction
// Plages saisies au format « 34-67 » (bornes incluses) — pages (1-based, n° de
// page du PDF) et numéros d'exercices. Une seule valeur (« 34 ») est acceptée.
type TargetDraft = {
  competency_id: string; eleve_page_range: string; prof_page_range: string; number_range: string
}
const RANGE_RE = /^\s*\d+\s*(?:[-–—]\s*\d+)?\s*$/
const rangeOk = (s: string) => RANGE_RE.test((s || '').trim())

/** Aperçu d'UNE page du manuel : saisis un numéro, la vignette s'affiche —
 *  aide à repérer les bonnes pages sans le multi-sélecteur d'avant. */
function PagePeek({ grade, which, info }: { grade: string; which: 'eleve' | 'prof'; info: ManualInfo }) {
  const [pg, setPg] = useState(1)
  if (!info.available) return <Text size="xs" c="dimmed">Manuel {which} indisponible</Text>
  return (
    <Stack gap={4} align="center">
      <Group gap={6} wrap="nowrap">
        <Text size="xs" fw={600}>{which === 'eleve' ? 'Élève' : 'Prof'}</Text>
        <NumberInput size="xs" w={80} min={1} max={info.pages} value={pg}
          onChange={(v) => setPg(Math.max(1, Math.min(info.pages, Number(v) || 1)))} />
        <Text size="10px" c="dimmed">/ {info.pages}</Text>
      </Group>
      <AuthImg src={`/api/indigo/manual/page.png?which=${which}&grade_level=${grade}&index=${pg - 1}`}
        reloadKey={pg} alt={`page ${pg}`}
        style={{ width: 150, height: 205, objectFit: 'contain', border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4 }} />
    </Stack>
  )
}

function ExtractionAssistant({ opened, onClose, comps, manuals, grade, onLaunched }: {
  opened: boolean; onClose: () => void; comps: Comp[]; manuals: Manuals | null
  grade: string; onLaunched: () => void
}) {
  const [step, setStep] = useState(0)
  const [chosen, setChosen] = useState<string[]>([])
  const [targets, setTargets] = useState<Record<string, TargetDraft>>({})
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (opened) { setStep(0); setChosen([]); setTargets({}) } }, [opened])

  const toggleComp = (id: string) => {
    setChosen((c) => c.includes(id) ? c.filter((x) => x !== id) : [...c, id])
    setTargets((t) => t[id] ? t
      : { ...t, [id]: { competency_id: id, eleve_page_range: '', prof_page_range: '', number_range: '' } })
  }
  const setField = (id: string, k: keyof TargetDraft, v: string) =>
    setTargets((t) => ({ ...t, [id]: { ...t[id], [k]: v } }))

  const byChapter = useMemo(() => {
    const m = new Map<string, Comp[]>()
    comps.forEach((c) => {
      const k = `${c.domain_code} — ${c.chapter_name}`
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(c)
    })
    return Array.from(m.entries())
  }, [comps])

  // prête si chaque compétence a une plage de pages élève ET une plage de numéros valides
  const ready = chosen.length > 0 && chosen.every((id) => {
    const t = targets[id]
    return t && rangeOk(t.eleve_page_range) && rangeOk(t.number_range)
      && (!t.prof_page_range.trim() || rangeOk(t.prof_page_range))
  })
  const launch = async () => {
    setBusy(true)
    try {
      await api.post('/api/indigo/extractions', { grade_level: grade, targets: chosen.map((id) => targets[id]) })
      notifications.show({ color: 'indigo', message: 'Extraction lancée en file de fond' })
      onLaunched(); onClose()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setBusy(false) }
  }

  return (
    <Modal opened={opened} onClose={onClose} size="xl" title={
      <Group gap={8}><Wand2 size={18} /><Text fw={700}>Extraire des exercices du manuel</Text></Group>}>
      <Stack>
        {step === 0 && (
          <>
            <Text size="sm" c="dimmed">1. Choisis une ou plusieurs compétences. Le manuel élève fournit les énoncés, le manuel prof les corrigés.</Text>
            <ScrollArea h={380}>
              <Stack gap="xs">
                {byChapter.map(([chap, list]) => (
                  <Box key={chap}>
                    <Text size="xs" fw={700} c="dimmed" mb={4}>{chap}</Text>
                    <Stack gap={2}>
                      {list.map((c) => (
                        <Checkbox key={c.id} checked={chosen.includes(c.id)} onChange={() => toggleComp(c.id)}
                          label={<Text size="sm"><b>{c.short_id}</b> {c.label}</Text>} />
                      ))}
                    </Stack>
                  </Box>
                ))}
              </Stack>
            </ScrollArea>
            <Group justify="flex-end">
              <Button disabled={chosen.length === 0} onClick={() => setStep(1)}>Suivant ({chosen.length})</Button>
            </Group>
          </>
        )}
        {step === 1 && (
          <>
            <Text size="sm" c="dimmed">
              2. Pour chaque compétence, indique la PLAGE de pages (élève = énoncés, prof = corrigés)
              et la PLAGE de numéros d'exercices — format « 34-67 », bornes incluses. Le NUMÉRO fait
              foi : seuls les exercices de cette plage sont repris.
            </Text>
            {/* aperçu d'une page pour repérer les bons numéros de page (PDF) */}
            <Group justify="center" gap="lg">
              <PagePeek grade={grade} which="eleve" info={manuals!.manuals.eleve} />
              <PagePeek grade={grade} which="prof" info={manuals!.manuals.prof} />
            </Group>
            <ScrollArea h={300}>
              <Stack>
                {chosen.map((id) => {
                  const c = comps.find((x) => x.id === id)!
                  const t = targets[id]
                  return (
                    <Paper key={id} withBorder p="sm" radius="md">
                      <Text size="sm" fw={700} mb={6}>{c.short_id} — {c.label}</Text>
                      <Group grow align="flex-start">
                        <TextInput label="Pages élève" placeholder="34-40" description="n° de page du PDF"
                          error={t?.eleve_page_range && !rangeOk(t.eleve_page_range) ? 'format 34-40' : undefined}
                          value={t?.eleve_page_range ?? ''} onChange={(e) => setField(id, 'eleve_page_range', e.currentTarget.value)} />
                        <TextInput label="Pages prof" placeholder="182-186" description="corrigés (optionnel)"
                          error={t?.prof_page_range && !rangeOk(t.prof_page_range) ? 'format 182-186' : undefined}
                          value={t?.prof_page_range ?? ''} onChange={(e) => setField(id, 'prof_page_range', e.currentTarget.value)} />
                        <TextInput label="Numéros d'exercices" placeholder="34-67" description="bornes incluses"
                          error={t?.number_range && !rangeOk(t.number_range) ? 'format 34-67' : undefined}
                          value={t?.number_range ?? ''} onChange={(e) => setField(id, 'number_range', e.currentTarget.value)} />
                      </Group>
                    </Paper>
                  )
                })}
              </Stack>
            </ScrollArea>
            <Group justify="space-between">
              <Button variant="subtle" onClick={() => setStep(0)}>Retour</Button>
              <Button disabled={!ready} loading={busy} leftSection={<Sparkles size={16} />} onClick={launch}>Lancer l'extraction</Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  )
}

// ----------------------------------------------------- tableau des compétences
function CompetencyTable({ rows, onSelect }: { rows: SummaryRow[]; onSelect: (r: SummaryRow) => void }) {
  const byDomain = useMemo(() => {
    const m = new Map<string, SummaryRow[]>()
    rows.forEach((r) => {
      const k = `${r.domain_code} — ${r.domain_name}`
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(r)
    })
    return Array.from(m.entries())
  }, [rows])

  return (
    <Stack>
      {byDomain.map(([domain, list]) => (
        <Paper key={domain} withBorder p="sm" radius="md">
          <Text fw={700} size="sm" mb={6}>{domain}</Text>
          <Table highlightOnHover verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Compétence</Table.Th>
                <Table.Th w={90} ta="center">Brouillon</Table.Th>
                <Table.Th w={80} ta="center">Validé</Table.Th>
                <Table.Th w={80} ta="center">Publié</Table.Th>
                <Table.Th w={70} ta="center">Terminé</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.map((r) => (
                <Table.Tr key={r.competency_id} style={{ cursor: 'pointer' }} onClick={() => onSelect(r)}>
                  <Table.Td><Text size="sm"><b>{r.short_id}</b> {r.label}</Text></Table.Td>
                  <Table.Td ta="center">{r.draft ? <Badge color="orange" variant="light">{r.draft}</Badge> : <Text c="dimmed" size="sm">—</Text>}</Table.Td>
                  <Table.Td ta="center">{r.validated ? <Badge color="blue" variant="light">{r.validated}</Badge> : <Text c="dimmed" size="sm">—</Text>}</Table.Td>
                  <Table.Td ta="center">{r.published ? <Badge color="teal" variant="light">{r.published}</Badge> : <Text c="dimmed" size="sm">—</Text>}</Table.Td>
                  <Table.Td ta="center">{r.done ? <CheckCircle2 size={18} color="var(--mantine-color-green-6)" /> : null}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      ))}
    </Stack>
  )
}

// ----------------------------------------------------- page principale
export default function Exercices() {
  const { cycle } = useAppState()
  const grade = cycle                    // '6e' | '5e' | '4e' | '3e' | 'all'
  const [manuals, setManuals] = useState<Manuals | null>(null)
  const [comps, setComps] = useState<Comp[]>([])
  const [summary, setSummary] = useState<SummaryRow[] | null>(null)
  const [selected, setSelected] = useState<SummaryRow | null>(null)
  const [exercises, setExercises] = useState<Exercise[] | null>(null)
  const [extractions, setExtractions] = useState<Extraction[]>([])
  const [assistant, setAssistant] = useState(false)
  const [editing, setEditing] = useState<Exercise | null>(null)
  const [pub, setPub] = useState<{ published: number; seeded: number } | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)   // modale « Tout supprimer »
  const [deletingAll, setDeletingAll] = useState(false)
  const [provider, setProvider] = useState('anthropic')            // fournisseur LLM des 3 étapes
  const [providerOffline, setProviderOffline] = useState(false)
  const [selMode, setSelMode] = useState(false)                    // mode sélection (régénérer)
  const [selIds, setSelIds] = useState<Set<string>>(new Set())
  const [regenerating, setRegenerating] = useState(false)

  const isAll = grade === 'all'
  const loadSummary = useCallback(() => {
    if (isAll) return
    api.get<SummaryRow[]>(`/api/indigo/summary?grade_level=${grade}`).then(setSummary)
  }, [grade, isAll])
  const loadPub = useCallback(() => {
    if (isAll) return
    api.get<{ published: number; seeded: number }>('/api/indigo/published').then(setPub)
  }, [isAll])
  const loadExtractions = useCallback(() => {
    api.get<Extraction[]>('/api/indigo/extractions').then(setExtractions)
  }, [])
  const loadExercises = useCallback((cid: string) => {
    api.get<Exercise[]>(`/api/indigo/exercises?competency_id=${cid}`).then(setExercises)
  }, [])

  useEffect(() => {
    if (isAll) { setSummary(null); return }
    setSelected(null); setExercises(null)
    api.get<Manuals>(`/api/indigo/manuals?grade_level=${grade}`).then(setManuals)
    api.get<{ competencies: Comp[] }>(`/api/indigo/competencies?grade_level=${grade}`).then((r) => setComps(r.competencies))
    loadSummary(); loadExtractions(); loadPub()
  }, [grade, isAll, loadSummary, loadExtractions, loadPub])

  useEffect(() => {   // fournisseur LLM global (indépendant de la classe)
    api.get<{ provider: string; offline: boolean }>('/api/indigo/llm-provider')
      .then((r) => { setProvider(r.provider); setProviderOffline(r.offline) }).catch(() => { /* silencieux */ })
  }, [])

  const active = extractions.some((e) => e.status === 'pending' || e.status === 'running')
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => { loadExtractions(); loadSummary(); if (selected) loadExercises(selected.competency_id) }, 2500)
    return () => clearInterval(t)
  }, [active, selected, loadExtractions, loadSummary, loadExercises])

  const openComp = (r: SummaryRow) => {
    setSelected(r); setExercises(null); setSelMode(false); setSelIds(new Set())
    loadExercises(r.competency_id)
  }
  const toggleSel = (id: string, v: boolean) => setSelIds((s) => {
    const n = new Set(s); if (v) n.add(id); else n.delete(id); return n
  })
  const regenerate = async () => {
    const ids = Array.from(selIds)
    if (!ids.length || !selected) return
    setRegenerating(true)
    try {
      const r = await api.post<{ regenerated: number; failed: number }>(
        '/api/indigo/exercises/regenerate', { ids })
      notifications.show({
        color: r.failed ? 'orange' : 'green',
        message: `${r.regenerated} exercice(s) régénéré(s)`
          + (r.failed ? `, ${r.failed} échec(s) (inchangés)` : ''),
      })
      setSelIds(new Set()); setSelMode(false)
      loadExercises(selected.competency_id); loadSummary()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setRegenerating(false) }
  }
  const dismissExtraction = async (id: string) => {
    // masque le bandeau côté serveur (statut « dismissed ») pour qu'il ne
    // revienne pas au rechargement, puis le retire de l'affichage
    setExtractions((xs) => xs.filter((e) => e.id !== id))
    try { await api.post(`/api/indigo/extractions/${id}/dismiss`) } catch { /* déjà retiré à l'écran */ }
  }
  const onDelete = async (id: string) => {
    await api.del(`/api/indigo/exercises/${id}`)
    // la suppression désenregistre aussi l'exercice de la banque publiée côté
    // serveur (cf. indigo.delete_exercise) : on recharge les deux compteurs
    // pour que l'écran suive, pas seulement le tableau de brouillons.
    setExercises((xs) => (xs || []).filter((x) => x.id !== id)); loadSummary(); loadPub()
  }
  const onChange = (e: Exercise) => { setExercises((xs) => (xs || []).map((x) => x.id === e.id ? e : x)); loadSummary() }
  const onDeleteAll = async () => {
    if (!selected) return
    setDeletingAll(true)
    try {
      const r = await api.del<{ deleted: number }>(`/api/indigo/exercises?competency_id=${selected.competency_id}`)
      setExercises([]); loadSummary(); loadPub()
      notifications.show({ color: 'green', message: `${r.deleted} exercice(s) supprimé(s)` })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setDeletingAll(false); setConfirmDeleteAll(false) }
  }
  const onProviderChange = async (value: string) => {
    const prev = provider
    setProvider(value)   // optimiste
    try {
      const r = await api.post<{ provider: string; offline: boolean }>('/api/indigo/llm-provider', { provider: value })
      setProvider(r.provider); setProviderOffline(r.offline)
      const name = r.provider === 'deepseek' ? 'DeepSeek pro v4' : 'Anthropic (Sonnet + Opus)'
      notifications.show({
        color: r.offline ? 'orange' : 'green',
        message: r.offline
          ? `Fournisseur : ${name} — ⚠ clé absente, les prochaines extractions seront en repli OCR brut`
          : `Fournisseur des 3 étapes : ${name}`,
      })
    } catch (e: any) { setProvider(prev); notifications.show({ color: 'red', message: e.message }) }
  }
  const validatedCount = (summary ?? []).reduce((n, s) => n + s.validated, 0)
  const publish = async () => {
    setPublishing(true)
    try {
      const r = await api.post<{ published: number; seeded: number }>('/api/indigo/publish')
      setPub(r); loadSummary()
      notifications.show({ color: 'green', message: `${r.published} exercice(s) publié(s) — pense à committer backend/app/data/indigo/` })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setPublishing(false) }
  }

  if (isAll) {
    return (
      <Stack>
        <Title order={2}>Exercices</Title>
        <Alert color="blue" icon={<AlertTriangle size={16} />}>
          Sélectionne une classe (6ᵉ, 5ᵉ, 4ᵉ ou 3ᵉ) avec le sélecteur en haut de la page pour voir ses compétences.
        </Alert>
      </Stack>
    )
  }

  const eleveOk = manuals?.manuals.eleve.available

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap={8}>
          {selected && <ActionIcon variant="subtle" onClick={() => setSelected(null)}><ChevronLeft size={20} /></ActionIcon>}
          <Title order={2}>Exercices <Text span c="dimmed" size="sm">· manuel Indigo {grade}</Text></Title>
        </Group>
        <Group>
          {pub && pub.seeded === pub.published && (
            <Text size="xs" c="dimmed">{pub.seeded} en banque · {validatedCount} validé(s)</Text>
          )}
          {pub && pub.seeded !== pub.published && (
            <Tooltip label={`${pub.seeded} exercice(s) Indigo actif(s) en banque, mais ${pub.published} figé(s) dans le fichier versionné (backend/app/data/indigo/) — la banque a été modifiée hors de cet onglet (purge, redémarrage...). Republie pour refiger le fichier sur l'état actuel des brouillons.`}>
              <Text size="xs" c="orange">{pub.seeded} en banque ⚠ {pub.published} figé(s) · {validatedCount} validé(s)</Text>
            </Tooltip>
          )}
          <Tooltip label={`Modèle des 3 étapes (découpage · génération · vérification). Anthropic = Sonnet puis Opus ; DeepSeek = DeepSeek pro v4.${providerOffline ? ' ⚠ Clé du fournisseur choisi absente : repli OCR brut.' : ''}`}>
            <SegmentedControl size="xs" value={provider} onChange={onProviderChange}
              color={providerOffline ? 'orange' : 'blue'}
              data={[{ label: 'Anthropic', value: 'anthropic' }, { label: 'DeepSeek', value: 'deepseek' }]} />
          </Tooltip>
          <ActionIcon variant="light" onClick={() => { loadSummary(); loadExtractions(); if (selected) loadExercises(selected.competency_id) }}><RefreshCw size={16} /></ActionIcon>
          <Tooltip label="Fige les exercices validés dans des fichiers versionnés (à committer)">
            <Button variant="light" color="teal" leftSection={<UploadCloud size={16} />}
              loading={publishing} disabled={validatedCount === 0} onClick={publish}>Publier</Button>
          </Tooltip>
          <Button leftSection={<Wand2 size={16} />} disabled={!eleveOk} onClick={() => setAssistant(true)}>Nouvelle extraction</Button>
        </Group>
      </Group>

      {manuals && !eleveOk && (
        <Alert color="orange" icon={<AlertTriangle size={16} />}>
          Manuel élève {grade} introuvable sur cette instance. Les PDF restent locaux (dossier <code>context/</code>) et ne sont pas livrés dans l'image — dépose-les pour extraire.
        </Alert>
      )}

      {extractions.filter((e) => ['pending', 'running', 'failed'].includes(e.status)).slice(0, 3).map((e) => (
        <Alert key={e.id} color={e.status === 'failed' ? 'red' : 'indigo'}
          icon={e.status === 'failed' ? <AlertTriangle size={16} /> : <Loader size={14} />}
          // une extraction en échec peut être fermée (croix) : le bandeau ne
          // revient pas au rechargement (ex. échec réseau, API hors ligne)
          withCloseButton={e.status === 'failed'} closeButtonLabel="Masquer"
          onClose={() => dismissExtraction(e.id)}>
          <Group justify="space-between">
            <Text size="sm">{e.status === 'failed' ? `Échec : ${e.error_message}` : e.progress_message || 'Extraction en cours…'}</Text>
            {e.status !== 'failed' && <Text size="xs" c="dimmed">{e.progress}%</Text>}
          </Group>
          {e.status !== 'failed' && <Progress value={e.progress} size="sm" mt={4} />}
        </Alert>
      ))}

      {/* vue TABLE (aucune compétence sélectionnée) */}
      {!selected && summary === null && <Loader />}
      {!selected && summary && <CompetencyTable rows={summary} onSelect={openComp} />}

      {/* vue LISTE (compétence sélectionnée) */}
      {selected && (
        <>
          <Group justify="space-between" align="center">
            <Text fw={600}>{selected.short_id} — {selected.label}</Text>
            <Group gap={8}>
              {/* mode sélection : régénérer les exercices choisis avec le prompt ACTUEL
                  (utile après une évolution du prompt de génération) */}
              {selMode && exercises && exercises.length > 0 && (
                <>
                  <Button size="xs" variant="subtle"
                    onClick={() => setSelIds(new Set(exercises.map((e) => e.id)))}>
                    Tout sélectionner
                  </Button>
                  <Tooltip label="Rejoue l'adaptation + la vérification depuis l'OCR stocké, avec le prompt et le fournisseur actuels. Les échecs laissent l'exercice inchangé.">
                    <Button size="xs" color="blue" leftSection={<RotateCcw size={14} />}
                      loading={regenerating} disabled={selIds.size === 0} onClick={regenerate}>
                      Régénérer ({selIds.size})
                    </Button>
                  </Tooltip>
                </>
              )}
              <Tooltip label="Sélectionner des exercices pour les régénérer avec le prompt actuel">
                <Button size="xs" variant={selMode ? 'filled' : 'light'} color={selMode ? 'blue' : 'gray'}
                  leftSection={<CheckSquare size={14} />}
                  disabled={!exercises || exercises.length === 0}
                  onClick={() => { setSelMode((m) => !m); setSelIds(new Set()) }}>
                  {selMode ? 'Annuler' : 'Sélectionner'}
                </Button>
              </Tooltip>
              {/* « Tout supprimer » : désactivé si la page n'a aucun exercice, confirmation obligatoire */}
              <Tooltip label="Supprimer TOUS les exercices de cette compétence (brouillons ET publiés)">
                <Button variant="light" color="red" size="xs" leftSection={<Trash2 size={16} />}
                  disabled={!exercises || exercises.length === 0}
                  onClick={() => setConfirmDeleteAll(true)}>
                  Tout supprimer{exercises && exercises.length ? ` (${exercises.length})` : ''}
                </Button>
              </Tooltip>
            </Group>
          </Group>
          {exercises === null && <Loader />}
          {exercises && exercises.length === 0 && (
            <Text c="dimmed" size="sm">Aucun exercice. Lance une extraction pour cette compétence.</Text>
          )}
          <Group align="flex-start" gap="md" wrap="wrap">
            {exercises?.map((ex) => (
              <ExerciseCard key={ex.id} ex={ex} onEdit={setEditing} onChange={onChange} onDelete={onDelete}
                selectable={selMode} selected={selIds.has(ex.id)} onToggleSelect={toggleSel} />
            ))}
          </Group>
        </>
      )}

      {manuals && (
        <ExtractionAssistant opened={assistant} onClose={() => setAssistant(false)} comps={comps}
          manuals={manuals} grade={grade} onLaunched={loadExtractions} />
      )}
      <EditModal ex={editing} onClose={() => setEditing(null)} onChange={onChange}
        onSaved={(e) => { onChange(e); setEditing(null) }} />

      <Modal opened={confirmDeleteAll} onClose={() => setConfirmDeleteAll(false)}
        title={<Text fw={650}>Supprimer tous les exercices</Text>}>
        <Stack>
          <Text size="sm">
            Supprimer définitivement les {exercises?.length ?? 0} exercice(s) de
            « {selected?.short_id} — {selected?.label} » ? Les brouillons ET les
            exercices déjà publiés (banque + fichier versionné) seront retirés.
            Action irréversible.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmDeleteAll(false)}>Annuler</Button>
            <Button color="red" loading={deletingAll} leftSection={<Trash2 size={16} />}
              onClick={onDeleteAll}>Supprimer définitivement</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
