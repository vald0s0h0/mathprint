from pathlib import Path
import json,re,sys
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,KeepTogether,Table,TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
ROOT=Path(__file__).resolve().parents[1]
for n,f in [('Guide','Arial.ttf'),('Guide-Bold','Arial Bold.ttf')]:pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
pdfmetrics.registerFontFamily('Guide',normal='Guide',bold='Guide-Bold',italic='Guide',boldItalic='Guide-Bold')
S={
 'title':ParagraphStyle('title',fontName='Guide-Bold',fontSize=21,leading=26,textColor=HexColor('#173F53'),spaceAfter=16),
 'h':ParagraphStyle('h',fontName='Guide-Bold',fontSize=12,leading=16,textColor=HexColor('#087F83'),spaceBefore=10,spaceAfter=7),
 'p':ParagraphStyle('p',fontName='Guide',fontSize=9.5,leading=13,spaceAfter=6,textColor=HexColor('#203849')),
 'small':ParagraphStyle('s',fontName='Guide',fontSize=8.5,leading=11,spaceAfter=5,textColor=HexColor('#506774'))}
def P(t,style='p'):return Paragraph(t,S[style])
def fmt(v):return f'{v:g}'.replace('.',',')
def clean(t):
 t=escape(t).replace('\n','<br/>')
 return re.sub(r'\*\*(.*?)\*\*',r'<b>\1</b>',t)
recs=json.loads((ROOT/'sources/scenarios_natifs.json').read_text())
story=[P('Préparer les trois contrôles','title'),P('Guide privé de démonstration • Version avec le moteur natif','h'),
 P('Chaque contrôle existe en deux versions : <b>ELEVE</b>, le sujet distribué sans correction, avec note et appréciation vierges ; <b>CORRIGE</b>, le même sujet avec les corrections rouges. Imprime en A4 portrait à 100 %. Pour la démonstration corrigée, une seule impression suffit : ajoute ensuite les réponses au stylo bleu. Les cases remplies en rouge montrent déjà les réponses attendues.'),
 P('Les noms, notes et commentaires sont prescrits pour cette démonstration. Aucun scan ni appel à un modèle de reconnaissance n’a été exécuté. Les QR sont générés par le moteur natif et signés ; leurs identifiants ne sont pas enregistrés comme contrôles dans la base de production.'),
 P('Notation et lecture des marques','h'),
 P('Le score obtenu et le barème sont affichés sous chaque carte et la note sur 20 dans l’en-tête. La note suit la règle actuelle de MathPrint : <b>conversion sur 20, puis arrondi au demi-point supérieur</b>. Une réponse partiellement créditée porte une croix, mais conserve ses points de méthode.'),
 P('Camille : <b>19/28 points → 13,57… → 14/20</b>.<br/>Ibrahim : <b>6/18 points → 6,66… → 7/20</b>.<br/>Léa : <b>13,5/20 points → 13,5/20</b>.'),
 P('Écriture et tracés','h'),
 P('Écris dans les champs saumon, et réserve les lignes aux raisonnements. Tu peux répartir une phrase sur deux lignes. En géométrie, utilise les coordonnées ci-après : <b>origine au coin inférieur gauche du quadrillage ; x vers la droite, y vers le haut ; une unité = un carreau</b>. Les coordonnées servent à préparer la démonstration, elles ne sont pas à écrire sur les copies.'),
 P('Nomme tous les points images avec une apostrophe. Les traits faux restent bleus et visibles. Le rouge remplace uniquement le segment visé ; ne repasse pas au bleu sur les segments rouges. La translation entièrement juste ne reçoit aucun trait correctif.'),
 P('Documents à imprimer','h'),
 P('<b>01_controle_raisonnement_ELEVE.pdf / _CORRIGE.pdf</b> : 2 pages chacun.<br/><b>02_controle_accompagne_ELEVE.pdf / _CORRIGE.pdf</b> : 1 page chacun.<br/><b>03_controle_geometrie_ELEVE.pdf / _CORRIGE.pdf</b> : 2 pages chacun.<br/>Le suffixe _CORRIGE remplace _ELEVE dans le nom complet. Ce guide reste séparé des copies élèves.')]
# Détails des points partiels, définis sur les réponses effectivement prescrites.
rubrics={
 ('01_controle_raisonnement',1,'b'):'Sur 3 pt : trouver 5 (1), vérifier 5 (0,5), conserver 0 (1), justifier la transformation sans division interdite (0,5). Ici : 1,5/3.',
 ('01_controle_raisonnement',4,'b'):'Sur 3 pt : montant de la hausse (1), baisse calculée sur 24 000 F (1), prix final et conclusion (1). Ici : seul le montant de la hausse est juste, soit 1/3.',
 ('01_controle_raisonnement',7,'b'):'Sur 3 pt : 12 cas favorables (1), fraction comme rapport de deux effectifs (0,5), univers limité aux 15 basketteurs (1), résultat final 4/5 (0,5). Ici : 1,5/3.',
 ('01_controle_raisonnement',10,'b'):'Sur 3 pt : vérification d’un cas particulier (1), paramétrage de deux impairs consécutifs (1), factorisation et conclusion générale (1). Ici : 1/3.',
 ('03_controle_geometrie',1,'a'):'Sur 4 pt : quatre images bien placées (2), trois premiers côtés corrects (1), fermeture D′A′ (1). Ici : 3/4.',
 ('03_controle_geometrie',2,''):'Sur 1 pt : distances égales de part et d’autre (0,5), perpendicularité à l’axe (0,5). Ici : 0,5/1.',
 ('03_controle_geometrie',3,'a'):'Sur 3 pt : points images (1), deux premiers côtés (1), fermeture C′A′ (1). Ici : 2/3.',
 ('03_controle_geometrie',5,'a'):'Sur 2 pt : position de N (1), segment MN parallèle à BC (1). Ici : 0/2 ; M est un point déjà donné.',
 ('03_controle_geometrie',7,'a'):'Sur 2 pt : quatre points translatés (1), quatre côtés correctement reliés (1). Ici : 2/2.',
 ('02_controle_accompagne',5,''):'Trois liens à 1 point chacun. Seul 2 m → 200 cm est juste : 1/3.'}
for rec in recs:
 lastpage=None
 for seq,(it,slot) in enumerate(zip(rec['items'],rec['slots']),1):
  if slot[0]!=lastpage:
   story.append(PageBreak()); lastpage=slot[0]
   story.append(P(rec['name']+' • page '+str(lastpage+1),'title'))
   story.append(P(f"Note : <b>{fmt(rec['note'])}/20</b> • Réponses à recopier dans l’ordre des cartes.",'small'))
  parts=it.get('parts') or [it]
  earned=sum(p['score'] for p in parts); total=sum(p['grading']['bareme_points'] for p in parts)
  block=[P(f'Exercice {seq} • {fmt(earned)}/{fmt(total)} points','h')]
  for k,p in enumerate(parts):
   letter=chr(97+k) if 'parts' in it else ''
   label=(letter+'. ' if letter else '')
   block.append(P('<b>'+label+'</b>'+clean(p['answer'])))
   if len(parts)>1:block.append(P(f"Sous-question : {fmt(p['score'])}/{fmt(p['grading']['bareme_points'])} pt.",'small'))
   rule=rubrics.get((rec['key'],seq,letter))
   if rule:block.append(P(clean(rule),'small'))
  story.append(KeepTogether(block))

def footer(c,doc):
 c.setStrokeColor(HexColor('#087F83')); c.setLineWidth(3); c.line(36,812,559,812)
 c.setFont('Guide',8); c.setFillColor(HexColor('#506774')); c.drawString(36,25,'MATHPRINT • Notice privée de préparation'); c.drawRightString(559,25,str(doc.page))
SimpleDocTemplate(str(ROOT/'00_notice_reponses_et_bareme.pdf'),pagesize=(595.2756,841.8898),leftMargin=36,rightMargin=36,topMargin=47,bottomMargin=42,title='MathPrint - Réponses manuscrites et barème',author='MathPrint').build(story,onFirstPage=footer,onLaterPages=footer)
