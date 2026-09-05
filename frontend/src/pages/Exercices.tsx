// Onglet Exercices (Indigo) — ADMIN uniquement.
// Copie/adaptation d'exercices d'un manuel réel. Vue par défaut : tableau de
// TOUTES les compétences de la classe (toggle 6/5/4/3) avec brouillon/validé/
// publié. Clic sur une compétence → ses exercices, un par carte (largeur d'une
// demi-colonne A4) : extrait manuel → tags/badges → énoncé → guide → corrigé →
// actions. « Modifier » ouvre une modale d'édition complète.
import {
  ActionIcon, Alert, Badge, Box, Button, Checkbox, FileButton, Group,
  Loader, Modal, NumberInput, Paper, Progress, ScrollArea, SegmentedControl, Select,
  Stack, TagsInput, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, BookOpen, Calculator, Check, CheckSquare, ChevronLeft, Clock,
  Download, HardDrive, ImageOff, ImagePlus, Minus, Package, PackagePlus, Pencil, Plus,
  RefreshCw, RotateCcw, Slash, Sparkles, Square, Trash2, UploadCloud, Wand2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, download } from '../api'
import AuthImg from '../components/AuthImg'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from '../components/CompetencyHierarchy'
import ExercisePrintPreview from '../components/ExercisePrintPreview'
import FigureEditor, { type FigureBox, type ImageRect } from '../components/FigureEditor'
import { useAppState } from '../state/AppState'
import { familyRows } from '../utils/families'
import GradeSelectionRequired from '../components/GradeSelectionRequired'

// ------------------------------------------------------------------- types
type Comp = {
  id: string; code: string; short_id: string; label: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
}
// `source` dit D'OÙ vient le contenu publié que lit cette instance : "volume"
// (publié ici, persiste aux mises à jour) ou "image" (livré avec le code).
type PublishState = {
  published: number; seeded: number
  source: 'volume' | 'image'; on_volume: boolean; generated_at: string
}
type Manuals = {
  grade_level: string; manuals: { eleve: ManualInfo; prof: ManualInfo }; pack: PackStatus
}
// `available` = on peut travailler ; `pdf` = le PDF lui-même est là. Une
// instance sans manuel mais avec un PACK DE TRAVAIL (pages rendues d'avance +
// index) fabrique exactement pareil — cf. services/indigo_pack.py.
type ManualInfo = { available: boolean; pdf: boolean; pages: number }
type PackStatus = {
  grade_level: string; has_manuals: boolean; can_export: boolean
  pack: { page_count: number; pages_present: number; built_at: string } | null
  index: { eleve: number; prof: number }
  source: 'manuel' | 'pack' | 'aucune'
}
type SummaryRow = {
  competency_id: string; short_id: string; label: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
  draft: number; validated: number; published: number; done: boolean
  problem_draft: number; problem_validated: number; problem_published: number
}
// Couverture de l'index du manuel : ce que la pipeline SAIT déjà, par
// compétence. C'est ce qui remplace la saisie à la main des trois plages.
type IndexCoverage = {
  grade_level: string
  eleve: { indexed: number; total: number }
  prof: { indexed: number; total: number }
  competencies: {
    competency_id: string; code: string; short_id: string; label: string
    chapter_name: string; pages: number[]; numbers: number[]; prof_pages: number[]
  }[]
}
type Extraction = {
  id: string; status: string; progress: number; progress_message: string
  error_message: string; stats: Record<string, any>; created_at: string
  targets: { kind?: string; eleve_page_start?: number; eleve_page_end?: number }[]
}
// Marqueur posé par services/indigo_offpeak.wait_until_open pendant l'attente
// du tarif creux — sert à distinguer une extraction EN FILE d'une extraction
// qui tourne réellement (§ waitingExtractions).
const WAITING_MARK = '⏸'
const fmtLocalTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const extractionLabel = (e: Extraction) => {
  const t = e.targets?.[0]
  const pages = t?.eleve_page_start && t?.eleve_page_end
    ? `pages ${t.eleve_page_start}–${t.eleve_page_end}` : 'extraction'
  return `${pages} — ${e.progress_message.replace(`${WAITING_MARK} `, '')}`
}
type Exercise = {
  id: string; ref: string; competency_id: string; competency_short_id: string
  source_page: number; source_number: string; order_index: number
  badge_type: string; difficulty: number; calculator: string
  // trio produit par le mode « QCM only » : la base et ses deux dérivés
  variant_kind: 'base' | 'facile' | 'difficile'; derived_from_id: string | null
  title: string; tags: string[]; has_figure: boolean; figure_required: boolean
  statement: string; response_type: string; expected: Record<string, any>; choices: string[]
  adapted: boolean
  // réserves de relecture posées par le mode « QCM multipass » : ce que les
  // portes reprochent encore à l'exercice. Vide = rien à signaler.
  review_notes: string[]
  // sous-ensemble GRAVE : ce que la passe de retouche n'a pas su réparer.
  // « à regarder » (jaune) et « ne l'imprime pas tel quel » (rouge) ne sont
  // pas la même chose et ne doivent pas porter le même badge.
  review_blocking: string[]
  // rattachement à la compétence non confirmé : exercice à ranger avant validation
  competency_confirmed: boolean
  row_labels: string[] | null; col_labels: string[] | null; lines: number | null
  bareme_points: number; correction_solution: string; correction_guide: string
  status: string; crop_url: string | null; figure_url: string | null
  figure_box: FigureBox | null
  // provenance brute : raw_ocr.pipeline vaut "cli-exos" pour la pipeline CLI
  // (agents/cli-exos, abonnement Claude) — sinon c'est l'extraction Indigo (API).
  raw_ocr: Record<string, any> | null
}

// --- palette de badges PROPRE (≠ manuel) : on re-colore à notre façon
const BADGE_COLOR: Record<string, string> = {
  exercice: 'indigo', flash: 'yellow', expert: 'grape', enigme: 'pink', probleme: 'orange',
}
const PROBLEME_COLOR: Record<number, string> = { 1: 'green', 2: 'orange', 3: 'red' }
// difficulté = 3 niveaux (1/2/3 = facile/moyen/difficile), miroir de
// exercise_gen.DIFFICULTY_LEVELS côté backend
const DIFF_LABEL: Record<number, string> = { 1: 'Facile', 2: 'Moyen', 3: 'Difficile' }
const DIFF_OPTS = [
  { value: '1', label: 'Facile' }, { value: '2', label: 'Moyen' }, { value: '3', label: 'Difficile' },
]
// Seul le mode « QCM multipass » est proposé depuis l'onglet (les fournisseurs
// anthropic/DeepSeek/QCM only restent codés côté serveur, cf. services/
// indigo_llm.py, mais ne sont plus atteignables depuis cette page) : la
// classe force ce mode au chargement plutôt que d'exposer un sélecteur.
const MULTIPASS = 'multipass'
// Case « Cheap and Wait » : heures creuses DeepSeek codées en dur côté serveur
// (cf. services/indigo_offpeak.py) — seule la case se règle ici.
type OffPeak = { enabled: boolean; open_now: boolean; next_open: string }
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

// L'aperçu d'un exercice (énoncé, figure, zone de réponse, guide, corrigé) est
// rendu par le composant PARTAGÉ components/ExercisePrintPreview — le même que
// la Banque et l'assistant de sujets, donc la même feuille à l'écran partout.
// Cet écran en avait autrefois sa propre copie ; elle a été retirée pour qu'il
// n'existe qu'un seul rendu à faire évoluer (tableaux, séries, gras…).

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
      {/* DÉRIVÉ : même exercice du manuel, repris plus simple ou plus exigeant.
          À ne pas confondre avec les VARIANTES d'un sujet (anti-copie entre
          voisins) — d'où le mot « dérivé » dans toute l'interface. */}
      {ex.variant_kind && ex.variant_kind !== 'base' && (
        <Tooltip label={ex.variant_kind === 'facile'
          ? "Dérivé FACILE du même exercice : servi aux élèves en difficulté"
          : "Dérivé DIFFICILE du même exercice : servi aux élèves à l'aise"}>
          <Badge variant="filled" size="xs"
            color={ex.variant_kind === 'facile' ? 'green' : 'red'}>
            Dérivé {DIFF_LABEL[ex.difficulty]?.toLowerCase() ?? ex.variant_kind}
          </Badge>
        </Tooltip>
      )}
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
      {/* compétence devinée par ressemblance : l'exercice est là, mais il n'est
          pas forcément au bon endroit — à ranger avant de le valider */}
      {ex.competency_confirmed === false && (
        <Tooltip label="Compétence non confirmée : c'est la plus proche du bandeau lu, rien ne l'a confirmée. Ouvre l'exercice pour le rattacher à la bonne compétence.">
          <Badge color="grape" variant="light" size="xs">Compétence à confirmer</Badge>
        </Tooltip>
      )}
      {/* ce que la passe de RETOUCHE n'a pas su réparer et juge grave : réponse
          fausse qu'elle ne sait pas refaire, consigne incompréhensible, figure
          indispensable dont rien ne dit le contenu. À traiter AVANT de valider —
          d'où le rouge, quand les réserves ordinaires restent en jaune. */}
      {ex.review_blocking?.length > 0 && (
        <Tooltip label={ex.review_blocking.slice(0, 6).join(' · ')} multiline w={420}>
          <Badge color="red" variant="filled" size="xs" leftSection={<AlertTriangle size={11} />}>
            À revoir
          </Badge>
        </Tooltip>
      )}
      {/* réserves de relecture : ce que la génération n'a pas su régler seule.
          Ce ne sont pas des erreurs certaines — c'est ce qu'il faut REGARDER. */}
      {ex.review_notes?.length > 0 && (
        <Tooltip label={ex.review_notes.slice(0, 6).join(' · ')} multiline w={420}>
          <Badge color="yellow" variant="light" size="xs" leftSection={<AlertTriangle size={11} />}>
            {ex.review_notes.length} point{ex.review_notes.length > 1 ? 's' : ''} à relire
          </Badge>
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
    <Box style={{
      minWidth: 0, padding: 8, borderRadius: 8,
      outline: selected ? '2px solid var(--mantine-color-blue-5)' : undefined,
      background: done ? 'var(--mantine-color-gray-1)' : undefined,
      opacity: done ? 0.72 : undefined,
    }}>
      <ExercisePrintPreview exercise={ex} color={color} showGuide showCorrection showAnswers
        beforeFrame={selectable ? (
          <Checkbox mb={8} checked={!!selected} label={<Text size="xs" fw={600}>Sélectionner</Text>}
            onChange={(e) => onToggleSelect?.(ex.id, e.currentTarget.checked)} />
        ) : null}
        badges={<BadgeRow ex={ex} />}
        afterFrame={<Group justify="space-between" mt={2}>
          <ActionIcon color="red" variant="subtle" onClick={() => onDelete(ex.id)}><Trash2 size={16} /></ActionIcon>
          <Group gap={6}>
            <Button size="xs" variant="light" leftSection={<Pencil size={14} />} onClick={() => onEdit(ex)}>Modifier</Button>
            {ex.status !== 'validated' &&
              <Button size="xs" color="green" leftSection={<Check size={14} />} onClick={validate}>Valider</Button>}
          </Group>
        </Group>} />
    </Box>
  )
}

// ----------------------------------------------------- modale d'édition
function EditModal({ ex, comps, onClose, onSaved, onChange, onFamilyChanged }: {
  ex: Exercise | null; comps: Comp[]; onClose: () => void; onSaved: (e: Exercise) => void
  onChange: (e: Exercise) => void   // répercute sur la carte SANS fermer la modale
  // Une image appartient à la FAMILLE, pas à la carte : le backend la recopie
  // sur les deux autres dérivés (cf. indigo._mirror_figure). Leurs cartes sont
  // donc périmées à l'écran — on recharge la liste plutôt que de deviner.
  onFamilyChanged: () => void
}) {
  const [form, setForm] = useState<Exercise | null>(ex)
  const [figV, setFigV] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setForm(ex); setFigV(0) }, [ex])
  if (!form) return null
  const isProb = form.badge_type === 'probleme' || form.badge_type === 'enigme'
  const correct: number[] = form.expected?.correct ?? []

  const removeFigure = async () => {
    const updated = await api.del<Exercise>(`/api/indigo/exercises/${form.id}/figure`)
    setForm(updated)
    onChange(updated)  // répercute tout de suite sur la carte (plus de « image indisponible »), modale ouverte
    onFamilyChanged()
    notifications.show({ color: 'gray', message: 'Image supprimée sur les trois dérivés' })
  }

  const addFigure = async () => {
    try {
      const updated = await api.post<Exercise>(`/api/indigo/exercises/${form.id}/figure/add`)
      setForm(updated); setFigV((v) => v + 1); onChange(updated); onFamilyChanged()
      notifications.show({ color: 'green', message: 'Image ajoutée depuis le PDF — sélectionne maintenant la zone utile' })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
  }

  const editFigure = async (crop: ImageRect, masks: ImageRect[]) => {
    setBusy(true)
    try {
      const updated = await api.post<Exercise>(`/api/indigo/exercises/${form.id}/figure/edit`, { crop, masks })
      setForm(updated); setFigV((v) => v + 1); onChange(updated); onFamilyChanged()
      notifications.show({ color: 'green', message: masks.length
        ? 'Cadrage et caches blancs appliqués aux trois dérivés'
        : 'Nouveau cadrage appliqué aux trois dérivés' })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setBusy(false) }
  }

  const buildPatch = () => {
    // le rattachement voyage avec le reste : le professeur corrige l'énoncé ET
    // le range au même moment, en un seul enregistrement.
    const p: any = {
      statement: form.statement, response_type: form.response_type,
      correction_solution: form.correction_solution, correction_guide: form.correction_guide,
      badge_type: form.badge_type, difficulty: form.difficulty, calculator: form.calculator,
      title: form.title, tags: form.tags, bareme_points: form.bareme_points, expected: form.expected,
      competency_id: form.competency_id,
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
    <Modal opened={!!ex} onClose={onClose} size="xl" title={<Text fw={700}>Modifier {form.ref}</Text>}>
      <Stack gap="sm">
        {/* Ce que la passe de retouche n'a PAS su réparer : à traiter, pas
            seulement à regarder. En tête de la modale, avant le reste. */}
        {form.review_blocking?.length > 0 && (
          <Alert color="red" variant="light" icon={<AlertTriangle size={16} />}
            title={`${form.review_blocking.length} point(s) à corriger avant de valider`}>
            <Stack gap={2}>
              {form.review_blocking.map((n, i) => <Text key={i} size="xs">• {n}</Text>)}
            </Stack>
          </Alert>
        )}
        {/* Ce que la génération n'a pas su régler seule. Ce ne sont pas des
            erreurs certaines : c'est ce qu'il faut REGARDER avant de valider. */}
        {form.review_notes?.length > 0 && (
          <Alert color="yellow" variant="light" icon={<AlertTriangle size={16} />}
            title={`${form.review_notes.length} point(s) à relire`}>
            <Stack gap={2}>
              {form.review_notes.map((n, i) => <Text key={i} size="xs">• {n}</Text>)}
            </Stack>
          </Alert>
        )}
        {/* Rattachement manuel : la sortie de secours d'un exercice mal rangé.
            Sans elle, il ne restait qu'à le supprimer et tout réextraire. */}
        <Select label="Compétence" searchable
          description={form.competency_confirmed === false
            ? 'Rattachement non confirmé : vérifie-le avant de valider.'
            : undefined}
          error={form.competency_confirmed === false ? ' ' : undefined}
          data={comps.map((c) => ({ value: c.id, label: `${c.short_id || c.code} — ${c.label}` }))}
          value={form.competency_id}
          onChange={(v) => v && setForm({ ...form, competency_id: v, competency_confirmed: true })} />
        <Group grow>
          <Select label="Type de réponse" data={RESPONSE_TYPES} value={form.response_type}
            onChange={(v) => setForm({ ...form, response_type: v || 'short_text' })} />
          <Select label="Badge" data={Object.keys(BADGE_LABEL).map((k) => ({ value: k, label: BADGE_LABEL[k] }))}
            value={form.badge_type} onChange={(v) => setForm({ ...form, badge_type: v || 'exercice' })} />
          {/* difficulté = 3 niveaux, UNIQUEMENT pour les problèmes/énigmes */}
          {isProb && (
            <Select label="Difficulté" data={DIFF_OPTS} value={String(form.difficulty)}
              onChange={(v) => setForm({ ...form, difficulty: Number(v) || 2 })} />
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
          <ExercisePrintPreview exercise={form} color={badgeColor(form)} />
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

        {/* L'ajout reste disponible même si l'analyse estime qu'aucune figure
            n'est nécessaire : l'utilisateur est l'autorité sur l'énoncé PDF. */}
        {!form.has_figure && (
          <Button size="xs" variant="light" leftSection={<ImagePlus size={14} />}
            onClick={addFigure} style={{ alignSelf: 'flex-start' }}>
            Ajouter une image depuis l’énoncé du PDF
          </Button>
        )}

        {/* Éditeur dédié : le crop est redéfini sur la page PDF originale, ce qui
            permet aussi de dé-rogner une détection Mistral trop serrée. */}
        {form.has_figure && (
          <Paper withBorder p="sm" radius="md">
            <Group justify="space-between" mb={4}>
              <Box>
                <Text size="sm" fw={650}>Image insérée dans l’énoncé</Text>
                <Text size="xs" c="dimmed">Aperçu actuel</Text>
              </Box>
              <Button size="compact-xs" variant="subtle" color="red"
                leftSection={<ImageOff size={12} />} onClick={removeFigure}>Supprimer l'image</Button>
            </Group>
            {!form.figure_url && (
              <Text size="xs" c="dimmed">Image absente ou indisponible — « Supprimer l'image » retire la référence à une figure pour cet énoncé.</Text>
            )}
            {form.figure_url && (
              <AuthImg src={form.figure_url} reloadKey={figV} alt="figure"
                style={{ maxWidth: 360, maxHeight: 180, display: 'block', marginBottom: 12,
                  border: '1px solid var(--mantine-color-gray-3)' }} />
            )}
            {form.figure_box && <FigureEditor exerciseId={form.id} figureBox={form.figure_box}
              busy={busy} onApply={editFigure} />}
          </Paper>
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

function ExtractionAssistant({ opened, onClose, comps, manuals, grade, coverage, onLaunched }: {
  opened: boolean; onClose: () => void; comps: Comp[]; manuals: Manuals | null
  grade: string; coverage: IndexCoverage | null; onLaunched: () => void
}) {
  const [step, setStep] = useState(0)
  const [chosen, setChosen] = useState<string[]>([])
  const [targets, setTargets] = useState<Record<string, TargetDraft>>({})
  const [busy, setBusy] = useState(false)
  // Mode AUTOMATIQUE par défaut dès que l'index couvre quelque chose : c'est
  // tout l'intérêt de l'index (plus aucune plage à relever dans deux PDF de
  // 161 et 216 pages). La saisie manuelle reste accessible d'un clic — elle est
  // le repli quand l'index ne reconnaît pas une compétence.
  const [manual, setManual] = useState(false)
  useEffect(() => { if (opened) { setStep(0); setChosen([]); setTargets({}); setManual(false) } }, [opened])

  // ce que l'index sait, par compétence
  const cov = useMemo(() => {
    const m = new Map<string, IndexCoverage['competencies'][number]>()
    ;(coverage?.competencies ?? []).forEach((c) => m.set(c.competency_id, c))
    return m
  }, [coverage])
  const covered = (id: string) => {
    const c = cov.get(id)
    return !!c && c.pages.length > 0 && c.numbers.length > 0
  }
  const indexed = (coverage?.competencies ?? []).some(
    (c) => c.pages.length > 0 && c.numbers.length > 0)
  const autoReady = !manual && chosen.length > 0 && chosen.some(covered)
  const launchAuto = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ skipped: string[] }>('/api/indigo/extractions/auto',
        { grade_level: grade, competency_ids: chosen })
      notifications.show({
        color: r.skipped?.length ? 'orange' : 'indigo',
        message: r.skipped?.length
          ? `Extraction lancée. Non couvertes par l'index : ${r.skipped.join(', ')} — saisis leurs plages à la main.`
          : 'Extraction lancée en file de fond (pages et numéros déduits de l\'index)',
      })
      onLaunched(); onClose()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setBusy(false) }
  }

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
            <Text size="sm" c="dimmed">
              Choisis une ou plusieurs compétences. Le manuel élève fournit les énoncés,
              le manuel prof les corrigés.
            </Text>
            {!indexed && (
              <Alert color="orange" icon={<AlertTriangle size={16} />}>
                Le manuel n'est pas encore indexé : il faut saisir les plages à la main.
                Lance <b>Indexer le manuel</b> une fois, et l'assistant se réduira à cocher
                une compétence.
              </Alert>
            )}
            <ScrollArea h={indexed ? 340 : 380}>
              <Stack gap="xs">
                {byChapter.map(([chap, list]) => (
                  <Box key={chap}>
                    <Text size="xs" fw={700} c="dimmed" mb={4}>{chap}</Text>
                    <Stack gap={2}>
                      {list.map((c) => {
                        const k = cov.get(c.id)
                        return (
                          <Checkbox key={c.id} checked={chosen.includes(c.id)} onChange={() => toggleComp(c.id)}
                            label={
                              <Group gap={6} wrap="nowrap">
                                <Text size="sm"><b>{c.short_id}</b> {c.label}</Text>
                                {/* ce que l'index a trouvé : l'admin voit AVANT de lancer
                                    ce qui sera extrait, au lieu de le découvrir après coup */}
                                {indexed && covered(c.id) && (
                                  <Badge size="xs" variant="light" color="teal">
                                    n° {k!.numbers[0]}–{k!.numbers[k!.numbers.length - 1]}
                                    {' · '}p. {k!.pages[0] + 1}–{k!.pages[k!.pages.length - 1] + 1}
                                    {k!.prof_pages.length ? '' : ' · sans corrigé'}
                                  </Badge>
                                )}
                                {indexed && !covered(c.id) && (
                                  <Badge size="xs" variant="light" color="gray">hors index</Badge>
                                )}
                              </Group>
                            } />
                        )
                      })}
                    </Stack>
                  </Box>
                ))}
              </Stack>
            </ScrollArea>
            <Group justify="space-between">
              <Button variant="subtle" size="xs" onClick={() => { setManual(true); setStep(1) }}>
                Saisir les plages à la main
              </Button>
              <Button disabled={!autoReady} loading={busy} leftSection={<Sparkles size={16} />}
                onClick={launchAuto}>
                Lancer l'extraction ({chosen.filter(covered).length})
              </Button>
            </Group>
          </>
        )}
        {step === 1 && (
          <>
            <Text size="sm" c="dimmed">
              Saisie manuelle : pour chaque compétence, indique la PLAGE de pages (élève =
              énoncés, prof = corrigés) et la PLAGE de numéros d'exercices — format
              « 34-67 », bornes incluses. Le NUMÉRO fait foi : seuls les exercices de cette
              plage sont repris.
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
              <Button variant="subtle" onClick={() => { setManual(false); setStep(0) }}>Retour</Button>
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
  const domains = useMemo(() => {
    const domainMap = new Map<string, {
      code: string; name: string
      chapters: Map<string, { code: string; name: string; rows: SummaryRow[] }>
    }>()
    rows.forEach((r) => {
      if (!domainMap.has(r.domain_code)) domainMap.set(r.domain_code, {
        code: r.domain_code, name: r.domain_name, chapters: new Map(),
      })
      const domain = domainMap.get(r.domain_code)!
      if (!domain.chapters.has(r.chapter_code)) domain.chapters.set(r.chapter_code, {
        code: r.chapter_code, name: r.chapter_name, rows: [],
      })
      domain.chapters.get(r.chapter_code)!.rows.push(r)
    })
    return Array.from(domainMap.values()).map((domain) => ({
      key: domain.code || domain.name,
      code: domain.code,
      name: domain.name,
      chapters: Array.from(domain.chapters.values()).map((chapter) => ({
        key: `${domain.code}/${chapter.code || chapter.name}`,
        code: chapter.code,
        name: chapter.name,
        rows: chapter.rows,
      })),
    }))
  }, [rows])

  const count = (value: number, color: string) => value
    ? <Badge size="sm" color={color} variant="light">{value}</Badge>
    : <Text c="dimmed" size="sm">—</Text>

  const columns: CompetencyHierarchyColumn<SummaryRow>[] = [
    { key: 'draft', label: 'Brouillon', width: 82, align: 'center',
      render: (row) => count(row.draft, 'orange') },
    { key: 'validated', label: 'Validé', width: 76, align: 'center',
      render: (row) => count(row.validated, 'blue') },
    { key: 'published', label: 'Publié', width: 76, align: 'center',
      render: (row) => count(row.published, 'teal') },
  ]

  return (
    <CompetencyHierarchy domains={domains} columns={columns} columnGroupLabel="Exercices"
      showColumnHeaders={false}
      getRowKey={(row) => row.competency_id}
      getShortId={(row) => row.short_id}
      getLabel={(row) => row.label}
      onRowClick={onSelect}
      chapterAside={(chapter) => {
        const problem = chapter.rows[0]
        if (!problem) return null
        return (
          <Group gap="md" wrap="wrap">
            <Text size="xs" fw={650}>Problèmes</Text>
            <Group gap={5} wrap="nowrap">
              <Text size="xs" c="dimmed">Brouillon</Text>
              {count(problem.problem_draft, 'orange')}
            </Group>
            <Group gap={5} wrap="nowrap">
              <Text size="xs" c="dimmed">Validé</Text>
              {count(problem.problem_validated, 'blue')}
            </Group>
            <Group gap={5} wrap="nowrap">
              <Text size="xs" c="dimmed">Publié</Text>
              {count(problem.problem_published, 'teal')}
            </Group>
          </Group>
        )
      }} />
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
  const [pub, setPub] = useState<PublishState | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [packBusy, setPackBusy] = useState(false)
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)   // modale « Tout supprimer »
  const [deletingAll, setDeletingAll] = useState(false)
  const [providerOffline, setProviderOffline] = useState(false)    // clé DeepSeek Flash absente
  const [offpeak, setOffpeak] = useState<OffPeak | null>(null)     // Cheap and Wait
  const [coverage, setCoverage] = useState<IndexCoverage | null>(null)  // index du manuel
  const [indexing, setIndexing] = useState(false)
  const [selMode, setSelMode] = useState(false)                    // mode sélection (régénérer)
  const [selIds, setSelIds] = useState<Set<string>>(new Set())
  const [regenerating, setRegenerating] = useState(false)
  // Mode multipass : l'extraction Vision découvre elle-même numéros et
  // compétences. Deux bornes de pages élève remplacent donc l'ancien assistant.
  const [visionPageStart, setVisionPageStart] = useState('')
  const [visionPageEnd, setVisionPageEnd] = useState('')
  const [visionBusy, setVisionBusy] = useState(false)

  const isAll = grade === 'all'
  const loadSummary = useCallback(() => {
    if (isAll) return
    api.get<SummaryRow[]>(`/api/indigo/summary?grade_level=${grade}`).then(setSummary)
  }, [grade, isAll])
  const loadPub = useCallback(() => {
    if (isAll) return
    api.get<PublishState>('/api/indigo/published').then(setPub)
  }, [isAll])
  const loadExtractions = useCallback(() => {
    api.get<Extraction[]>('/api/indigo/extractions').then(setExtractions)
  }, [])
  const loadExercises = useCallback((cid: string) => {
    api.get<Exercise[]>(`/api/indigo/exercises?competency_id=${cid}`).then(setExercises)
  }, [])
  const loadCoverage = useCallback(() => {
    if (isAll) return
    api.get<IndexCoverage>(`/api/indigo/index?grade_level=${grade}`)
      .then(setCoverage).catch(() => setCoverage(null))
  }, [grade, isAll])

  useEffect(() => {
    if (isAll) { setSummary(null); return }
    setSelected(null); setExercises(null)
    setVisionPageStart(''); setVisionPageEnd('')
    api.get<Manuals>(`/api/indigo/manuals?grade_level=${grade}`).then(setManuals)
    api.get<{ competencies: Comp[] }>(`/api/indigo/competencies?grade_level=${grade}`).then((r) => setComps(r.competencies))
    loadSummary(); loadExtractions(); loadPub(); loadCoverage()
  }, [grade, isAll, loadSummary, loadExtractions, loadPub, loadCoverage])

  useEffect(() => {   // fournisseur LLM global (indépendant de la classe)
    // Plus de sélecteur : cette page force le mode multipass au chargement si
    // le réglage serveur pointait encore vers un autre fournisseur (anthropic/
    // deepseek/qcm), sans notification — ce n'est pas un geste de l'admin.
    api.get<{ provider: string; offline: boolean }>('/api/indigo/llm-provider')
      .then((r) => {
        if (r.provider === MULTIPASS) { setProviderOffline(r.offline); return }
        return api.post<{ provider: string; offline: boolean }>(
          '/api/indigo/llm-provider', { provider: MULTIPASS })
          .then((r2) => setProviderOffline(r2.offline))
      }).catch(() => { /* silencieux */ })
    api.get<OffPeak>('/api/indigo/offpeak').then(setOffpeak).catch(() => { /* silencieux */ })
  }, [])

  const active = extractions.some((e) => ['pending', 'running', 'cancelling'].includes(e.status))
  // Jobs créés mais dont les appels DeepSeek patientent le tarif creux
  // (Cheap and Wait) — repérés au marqueur posé par indigo_offpeak pendant
  // l'attente (§ WAITING_MARK).
  const waitingExtractions = useMemo(
    () => extractions.filter((e) => e.status === 'running'
      && e.progress_message.startsWith(WAITING_MARK)),
    [extractions])
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => {
      loadExtractions(); loadSummary(); loadCoverage()
      if (selected) loadExercises(selected.competency_id)
    }, 2500)
    return () => clearInterval(t)
  }, [active, selected, loadExtractions, loadSummary, loadExercises, loadCoverage])

  // Une ligne par famille (cf. utils/families) : facile, base, difficile.
  const exerciseRows = useMemo(() => familyRows(exercises), [exercises])

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
  const cancelExtraction = async (id: string) => {
    // "pending" s'arrête net (statut "cancelled") ; "running" repasse par
    // "cancelling" — le worker le relit entre deux cibles/pages (cf.
    // services.indigo._ExtractionCancelled), donc pas instantané.
    try {
      const r = await api.post<Extraction>(`/api/indigo/extractions/${id}/cancel`)
      setExtractions((xs) => xs.map((e) => e.id === id ? r : e))
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
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
  // Le réglage est relu par le serveur À CHAQUE tour d'attente : décocher la
  // case pendant qu'une extraction patiente en heures pleines la libère en
  // moins d'une minute, sans rien redémarrer.
  const saveOffpeak = async (patch: Partial<Omit<OffPeak, 'open_now' | 'next_open'>>) => {
    const prev = offpeak
    if (offpeak) setOffpeak({ ...offpeak, ...patch })   // optimiste
    try { setOffpeak(await api.post<OffPeak>('/api/indigo/offpeak', patch)) }
    catch (e: any) { setOffpeak(prev); notifications.show({ color: 'red', message: e.message }) }
  }
  const buildIndex = async () => {
    setIndexing(true)
    try {
      await api.post(`/api/indigo/index?grade_level=${grade}`)
      notifications.show({
        color: 'indigo',
        message: 'Indexation lancée. Le manuel prof se lit gratuitement ; les pages '
          + 'élève passent une seule fois par l\'OCR, et une indexation interrompue reprend.',
      })
      loadExtractions()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setIndexing(false) }
  }
  const visionPageOk = (value: string) => {
    if (!/^\d+$/.test(value.trim())) return false
    const page = Number(value)
    return page >= 1 && page <= (manuals?.manuals.eleve.pages ?? 0)
  }
  const visionRangeOk = visionPageOk(visionPageStart) && visionPageOk(visionPageEnd)
  const launchVision = async () => {
    if (!visionRangeOk) return
    setVisionBusy(true)
    try {
      const a = Number(visionPageStart); const b = Number(visionPageEnd)
      await api.post('/api/indigo/extractions/vision', {
        grade_level: grade, page_start: Math.min(a, b), page_end: Math.max(a, b),
      })
      notifications.show({
        color: 'indigo',
        message: `Extraction Vision lancée sur les pages ${Math.min(a, b)} à ${Math.max(a, b)}`,
      })
      loadExtractions()
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setVisionBusy(false) }
  }
  const validatedCount = useMemo(() => {
    const rows = summary ?? []
    const exerciseCount = rows.reduce((n, s) => n + s.validated, 0)
    const seen = new Set<string>()
    const problemCount = rows.reduce((n, s) => {
      const key = `${s.domain_code}/${s.chapter_code}`
      if (seen.has(key)) return n
      seen.add(key)
      return n + s.problem_validated
    }, 0)
    return exerciseCount + problemCount
  }, [summary])
  const publish = async () => {
    setPublishing(true)
    try {
      const r = await api.post<PublishState>('/api/indigo/publish')
      setPub(r); loadSummary()
      notifications.show({
        color: 'green', autoClose: 8000,
        message: `${r.published} exercice(s) publié(s) sur cette instance. `
          + 'Pour les livrer aux autres utilisateurs : « Exporter pour le dépôt ».',
      })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setPublishing(false) }
  }
  // Le volume de cette instance ne sort pas tout seul de la machine : l'archive
  // se décompresse dans backend/app/data/indigo/ du dépôt, se commite, et repart
  // dans l'image au build suivant — d'où elle est semée en banque pour TOUS.
  const exportBundle = async () => {
    setExporting(true)
    try {
      await download('/api/indigo/export', 'indigo-publication.zip')
      notifications.show({
        color: 'blue', autoClose: 10000,
        message: 'Archive téléchargée. Décompresse-la dans backend/app/data/indigo/ '
          + 'du dépôt, puis commite : les exercices partiront dans l\'image suivante.',
      })
    } catch (e: any) { notifications.show({ color: 'red', message: e.message }) }
    finally { setExporting(false) }
  }

  // ---- pack de travail : le pont entre la machine qui a les manuels et
  // l'instance qui fabrique. Les PDF (203 Mo, sous droits) ne sont livrés dans
  // aucune image ; le pack porte l'index déjà payé + les pages rendues, tout ce
  // dont la fabrication a besoin.
  const loadManuals = useCallback(() => {
    api.get<Manuals>(`/api/indigo/manuals?grade_level=${grade}`).then(setManuals)
  }, [grade])
  const exportPack = async () => {
    setPackBusy(true)
    notifications.show({
      id: 'pack-export', loading: true, autoClose: false, withCloseButton: false,
      message: 'Rendu des pages du manuel… (~90 Mo, une trentaine de secondes)',
    })
    try {
      await download(`/api/indigo/pack/export?grade_level=${grade}`, `indigo-pack-${grade}.zip`)
      notifications.update({
        id: 'pack-export', loading: false, autoClose: 12000, color: 'green',
        message: 'Pack téléchargé. Sur l\'instance qui fabrique : onglet Exercices → '
          + '« Importer le pack ». Elle pourra alors créer des exercices sans les PDF.',
      })
    } catch (e: any) {
      notifications.update({ id: 'pack-export', loading: false, color: 'red', message: e.message })
    } finally { setPackBusy(false) }
  }
  // `file` absent = archive déposée à la main sur le volume (indigo-pack.zip) :
  // le seul recours quand ~90 Mo ne passent pas par le navigateur.
  const importPack = async (file: File | null) => {
    setPackBusy(true)
    let body: FormData | undefined
    if (file) { body = new FormData(); body.append('file', file) }
    try {
      const r = await api.post<{ pages: number; index: { eleve: number; prof: number } }>(
        `/api/indigo/pack/import?grade_level=${grade}`, body)
      loadManuals(); loadCoverage()
      notifications.show({
        color: 'green', autoClose: 12000,
        message: `Pack installé : ${r.pages} page(s) de manuel et l'index `
          + `(${r.index.eleve} pages élève, ${r.index.prof} pages prof). `
          + 'Tu peux lancer une extraction — aucune plage à saisir, aucun OCR à repayer.',
      })
    } catch (e: any) { notifications.show({ color: 'red', autoClose: 12000, message: e.message }) }
    finally { setPackBusy(false) }
  }

  if (isAll) {
    return <GradeSelectionRequired title="Exercices" />
  }

  const eleveOk = manuals?.manuals.eleve.available
  const pack = manuals?.pack
  const hasPdf = manuals?.manuals.eleve.pdf

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap={8}>
          {selected && <ActionIcon variant="subtle" onClick={() => setSelected(null)}><ChevronLeft size={20} /></ActionIcon>}
          <Title order={2}>Exercices <Text span c="dimmed" size="sm">· manuel Indigo {grade}</Text></Title>
        </Group>
        <Group>
          {pub && pub.seeded === pub.published && (
            <Tooltip label={pub.on_volume
              ? 'Publié depuis cette instance, sur le volume persistant : survit aux mises à '
                + 'jour du conteneur. Utilise « Exporter pour le dépôt » pour le livrer aux autres.'
              : 'Contenu livré avec l\'image (dépôt). Cette instance n\'a encore rien publié.'}>
              <Text size="xs" c="dimmed">
                {pub.seeded} en banque · {validatedCount} validé(s)
                <Text span c={pub.on_volume ? 'teal' : 'dimmed'} ml={6}>
                  {pub.on_volume ? '· volume' : '· image'}
                </Text>
              </Text>
            </Tooltip>
          )}
          {pub && pub.seeded !== pub.published && (
            <Tooltip label={`${pub.seeded} exercice(s) Indigo actif(s) en banque, mais ${pub.published} figé(s) dans le fichier versionné (backend/app/data/indigo/) — la banque a été modifiée hors de cet onglet (purge, redémarrage...). Republie pour refiger le fichier sur l'état actuel des brouillons.`}>
              <Text size="xs" c="orange">{pub.seeded} en banque ⚠ {pub.published} figé(s) · {validatedCount} validé(s)</Text>
            </Tooltip>
          )}
          <Tooltip multiline w={420} label={
            'Mode de génération : QCM multipass. Extraction directe des pages par '
            + 'DeepSeek Vision, puis QCM à choix unique, QCM multiple et grille à '
            + 'cocher — tous corrigés par vision par ordinateur — par lots de deux '
            + 'sources et SIX passes (filtre, contexte, génération, résolution '
            + 'indépendante, mise en page, retouche). La passe contexte juge d\'abord si '
            + 'l\'énoncé seul suffit, sinon si le corrigé du prof (retrouvé dans l\'index) '
            + 'comble ce qu\'une figure emporte seule, sinon si le contexte pédagogique '
            + 'suffit encore à INVENTER un exercice fidèle à cet esprit — la source n\'est '
            + 'écartée qu\'en dernier recours, quand rien de fiable ne s\'en dégage. '
            + 'Un guide de 30 mots par exercice, et le duo Base/Facile part en BROUILLON. '
            + 'Un exercice « Expert » du manuel tient lieu de dérivé Difficile. Aucun '
            + 'exercice n\'est renvoyé en génération : la passe 5 répare sur place ce que '
            + 'les contrôles reprochent, et signale d\'un badge rouge ce qu\'elle n\'a pas '
            + 'su réparer.'
            + (providerOffline ? ' ⚠ Clé DeepSeek Flash absente.' : '')}>
            <Badge size="lg" variant="light" color={providerOffline ? 'orange' : 'blue'}>
              QCM multipass
            </Badge>
          </Tooltip>
          <ActionIcon variant="light" onClick={() => { loadSummary(); loadExtractions(); loadCoverage(); if (selected) loadExercises(selected.competency_id) }}><RefreshCw size={16} /></ActionIcon>
          <Tooltip label={
            !hasPdf
              ? 'Indexer demande les PDF des manuels, absents de cette instance. '
                + 'Inutile ici : le pack de travail importé porte déjà l\'index.'
              : coverage
                ? `Index du manuel : ${coverage.eleve.indexed}/${coverage.eleve.total} page(s) élève, `
                  + `${coverage.prof.indexed}/${coverage.prof.total} page(s) prof. Une fois indexé, `
                  + `l'assistant n'a plus besoin d'aucune plage — et l'OCR n'est jamais repayé.`
                : "Lit le manuel une fois pour en déduire les pages et les numéros d'exercices "
                  + 'de chaque compétence. Reprend là où il s\'est arrêté.'}>
            <Button variant="light" color="indigo" leftSection={<BookOpen size={16} />}
              loading={indexing} disabled={!hasPdf} onClick={buildIndex}>
              Indexer{coverage?.eleve.total
                ? ` (${Math.round(100 * coverage.eleve.indexed / coverage.eleve.total)} %)`
                : ''}
            </Button>
          </Tooltip>
          {hasPdf ? (
            <Tooltip label={"Archive à porter sur l'instance qui fabrique les exercices : "
              + "l'index déjà payé + toutes les pages du manuel élève rendues. Elle pourra "
              + 'alors créer, corriger et publier des exercices sans jamais avoir les PDF '
              + '(~90 Mo). À refaire seulement si le manuel change.'}>
              <Button variant="light" color="grape" leftSection={<Package size={16} />}
                loading={packBusy} disabled={!coverage?.eleve.total
                  || coverage.eleve.indexed < coverage.eleve.total}
                onClick={exportPack}>Exporter le pack</Button>
            </Tooltip>
          ) : (
            <Group gap={4}>
              <FileButton accept="application/zip,.zip" onChange={(f) => { if (f) importPack(f) }}>
                {(props) => (
                  <Tooltip label={pack?.pack
                    ? `Pack installé : ${pack.pack.pages_present} page(s) de manuel, index `
                      + `${pack.index.eleve}/${pack.index.prof}. Réimporte pour le remplacer.`
                    : "Installe le pack exporté depuis l'instance qui porte les manuels : "
                      + "index + pages. C'est ce qui rend cette instance capable de fabriquer."}>
                    <Button {...props} variant={pack?.pack ? 'subtle' : 'filled'} color="grape"
                      leftSection={<PackagePlus size={16} />} loading={packBusy}>
                      {pack?.pack ? 'Pack installé' : 'Importer le pack'}
                    </Button>
                  </Tooltip>
                )}
              </FileButton>
              <Tooltip label={'Archive trop grosse pour le navigateur ? Dépose-la sur le '
                + 'volume sous « indigo-pack.zip » (à la racine de /data, soit '
                + 'volumes/data/ sur un NAS), puis clique ici.'}>
                <ActionIcon variant="light" color="grape" size="lg" loading={packBusy}
                  onClick={() => importPack(null)} aria-label="Importer depuis le volume">
                  <HardDrive size={16} />
                </ActionIcon>
              </Tooltip>
            </Group>
          )}
          <Tooltip label={"Fige les exercices validés sur cette instance (volume persistant : "
            + 'ils survivent aux mises à jour) et les sème en banque.'}>
            <Button variant="light" color="teal" leftSection={<UploadCloud size={16} />}
              loading={publishing} disabled={validatedCount === 0} onClick={publish}>Publier</Button>
          </Tooltip>
          <Tooltip label={"Archive du contenu publié, à décompresser dans "
            + 'backend/app/data/indigo/ du dépôt puis à committer : c\'est ce qui livre '
            + 'ces exercices à TOUS les déploiements au build suivant.'}>
            <Button variant="light" color="gray" leftSection={<Download size={16} />}
              loading={exporting} disabled={!pub?.published} onClick={exportBundle}>
              Exporter pour le dépôt
            </Button>
          </Tooltip>
        </Group>
      </Group>

      <Paper withBorder p="sm" radius="md">
        <Group justify="space-between" align="flex-end">
          <Box>
            <Text fw={650}>Pages du manuel élève à extraire</Text>
            <Text size="xs" c="dimmed">
              DeepSeek Vision lit tous les exercices, leur titre de compétence rose et les crops de figures.
            </Text>
            {offpeak && (
              <Group gap={6} mt={6}>
                <Tooltip multiline w={360} label={
                  'Coché : les extractions sont créées immédiatement et mises EN ATTENTE ; '
                  + 'les appels DeepSeek ne partent qu\'aux heures creuses du fournisseur — '
                  + 'jamais 01h-04h ni 06h-10h UTC en semaine (tarif plein). Un appel commencé '
                  + 'va toujours à son terme — c\'est le suivant qui attend la prochaine '
                  + 'fenêtre creuse. Décocher libère la file en moins d\'une minute.'}>
                  <Checkbox size="xs" checked={offpeak.enabled} label="Cheap and Wait"
                    onChange={(e) => saveOffpeak({ enabled: e.currentTarget.checked })} />
                </Tooltip>
                {offpeak.enabled && (
                  <Badge size="xs" variant="light" color={offpeak.open_now ? 'teal' : 'orange'}
                    leftSection={<Clock size={11} />}>
                    {offpeak.open_now ? 'tarif creux' : `reprend à ${fmtLocalTime(offpeak.next_open)}`}
                  </Badge>
                )}
              </Group>
            )}
          </Box>
          <Group align="flex-start">
            <TextInput label="Première page" placeholder="34" w={130} inputMode="numeric"
              value={visionPageStart}
              error={visionPageStart && !visionPageOk(visionPageStart) ? `1–${manuals?.manuals.eleve.pages ?? 0}` : undefined}
              onChange={(e) => setVisionPageStart(e.currentTarget.value)} />
            <TextInput label="Dernière page" placeholder="40" w={130} inputMode="numeric"
              value={visionPageEnd}
              error={visionPageEnd && !visionPageOk(visionPageEnd) ? `1–${manuals?.manuals.eleve.pages ?? 0}` : undefined}
              onChange={(e) => setVisionPageEnd(e.currentTarget.value)} />
            <Button mt={25} leftSection={<Wand2 size={16} />} loading={visionBusy}
              disabled={!eleveOk || providerOffline || !visionRangeOk}
              onClick={launchVision}>Extraire ces pages</Button>
          </Group>
        </Group>
        {offpeak?.enabled && waitingExtractions.length > 0 && (
          <Stack gap={4} mt="sm" pt="sm" style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}>
            <Text size="xs" fw={600} c="dimmed">
              File d'attente Cheap and Wait — envoyée à {fmtLocalTime(offpeak.next_open)}
            </Text>
            {waitingExtractions.map((e) => (
              <Group key={e.id} gap={6} wrap="nowrap">
                <Clock size={13} color="var(--mantine-color-orange-6)" />
                <Text size="xs" c="dimmed">{extractionLabel(e)}</Text>
              </Group>
            ))}
          </Stack>
        )}
      </Paper>

      {manuals && !eleveOk && (
        <Alert color="orange" icon={<AlertTriangle size={16} />}
          title={`Aucune source de pages pour le manuel ${grade} sur cette instance`}>
          <Stack gap={6}>
            <Text size="sm">
              Les manuels sont sous droits et trop volumineux pour le dépôt : ils ne sont
              livrés dans <b>aucune</b> image. Deux façons d'équiper cette instance —
              la première suffit, et ne demande aucun PDF :
            </Text>
            <Text size="sm">
              <b>1. Importer le pack de travail</b> (recommandé) — sur la machine qui a les
              manuels, onglet Exercices → <b>Exporter le pack</b> ; ici →{' '}
              <b>Importer le pack</b>. Le pack contient l'index (OCR déjà payé) et toutes
              les pages du manuel : extraction, découpe, couleurs des badges et figures
              fonctionnent à l'identique. Archive trop grosse pour le navigateur ? Dépose-la
              sur le volume sous <code>indigo-pack.zip</code>, puis clique sur Importer sans
              choisir de fichier.
            </Text>
            <Text size="sm">
              <b>2. Déposer les PDF</b> — sur un NAS (Docker), dans le volume{' '}
              <code>volumes/data/manuals/</code> à côté du <code>docker-compose.yml</code> ;
              en développement, dans <code>context/</code>. Noms exacts attendus :{' '}
              <code>{grade === '3e' ? '3_indigo.pdf' : `${grade[0]}_indigo.pdf`}</code> et la
              variante <code>_prof</code>. C'est la seule voie qui permet aussi d'indexer.
            </Text>
          </Stack>
        </Alert>
      )}

      {extractions.filter((e) => ['pending', 'running', 'cancelling', 'cancelled', 'failed'].includes(e.status)).slice(0, 3).map((e) => {
        const done = e.status === 'failed' || e.status === 'cancelled'
        const stoppable = e.status === 'pending' || e.status === 'running'
        // marqué par indigo_offpeak.wait_until_open : en file d'attente Cheap
        // and Wait, aucun appel DeepSeek en cours — distinct d'un job actif.
        const waiting = e.status === 'running' && e.progress_message.startsWith(WAITING_MARK)
        return (
          <Alert key={e.id} color={e.status === 'failed' ? 'red' : waiting ? 'orange' : done ? 'gray' : 'indigo'}
            icon={e.status === 'failed' ? <AlertTriangle size={16} /> : waiting ? <Clock size={14} /> : <Loader size={14} />}
            // une extraction terminée (échec ou arrêtée) peut être fermée (croix) :
            // le bandeau ne revient pas au rechargement (ex. échec réseau, API hors ligne)
            withCloseButton={done} closeButtonLabel="Masquer"
            onClose={() => dismissExtraction(e.id)}>
            <Group justify="space-between">
              <Text size="sm">
                {e.status === 'failed' ? `Échec : ${e.error_message}`
                  : e.status === 'cancelled' ? 'Arrêtée par l\'utilisateur'
                  : e.status === 'cancelling' ? 'Arrêt en cours…'
                  : e.progress_message || 'Extraction en cours…'}
              </Text>
              <Group gap={8}>
                {!done && <Text size="xs" c="dimmed">{e.progress}%</Text>}
                {stoppable && (
                  <Button size="xs" variant="subtle" color="red" leftSection={<Square size={12} />}
                    onClick={() => cancelExtraction(e.id)}>
                    Stopper
                  </Button>
                )}
              </Group>
            </Group>
            {!done && <Progress value={e.progress} size="sm" mt={4} />}
          </Alert>
        )
      })}

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
          {/* Une LIGNE par famille : facile à gauche, base au milieu, difficile à
              droite. Les trois dérivés d'un même exercice source se relisent
              alors côte à côte — c'est la comparaison qui dit si l'étayage du
              facile et l'exigence du difficile tiennent la route. */}
          <Box style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, minmax(280px, 1fr))',
            alignItems: 'start', gap: 'var(--mantine-spacing-md)',
          }}>
            {exerciseRows.flatMap((row, r) => row.map((ex, c) => (ex ? (
              <ExerciseCard key={ex.id} ex={ex} onEdit={setEditing} onChange={onChange} onDelete={onDelete}
                selectable={selMode} selected={selIds.has(ex.id)} onToggleSelect={toggleSel} />
            ) : (
              <Box key={`${r}-${c}`} aria-hidden />
            ))))}
          </Box>
        </>
      )}

      {manuals && (
        <ExtractionAssistant opened={assistant} onClose={() => setAssistant(false)} comps={comps}
          manuals={manuals} grade={grade} coverage={coverage} onLaunched={loadExtractions} />
      )}
      <EditModal ex={editing} comps={comps} onClose={() => setEditing(null)} onChange={onChange}
        onFamilyChanged={() => { if (selected) loadExercises(selected.competency_id) }}
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
