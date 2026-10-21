"""Bilans élève et famille : trois profils de 3e B, sur les 42 compétences de 3e de la plateforme."""
import os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/mathprint-mpl')
os.environ.setdefault('XDG_CACHE_HOME','/private/tmp/mathprint-cache')
from pathlib import Path
from datetime import date,timedelta
import json,zlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
ROOT=Path(__file__).resolve().parents[1];REPO=ROOT.parents[1];TMP=REPO/'tmp/pdfs/bilan_eleve';TMP.mkdir(parents=True,exist_ok=True)
for n,f in [('R','Arial.ttf'),('B','Arial Bold.ttf'),('I','Arial Italic.ttf')]:pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
pdfmetrics.registerFontFamily('R',normal='R',bold='B',italic='I',boldItalic='B')
NAVY='#183449';TEAL='#087F83';MUTED='#637888';RED='#BC4855';GOLD='#C58935';PALE='#EFF5F7';LINE='#D8E3E8'
LIGHT='#A9D9D6';TRACK='#E6EDF0';GREY='#A8BAC5'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':LINE,'axes.labelcolor':NAVY,'xtick.color':MUTED,'ytick.color':MUTED,'text.color':NAVY,'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white'})

# Les 42 compétences du référentiel 3e de la plateforme, dans l'ordre du programme.
FW=next(f for f in json.loads((REPO/'backend/app/data/competencies_fr.json').read_text())['frameworks'] if f['grade_level']=='3e')
COMPS=[dict(code=cp['code'],label=cp['label'].replace('$x^2 = a$','x² = a'),domain=dm['code']) for dm in FW['domains'] for ch in dm['chapters'] for cp in ch['competencies']]
DOMAINS={'A':'Nombres et calcul littéral','B':'Données et fonctions','C':'Géométrie'}
SHORT={'B3.4':'Modéliser la proportionnalité par une fonction linéaire','B5.3':'Étudier des expériences aléatoires à deux épreuves','B2.2':'Tracer la représentation graphique d’une fonction','B2.3':'Exploiter la représentation graphique d’une fonction','A1.3':'Décomposer un entier en facteurs premiers'}
# Progression annuelle de la classe : mois d'étude (0 = septembre) ou mois prévu.
MONTHS=['septembre','octobre','novembre','décembre','janvier','février','mars','avril','mai','juin']
STUDIED={'A1.1':0,'A1.2':0,'A1.3':0,'A2.1':0,'A2.3':0,'A2.4':0,'A2.2':1,'A3.1':1,'C3.1':1,'A3.2':2,'A3.3':2,'B4.1':2,'B4.2':2,'B4.3':2,'B1.1':3,'B1.2':3,'B1.3':3,'C2.1':3,'C2.2':3,'A4.1':4,'A4.2':4,'B5.1':4,'B5.2':4,'C1.1':4,'A4.4':5,'B2.1':5,'B2.2':5,'B2.3':5,'C2.3':5,'B3.1':6,'B3.3':6,'C1.2':6}
PLANNED={'B3.2':6,'A4.3':7,'B3.4':7,'C3.2':7,'C3.3':7,'B5.3':8,'C1.3':8,'C4.1':8,'C4.2':9,'C4.3':9}
assert len(COMPS)==42 and set(STUDIED)|set(PLANNED)=={c['code'] for c in COMPS} and not set(STUDIED)&set(PLANNED)
TODAY=date(2026,3,13);END=date(2026,7,3);NOW=6.4
EVAL=['fractions','puissances','calcul littéral','équations','Pythagore','Thalès']*2
CLASS=json.loads((ROOT/'sources/donnees_bilan_3eB.json').read_text())['records']
RANKS={r['id']:i+1 for i,r in enumerate(sorted(CLASS,key=lambda r:-r['delta']))}
MEANRANKS={r['id']:i+1 for i,r in enumerate(sorted(CLASS,key=lambda r:-r['mean']))}

def fr(n,d=1):return f'{n:.{d}f}'.replace('.',',')
def signed(n):return ('+' if n>=0 else '')+fr(n)
def day(d):return f'{d.day} {["janv.","févr.","mars","avril","mai","juin","juil."][d.month-1]}'
def retention(m,month,bonus,key):
 # Part de la maîtrise actuelle encore disponible le dernier jour de l'année, sans révision.
 jitter=((zlib.crc32(key.encode())%1000)/1000-.5)*.06
 return float(np.clip(.22+.58*(m/100)**1.4+.035*(NOW-month)+bonus+jitter,.2,.95))
def status(c):
 if not c['studied']:return 'new'
 if c['durable']>=60:return 'solid'
 return 'review' if c['m']>=50 else 'build'
STATUS={'solid':('Solide',TEAL),'review':('À réviser',GOLD),'build':('À renforcer',RED),'new':('À venir',GREY)}

class Student:
 def __init__(s,spec):
  s.__dict__.update(spec);r=next(r for r in CLASS if r['id']==spec['id'])
  s.name=r['name'];s.t2=r['t2'];s.mean=r['mean'];s.previous=r['previous'];s.delta=r['delta'];s.rank=RANKS[s.id];s.meanrank=MEANRANKS[s.id]
  s.comps=[]
  for c in COMPS:
   c=dict(c);c['studied']=c['code'] in STUDIED
   if c['studied']:
    c['m']=spec['mastery'][c['code']];c['R']=retention(c['m'],STUDIED[c['code']],spec['bonus'],c['code']+s.name)
    c['durable']=round(c['m']*c['R']);c['lost']=c['m']-c['durable']
   else:c['month']=PLANNED[c['code']]
   c['status']=status(c);s.comps.append(c)
  s.by={c['code']:c for c in s.comps};studied=[c for c in s.comps if c['studied']];new=[c for c in s.comps if not c['studied']]
  s.n_know=sum(c['m']>=50 for c in studied);s.n_solid=sum(c['status']=='solid' for c in studied);s.n_new=len(new)
  s.n_risk=sum(c['m']>=50 and c['lost']>=15 for c in studied)
  m_new=np.mean([c['m'] for c in studied])*.9
  # Objectif des priorités : progression ciblée puis révisions espacées jusqu'au 3 juillet.
  s.targets={}
  for code,kind in s.priorities:
   c=s.by[code];s.targets[code]=round(min(c['m']+(s.gain if kind=='build' else 0),94)*.92/5)*5
  def planned(c):
   if c['code'] in s.targets:return s.targets[c['code']]
   m=c['m']+(12 if c['m']<50 else 0);return m*(c['R']+(1-c['R'])*.8)
  s.today=sum(c['m'] for c in studied)/42
  s.no=(sum(c['durable'] for c in studied)+len(new)*m_new*.8)/42
  s.pace=(sum(c['m']*(c['R']+(1-c['R'])*.45) for c in studied)+len(new)*m_new*.85)/42
  s.plan=(sum(planned(c) for c in studied)+len(new)*m_new*.95)/42
  s.best=max(range(12),key=lambda j:s.t2[j])
 def m(s,code):return s.by[code]['m']
 def dur(s,code):return s.by[code]['durable']
 def label(s,code):return s.by[code]['label']

BUILD_DATES=[TODAY+timedelta(d) for d in (3,6,13,27,55,83)];SAVE_DATES=[TODAY+timedelta(d) for d in (7,21,35,63,91)]

STUDENTS=[
 dict(id=8,first='Cheick',file='05a_bilan_eleve_Cheick_Coulibaly',bonus=0,gain=34,band=5,
  mastery={'A1.1':62,'A1.2':55,'A1.3':38,'A2.1':60,'A2.2':48,'A2.3':40,'A2.4':45,'A3.1':42,'A3.2':34,'A3.3':22,'A4.1':44,'A4.2':30,'A4.4':20,'B1.1':70,'B1.2':58,'B1.3':52,'B2.1':48,'B2.2':40,'B2.3':50,'B3.1':28,'B3.3':35,'B4.1':80,'B4.2':75,'B4.3':45,'B5.1':55,'B5.2':50,'C1.1':64,'C1.2':38,'C2.1':18,'C2.2':16,'C2.3':26,'C3.1':58},
  priorities=[('C2.1','build'),('A3.2','build'),('C3.1','save')]),
 dict(id=5,first='Aya',file='05b_bilan_eleve_Aya_Kouassi',bonus=.03,gain=30,band=4,
  mastery={'A1.1':80,'A1.2':74,'A1.3':66,'A2.1':78,'A2.2':75,'A2.3':64,'A2.4':62,'A3.1':58,'A3.2':52,'A3.3':44,'A4.1':68,'A4.2':55,'A4.4':48,'B1.1':82,'B1.2':74,'B1.3':66,'B2.1':70,'B2.2':62,'B2.3':60,'B3.1':50,'B3.3':56,'B4.1':85,'B4.2':80,'B4.3':66,'B5.1':72,'B5.2':68,'C1.1':78,'C1.2':58,'C2.1':52,'C2.2':48,'C2.3':55,'C3.1':70},
  priorities=[('A3.2','build'),('C2.2','build'),('C3.1','save')]),
 dict(id=11,first='Élise',file='05c_bilan_eleve_Elise_Durand',bonus=.07,gain=16,band=2.5,
  mastery={'A1.1':95,'A1.2':92,'A1.3':90,'A2.1':94,'A2.2':88,'A2.3':96,'A2.4':98,'A3.1':84,'A3.2':78,'A3.3':70,'A4.1':86,'A4.2':80,'A4.4':76,'B1.1':96,'B1.2':92,'B1.3':88,'B2.1':90,'B2.2':86,'B2.3':84,'B3.1':78,'B3.3':82,'B4.1':95,'B4.2':94,'B4.3':88,'B5.1':90,'B5.2':86,'C1.1':92,'C1.2':80,'C2.1':92,'C2.2':88,'C2.3':84,'C3.1':95},
  priorities=[('A3.3','build'),('A4.4','build'),('B3.1','save')]),
]

def many(s):return 'toutes' if s.n_risk==s.n_know else str(s.n_risk)
def texts(s):
 m,dur,P=s.m,s.dur,round
 risky=sorted([c for c in s.comps if c['studied'] and c['m']>=50],key=lambda c:-c['lost'])[:3]
 risky_txt=', '.join(f'{c["label"].lower()} ({c["m"]} % → {c["durable"]} %)' for c in risky)
 if s.id==8:
  return dict(
   good=f'Ta moyenne a gagné <b>{fr(s.delta)} points</b> en un trimestre ({fr(s.previous)} → {fr(s.mean)}/20) : c’est l’une des plus fortes progressions de la classe. Tu es à l’aise quand les maths sont concrètes : représenter des données ({m("B4.1")} %), calculer une moyenne ({m("B4.2")} %), reconnaître une situation de proportionnalité ({m("B1.1")} %). Et Pythagore t’a valu ta meilleure note du trimestre ({fr(s.t2[s.best])}/20).',
   brake=f'Deux blocages expliquent l’essentiel des points perdus. <b>Thalès</b> ({m("C2.1")} %) : tu écris l’égalité des rapports avant d’avoir vérifié que les droites sont parallèles. <b>Le calcul littéral</b> (développer {m("A3.2")} %, factoriser {m("A3.3")} %) : c’est lui qui fait ensuite échouer les équations ({m("A4.1")} %) et, bientôt, les fonctions affines.',
   forget=f'Sur les {s.n_know} compétences que tu sais faire aujourd’hui, <b>{many(s)} vont en partie s’effacer</b> d’ici le 3 juillet si tu ne les revois pas. Pythagore, par exemple, passerait de {m("C3.1")} % à {dur("C3.1")} %. Ce n’est pas un manque de travail : c’est le fonctionnement normal de la mémoire, et des révisions courtes et espacées suffisent à l’éviter.',
   next=f'Trois priorités, dans cet ordre : Thalès, le développement, puis entretenir Pythagore (détails page 3). <b>Deux séances de 20 minutes par semaine</b> suffisent. Avec ce plan, tu peux maîtriser environ <b>{P(s.plan)} % du programme de 3e le 3 juillet</b>, contre {P(s.today)} % aujourd’hui.',
   prio=[dict(why='Tu écris AM/AB = AN/AC sans avoir d’abord justifié que (MN) et (BC) sont parallèles. Le calcul est souvent juste, mais le raisonnement n’est pas validé.',
              how='Commence chaque exercice par la phrase « Je sais que (MN) // (BC) ». Colorie de la même couleur les côtés qui se correspondent, puis écris les rapports dans le même sens. Un exercice par séance.'),
         dict(why=f'Développer (x + 3)(x − 2) reste hésitant ({m("A3.2")} %), factoriser encore plus ({m("A3.3")} %). Or les équations et les fonctions affines du 3e trimestre en dépendent.',
              how='Utilise le tableau à 4 cases : x et 3 en ligne, x et −2 en colonne. Fais 5 calculs par séance et vérifie chacun en remplaçant x par 1 des deux côtés.'),
         dict(why=f'C’est ton point fort en géométrie ({m("C3.1")} % aujourd’hui), mais sans révision il ne t’en resterait que {dur("C3.1")} % le 3 juillet.',
              how='Un seul exercice de Pythagore toutes les deux semaines suffit à le garder. Commence toujours par repérer l’angle droit et colorier l’hypoténuse.')],
   defi=('Dans le triangle ABC, M est sur [AB] et N sur [AC], avec (MN) // (BC). AM = 3 cm, AB = 6 cm et AN = 4 cm. Combien mesure AC ? Commence par la phrase magique !',
         'AC = 8 cm, car (MN) // (BC) donc AM/AB = AN/AC, soit 3/6 = 4/8.'),
   parents=f'Pas besoin de maîtriser les maths pour aider Cheick. <b>1.</b> Fixez deux rendez-vous de 20 minutes par semaine, toujours au même moment. <b>2.</b> Demandez-lui d’expliquer à voix haute la méthode du jour : expliquer, c’est déjà réviser. <b>3.</b> Félicitez la régularité plus que la note : les {fr(s.delta)} points gagnés ce trimestre viennent de ses efforts, et c’est ce message qui donne envie de continuer.')
 if s.id==5:
  p3=s.by['C3.1']
  return dict(
   good=f'Tu as franchi la barre des 10/20 : {fr(s.previous)} au 1er trimestre, <b>{fr(s.mean)}/20</b> au 2e, soit {signed(s.delta)} points, l’une des plus fortes hausses de la classe. Tes appuis sont nombreux : statistiques ({m("B4.1")} %), proportionnalité ({m("B1.1")} %), diviseurs ({m("A1.1")} %), symétries et translations ({m("C1.1")} %). Puissances ({m("A2.2")} %) et Pythagore ({m("C3.1")} %) sont en bonne voie.',
   brake=f'Une seule erreur te coûte beaucoup : devant une parenthèse précédée d’un signe moins, tu changes le signe du premier terme mais pas des suivants. Elle explique ton score en développement ({m("A3.2")} %) et en factorisation ({m("A3.3")} %), et on la retrouve dans les équations et les fonctions affines ({m("B3.1")} %). En géométrie, la réciproque de Thalès ({m("C2.2")} %) demande une rédaction plus rigoureuse.',
   forget=f'<b>{s.n_risk} de tes {s.n_know} compétences acquises vont en partie s’effacer</b> d’ici le 3 juillet sans révision. Les plus exposées : {risky_txt}. Tu les sais aujourd’hui : il serait dommage de les perdre avant la fin de l’année.',
   next=f'Tes trois priorités : le signe moins en calcul littéral, la réciproque de Thalès, puis entretenir Pythagore. Avec ce plan, tu peux maîtriser environ <b>{P(s.plan)} % du programme de 3e le 3 juillet</b>, contre {P(s.today)} % aujourd’hui.',
   prio=[dict(why='Dans 5 − (2x − 3), tu écris 5 − 2x − 3 au lieu de 5 − 2x + 3. Une seule règle mal appliquée, mais elle revient dans presque chaque exercice d’algèbre.',
              how='Avant de supprimer une parenthèse précédée de « − », réécris-la −1 × ( … ) et entoure chaque terme qui change de signe. Vérifie ensuite en remplaçant x par 1.'),
         dict(why='Pour prouver que deux droites sont parallèles, tu compares bien les rapports, mais tu oublies de vérifier l’alignement des points dans le même ordre : la conclusion n’est pas justifiée.',
              how='Rédige en trois lignes fixes : « je calcule les deux rapports », « les points sont alignés dans le même ordre », « donc, d’après la réciproque de Thalès, les droites sont parallèles ».'),
         dict(why=f'Tu le maîtrises aujourd’hui ({p3["m"]} %), mais sans révision il ne t’en resterait que {p3["durable"]} % le 3 juillet. C’est pourtant un outil qui revient dans de nombreux problèmes de géométrie.',
              how='Toutes les deux semaines, un exercice sans la leçon : repère l’angle droit, écris l’égalité avant de remplacer par les nombres, puis vérifie que l’hypoténuse est le plus long côté.')],
   defi=('Développe et réduis : 5 − (2x − 3). Puis vérifie ton résultat en remplaçant x par 1 dans les deux expressions.',
         '8 − 2x. Vérification avec x = 1 : 5 − (2 − 3) = 6 et 8 − 2 = 6.'),
   parents=f'Aya a franchi un cap ce trimestre : dites-le-lui ! <b>1.</b> Deux séances de 20 minutes par semaine, au même moment. <b>2.</b> Demandez à Aya d’expliquer la règle du signe moins sur un exemple : si l’explication est claire, la règle est acquise. <b>3.</b> Valorisez la régularité : c’est elle qui a fait passer la moyenne au-dessus de 10.')
 return dict(
  good=f'Un très beau trimestre : <b>{fr(s.mean)}/20</b> de moyenne{", la meilleure de la classe" if s.meanrank==1 else ""}, et une maîtrise presque complète des nombres : fractions ({m("A2.4")} %), forme irréductible ({m("A2.3")} %), nombres relatifs ({m("A2.1")} %). En géométrie, Pythagore ({m("C3.1")} %) et Thalès ({m("C2.1")} %) sont des points forts nets.',
  brake=f'Ton point le moins sûr est le calcul littéral : factoriser ({m("A3.3")} %) et développer ({m("A3.2")} %). Les erreurs viennent moins de la méthode que de la vigilance : un signe moins devant une parenthèse appliqué au premier terme seulement. Mettre un problème en équation ({m("A4.4")} %) est l’autre marche à franchir pour viser l’excellence.',
  forget=f'À ton niveau, le vrai risque est l’oubli. Les chapitres récents ne sont pas encore ancrés : les fonctions affines passeraient de {m("B3.1")} % aujourd’hui à {dur("B3.1")} % le 3 juillet sans révision. Au total, {s.n_risk} compétences perdraient au moins 15 points.',
  next=f'Trois objectifs : fiabiliser le calcul littéral, t’entraîner sur des problèmes ouverts, ancrer les fonctions affines. Avec ce plan, tu peux maîtriser environ <b>{P(s.plan)} % du programme de 3e le 3 juillet</b>. Tu peux aussi prendre de l’avance sur la trigonométrie, prévue en avril.',
  prio=[dict(why='Tu connais les méthodes, mais une erreur de signe ou une identité remarquable mal reconnue, comme 4x² − 9 = (2x − 3)(2x + 3), te coûte des points sur des exercices que tu maîtrises.',
             how='Pour chaque factorisation, écris d’abord qui sont a et b, puis redéveloppe ton résultat pour le contrôler. Termine chaque séance par 3 calculs « pièges » avec des signes moins.'),
        dict(why='Mettre un problème en équation est ce qui sépare les très bonnes notes des excellentes : choisir l’inconnue, écrire l’équation, conclure avec une phrase.',
             how='Une fois par semaine, un problème ouvert : choisis l’inconnue, écris l’équation, résous, puis rédige la réponse avec l’unité. Cherche ensuite une seconde méthode.'),
        dict(why=f'Chapitre tout juste étudié : tu le réussis ({m("B3.1")} %), mais il ne t’en resterait que {dur("B3.1")} % le 3 juillet sans révision.',
             how='Toutes les deux semaines, un exercice : lire a et b sur un graphique, puis retrouver l’expression de f(x) et la vérifier sur un point.')],
  defi=('Prouve que la somme de deux nombres impairs consécutifs est toujours un multiple de 4. Deux exemples ne suffisent pas : il faut une preuve valable pour tous les nombres !',
        '2n + 1 et 2n + 3 sont deux impairs consécutifs : leur somme vaut 4n + 4 = 4(n + 1).'),
  parents='À ce niveau, le risque est moins l’échec que l’ennui et l’oubli. <b>1.</b> Encouragez Élise à prendre de l’avance (trigonométrie en avril) plutôt qu’à refaire ce qui est acquis. <b>2.</b> Proposez-lui d’expliquer une notion à un camarade, un frère ou une sœur : enseigner est la meilleure façon d’ancrer un savoir. <b>3.</b> Valorisez la rigueur de la rédaction autant que le résultat juste.')

# ---------- Graphiques ----------
def save(fig,name):fig.savefig(TMP/(name+'.png'),dpi=220,bbox_inches='tight',pad_inches=.14);plt.close(fig)
MARK={'A':'o','B':'s','C':'^'}
def nuage(s,name):
 fig,ax=plt.subplots(figsize=(10,4.7))
 xx,yy=np.meshgrid(np.linspace(0,100,401),np.linspace(0,50,201))
 zone=np.where(xx<50,2,np.where(xx-yy>=60,0,1)).astype(float);zone[yy>xx]=np.nan
 ax.imshow(zone,extent=(0,100,0,50),origin='lower',aspect='auto',cmap=ListedColormap(['#E3F2F1','#FBF1DF','#F7E5E7']),vmin=0,vmax=2,zorder=0)
 ax.text(25,46,'À RENFORCER',ha='center',fontsize=9,color=RED,fontweight='bold',alpha=.8)
 ax.text(78,46,'À RÉVISER AVANT L’OUBLI',ha='center',fontsize=9,color=GOLD,fontweight='bold',alpha=.9)
 ax.text(96,2.2,'SOLIDE',ha='right',fontsize=9,color=TEAL,fontweight='bold',alpha=.9)
 prio={code:k+1 for k,(code,_) in enumerate(s.priorities)}
 for c in s.comps:
  if not c['studied']:continue
  col={'solid':TEAL,'review':GOLD,'build':RED}[c['status']]
  ax.scatter(c['m'],c['lost'],marker=MARK[c['domain']],s=70 if c['code'] in prio else 44,color=col,edgecolor='white',lw=.8,zorder=3)
 placed=[]
 for code,k in prio.items():
  c=s.by[code]
  # Étiquettes décalées sans chevauchement (boîtes d'environ 12 × 4 unités).
  for dx,dy in [(-10,7),(10,7),(-13,0),(13,0),(-10,13),(10,13),(0,19),(-18,8),(18,8)]:
   lx,ly=c['m']+dx,c['lost']+dy
   if 7<lx<93 and 3<ly<44 and all(abs(lx-a)>13 or abs(ly-b)>4.6 for a,b in placed):break
  placed.append((lx,ly))
  ax.annotate(f'{k}  {code}',xy=(c['m'],c['lost']),xytext=(lx,ly),ha='center',va='center',fontsize=8.5,fontweight='bold',color='white',
   bbox=dict(boxstyle='round,pad=.3',fc=NAVY,ec='none'),arrowprops=dict(arrowstyle='-',color=NAVY,lw=.8,shrinkA=0,shrinkB=3),zorder=5)
 ax.set(xlim=(0,100),ylim=(0,50),xlabel='Ce que tu sais aujourd’hui (%)',ylabel='Oubli prévu d’ici le 3 juillet (points)')
 handles=[Line2D([],[],marker=MARK[d],ls='',color=NAVY,ms=7,label=l) for d,l in DOMAINS.items()]
 ax.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,-.15),ncol=3,frameon=False,fontsize=9);save(fig,name)
def trajectoire(s,name):
 fig,ax=plt.subplots(figsize=(10,4.1))
 points=[date(2025,9,30),date(2025,10,31),date(2025,11,30),date(2025,12,19),date(2026,1,31),date(2026,2,28),TODAY]
 hist=[sum(c['m'] for c in s.comps if c['studied'] and STUDIED[c['code']]<=k)*(.84+.16*k/6)/42 for k in range(7)]
 hist=[0]+hist;points=[date(2025,9,1)]+points
 fut=[TODAY+timedelta(days=int(d)) for d in np.linspace(0,(END-TODAY).days,30)];t=np.linspace(0,1,30)
 curve=lambda end,p:s.today+(end-s.today)*t**p
 band=s.band*t
 ax.fill_between(fut,curve(s.plan,.85)-band,curve(s.plan,.85)+band,color=TEAL,alpha=.14,lw=0,label='Fourchette d’élèves au profil comparable')
 ax.plot(points,hist,color=NAVY,lw=2.6,marker='o',ms=4.5,label='Ta progression depuis septembre')
 ax.plot(fut,curve(s.no,1),color=GREY,lw=2,ls='--',label='Sans révision')
 ax.plot(fut,curve(s.pace,.95),color=GOLD,lw=2,ls='-.',label='À ton rythme actuel')
 ax.plot(fut,curve(s.plan,.85),color=TEAL,lw=3,label='Avec ton plan d’action')
 for v,col,b in [(s.no,GREY,False),(s.pace,GOLD,False),(s.plan,TEAL,True)]:
  ax.text(END+timedelta(days=4),v,f'{v:.0f} %',va='center',fontsize=10.5 if b else 9.5,color=col,fontweight='bold')
 ax.scatter([TODAY],[s.today],s=90,color=NAVY,zorder=5);ax.annotate(f'Aujourd’hui : {s.today:.0f} %',xy=(TODAY,s.today),xytext=(-12,14),textcoords='offset points',ha='right',fontsize=9.5,fontweight='bold',color=NAVY)
 top=min(100,max(60,int(np.ceil((s.plan+s.band+12)/10))*10))
 ax.set(ylim=(0,top),xlim=(date(2025,9,1),date(2026,7,20)),ylabel='Programme de 3e maîtrisé (%)',yticks=range(0,top+1,10 if top<=60 else 20))
 ax.axvline(END,color=MUTED,lw=.8,ls=':');ax.text(END,top*1.01,'3 juillet',ha='center',fontsize=8.5,color=MUTED)
 ax.xaxis.set_major_locator(mdates.MonthLocator());ax.xaxis.set_major_formatter(mdates.DateFormatter('%m'))
 ax.set_xticks([date(2025,m,1) for m in (9,10,11,12)]+[date(2026,m,1) for m in (1,2,3,4,5,6,7)]);ax.set_xticklabels(['sept.','oct.','nov.','déc.','janv.','févr.','mars','avril','mai','juin','juil.'])
 ax.grid(axis='y',alpha=.2);ax.legend(loc='upper center',bbox_to_anchor=(.5,-.1),ncol=3,frameon=False,fontsize=8.5);save(fig,name)

# ---------- Mise en page ----------
W,H=595.2756,841.8898
def render(s):
 T=texts(s);nuage(s,s.file+'_nuage');trajectoire(s,s.file+'_trajectoire')
 c=canvas.Canvas(str(ROOT/(s.file+'.pdf')),pagesize=(W,H));c.setTitle(f'MathPrint | Bilan de {s.first} | 3e B | T2 2025-2026');c.setAuthor('MathPrint')
 def rect(x,y,w,h,col,r=0,stroke=None,lw=.6):
  c.setFillColor(HexColor(col));c.setStrokeColor(HexColor(stroke or col));c.setLineWidth(lw);c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=bool(stroke))
 def txt(x,y,t,fs=10,col=NAVY,font='R',align='l'):
  c.setFillColor(HexColor(col));c.setFont(font,fs)
  {'l':c.drawString,'c':c.drawCentredString,'r':c.drawRightString}[align](x,H-y-fs,t)
 def style(fs,col,leading=None):return ParagraphStyle('p',fontName='R',fontSize=fs,leading=leading or fs*1.38,textColor=HexColor(col))
 def measure(t,w,fs=10,col=NAVY,leading=None):p=Paragraph(t,style(fs,col,leading));return p,p.wrap(w,2000)[1]
 def para(x,y,t,w=523,fs=10,col=NAVY,leading=None,limit=792):
  p,h=measure(t,w,fs,col,leading);assert y+h<limit,(t[:70],y,h);p.drawOn(c,x,H-y-h);return h
 def header(n,title,sub):
  rect(0,0,W,7,TEAL);txt(36,22,'mathprint',18,NAVY,'B');txt(559,28,f'BILAN ÉLÈVE ET FAMILLE  /  {s.name.upper()}',8,TEAL,'B','r')
  c.setStrokeColor(HexColor(LINE));c.setLineWidth(.5);c.line(36,H-42,559,H-42)
  txt(36,62,title,25,NAVY,'B');rect(36,102,523,41,PALE,r=6);rect(36,108,3,29,TEAL,r=1);para(48,111,sub,499,9,MUTED,leading=12)
  txt(36,807,f'{s.name} • 3e B • Mathématiques • T2 2025-2026 • Bilan élève et famille',7,MUTED);txt(559,807,f'{n}/3',8,MUTED,'R','r')
 def chart(name,x,y,w,h):c.drawImage(str(TMP/(name+'.png')),x,H-y-h,width=w,height=h,mask='auto',preserveAspectRatio=True,anchor='c')
 def metric(x,y,w,value,label,detail):
  rect(x,y,w,96,PALE,r=8);fs=26
  while pdfmetrics.stringWidth(value,'B',fs)>w-26:fs-=1
  txt(x+13,y+12,value,fs,TEAL,'B');txt(x+13,y+45,label,9.5,NAVY,'B');para(x+13,y+62,detail,w-26,8,MUTED)
 # Page 1 : la synthèse.
 header(1,f'Le bilan de {s.first}',f'Mathématiques • 3e B • 2e trimestre 2025-2026, bilan arrêté au vendredi 13 mars 2026. Point de repère de ce bilan : <b>le dernier jour de l’année scolaire, vendredi 3 juillet 2026</b>. Ce qui compte, c’est ce que tu sauras encore ce jour-là.')
 metric(36,155,166,fr(s.mean)+'/20','Moyenne du 2e trimestre',f'1er trimestre : {fr(s.previous)}/20 • {signed(s.delta)} point{"s" if abs(s.delta)>=2 else ""}')
 metric(215,155,166,f'{s.n_know}/42','Compétences que tu sais faire',f'dont {s.n_solid} solide{"s" if s.n_solid>1 else ""} jusqu’en juillet • {s.n_new} pas encore étudiées')
 metric(394,155,165,f'{s.today:.0f} % → {s.plan:.0f} %','Ton objectif au 3 juillet','Part du programme de 3e maîtrisée, avec ton plan d’action')
 txt(36,266,'Le mot du bilan',13,TEAL,'B');y=290
 for lab,key,col in [('Ce qui va bien','good',TEAL),('Ce qui te freine','brake',RED),('Ce que tu risques d’oublier','forget',GOLD),('Sur quoi travailler maintenant','next',NAVY)]:
  y+=para(36,y,f'<font color="{col}"><b>{lab.upper()}</b></font>  {T[key]}',523,9.4,leading=12.6)+8
 y+=6;txt(36,y,'La carte de tes compétences',13,TEAL,'B');y+=20
 h=min(232,770-y-34);chart(s.file+'_nuage',36,y,523,h);y+=h+4
 para(36,y,f'<b>Lecture.</b> Chaque point est une compétence déjà étudiée. Plus il est à droite, mieux tu la maîtrises aujourd’hui ; plus il est haut, plus tu risques de l’oublier d’ici le 3 juillet. Les numéros renvoient à tes 3 priorités (page 3). Les {s.n_new} compétences pas encore étudiées figurent page 2.',523,8,MUTED,leading=10.5,limit=800)
 c.showPage()
 # Page 2 : toutes les compétences.
 header(2,'Toutes tes compétences',f'Les 42 compétences du programme de 3e. <b>{s.n_know} sont acquises aujourd’hui</b> ; {many(s)}{"" if s.n_risk==s.n_know else " d’entre elles"} vont en partie s’effacer d’ici le 3 juillet sans révision ; {s.n_new} seront étudiées d’ici la fin de l’année.')
 y=152;lx=36
 for col,lab in [(TEAL,'Acquis durablement : ce qui restera le 3 juillet'),(LIGHT,'Su aujourd’hui, mais oublié le 3 juillet sans révision'),(TRACK,'Reste à apprendre')]:
  rect(lx,y+1,18,8,col,r=2);txt(lx+23,y+1,lab,7.4,NAVY);lx+=23+pdfmetrics.stringWidth(lab,'R',7.4)+16
 y=172;rect(36,y,523,15,NAVY,r=3)
 for x,lab,al in [(48,'COMPÉTENCE','l'),(262,'AUJOURD’HUI','l'),(443,'3 JUILLET','c'),(483,'OUBLI','c'),(531,'STATUT','c')]:txt(x,y+4,lab,6.8,'#FFFFFF','B',al)
 y+=17;BX,BW=262,128;prio={code:k+1 for k,(code,_) in enumerate(s.priorities)}
 for d,dname in DOMAINS.items():
  rows=[cc for cc in s.comps if cc['domain']==d];st=[cc for cc in rows if cc['studied']]
  y+=3;txt(36,y+3,dname.upper(),8.5,TEAL,'B')
  txt(559,y+4,f'aujourd’hui {np.mean([cc["m"] for cc in st]):.0f} %  •  le 3 juillet sans révision {np.mean([cc["durable"] for cc in st]):.0f} %  •  {len(rows)-len(st)} à venir',7,MUTED,'R','r');y+=16
  for i,cc in enumerate(rows):
   rh=12.4
   if i%2==0:rect(36,y,523,rh,PALE)
   if cc['code'] in prio:
    c.setFillColor(HexColor(NAVY));c.circle(42,H-y-rh/2,4.4,fill=1,stroke=0);txt(42,y+3.6,str(prio[cc['code']]),6.2,'#FFFFFF','B','c')
   txt(49,y+3.4,cc['code'],6.8,MUTED,'B')
   lab=SHORT.get(cc['code'],cc['label']);fs=7.3
   while pdfmetrics.stringWidth(lab,'R',fs)>200 and fs>6.4:fs-=.1
   txt(74,y+3.2,lab,fs,NAVY,'B' if cc['code'] in prio else 'R')
   if cc['studied']:
    rect(BX,y+2.4,BW,7.6,TRACK,r=2);rect(BX,y+2.4,BW*cc['m']/100,7.6,LIGHT,r=2);rect(BX,y+2.4,BW*cc['durable']/100,7.6,TEAL,r=2)
    txt(BX+BW+4,y+3.3,f'{cc["m"]} %',6.8,MUTED)
    txt(443,y+3.2,f'{cc["durable"]} %',7.3,TEAL,'B','c');txt(483,y+3.3,f'−{cc["lost"]}' if cc['lost'] else '0',7,GOLD if cc['lost']>=15 else MUTED,'B' if cc['lost']>=15 else 'R','c')
   else:
    c.setDash(2,2);rect(BX,y+2.4,BW,7.6,'#FFFFFF',r=2,stroke=GREY,lw=.5);c.setDash()
    txt(BX+BW/2,y+3.4,f'prévu en {MONTHS[cc["month"]]}',6.6,MUTED,'I','c');txt(443,y+3.3,'—',7,GREY,'R','c');txt(483,y+3.3,'—',7,GREY,'R','c')
   lab,col=STATUS[cc['status']];rect(506,y+1.6,50,9.2,col,r=4.6);txt(531,y+3.4,lab,6.3,'#FFFFFF','B','c')
   y+=rh
 assert y<795,y
 c.showPage()
 # Page 3 : le plan d'action.
 header(3,'Ton plan jusqu’au 3 juillet',f'Où tu peux arriver le dernier jour de l’année, selon ce que tu fais d’ici là. Les estimations s’appuient sur ta progression et sur celle d’élèves au profil comparable.')
 chart(s.file+'_trajectoire',36,150,523,212)
 para(36,366,f'<b>Lecture.</b> Sans révision, une partie de ce que tu sais s’efface : même avec les nouveaux chapitres, tu finirais l’année vers <b>{s.no:.0f} %</b>{", moins qu’aujourd’hui" if s.no<s.today else ""}. À ton rythme actuel : {s.pace:.0f} %. <b>Avec ton plan d’action : {s.plan:.0f} %</b>, soit {s.plan-s.today:.0f} points de plus qu’aujourd’hui.',523,8.6,NAVY,leading=11.4)
 txt(36,402,'Tes 3 priorités',13,TEAL,'B')
 cw,gap=167,11;cards=[]
 for k,((code,kind),pr) in enumerate(zip(s.priorities,T['prio'])):
  cc=s.by[code];dates=BUILD_DATES if kind=='build' else SAVE_DATES
  head='À RENFORCER' if kind=='build' and cc['m']<50 else 'À FIABILISER' if kind=='build' else 'À SAUVER DE L’OUBLI'
  body=f'<b>Pourquoi.</b> {pr["why"]}<br/><b>Comment.</b> {pr["how"]}<br/><b>Quand.</b> {" • ".join(day(d) for d in dates)}'
  cards.append((code,kind,head,cc,body,measure(body,cw-20,7.9,NAVY,10.4)[1]))
 ch=max(cd[-1] for cd in cards)+104;y0=424;assert y0+ch<668,ch
 for k,(code,kind,head,cc,body,_) in enumerate(cards):
  x=36+k*(cw+gap);col=RED if head=='À RENFORCER' else GOLD if kind=='save' else TEAL
  rect(x,y0,cw,ch,'#FFFFFF',r=8,stroke=LINE);rect(x,y0,cw,22,col,r=8);rect(x,y0+12,cw,10,col)
  c.setFillColor(HexColor('#FFFFFF'));c.circle(x+16,H-y0-11,7,fill=1,stroke=0);txt(x+16,y0+6.5,str(k+1),8.5,col,'B','c');txt(x+28,y0+7.5,head,7.6,'#FFFFFF','B')
  hh=para(x+10,y0+30,f'<b>{cc["label"]}</b> <font color="{MUTED}">({code})</font>',cw-20,9,NAVY,11.2)
  yy=y0+34+hh;target=s.targets[code];now=cc['m'] if kind=='build' else cc['durable']
  txt(x+10,yy,f'{now} %',15,MUTED,'B');w1=pdfmetrics.stringWidth(f'{now} %','B',15);txt(x+14+w1,yy+1,'→',13,MUTED);txt(x+32+w1,yy,f'{target} %',15,col,'B')
  txt(x+10,yy+19,('aujourd’hui → le 3 juillet' if kind=='build' else 'le 3 juillet : sans → avec révisions'),7,MUTED)
  para(x+10,yy+33,body,cw-20,7.9,NAVY,10.4)
 y=y0+ch+12;bh=792-y
 rect(36,y,250,bh,'#FFFFFF',r=8,stroke=GOLD,lw=1.2);txt(48,y+10,'TON DÉFI',9,GOLD,'B')
 para(48,y+26,T['defi'][0],226,8.6,NAVY,11.2)
 c.saveState();c.translate(274,H-(y+bh-10));c.rotate(180);c.setFillColor(HexColor(MUTED));c.setFont('I',6.4)
 ans='Réponse : '+T['defi'][1];c.drawString(0,0,ans) if pdfmetrics.stringWidth(ans,'I',6.4)<226 else None
 if pdfmetrics.stringWidth(ans,'I',6.4)>=226:
  words=ans.split();line='';lines=[]
  for wd in words:
   if pdfmetrics.stringWidth(line+' '+wd,'I',6.4)>226:lines.append(line);line=wd
   else:line=(line+' '+wd).strip()
  lines.append(line)
  for j,l in enumerate(lines):c.drawString(0,-j*8,l)
 c.restoreState();txt(48,y+bh-30,'Réponse à l’envers : retourne la feuille !',6.4,GOLD,'I')
 rect(298,y,261,bh,PALE,r=8);txt(310,y+10,'POUR LES PARENTS',9,TEAL,'B');para(310,y+26,T['parents'],237,8.4,NAVY,11)
 c.showPage();c.save()
 return dict(name=s.name,today=round(s.today,1),no=round(s.no,1),pace=round(s.pace,1),plan=round(s.plan,1),know=s.n_know,solid=s.n_solid,risk=s.n_risk,
  statuses={k:sum(cc['status']==k for cc in s.comps) for k in STATUS},targets=s.targets,rank_delta=s.rank,rank_mean=s.meanrank)

if __name__=='__main__':
 for spec in STUDENTS:print(json.dumps(render(Student(spec)),ensure_ascii=False))
