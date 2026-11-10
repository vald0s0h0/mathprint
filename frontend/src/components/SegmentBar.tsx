// Pipeline d'un lot de scan, en barre segmentée qui se remplit de gauche à droite :
//   vert   = étape faite
//   bleu   = en cours (travail de la machine : seul cas animé, un reflet lent qui avance)
//   orange = à vous (action du professeur)
//   rouge  = bloqué
//   gris   = à venir
// Même hauteur que le badge de taille du lot ; utilisée par chaque lot scanné.
import { Text, Tooltip } from '@mantine/core'

export type SegState = 'green' | 'orange' | 'blue' | 'gray' | 'red'
export type Segment = { phase: string; label?: string; state: SegState }
/** Étape ajoutée en fin de barre (ex. « Distribué »), cliquable si `onClick`. */
export type ExtraStep = Segment & { onClick?: () => void; hint?: string }

export const BAR_HEIGHT = 26

// Pastels : fond clair + texte foncé en mode clair, fond translucide + texte clair en sombre.
const pastel = (light: string, dark: string) => `light-dark(${light}, ${dark})`
const STYLE: Record<SegState, { bg: string; fg: string }> = {
  green: { bg: pastel('#CDEBD6', 'rgba(81, 207, 102, 0.22)'), fg: pastel('#24603A', '#B2F2BB') },
  blue: { bg: pastel('#D3E3FB', 'rgba(77, 171, 247, 0.26)'), fg: pastel('#1D4F94', '#A5D8FF') },
  // plus soutenu que les autres : seule couleur qui demande une action du professeur
  orange: { bg: pastel('#FFB65C', 'rgba(255, 146, 43, 0.55)'), fg: pastel('#4A2600', '#FFF0DB') },
  red: { bg: pastel('#FFD3D3', 'rgba(255, 107, 107, 0.28)'), fg: pastel('#8C2222', '#FFC9C9') },
  gray: { bg: pastel('#EEF0F3', 'rgba(255, 255, 255, 0.06)'), fg: 'var(--mantine-color-dimmed)' },
}

// Les cases n'ont pas toutes la même largeur (le libellé n'est jamais tronqué), mais
// chaque étape tombe dans la même colonne de la grille de la carte : les barres de
// tous les lots restent alignées, étape par étape.
export const STEP_COUNT = 8   // 7 étapes du pipeline + « Distribué »
const PHASES = ['scan', 'read', 'ocrize', 'correct', 'validate', 'done', 'overlay_print', 'distribute']
const PAD_X = 10

let widths: number[] | null = null
/** Largeur (px) de chaque colonne d'étape : celle de son plus long libellé possible. Fixe
 *  et commune à TOUTES les cartes, pour que les barres s'alignent d'un sujet à l'autre. */
export function stepWidths(): number[] {
  if (widths) return widths
  const ctx = document.createElement('canvas').getContext('2d')
  const family = getComputedStyle(document.body).fontFamily || 'sans-serif'
  if (ctx) ctx.font = `400 12px ${family}`
  widths = PHASES.map((phase) => {
    const labels = Object.values(LABELS[phase])
    const longest = Math.max(...labels.map((l) => ctx?.measureText(l).width ?? l.length * 6.6))
    return Math.ceil(longest) + 2 * PAD_X + 4
  })
  return widths
}

// Libellé selon l'état : verbe au participe quand c'est fait (« Corrigé »), nom d'action
// quand ça travaille (« Correction »), à-faire quand c'est à vous (« À corriger »).
const LABELS: Record<string, { green: string; blue: string; orange: string; gray: string }> = {
  scan: { green: 'Scan déposé', blue: 'Scan en cours', orange: 'Scan à déposer', gray: 'Scan' },
  read: { green: 'Copies lues', blue: 'Lecture', orange: 'Lecture à vérifier', gray: 'Lecture' },
  ocrize: { green: 'OCRisé', blue: 'OCRisation', orange: 'À OCRiser', gray: 'OCRisation' },
  correct: { green: 'Corrigé', blue: 'Correction', orange: 'À corriger', gray: 'Correction' },
  validate: { green: 'Validé', blue: 'Validation', orange: 'À valider', gray: 'Validation' },
  done: { green: 'Copies corrigées', blue: 'Génération', orange: 'Overlay à régénérer', gray: 'Copies' },
  overlay_print: { green: 'Overlay imprimé', blue: 'Impression', orange: 'Overlay à imprimer', gray: 'Impression' },
  distribute: { green: 'Distribué', blue: 'Distribution', orange: 'À distribuer', gray: 'Distribution' },
}
function labelOf(s: Segment): string {
  const table = LABELS[s.phase]
  if (!table) return s.label ?? s.phase
  // bloqué : on garde le libellé de l'étape en cours
  return table[s.state === 'red' ? 'blue' : s.state]
}

function Cell({ s, first, last }: { s: ExtraStep; first: boolean; last: boolean }) {
  const { bg, fg } = STYLE[s.state]
  const clickable = !!s.onClick
  const cell = (
    <div
      className={s.state === 'blue' ? 'mp-seg-busy' : undefined}
      role={clickable ? 'button' : undefined} tabIndex={clickable ? 0 : undefined}
      onClick={s.onClick}
      onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); s.onClick?.() } } : undefined}
      style={{
        height: BAR_HEIGHT, padding: `0 ${PAD_X}px`, marginRight: last ? 0 : 1, backgroundColor: bg, color: fg,
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, boxSizing: 'border-box', whiteSpace: 'nowrap',
        cursor: clickable ? 'pointer' : 'default',
        borderRadius: `${first ? 4 : 0}px ${last ? 4 : 0}px ${last ? 4 : 0}px ${first ? 4 : 0}px`,
      }}>
      <Text span inherit fz="xs" fw={400}
        style={{ whiteSpace: 'nowrap' }}>{labelOf(s)}</Text>
    </div>
  )
  return s.hint ? <Tooltip label={s.hint} withinPortal>{cell}</Tooltip> : cell
}

/** Rend une cellule de grille par étape : à placer dans la grille de la carte. */
export default function SegmentBar({ segments, extra }: { segments: Segment[]; extra?: ExtraStep }) {
  const all: ExtraStep[] = extra ? [...segments, extra] : segments
  return (
    <>
      {all.map((s, i) => (
        <Cell key={s.phase} s={s} first={i === 0} last={i === all.length - 1} />
      ))}
    </>
  )
}
