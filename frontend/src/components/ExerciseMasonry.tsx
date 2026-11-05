import { Box, Stack } from '@mantine/core'
import { Children, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

/** Chaque carte garde sa colonne lorsque sa hauteur change (image chargée ou
 * micro-corrections ouvertes). Seule la largeur disponible change le nombre
 * de colonnes ; aucun rééquilibrage n'est fait à partir des hauteurs.
 *
 * Changer le nombre de colonnes recrée toutes les cartes (clé des colonnes). Sinon
 * celles de la première colonne survivent, déplacées sans défilement : leur
 * IntersectionObserver, déjà notifié « hors écran », ne se réveille pas toujours, et
 * la carte attend son aperçu sans fin, sous les yeux de l'utilisateur. */
export default function ExerciseMasonry({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [count, setCount] = useState(1)
  // avant le premier affichage : la mise en page transitoire à 1 colonne n'est jamais peinte
  useLayoutEffect(() => {
    const container = ref.current
    if (!container) return
    const resize = (width: number) => setCount(Math.max(1, Math.floor((width + 20) / 360)))
    resize(container.getBoundingClientRect().width)
    const observer = new ResizeObserver(([entry]) => resize(entry.contentRect.width))
    observer.observe(container)
    return () => observer.disconnect()
  }, [])
  const columns = Array.from({ length: count }, () => [] as ReactNode[])
  Children.toArray(children).forEach((child, index) => columns[index % count].push(child))
  return <Box ref={ref} style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
    {columns.map((column, index) => <Stack key={`${count}-${index}`} gap={20} style={{ flex: 1, minWidth: 0 }}>
      {column}
    </Stack>)}
  </Box>
}
