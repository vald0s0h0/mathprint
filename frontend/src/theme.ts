import { createTheme, rem, type CSSVariablesResolver } from '@mantine/core'

// Thème MathPrint : indigo sobre, coins doux, typographie système propre.
export const theme = createTheme({
  primaryColor: 'indigo',
  defaultRadius: 'md',
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  headings: {
    fontWeight: '650',
    sizes: {
      h2: { fontSize: rem(24) },
      h3: { fontSize: rem(19) },
      h4: { fontSize: rem(16) },
    },
  },
  components: {
    Card: { defaultProps: { radius: 'md' } },
    Button: { defaultProps: { radius: 'md' } },
    Badge: { defaultProps: { radius: 'sm' } },
  },
})

// Couleurs adaptées au mode sombre. Les nuances fixes de Mantine (gray.8,
// gray-1, blue.7…) ne s'inversent pas : un texte foncé ou un fond clair
// devient illisible sur le fond sombre. Ces variables gardent EXACTEMENT les
// valeurs du mode clair et prennent une nuance lisible en mode sombre.
const ACCENTS = ['blue', 'green', 'red', 'orange'] as const

export const cssVariablesResolver: CSSVariablesResolver = () => {
  const light: Record<string, string> = {
    '--mp-text-strong': 'var(--mantine-color-gray-8)',
    '--mp-surface-sunken': 'var(--mantine-color-gray-0)',
    '--mp-surface-muted': 'var(--mantine-color-gray-1)',
    '--mp-surface-strong': 'var(--mantine-color-gray-2)',
    '--mp-border-subtle': 'var(--mantine-color-gray-3)',
    '--mp-guide-bg': '#FFF4C2',
    '--mp-guide-border': '#E4B42D',
    '--mp-guide-text': '#4D3B05',
    '--mp-guide-icon': '#C99A12',
  }
  const dark: Record<string, string> = {
    '--mp-text-strong': 'var(--mantine-color-dark-0)',
    '--mp-surface-sunken': 'var(--mantine-color-dark-8)',
    '--mp-surface-muted': 'var(--mantine-color-dark-5)',
    '--mp-surface-strong': 'var(--mantine-color-dark-4)',
    '--mp-border-subtle': 'var(--mantine-color-dark-4)',
    '--mp-guide-bg': 'rgba(250, 176, 5, 0.12)',
    '--mp-guide-border': '#8A6A12',
    '--mp-guide-text': '#FFE8A3',
    '--mp-guide-icon': '#F0C239',
    // Badges color="dark" (problème « Difficile ») : en sombre Mantine écrit
    // dark-3 sur un fond quasi transparent, illisible.
    '--mantine-color-dark-light': 'rgba(201, 201, 201, 0.12)',
    '--mantine-color-dark-light-hover': 'rgba(201, 201, 201, 0.18)',
    '--mantine-color-dark-light-color': 'var(--mantine-color-dark-0)',
    '--mantine-color-dark-text': 'var(--mantine-color-dark-1)',
    '--mantine-color-dark-outline': 'var(--mantine-color-dark-2)',
  }
  for (const c of ACCENTS) {
    light[`--mp-text-${c}`] = `var(--mantine-color-${c}-7)`
    light[`--mp-text-${c}-strong`] = `var(--mantine-color-${c}-8)`
    light[`--mp-border-${c}`] = `var(--mantine-color-${c}-3)`
    dark[`--mp-text-${c}`] = `var(--mantine-color-${c}-3)`
    dark[`--mp-text-${c}-strong`] = `var(--mantine-color-${c}-2)`
    dark[`--mp-border-${c}`] = `var(--mantine-color-${c}-8)`
  }
  return { variables: {}, light, dark }
}
