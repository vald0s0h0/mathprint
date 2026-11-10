// Aperçu du SUJET avant impression : même visionneuse que « Affiner » (page entière,
// recto-verso côte à côte, navigation et recherche d'élève), sans lecteur PDF à charger.
import RefineCorrectionModal from './RefineCorrectionModal'

export default function PdfPreviewModal({ assessmentId, opened, onClose }: {
  assessmentId: string | null; opened: boolean; onClose: () => void
}) {
  return (
    <RefineCorrectionModal mode="subject" assessmentId={assessmentId} opened={opened}
      onClose={onClose} />
  )
}
