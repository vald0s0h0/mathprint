"""Démonstrations imprimées via les moteurs natifs, sans base de production."""
import sys, io, json, copy, uuid, os
os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/mathprint-mpl')
os.environ.setdefault('XDG_CACHE_HOME', '/private/tmp/mathprint-cache')
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
REPO=ROOT.parents[1]
sys.path.insert(0,str(REPO/'backend'))
from app.services import pdfgen as pg, pipeline, scoring
from app.services.security import sign_page
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from pypdf import PdfReader,PdfWriter
import fitz
TMP=REPO/'tmp/pdfs/native'; TMP.mkdir(parents=True,exist_ok=True)
RED='#BD2438'; TEAL='#087F83'
TPL=copy.deepcopy(pg.DEFAULT_TEMPLATES)
TPL['header'].update(accent=TEAL,show_date=False)
TPL['exercise'].update(font_size=9,math_size=9,border='#BDCDD4',radius=2.2)
serial=0

def part(stmt,typ,points,score,answer,feedback='',**kw):
 global serial
 serial+=1
 g={'max_score':points,'bareme_points':points,**kw.pop('grading',{})}
 if typ=='multiline_text': g.setdefault('lines',3)
 return {'item_id':f'demo-{serial}','statement':stmt,
  'response_type':typ,'grading':g,'expected':kw.pop('expected',{}),
  'score':score,'answer':answer,'feedback':feedback,**kw}

def multi(stmt,points,score,answer,feedback,lines=3):
 return part(stmt,'multiline_text',points,score,answer,feedback,grading={'lines':lines})
def short(stmt,points,score,answer,feedback):
 return part(stmt,'short_text',points,score,answer,feedback)
def blank(stmt,points,score,answer,feedback):
 return part(stmt+'\n{{blank_right}}','short_text',points,score,answer,feedback,inline=True)
def qcm(stmt,choices,right,selected,points,feedback):
 return part(stmt,'qcm_single',points,points if right==selected else 0,
  'Cocher « '+__import__('re').sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}',r'\1/\2',choices[selected].replace('$',''))+' ».',feedback,grading={'choices':choices,'exclusive':True},expected={'correct':[right]},selected=[selected])
def card(title,parts,feedback=None):
 global serial
 serial+=1
 return {'item_id':f'card-{serial}','statement':title,'response_type':'composite',
  'grading':{'parts':parts},'parts':parts,'part_item_ids':[p['item_id'] for p in parts],
  'feedback':feedback or '\n'.join(f"**{chr(97+i)}.** {p['feedback']}" for i,p in enumerate(parts) if p['feedback']),
  'level3':3 if 'Problème' in title else 2,'is_probleme':'Problème' in title,'guides':'include','guides_position':'before_answer'}
def single(p):
 return {**p,'choices':p['grading'].get('choices',[]),'level3':2,'guides':'include','guides_position':'before_answer'}

# Une grande carte de raisonnement + une carte complémentaire par colonne.
strong=[
 [card('**Problème - Une division qui change tout**\nOn cherche toutes les solutions réelles de $x(x-3)=2x$.',[
  short('Vérifie x = 5 : quelle valeur commune donnent les deux membres ?',1,1,'10',''),
  multi('Résous l’équation. Justifie chaque transformation et conclus sur toutes les solutions.',3,1.5,'Je divise par x : x - 3 = 2.\nDonc x = 5.\n5(5 - 3) = 10 et 2 × 5 = 10.\nL’unique solution est 5.','**Diviser par x suppose x non nul.** Tu trouves 5, mais tu perds 0, qui vérifie aussi l’équation. Écris x(x - 5) = 0 : **x = 0 ou x = 5**. Vérifier une solution ne prouve pas qu’elle est unique.',5)]),
  single(blank('Développe puis réduis : $(x-3)^2$ ',1,0,'x² - 9','**Le double produit manque.** (x - 3)² = x² - 6x + 9 ; x² - 9 correspond à (x - 3)(x + 3).'))],
 [card('**Problème - Deux évolutions successives**\nUn abonnement à 20 000 F CFA augmente de 20 %, puis baisse de 20 %.',[
  short('Quel est son prix après la hausse, en F CFA ?',1,1,'24 000',''),
  multi('Retrouve-t-il son prix initial après la baisse ? Calcule le prix final et justifie.',3,1,'La hausse vaut 4 000 F.\n+20 % - 20 % = 0 %.\nLes variations se compensent.\nLe prix final est 20 000 F CFA.\nIl revient à son prix initial.','**La baisse porte sur 24 000 F**, pas sur 20 000 F. Elle vaut 4 800 F, contre 4 000 F pour la hausse. 1,20 × 0,80 = 0,96 : **19 200 F**, soit une baisse totale de 4 %. Tu as bien calculé la première étape.',5)]),
  single(blank('Calcule : $3^{-2}$ ',1,0,'-9','**Le signe moins est dans l’exposant.** Il indique un inverse : 3 puissance -2 vaut **1/9**, et non l’opposé de 9.'))],
 [card('**Problème - Choisir la bonne population**\nDans un club de 30 élèves, 18 sont des filles. Parmi elles, 12 font du basket. Parmi les 12 garçons, 3 font du basket.',[
  short('Combien d’élèves pratiquent le basket ?',1,1,'15',''),
  multi('On choisit au hasard un élève parmi les basketteurs. Quelle est la probabilité que ce soit une fille ? Justifie les deux termes de ta fraction.',3,1.5,'Il y a 12 filles basketteuses.\nIl y a 30 élèves dans le club.\nLa probabilité vaut 12/30 = 2/5.\n12 est le nombre de cas favorables\net 30 celui des cas possibles.','**Ton 12 est juste ; ton 30 change le tirage.** Il faut se limiter aux 15 basketteurs : **12/15 = 4/5**. Ton 12/30 serait la probabilité de choisir une fille basketteuse parmi tous les élèves du club.',5)]),
  single(blank(r'Réduis : $\sqrt{49}+\sqrt{16}$ ',1,1,'11','**Juste :** 7 + 4 = 11. Tu calcules chaque racine avant d’additionner.'))],
 [card('**Problème - Un exemple suffit-il ?**\nLa somme de deux entiers impairs consécutifs est-elle toujours un multiple de 4 ?',[
  short('Calcule la somme des deux impairs 7 et 9.',1,1,'16',''),
  multi('Démontre l’affirmation pour tous les entiers impairs consécutifs. Une liste d’exemples ne suffit pas.',3,1,'3 + 5 = 8 et 7 + 9 = 16.\n8 et 16 sont divisibles par 4.\nDonc la somme de deux impairs\nconsécutifs est toujours\nun multiple de 4.','**Tes exemples sont exacts**, mais ils ne prouvent pas « toujours ». Deux impairs consécutifs s’écrivent 2n + 1 et 2n + 3. Leur somme vaut **4(n + 1)**. Comme n + 1 est entier, c’est un multiple de 4.',5)]),
  single(blank('Factorise : $9x^2-25$ ',1,1,'(3x - 5)(3x + 5)','**Juste.** Tu reconnais une différence de deux carrés. Pour la preuve précédente, cherche aussi une écriture valable pour tout n.'))]
]

weak=[
 [single(qcm('La tablette dessinée contient 8 parts égales, dont 3 colorées. Quelle fraction est colorée ?\n{{aide}} En bas : toutes les parts. En haut : les parts colorées.', [r'$\frac{3}{8}$',r'$\frac{8}{3}$',r'$\frac{3}{5}$'],0,2,2,'**Tu as compté les 5 parts blanches.** Le dénominateur compte toute la tablette : 8 parts. La fraction est **3/8**.')),
  single(qcm('Quel est le plus grand nombre ?\n{{aide}} Ajoute un zéro : 4,7 = 4,70. Compare les centièmes.', ['4,35','4,7','Ils sont égaux'],1,0,2,'**35 et 7 ne sont pas de même rang.** Compare 35 centièmes à 70 centièmes : **4,7 est plus grand**.')),
  single(part('Relie les longueurs égales.\n{{aide}} 1 m = 100 cm. De m à cm, multiplie par 100.','matching',3,1,'Relier 1,2 m à 50 cm ; 0,5 m à 120 cm ; 2 m à 200 cm.','**2 m = 200 cm est juste.** Les deux autres liens : **1,2 m = 120 cm** et **0,5 m = 50 cm**. Un demi-mètre, c’est la moitié de 100 cm.',grading={'left':['1,2 m','0,5 m','2 m'],'right':['50 cm','200 cm','120 cm']},expected={'pairs':[[0,2],[1,0],[2,1]]},pairs=[[0,0],[1,2],[2,1]])),
  single(blank('Complète en centimètres : 3 m =\n{{aide}} Un mètre contient 100 centimètres.\n',1,1,'300','**Juste :** 3 × 100 = 300 cm. Garde ce repère pour les mètres décimaux.'))],
 [card('**Le contour d’un rectangle**\nUn rectangle mesure 7 cm de long et 3 cm de large.\n{{aide}} Le périmètre est la longueur du tour : il y a quatre côtés. Imagine un fil posé tout autour du rectangle, puis déplié.',[
  qcm('Quelle est la longueur du contour ?',['21 cm','10 cm','20 cm'],2,0,2,'**7 × 3 donne l’aire.** Pour le tour : 7 + 3 + 7 + 3 = **20 cm**.'),
  short('Combien de côtés mesurent 7 cm ?',1,1,'2','Deux côtés opposés ont la même longueur : **2 est juste**.')]),
  card('**Calculer dans le bon ordre**\n{{aide}} Effectue la multiplication avant l’addition.',[
   short('Calcule 3 + 4 × 2.',2,0,'14','Tu as fait (3 + 4) × 2. Sans parenthèses, 4 × 2 = 8, puis 3 + 8 = **11**.'),
   short('Calcule (3 + 4) × 2.',1,1,'14','**Juste :** les parenthèses changent l’ordre.')]),
  single(qcm('Quelle fraction est égale à 1/2 ?\n{{aide}} Multiplie le haut et le bas par le même nombre. Ici, essaie de multiplier par 2.', [r'$\frac{2}{3}$',r'$\frac{2}{4}$',r'$\frac{1}{4}$'],1,0,2,'Tu as ajouté 1 en haut et en bas. Cela change la fraction. Multiplie les deux par 2 : **1/2 = 2/4**.')),
  card('**Lire des durées**\nUn atelier commence à 9 h 40 et finit à 10 h 10.\n{{aide}} De 9 h 40 à 10 h, compte 20 min ; continue ensuite.',[
   short('Durée totale, en minutes ?',2,2,'30','**30 min est juste :** 20 min + 10 min.'),
   qcm('Une demi-heure vaut…',['50 min','30 min','15 min'],1,1,1,'Tu sais utiliser le repère d’une demi-heure.')])]
]
# Figures ajoutées DANS les zones manual_drawing natives à partir de leurs coordonnées.
# Toutes les coordonnées sont en carreaux, origine en bas à gauche.
FIGS={
 'axial':{'nx':20,'ny':12,'points':{'A':(2,9),'B':(7,9),'C':(7,4),'D':(2,4)},'edges':['AB','BC','CD','DA'],'axis':10,
 'fix':[((18,4),(18,9))], 'note':"Placer A' (18 ; 9), B' (13 ; 9), C' (13 ; 4), D' (18 ; 4). Tracer A'B', B'C', C'D' correctement, puis D'B' à tort. Ne pas tracer D'A'."},
 'central':{'nx':20,'ny':12,'points':{'A':(2,9),'B':(7,9),'C':(4,6),'O':(10,6)},'edges':['AB','BC','CA'],
 'fix':[((16,6),(18,3))], 'note':"Placer A' (18 ; 3), B' (13 ; 3), C' (16 ; 6). Tracer A'B' et B'C' correctement, puis C'A (vers A initial) à tort. Ne pas tracer C'A'."},
 'thales':{'nx':20,'ny':12,'points':{'A':(3,2),'B':(15,2),'C':(3,11),'M':(9,2)},'edges':['AB','BC','CA'],
 'fix':[((9,2),(3,6.5))], 'label':((1.8,6.6),'N'), 'note':"Ici 3 carreaux représentent 2 cm. Placer N incorrectement en (3 ; 5), donc 3 carreaux au-dessus de A (AN = 2 cm), puis tracer MN. Le bon N rouge est en (3 ; 6,5), soit AN = 3 cm."},
 'translation':{'nx':20,'ny':12,'points':{'A':(2,7),'B':(6,7),'C':(6,10),'D':(2,10)},'edges':['AB','BC','CD','DA'],
 'fix':[], 'note':"Placer A' (10 ; 3), B' (14 ; 3), C' (14 ; 6), D' (10 ; 6). Tracer les quatre côtés correctement. Aucune correction de figure ne sera ajoutée."}}

def drawing(stmt,points,score,kind,feedback):
 return part(stmt,'manual_drawing',points,score,FIGS[kind]['note'],feedback,drawing=kind)
geo=[
 [card('**Construire un reflet**\n1 carreau = 1 unité. Utilise la règle et l’équerre.',[
   drawing("Construis A'B'C'D', image de ABCD par la symétrie d’axe (d).",4,3,'axial',"**Les quatre points images sont justes.** Mais D'B' est une diagonale. Le côté [DA] devient **[D'A']**, seul trait rouge à substituer. Conserve tes trois autres côtés."),
   short("À quelle distance de (d), en unités, se trouve A' ?",1,1,'8','La distance à l’axe est conservée : **8 unités**, c’est juste.')]),
  single(multi('Explique comment vérifier que deux points sont symétriques par rapport à une droite.',1,0.5,"Ils sont à la même distance\nde la droite, de chaque côté.",'**La distance seule ne suffit pas.** Le segment qui joint les deux points doit aussi être perpendiculaire à l’axe. L’axe est sa médiatrice.',3))],
 [card('**Construire un demi-tour**\n1 carreau = 1 unité. Le centre de symétrie est O.',[
   drawing("Construis A'B'C', image du triangle ABC par la symétrie de centre O.",3,2,'central',"**Tes points images et deux côtés sont justes.** Tu as relié C' à A, le sommet initial. Pour fermer l’image, relie **C' à A'**. Seul ce côté est corrigé."),
   qcm("O est le milieu…",["de [AA']","de [AB]","de [A'B']"],0,0,1,"**Juste.** O est le milieu de chaque segment joignant un point à son image.")]),
  single(multi('Une symétrie centrale conserve-t-elle les longueurs et les angles ? Justifie à partir du demi-tour.',1,1,'Oui. Le demi-tour déplace la figure\nsans la déformer. Les longueurs\net les angles sont conservés.','**Juste :** le demi-tour est un déplacement sans déformation. Tu distingues bien la position de la forme.',3))],
 [card('**Problème - Construire puis contrôler**\nABC est rectangle en A ; AB = 8 cm, AC = 6 cm. M est le milieu de [AB]. Le dessin est réduit : **3 carreaux représentent 2 cm**.',[
   drawing('Place N sur [AC] tel que (MN) soit parallèle à (BC). Trace [MN].',2,0,'thales','**Ton N est à 2 cm de A.** Or AM/AB = 1/2 : AN doit valoir la moitié de 6 cm, donc **3 cm**. Le rouge remplace seulement [MN].'),
   short('Calcule BC, en cm.',1,1,'10','**BC = 10 cm est juste :** 8² + 6² = 100.'),
   short('Pour le tracé demandé, calcule MN, en cm.',1,0,'10/3','Le rapport attendu est 1/2, pas 2/6 : **MN = 5 cm**.')]),
  single(multi('Justifie le calcul de BC avec le théorème de Pythagore.',1,1,'ABC est rectangle en A.\nBC² = AB² + AC² = 64 + 36 = 100.\nDonc BC = 10 cm.','**Juste :** tu identifies l’hypoténuse avant d’appliquer Pythagore. C’est la construction du parallèle qui reste à contrôler.',3))],
 [card('**Déplacer sans déformer**\n1 carreau = 1 unité. Utilise la règle.',[
   drawing("Construis A'B'C'D', image du rectangle par la translation de 8 carreaux vers la droite et de 4 carreaux vers le bas.",2,2,'translation',"**Construction juste.** Chaque sommet a subi le même déplacement : 8 carreaux à droite, 4 vers le bas. Aucun segment n’est à corriger."),
   short("Quelle est la longueur de A'B', en unités ?",1,1,'4','**4 unités : juste.** Une translation conserve les longueurs.')]),
  single(multi('Un élève dit : « Une symétrie axiale est toujours une translation. » Réfute cette affirmation.',1,0,'Oui, car les deux conservent\nles longueurs et les angles.','**Conserver les longueurs ne suffit pas à identifier une transformation.** Un reflet inverse l’orientation ; une translation déplace tous les points du même vecteur. Ces transformations sont différentes.',3))]
]


# Compléments courts : densifier le contrôle sans gonfler les zones de rédaction.
extras=[
 [('Calcule 2 puissance 5.', '32'), ('Résous 3x + 7 = 22.', '5')],
 [('Calcule 15 % de 240.', '36'), ('Un prix de 800 F baisse de 25 %. Prix final en F ?', '600')],
 [('Une urne contient 4 boules rouges et 6 bleues. P(rouge) ?', '2/5'), ('Donne une fraction simplifiée égale à 18/24.', '3/4')],
 [('Développe 4(n + 1).', '4n + 4'), ('Écris l’impair qui suit 2n + 1.', '2n + 3')]
]
for col, questions in zip(strong,extras):
 col.append(card('**Applications directes**', [short(q,1,1,a,'') for q,a in questions],
                  '**Deux réponses justes.** Ces automatismes sont acquis ; mobilise-les pour contrôler les étapes de tes démonstrations.'))
# Une page accompagnée : sept cartes, les deux colonnes entièrement exploitées.
wleft,wright=weak
weak=[[wleft[0],wright[0],wright[1]],[wleft[1],wleft[2],wright[2],wright[3]]]
# Illustration de fraction, insérée par le moteur de figures natif.
from PIL import Image, ImageDraw
im=Image.new('RGB',(1000,125),'white'); dr=ImageDraw.Draw(im)
for i in range(8):
 dr.rectangle((i*124+4,15,(i+1)*124,110),fill=TEAL if i<3 else 'white',outline=TEAL,width=3)
 if i<3: dr.line((i*124+4,15,i*124+4,110),fill='white',width=3)
figpath=TMP/'fraction.png'; im.save(figpath,dpi=(300,300))
weak[0][0]['figure']={'type':'image','params':{'path':str(figpath)}}
weak[0][0]['statement']=weak[0][0]['statement'].replace('Quelle fraction est colorée ?', 'Quelle fraction est colorée ?\n{{figure}}')

def leaves(it): return it.get('parts') or [it]
def points(it): return sum(p['grading']['bareme_points'] for p in leaves(it))
def earned(it): return sum(p['score'] for p in leaves(it))
def height(it): return pg.estimate_item_height(it,9,9,TPL['exercise'])
# Augmente seulement des zones de rédaction utiles : aucun espace intercalaire artificiel.
def fill_columns(cols):
 for k,col in enumerate(cols):
  cap=pg.column_capacity(k)
  while sum(map(height,col))>cap:
   eligible=[p for it in col for p in leaves(it) if p['response_type']=='multiline_text' and p['grading']['lines']>3]
   if not eligible: raise ValueError(('overflow',k,sum(map(height,col)),cap))
   max(eligible,key=lambda p:p['grading']['lines'])['grading']['lines']-=1
  while True:
   elig=[p for it in col for p in leaves(it) if p['response_type']=='multiline_text' and p['grading']['lines']<12]
   if not elig: break
   p=min(elig,key=lambda p:p['grading']['lines']); p['grading']['lines']+=1
   if sum(map(height,col))>cap: p['grading']['lines']-=1; break
  print('col',k,'used',round(sum(map(height,col)),1),'capacity',round(cap,1),'fill',round(sum(map(height,col))/cap,3))

class Readings:
 """Adaptateur en mémoire des réponses prescrites ; aucune écriture en base."""
 def __init__(self,p): self.p=p
 def get(self,*args): return NS(selected_choices=self.p.get('selected',[]),selected_pairs=self.p.get('pairs',[]))
 def query(self,*args): return self
 def filter_by(self,**kw): return self
 def order_by(self,*args): return self
 def first(self): return None

def drawing_canvas(c,z,kind):
 spec=FIGS[kind]; step=min((z['w_pt']-28)/spec['nx'],(z['h_pt']-26)/spec['ny'])
 ox=z['x_pt']+(z['w_pt']-step*spec['nx'])/2; oy=z['y_pt']+13
 def xy(p): return ox+p[0]*step,oy+p[1]*step
 c.saveState(); c.setStrokeColor(HexColor('#D4E1E5')); c.setLineWidth(.35)
 for i in range(spec['nx']+1): c.line(*xy((i,0)),*xy((i,spec['ny'])))
 for j in range(spec['ny']+1): c.line(*xy((0,j)),*xy((spec['nx'],j)))
 c.setStrokeColor(HexColor('#183F53')); c.setFillColor(HexColor('#183F53')); c.setLineWidth(1)
 for a,b in spec['edges']: c.line(*xy(spec['points'][a]),*xy(spec['points'][b]))
 for name,p in spec['points'].items():
  x,y=xy(p); c.line(x-2,y,x+2,y); c.line(x,y-2,x,y+2); c.setFont('Helvetica-Bold',7); c.drawString(x+3,y+4,name)
 if 'axis' in spec:
  c.setStrokeColor(HexColor(TEAL)); c.line(*xy((spec['axis'],0)),*xy((spec['axis'],spec['ny']))); c.drawString(*xy((spec['axis']+.3,spec['ny']-.8)),'(d)')
 c.restoreState()
 marks={'segments':[dict(zip(['x1','y1','x2','y2'],(*xy(a),*xy(b)))) for a,b in spec['fix']]}
 if 'label' in spec:
  p,s=spec['label']; x,y=xy(p); marks['labels']=[{'x':x,'y':y,'text':s}]
 return marks

ALL=[]
def build(key,name,cls,title,cols,synthesis):
 fill_columns(cols)
 items=[it for col in cols for it in col]; slots=[(i//2,i%2) for i,col in enumerate(cols) for it in col]
 npages=(len(cols)+1)//2
 total=sum(map(points,items)); score=sum(map(earned,items)); note=scoring.round_half_up(20*score/total)
 print(key,'score',score,'/',total,'note',note)
 pages_meta=[{'page_id':str(uuid.uuid5(uuid.NAMESPACE_URL,f'mathprint-demo-{key}-{p}'))} for p in range(npages)]
 for p in pages_meta: p['payload']=sign_page(p['page_id'])
 b=io.BytesIO(); c=canvas.Canvas(b,pagesize=pg.A4)
 zones=pg.render_copy(c,student_name=name,class_name=cls,title=title,assessment_type='control',items=items,pages_meta=pages_meta,tpl=TPL,placement=slots,min_pages=npages)
 c.save()
 assert max(z['page_index'] for z in zones)==npages-1
 lookup={p['item_id']:(it,p) for it in items for p in leaves(it)}
 # Seed geometry and tiny footer only; cards, boxes, header, QR, tags are native.
 seed=io.BytesIO(); sc=canvas.Canvas(seed,pagesize=pg.A4); geofix={}
 for pi in range(npages):
  if pi>0: pg._draw_header(sc,name,cls,title,'control','21/10/2026',TPL['header'])
  for z in (z for z in zones if z['page_index']==pi):
   it,p=lookup[z['item_id']]
   if p.get('drawing'): geofix[z['item_id']]=drawing_canvas(sc,z,p['drawing'])
  sc.setFont('Helvetica',7); sc.setFillColor(HexColor(TEAL)); sc.drawCentredString(pg.PAGE_W/2,36,f'MATHPRINT  |  Contrôle - {scoring.format_points(total)} points  |  {pi+1}/{npages}')
  sc.showPage()
 sc.save()
 annotations=[]
 for pi in range(npages):
  anns=[]
  for z in (z for z in zones if z['page_index']==pi):
   it,p=lookup[z['item_id']]
   zi=NS(**z,id=p['item_id'],meta_json=z['meta'])
   item=NS(response_type=p['response_type'],expected_json=p['expected'],grading_json=p['grading'])
   decision=NS(score=p['score'],max_score=p['grading']['max_score'],response_id='prescribed')
   marks=pipeline._zone_marks(Readings(p),item,zi,decision)
   if p.get('drawing'): marks.update(geofix[p['item_id']])
   first=leaves(it)[0] is p
   strip=z['meta'].get('correction_strip') or z['meta'].get('group_correction_strip')
   anns.append({**z,'marks':marks,'strip':strip,'score':earned(it),'max_score':points(it),'feedback':it['feedback'] if first else '', 'suppress_score':not first})
  annotations.append({'student':name,'assessment_type':'control','note':scoring.format_points(note)+'/20','note_raw':f'{scoring.format_points(score)} / {scoring.format_points(total)} pts','synthesis':synthesis,'appreciation_font_size':8.5,'page_zones':anns})
 ov=TMP/(key+'_overlay.pdf'); pg.render_overlay(str(ov),copies_annotations=annotations,color=RED)
 # Même sujet et mêmes coordonnées pour les deux versions ; l'élève ne reçoit
 # aucune marque, note, appréciation ou construction corrective.
 for corrected in (False, True):
  base=PdfReader(b); seedr=PdfReader(seed); over=PdfReader(ov); w=PdfWriter()
  for i,page in enumerate(base.pages):
   page.merge_page(seedr.pages[i])
   if corrected: page.merge_page(over.pages[i])
   w.add_page(page)
  suffix='CORRIGE' if corrected else 'ELEVE'
  w.add_metadata({'/Title':title+' - '+name+' - '+suffix,'/Author':'MathPrint'})
  out=ROOT/(key+'_'+suffix+'.pdf')
  with open(out,'wb') as f:w.write(f)
 record={'key':key,'name':name,'score':score,'total':total,'note':note,'items':items,'zones':zones,'slots':slots,'synthesis':synthesis}
 ALL.append(record)
 (TMP/(key+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))
 return out

if __name__=='__main__':
 build('01_controle_raisonnement','Camille Moreau','3e B','Raisonnement et démonstration',strong,'Des calculs solides. Vérifie les conditions de tes opérations et distingue exemples et preuve générale.')
 build('02_controle_accompagne','Ibrahim Koné','6e A','Nombres et mesures',weak,'Tu réussis les durées et les mètres entiers. À reprendre : fractions, décimaux et périmètre. Un repère à la fois !')
 build('03_controle_geometrie','Léa Kouamé','4e C','Transformations et longueurs',geo,'Les images sont bien placées. Vérifie les sommets à relier et les rapports avant de valider un parallèle.')
 (ROOT/'sources/scenarios_natifs.json').write_text(json.dumps(ALL,ensure_ascii=False,indent=2))
