import { createTheme, rem } from '@mantine/core'

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
