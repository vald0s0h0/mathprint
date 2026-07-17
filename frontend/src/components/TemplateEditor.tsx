// Éditeur visuel des trois templates de documents : en-tête, carte exercice,
// rappel de leçon. Cliquer un élément pour le sélectionner, tirer la poignée
// pour redimensionner le texte, régler couleurs/coins/ombre à droite.
// L'aperçu HTML est un miroir fidèle du rendu PDF (pdfgen) ; le bouton
// « Aperçu PDF réel » rend un vrai PDF via le backend.
import {
  Badge, Box, Button, Card, ColorInput, Group, Modal, SegmentedControl,
  Slider, Stack, Switch, Text,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { BookOpen, Eye, RotateCcw, Save } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, getToken } from '../api'
import MathText from './MathText'

export type DocTemplates = {
  header: { name_size: number; title_size: number; accent: string; show_date: boolean }
  exercise: { font_size: number; math_size: number; border: string; radius: number; shadow: boolean }
  lesson: { font_size: number; bg: string; border: string; text: string }
}

export const TEMPLATE_DEFAULTS: DocTemplates = {
  header: { name_size: 14, title_size: 8, accent: '#37474F', show_date: true },
  exercise: { font_size: 9, math_size: 12, border: '#C7CDD4', radius: 2.2, shadow: true },
  lesson: { font_size: 8, bg: '#FFF6DF', border: '#E4C46A', text: '#6B5310' },
}

// pt (PDF) -> px (aperçu) : l'aperçu est agrandi pour rester lisible à l'écran
const S = 1.7

// Miroir de pdfgen.DIFFICULTY_COLORS (badge de numéro d'exercice, 1 -> 5)
const DIFFICULTY_COLORS: Record<number, string> = {
  1: '#2563EB', 2: '#16A34A', 3: '#CA8A04', 4: '#EA580C', 5: '#DC2626',
}

type Sel =
  | { part: 'header'; field: 'name_size' | 'title_size' }
  | { part: 'exercise'; field: 'font_size' | 'math_size' }
  | { part: 'lesson'; field: 'font_size' }
  | null

const FIELD_LABELS: Record<string, string> = {
  name_size: 'Nom, classe', title_size: 'Titre',
  font_size: 'Texte', math_size: 'Expression mathématique',
}
const LIMITS: Record<string, [number, number]> = {
  name_size: [9, 24], title_size: [6, 16],
  font_size: [6, 14], math_size: [8, 20],
}

/** Texte cliquable + poignée de redimensionnement (glisser verticalement). */
function Resizable({ sel, me, onSelect, onResize, size, children, style }: {
  sel: Sel; me: NonNullable<Sel>; onSelect: (s: Sel) => void
  onResize: (v: number) => void; size: number
  children: React.ReactNode; style?: React.CSSProperties
}) {
  const selected = sel?.part === me.part && sel?.field === me.field
  const drag = useRef<{ y: number; v: number } | null>(null)
  const [min, max] = LIMITS[me.field]

  const onPointerDown = (e: React.PointerEvent) => {
    e.stopPropagation(); e.preventDefault()
    drag.current = { y: e.clientY, v: size }
    const move = (ev: PointerEvent) => {
      if (!drag.current) return
      const next = Math.round(Math.min(max, Math.max(min, drag.current.v + (ev.clientY - drag.current.y) / 4)))
      onResize(next)
    }
    const up = () => {
      drag.current = null
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <span
      onClick={(e) => { e.stopPropagation(); onSelect(me) }}
      style={{
        position: 'relative', display: 'inline-block', cursor: 'pointer',
        outline: selected ? '2px solid var(--mantine-color-indigo-5)' : '1px dashed transparent',
        outlineOffset: 2, borderRadius: 2, ...style,
      }}>
      {children}
      {selected && (
        <span onPointerDown={onPointerDown}
          title="Glisser pour redimensionner"
          style={{
            position: 'absolute', right: -7, bottom: -7, width: 11, height: 11,
            background: 'var(--mantine-color-indigo-5)', borderRadius: 3,
            cursor: 'ns-resize', border: '2px solid white', boxSizing: 'content-box',
          }} />
      )}
    </span>
  )
}

export default function TemplateEditor() {
  const [tpl, setTpl] = useState<DocTemplates>(TEMPLATE_DEFAULTS)
  const [sel, setSel] = useState<Sel>(null)
  const [tab, setTab] = useState<'header' | 'exercise' | 'lesson'>('exercise')
  const [saving, setSaving] = useState(false)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfBusy, setPdfBusy] = useState(false)

  useEffect(() => {
    api.get<Record<string, any>>('/api/settings/system').then((s) => {
      const saved = s.doc_templates
      if (saved) {
        setTpl({
          header: { ...TEMPLATE_DEFAULTS.header, ...(saved.header || {}) },
          exercise: { ...TEMPLATE_DEFAULTS.exercise, ...(saved.exercise || {}) },
          lesson: { ...TEMPLATE_DEFAULTS.lesson, ...(saved.lesson || {}) },
        })
      }
    })
  }, [])

  const set = useCallback(<P extends keyof DocTemplates>(part: P, patch: Partial<DocTemplates[P]>) => {
    setTpl((t) => ({ ...t, [part]: { ...t[part], ...patch } }))
  }, [])

  async function save() {
    setSaving(true)
    try {
      await api.post('/api/settings/system', { key: 'doc_templates', value: tpl })
      notifications.show({ color: 'green', message: 'Templates enregistrés — appliqués aux prochains sujets générés' })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  async function previewPdf() {
    setPdfBusy(true)
    try {
      const res = await fetch('/api/settings/templates/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ templates: tpl }),
      })
      if (!res.ok) throw new Error('Aperçu indisponible')
      setPdfUrl(URL.createObjectURL(await res.blob()))
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPdfBusy(false)
    }
  }

  const h = tpl.header, ex = tpl.exercise, le = tpl.lesson

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <Text size="sm" c="dimmed" maw={520}>
          Cliquez un élément de l'aperçu pour le sélectionner, tirez la poignée bleue
          pour redimensionner le texte. La géométrie des QR et marqueurs de coin est
          figée (repérage scanner) et n'est pas modifiable.
        </Text>
        <Group gap="xs">
          <Button size="xs" variant="default" leftSection={<RotateCcw size={14} />}
            onClick={() => { setTpl(TEMPLATE_DEFAULTS); setSel(null) }}>
            Réinitialiser
          </Button>
          <Button size="xs" variant="light" leftSection={<Eye size={14} />}
            loading={pdfBusy} onClick={previewPdf}>
            Aperçu PDF réel
          </Button>
          <Button size="xs" leftSection={<Save size={14} />} loading={saving} onClick={save}>
            Enregistrer
          </Button>
        </Group>
      </Group>

      <Group align="flex-start" gap="lg" wrap="nowrap">
        {/* ---------------------------------------------------- aperçu */}
        <Box style={{ flex: 1, minWidth: 0 }} onClick={() => setSel(null)}>
          <Stack gap="md" p="md" style={{
            background: 'white', color: '#111', borderRadius: 8,
            border: '1px solid var(--mantine-color-default-border)', maxWidth: 560,
          }}>
            {/* en-tête */}
            <div style={{ borderBottom: `2px solid ${h.accent}`, paddingBottom: 10 }}>
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <div>
                  <div style={{
                    border: '1.5px dashed #9AA3AC', borderRadius: 6, width: 84, height: 44,
                    fontSize: 9, color: '#9AA3AC', textAlign: 'center', paddingTop: 30,
                    boxSizing: 'border-box',
                  }}>NOTE</div>
                  <div style={{
                    border: '1.5px dashed #9AA3AC', borderRadius: 6, width: 190, height: 30,
                    fontSize: 8, color: '#9AA3AC', paddingLeft: 6, paddingTop: 2, marginTop: 6,
                  }}>APPRÉCIATION</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <Group gap={8} justify="flex-end" wrap="nowrap">
                    <Resizable sel={sel} me={{ part: 'header', field: 'name_size' }}
                      onSelect={setSel} size={h.name_size}
                      onResize={(v) => set('header', { name_size: v })}>
                      <span style={{ fontWeight: 700, fontSize: h.name_size * S }}>
                        Durand Camille  /  5eA
                      </span>
                    </Resizable>
                    <div style={{
                      width: 52, height: 52, background:
                        'repeating-conic-gradient(#111 0% 25%, #fff 0% 50%) 0 0 / 10px 10px',
                      borderRadius: 2, flexShrink: 0,
                    }} title="QR d'identité (figé)" />
                  </Group>
                  <div style={{ marginTop: 8 }}>
                    <Resizable sel={sel} me={{ part: 'header', field: 'title_size' }}
                      onSelect={setSel} size={h.title_size}
                      onResize={(v) => set('header', { title_size: v })}>
                      <span style={{ fontWeight: 700, fontSize: h.title_size * S, color: h.accent }}>
                        Fractions — semaine 12
                      </span>
                    </Resizable>
                    {h.show_date && (
                      <div style={{ fontSize: (h.title_size - 1) * S, color: '#6A737C' }}>
                        Entraînement · 12/07/2026
                      </div>
                    )}
                  </div>
                </div>
              </Group>
            </div>

            {/* carte exercice */}
            <div style={{
              border: `1.5px solid ${ex.border}`, borderRadius: ex.radius * 4,
              boxShadow: ex.shadow ? '2px 3px 6px rgba(90,100,110,0.35)' : 'none',
              padding: 10,
            }}>
              {/* Badge numéroté coloré par la difficulté (1 bleu -> 5 rouge),
                  l'énoncé démarre sur la même ligne — miroir de pdfgen. */}
              <div style={{ marginTop: 2 }}>
                <span style={{
                  display: 'inline-block', verticalAlign: 'middle',
                  background: DIFFICULTY_COLORS[2], color: '#fff', fontWeight: 700,
                  fontSize: ex.font_size * S * 0.95, lineHeight: 1.15,
                  borderRadius: 3, padding: '1px 5px', marginRight: 5,
                }}>2</span>
                <Resizable sel={sel} me={{ part: 'exercise', field: 'font_size' }}
                  onSelect={setSel} size={ex.font_size}
                  onResize={(v) => set('exercise', { font_size: v })}>
                  <span style={{ fontSize: ex.font_size * S }}>Calculer :</span>
                </Resizable>
                <div style={{ textAlign: 'center', margin: '8px 0' }}>
                  <Resizable sel={sel} me={{ part: 'exercise', field: 'math_size' }}
                    onSelect={setSel} size={ex.math_size}
                    onResize={(v) => set('exercise', { math_size: v })}>
                    <span style={{ fontSize: ex.math_size * S, fontWeight: 500 }}>
                      <MathText text={'$\\dfrac{3}{4} + \\dfrac{5}{6}$'} centered />
                    </span>
                  </Resizable>
                </div>
              </div>
              <div style={{
                border: '1.5px solid #F5B7A8', borderRadius: 6, height: 34, marginTop: 4,
              }} title="Zone de réponse élève (rouge saumon, supprimée avant OCR)" />
              <div style={{ height: 10 }}
                title="Bande de correction (hors carte, invisible sur le sujet imprimé)" />
            </div>

            {/* rappel de leçon */}
            <div style={{
              background: le.bg, border: `1.5px solid ${le.border}`,
              borderRadius: 8, padding: 10, color: le.text,
            }}>
              <Group gap={6} wrap="nowrap">
                <BookOpen size={le.font_size * S} color={le.text} />
                <Resizable sel={sel} me={{ part: 'lesson', field: 'font_size' }}
                  onSelect={setSel} size={le.font_size}
                  onResize={(v) => set('lesson', { font_size: v })}>
                  <span style={{ fontWeight: 700, fontSize: le.font_size * S }}>
                    Rappel — Additionner des fractions
                  </span>
                </Resizable>
              </Group>
              <div style={{ fontStyle: 'italic', fontSize: le.font_size * S, marginTop: 4 }}>
                Pour additionner deux fractions, on les met au même dénominateur,
                puis on additionne les numérateurs.
              </div>
            </div>
          </Stack>
        </Box>

        {/* ---------------------------------------------------- réglages */}
        <Card withBorder padding="md" w={300} style={{ flexShrink: 0 }}>
          <SegmentedControl fullWidth size="xs" value={tab}
            onChange={(v) => setTab(v as typeof tab)}
            data={[{ value: 'header', label: 'En-tête' },
                   { value: 'exercise', label: 'Exercice' },
                   { value: 'lesson', label: 'Leçon' }]} />

          {sel && (
            <Stack gap={4} mt="sm">
              <Group justify="space-between">
                <Badge variant="light" size="sm">{FIELD_LABELS[sel.field]}</Badge>
                <Text size="xs" c="dimmed">{(tpl[sel.part] as any)[sel.field]} pt</Text>
              </Group>
              <Slider size="sm" min={LIMITS[sel.field][0]} max={LIMITS[sel.field][1]}
                value={(tpl[sel.part] as any)[sel.field]}
                onChange={(v) => set(sel.part, { [sel.field]: v } as any)} />
            </Stack>
          )}

          {tab === 'header' && (
            <Stack gap="sm" mt="md">
              <ColorInput size="xs" label="Couleur d'accent (titre + filet)"
                value={h.accent} onChange={(v) => set('header', { accent: v })} />
              <Switch size="xs" label="Afficher type et date"
                checked={h.show_date}
                onChange={(e) => set('header', { show_date: e.currentTarget.checked })} />
              <Text size="xs" c="dimmed">
                Tailles : cliquez le nom, la classe ou le titre dans l'aperçu.
              </Text>
            </Stack>
          )}
          {tab === 'exercise' && (
            <Stack gap="sm" mt="md">
              <ColorInput size="xs" label="Couleur du cadre"
                value={ex.border} onChange={(v) => set('exercise', { border: v })} />
              <Text size="xs" c="dimmed">
                Le badge de numéro prend la couleur de la difficulté
                (1 bleu, 2 vert, 3 jaune, 4 orange, 5 rouge) — non réglable.
              </Text>
              <div>
                <Text size="xs" fw={500} mb={4}>Arrondi des coins ({ex.radius.toFixed(1)} mm)</Text>
                <Slider size="sm" min={0} max={5} step={0.2} value={ex.radius}
                  onChange={(v) => set('exercise', { radius: v })} />
              </div>
              <Switch size="xs" label="Ombre portée" checked={ex.shadow}
                onChange={(e) => set('exercise', { shadow: e.currentTarget.checked })} />
            </Stack>
          )}
          {tab === 'lesson' && (
            <Stack gap="sm" mt="md">
              <ColorInput size="xs" label="Fond"
                value={le.bg} onChange={(v) => set('lesson', { bg: v })} />
              <ColorInput size="xs" label="Bordure"
                value={le.border} onChange={(v) => set('lesson', { border: v })} />
              <ColorInput size="xs" label="Texte"
                value={le.text} onChange={(v) => set('lesson', { text: v })} />
            </Stack>
          )}
        </Card>
      </Group>

      <Modal opened={!!pdfUrl} size="90%" title={<Text fw={650}>Aperçu PDF réel</Text>}
        onClose={() => { if (pdfUrl) URL.revokeObjectURL(pdfUrl); setPdfUrl(null) }}>
        {pdfUrl && (
          <iframe src={pdfUrl} title="Aperçu des templates"
            style={{ width: '100%', height: '75vh', border: 'none' }} />
        )}
      </Modal>
    </Stack>
  )
}
