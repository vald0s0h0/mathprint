// Imprimantes du centre (Atelier) : imprimantes locales, réseau et connecteurs
// partagés entre l'admin et les comptes Atelier.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import PrinterSettings, { type PrintersInfo } from '../components/PrinterSettings'

export default function AtelierPrinters() {
  const [printers, setPrinters] = useState<PrintersInfo | null>(null)
  const refresh = useCallback(() => {
    api.get<PrintersInfo>('/api/printers').then(setPrinters).catch(() => {})
  }, [])
  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 10_000)
    return () => window.clearInterval(timer)
  }, [refresh])
  return <PrinterSettings printers={printers} refresh={refresh} />
}
