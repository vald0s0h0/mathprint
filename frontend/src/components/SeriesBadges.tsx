// Séries du lycée ivoirien dans l'onglet Exercices : badges de couleur fixes
// (une couleur par série, partout), emplacements alignés d'une ligne à l'autre,
// et libellé d'une variante (même contenu, un détail propre à chaque série).
import { Box, Group, UnstyledButton } from '@mantine/core'
import type { ReactNode } from 'react'
import MathText from './MathText'

// Couleur pleine (badge) et couleurs claires (partie de variante dans le libellé).
// Texte blanc sur la couleur pleine : contraste ≥ 4,5:1 pour chacune.
export const SERIES_COLORS: Record<string, { solid: string; light: string; ink: string }> = {
  C: { solid: '#2563eb', light: '#dbeafe', ink: '#1e3a8a' },
  D: { solid: '#c2410c', light: '#ffedd5', ink: '#7c2d12' },
  A1: { solid: '#7c3aed', light: '#ede9fe', ink: '#4c1d95' },
  A2: { solid: '#be185d', light: '#fce7f3', ink: '#831843' },
  A: { solid: '#0f766e', light: '#ccfbf1', ink: '#134e4a' },
}
const FALLBACK = { solid: '#4b5563', light: '#f3f4f6', ink: '#1f2937' }
export const seriesColor = (series: string) => SERIES_COLORS[series] ?? FALLBACK

export const SLOT_WIDTH = 80
const SLOT_GAP = 6

/** Largeur de la colonne des badges pour `count` séries (marges de cellule comprises). */
export const seriesColumnWidth = (count: number) => count * SLOT_WIDTH + (count - 1) * SLOT_GAP + 26

const slotStyle = { width: SLOT_WIDTH, flex: `0 0 ${SLOT_WIDTH}px`, boxSizing: 'border-box' as const }

/** Un emplacement par série, dans l'ordre de la filière ; une série absente laisse sa place vide. */
export function SeriesSlots({ series, children }: {
  series: string[]
  children: (series: string) => ReactNode
}) {
  return <Group gap={SLOT_GAP} wrap="nowrap" align="center">
    {series.map(s => <Box key={s} style={slotStyle}>{children(s)}</Box>)}
  </Group>
}

export function SeriesBadge({ series, text, onClick, title }: {
  series: string; text: string; onClick?: () => void; title?: string
}) {
  const color = seriesColor(series)
  const style = {
    display: 'block', width: '100%', textAlign: 'center' as const, whiteSpace: 'nowrap' as const,
    fontSize: 11, fontWeight: 600, lineHeight: '18px', color: '#ffffff',
    background: color.solid, borderRadius: 10, padding: '0 6px',
  }
  const content = `${series} ${text}`
  if (!onClick) return <Box component="span" style={style} title={title}>{content}</Box>
  return <UnstyledButton style={style} title={title} aria-label={title}
    onClick={(event) => { event.stopPropagation(); onClick() }}>{content}</UnstyledButton>
}

export type Variant = { pre: string; post: string; parts: Record<string, string> }

/** « Calculer les coordonnées du barycentre de [C : 2, 3 ou 4] [D : 2 ou 3] points pondérés ». */
export function VariantLabel({ variant, series }: { variant: Variant; series: string[] }) {
  return <>
    <MathText text={variant.pre} />
    {series.filter(s => variant.parts[s]).map((s, i) => {
      const color = seriesColor(s)
      return <span key={s}>{i > 0 && ' '}<span style={{
        background: color.light, color: color.ink, borderRadius: 4, padding: '1px 5px', whiteSpace: 'nowrap',
      }}>{s} : {variant.parts[s].includes('$')
        ? <MathText text={variant.parts[s]} />
        // texte simple : pas de MathText, dont le pre-wrap couperait le badge
        : variant.parts[s]}</span></span>
    })}
    <MathText text={variant.post} />
  </>
}
