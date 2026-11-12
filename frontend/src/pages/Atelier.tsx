// Atelier : logistique du centre (suivi des sujets, corrections des lots,
// sujets, impression, calibration). Réservé à l'admin et au rôle Atelier,
// édition centre seulement.
//
// La page ne dépend pas de la barre latérale : la future interface « employé »
// pourra la servir seule, sans les onglets du prof ni ceux de l'admin.
import { Group, Stack, Tabs, Text, Title } from '@mantine/core'
import { Factory } from 'lucide-react'
import type { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export const ATELIER_SECTIONS = [
  { to: '/atelier', label: 'Suivi des sujets' },
  { to: '/atelier/corrections', label: 'Corrections' },
  { to: '/atelier/sujets', label: 'Sujets' },
  { to: '/atelier/imprimantes', label: 'Imprimantes' },
  { to: '/atelier/calibration', label: 'Calibration' },
]

export default function Atelier({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const current = ATELIER_SECTIONS.find((s) => s.to === pathname)?.to ?? '/atelier'
  return (
    <Stack gap="md">
      <div>
        <Group gap="sm"><Factory size={26} /><Title order={2}>Atelier</Title></Group>
        <Text c="dimmed" size="sm" mt={4}>
          Préparer, imprimer, livrer, scanner, corriger et restituer les copies des établissements.
        </Text>
      </div>
      <Tabs value={current} onChange={(v) => v && navigate(v)}>
        <Tabs.List>
          {ATELIER_SECTIONS.map((s) => <Tabs.Tab key={s.to} value={s.to}>{s.label}</Tabs.Tab>)}
        </Tabs.List>
      </Tabs>
      {children}
    </Stack>
  )
}
