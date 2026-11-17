import { Button, MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { renderToStaticMarkup } from 'react-dom/server'
import MathText from './MathText'

type PrintDomain = { code: string; name: string; chapters: { code: string; name: string; rows: { code: string; label: string }[] }[] }

export default function CompetencyPrintButton({ domains, context }: { domains: PrintDomain[]; context: string }) {
  const print = () => {
    const popup = window.open('', '_blank')
    if (!popup) {
      notifications.show({ color: 'red', message: 'Autorisez les fenêtres surgissantes pour imprimer la liste.' })
      return
    }
    const heading = (code: string, name: string) => [code, name].filter(Boolean).join(' — ')
    const content = renderToStaticMarkup(<MantineProvider>
      <h1>Liste des compétences</h1><p className="context">{context}</p>
      <main>{domains.map((domain, di) => <section key={di}>
        <h2>{heading(domain.code, domain.name)}</h2>
        {domain.chapters.map((chapter, ci) => <div key={ci}>
          <h3>{heading(chapter.code, chapter.name)}</h3>
          {chapter.rows.map((row, ri) => <div className="row" key={ri}>
            <strong>{row.code}</strong><span><MathText text={row.label.replace(/\s*◾\s*/g, '\n• ')} /></span>
          </div>)}
        </div>)}
      </section>)}</main>
    </MantineProvider>)
    const styles = Array.from(document.querySelectorAll('link[rel="stylesheet"], style')).map(node => node.outerHTML).join('')
    popup.document.open()
    popup.document.write(`<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Liste des compétences</title>${styles}<style>
      @page { size: A4 landscape; margin: 10mm; }
      body { color: #111; font: 9pt Arial, sans-serif; margin: 0; }
      h1 { font-size: 15pt; margin: 0 0 2mm; } .context { margin: 0 0 5mm; font-size: 9pt; }
      main { columns: 2; column-gap: 10mm; column-rule: 0.2mm solid #ddd; }
      h2 { font-size: 11pt; font-weight: 800; margin: 4mm 0 2mm; padding: 1.5mm 2mm; border-left: 1.5mm solid #222; background: #e5e5e5; color: #111; break-after: avoid; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
      h3 { font-size: 9.5pt; font-weight: 700; margin: 2.5mm 0 1mm; padding-bottom: 0.7mm; border-bottom: 0.2mm solid #999; break-after: avoid; }
      .row { display: flex; gap: 2mm; margin: 0 0 1.3mm; break-inside: avoid; }
      .row strong { flex: none; } .row span { min-width: 0; }
    </style></head><body>${content}</body></html>`)
    popup.document.close()
    const launch = () => { popup.focus(); popup.print() }
    if (popup.document.readyState === 'complete') void popup.document.fonts.ready.then(launch)
    else popup.addEventListener('load', () => { void popup.document.fonts.ready.then(launch) }, { once: true })
  }
  return <Button size="xs" variant="light" leftSection={<Printer size={14} />} disabled={!domains.length} onClick={print}>
    Imprimer / enregistrer en PDF
  </Button>
}
