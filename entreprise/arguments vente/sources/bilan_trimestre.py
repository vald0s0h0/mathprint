"""Bilan de classe du professeur (3e B, mathématiques, T2) : les compétences d'abord, les notes ensuite."""
import os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/mathprint-mpl')
os.environ.setdefault('XDG_CACHE_HOME','/private/tmp/mathprint-cache')
from pathlib import Path
from datetime import date,timedelta
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
ROOT=Path(__file__).resolve().parents[1];TMP=ROOT.parents[1]/'tmp/pdfs/bilan';TMP.mkdir(parents=True,exist_ok=True)
OUT=ROOT/'04_bilan_trimestriel_3eB_PROFESSEUR.pdf'
for n,f in [('R','Arial.ttf'),('B','Arial Bold.ttf'),('I','Arial Italic.ttf')]:pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
pdfmetrics.registerFontFamily('R',normal='R',bold='B',italic='I',boldItalic='B')
NAVY='#183449';TEAL='#087F83';BLUE='#4F70AE';MUTED='#637888';RED='#BC4855';GOLD='#C58935';PALE='#EFF5F7';LINE='#D8E3E8';LIGHT='#A9D9D6';TRACK='#E6EDF0';GREY='#A8BAC5'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':LINE,'axes.labelcolor':NAVY,'xtick.color':MUTED,'ytick.color':MUTED,'text.color':NAVY,'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white'})
NAMES=['Aïcha Traoré','Alexandre N’Guessan','Aminata Diabaté','Arthur Moreau','Aya Kouassi','Baptiste Laurent','Camille Bernard','Cheick Coulibaly','Chloé Martin','David Yao','Élise Durand','Fatoumata Koné','Gabriel Assi','Inès Bamba','Issa Ouattara','Jade Lefèvre','Koffi Kouadio','Léa Kouamé','Louis Mercier','Mariam Touré','Moussa Diallo','Nadia Bakayoko','Nathan Amian','Noémie Petit','Oumar Sanogo','Paul Adou','Rokia Soro','Sarah Dembélé','Souleymane Cissé','Yasmine Fofana']
# Niveau de départ actuel et écart latent au trimestre précédent, hétérogènes.
LEVEL=[7.4,5.8,4.0,8.0,6.0,7.0,8.4,4.2,6.8,5.5,8.6,5.0,6.0,7.5,3.6,7.7,5.5,6.8,8.3,6.1,4.1,7.2,5.8,8.0,4.4,6.8,5.7,7.6,3.8,7.4]
GAIN=[.7,.2,.4,.5,1.0,-.7,.3,.9,-.5,.6,.2,1.2,.4,.7,.1,.3,-.8,.8,.3,.7,.2,.8,-.6,.6,.8,.3,1.1,.3,-.3,.9]
SKILLS=['Fractions','Puissances','Calcul littéral','Équations','Pythagore','Thalès','Justifier','Vérifier']
TODAY=date(2026,3,13);END=date(2026,7,3);CHECK=TODAY+timedelta(14)
MONTHS=['janv.','févr.','mars','avril','mai','juin','juil.']
def day(d):return f'{d.day} {MONTHS[d.month-1]}'

# ---------- Notes des deux trimestres (12 évaluations chacun) ----------
rng=np.random.default_rng(27102026);records=[]
for i,name in enumerate(NAMES):
 base=LEVEL[i];offsets=np.array([.1,1.35,-.45,-.15,.65,-1.0])+rng.normal(0,.65,6)
 offsets[(i*5)%6]-=.9
 seqgain=.20+GAIN[i]*.32
 current=[];previous=[];targets=[];prior_targets=[];reason=[];prior_reason=[];verify=[];prior_verify=[]
 for j in range(12):
  k=j%6;stage=j//6
  t=np.clip(np.round((base+offsets[k]+stage*seqgain+rng.normal(0,.35))*2)/2,0,10)
  r=np.clip(np.round((base*.5-.55+stage*.22+rng.normal(0,.35))*2)/2,0,5)
  v=np.clip(np.round((base*.5-.45+stage*.15+rng.normal(0,.4))*2)/2,0,5)
  pt=np.clip(np.round((base-GAIN[i]+offsets[k]+rng.normal(0,.4))*2)/2,0,10)
  pr=np.clip(np.round(((base-GAIN[i])*.5-.55+rng.normal(0,.35))*2)/2,0,5)
  pv=np.clip(np.round(((base-GAIN[i])*.5-.45+rng.normal(0,.4))*2)/2,0,5)
  targets.append(t);prior_targets.append(pt);reason.append(r);prior_reason.append(pr);verify.append(v);prior_verify.append(pv)
  current.append(t+r+v);previous.append(pt+pr+pv)
 skill=[np.mean([targets[k],targets[k+6]])*10 for k in range(6)]+[np.mean(reason)*20,np.mean(verify)*20]
 prior_skill=[np.mean([prior_targets[k],prior_targets[k+6]])*10 for k in range(6)]+[np.mean(prior_reason)*20,np.mean(prior_verify)*20]
 avg=float(np.mean(current));prev=float(np.mean(previous))
 records.append(dict(id=i+1,name=name,t2=[float(x) for x in current],t1=[float(x) for x in previous],skills=[float(x) for x in skill],prior_skills=[float(x) for x in prior_skill],mean=avg,previous=prev,delta=avg-prev))
A=np.array([r['t2'] for r in records]);B=np.array([r['t1'] for r in records]);means=A.mean(1);prevs=B.mean(1);deltas=means-prevs

# ---------- Compétences : référentiel 3e de la plateforme, plus deux compétences transversales ----------
# (code, libellé court, libellé complet, domaine de notes associé, décalage, erreurs observées : trace, geste, vérification)
COMPS=[
 ('A2.4','Fractions','Calculer avec des fractions',0,0,[('additionne les dénominateurs : 1/3 + 1/4 = 2/7','représenter les deux fractions sur une même unité, puis passer au dénominateur commun','2/5 + 1/3 = 11/15'),('affirme que 3/8 > 3/5 parce que 8 > 5','comparer avec deux bandes de même longueur et verbaliser le rôle du dénominateur','3/8 < 3/5')]),
 ('A2.2','Puissances','Calculer avec des puissances',1,0,[('multiplie les exposants dans 3² × 3³','revenir une fois au produit de facteurs avant de formuler la règle','3² × 3³ = 3 × 3 × 3 × 3 × 3'),('confond (−3)² et −3²','écrire les deux produits en toutes lettres en gardant les parenthèses','(−4)² = 16 et −4² = −16')]),
 ('A3.2','Développer','Développer un produit',2,5,[('oublie le double produit dans (x − 3)²','décomposer (x − 3)(x − 3) avec un tableau à quatre cases','(x − 2)² = x² − 4x + 4'),('ne change que le premier signe dans 5 − (2x − 3)','réécrire la soustraction d’une parenthèse comme un produit par −1','5 − (2x − 3) = 8 − 2x')]),
 ('A3.3','Factoriser','Factoriser une somme ou une différence',2,-8,[('écrit 4x² − 9 = (4x − 3)(4x + 3)','identifier a et b à l’écrit avant de choisir a² − b²','4x² − 9 = (2x − 3)(2x + 3)'),('ne repère pas le facteur commun (x + 1)','souligner le facteur commun avant d’écrire quoi que ce soit','3x(x + 1) + 5(x + 1) = (x + 1)(3x + 5)')]),
 ('A4.1','Équations','Résoudre une équation du premier degré',3,6,[('modifie un membre sans modifier l’autre','une ligne par opération, en nommant l’opération appliquée aux deux membres','3x + 5 = 20 donne x = 5'),('ne teste pas la solution trouvée','imposer une ligne de vérification avant la conclusion','2x − 7 = 5 : tester x = 6')]),
 ('A4.2','Équation produit','Résoudre une équation produit',3,-6,[('divise par x et perd la solution 0','comparer division et produit nul sur le même exemple','x(x − 4) = 0 donne 0 et 4'),('ne trouve qu’une solution sur deux','écrire la règle du produit nul, puis traiter les deux cas','(x + 2)(x − 5) = 0 donne −2 et 5')]),
 ('C3.1','Pythagore','Utiliser l’égalité de Pythagore',4,0,[('prend un côté de l’angle droit pour l’hypoténuse','repérer l’angle droit et colorier le côté opposé avant tout calcul','6² + 8² = 10²'),('soustrait les longueurs au lieu des carrés','écrire l’égalité des carrés avant de remplacer par les nombres','si c = 13 et a = 5, alors b = 12')]),
 ('C2.1','Thalès : longueurs','Calculer des longueurs avec le théorème de Thalès',5,3,[('inverse un seul rapport dans l’égalité de Thalès','colorier les côtés homologues et écrire les rapports dans le même sens','3/6 = 4/8'),('applique Thalès sans établir le parallélisme','séparer sur la copie « hypothèses » et « égalité de rapports »','nommer le parallèle avant les rapports')]),
 ('C2.2','Thalès : parallèles','Reconnaître des droites parallèles',5,-7,[('compare les rapports sans vérifier l’ordre des points','trame en trois lignes : rapports, alignement dans le même ordre, conclusion','réciproque de Thalès sur une figure donnée'),('conclut au parallélisme sur la seule figure','exiger le calcul des deux rapports avant toute conclusion','AM/AB = AN/AC donc (MN) // (BC)')]),
 ('T.1','Justifier','Justifier une démarche (compétence transversale)',6,0,[('valide une affirmation générale avec deux exemples','demander un exemple, puis une écriture qui marche dans tous les cas','2n + 1 + 2n + 3 = 4(n + 1)'),('cite un théorème sans en vérifier les hypothèses','trame : données, hypothèses, théorème, conclusion, puis la retirer','nommer l’angle droit avant Pythagore')]),
 ('T.2','Vérifier','Vérifier un résultat (compétence transversale)',7,0,[('ne contrôle ni l’unité ni l’ordre de grandeur','relecture en deux questions : quelle unité ? quelle taille plausible ?','comparer le résultat à une estimation'),('accepte une longueur incompatible avec la figure','demander un encadrement avant de valider la valeur','l’hypoténuse est le plus long côté')]),
]
NC=len(COMPS)
SL={'Fractions':'les fractions','Puissances':'les puissances','Développer':'le développement','Factoriser':'la factorisation','Équations':'les équations','Équation produit':'l’équation produit','Pythagore':'Pythagore','Thalès : longueurs':'Thalès (longueurs)','Thalès : parallèles':'Thalès (parallèles)','Justifier':'la justification','Vérifier':'la vérification'}
rng2=np.random.default_rng(13032026)
for i,r in enumerate(records):
 cs=[]
 for code,short,label,sk,off,traces in COMPS:
  m2=float(np.clip(round(r['skills'][sk]+off+rng2.normal(0,5)),3,99));m1=float(np.clip(round(r['prior_skills'][sk]+off+rng2.normal(0,5)),3,99))
  # Part de la maîtrise encore disponible le 3 juillet sans révision.
  R=float(np.clip(.38+.54*(m2/100)**1.1+.04*(LEVEL[i]-6)/2+rng2.normal(0,.03),.25,.95));dur=round(m2*R)
  st='solid' if dur>=60 else 'review' if m2>=50 else 'build'
  cs.append(dict(code=code,short=short,label=label,m1=m1,m2=m2,dur=dur,lost=m2-dur,status=st,traces=traces))
 r['comp']=cs;r['m1']=np.mean([c['m1'] for c in cs]);r['m2']=np.mean([c['m2'] for c in cs]);r['dur']=np.mean([c['dur'] for c in cs])
 r['k1']=sum(c['m1']>=50 for c in cs);r['k2']=sum(c['m2']>=50 for c in cs);r['solid']=sum(c['status']=='solid' for c in cs);r['dm']=r['m2']-r['m1']
 r['profile']='Soutien prioritaire' if r['m2']<45 else 'Vigilance' if r['dm']<=-3 else 'Consolidation' if r['m2']<70 else 'Approfondissement'
PCOL={'Soutien prioritaire':RED,'Vigilance':GOLD,'Consolidation':BLUE,'Approfondissement':TEAL}
STCOL={'solid':TEAL,'review':GOLD,'build':RED};STLAB={'solid':'Solide','review':'À réviser','build':'À renforcer'}
(ROOT/'sources/donnees_bilan_3eB.json').write_text(json.dumps(dict(scope='3e B, T2 2025-2026 comparé à T1',records=[{k:v for k,v in r.items() if k!='comp'}|{'comp':[{k:v for k,v in c.items() if k!='traces'} for c in r['comp']]} for r in records]),ensure_ascii=False,indent=1,default=float))

def fr(n,d=1):return f'{n:.{d}f}'.replace('.',',')
def signed(n,d=1):return fr(0,d) if round(abs(n),d)==0 else ('+' if n>=0 else '−')+fr(abs(n),d)
def pct(n):return f'{n:.0f} %'
def pts(n):return ('+' if n>=0 else '−')+f'{abs(n):.0f} pts'

# ---------- Indicateurs de classe ----------
CM2=np.array([[c['m2'] for c in r['comp']] for r in records]);CM1=np.array([[c['m1'] for c in r['comp']] for r in records]);CD=np.array([[c['dur'] for c in r['comp']] for r in records])
ST=np.array([[c['status'] for c in r['comp']] for r in records])
known1=(CM1>=50).mean()*100;known2=(CM2>=50).mean()*100;solid=(ST=='solid').mean()*100;forget=(CM2-CD).sum()/CM2.sum()*100
nbuild=(ST=='build').sum(0);nsolid=(ST=='solid').sum(0);nreview=(ST=='review').sum(0)
order_weak=list(np.argsort(-nbuild));best=int(np.argmax(nsolid+.01*CM2.mean(0)))
lost_known=[np.mean([r['comp'][j]['lost'] for r in records if r['comp'][j]['m2']>=50] or [0]) for j in range(9)];risk_order=list(np.argsort(lost_known)[::-1])
prof_count={p:sum(r['profile']==p for r in records) for p in PCOL}
up=sorted(records,key=lambda r:-r['dm'])[:3];down=[r for r in records if r['dm']<=-3]
COPIES=len(records)*12

# ---------- Graphiques ----------
def save(fig,name):fig.savefig(TMP/(name+'.png'),dpi=220,bbox_inches='tight',pad_inches=.14);plt.close(fig)
fig,ax=plt.subplots(figsize=(10,3.3));x=np.arange(1,13)
ax.fill_between(x,np.quantile(A,.25,axis=0),np.quantile(A,.75,axis=0),color=TEAL,alpha=.12,label='Moitié centrale des élèves · T2')
ax.plot(x,B.mean(0),color=MUTED,ls='--',lw=1.8,marker='o',ms=4,label='T1 · moyenne')
ax.plot(x,A.mean(0),color=TEAL,lw=2.5,marker='o',ms=5,label='T2 · moyenne')
ax.axvline(6.5,color=GOLD,lw=1,ls=':');ax.text(3.5,19.1,'SÉQUENCE 1',ha='center',fontsize=9,color=MUTED);ax.text(9.5,19.1,'SÉQUENCE 2',ha='center',fontsize=9,color=MUTED)
ax.set(ylim=(0,20),xlim=(.7,12.3),ylabel='Note / 20',yticks=[0,5,10,15,20],xticks=x,xticklabels=[f'E{i}' for i in x]);ax.grid(axis='y',alpha=.25);ax.legend(ncol=3,loc='lower center',bbox_to_anchor=(.5,-.36),frameon=False,fontsize=9);save(fig,'trajectoire')
fig,ax=plt.subplots(figsize=(10,2.4));bins=[0,8,10,12,14,16,20.01];labels=['< 8','8 à < 10','10 à < 12','12 à < 14','14 à < 16','16 à 20'];xx=np.arange(6)
for vals,off,col,label in [(prevs,-.19,GREY,'T1'),(means,.19,TEAL,'T2')]:
 counts=np.histogram(vals,bins=bins)[0];bars=ax.bar(xx+off,counts,.36,color=col,label=label)
 for b_,n_ in zip(bars,counts):ax.text(b_.get_x()+b_.get_width()/2,n_+.12,str(n_),ha='center',fontsize=10)
ax.set(xticks=xx,xticklabels=labels,ylabel='Élèves',ylim=(0,12));ax.grid(axis='y',alpha=.15);ax.legend(frameon=False,ncol=2);save(fig,'distribution')
# Qui maîtrise quoi : élèves × compétences.
cmap=LinearSegmentedColormap.from_list('mp',['#BC4855','#E9B7A0','#F6E7C8','#BFE0DC','#087F83'])
fig,ax=plt.subplots(figsize=(10,10.6));ax.imshow(CM2,cmap=cmap,vmin=15,vmax=95,aspect='auto')
for i in range(len(records)):
 for j in range(NC):
  v=CM2[i,j];ax.text(j,i,f'{v:.0f}',ha='center',va='center',fontsize=7.6,color='white' if v<32 or v>82 else NAVY)
  if records[i]['comp'][j]['m2']>=50 and records[i]['comp'][j]['lost']>=15:ax.scatter(j+.36,i-.3,marker='v',s=13,color=NAVY,zorder=4)
ax.set_xticks(range(NC));ax.set_xticklabels([c[1] for c in COMPS],rotation=35,ha='left',fontsize=8.6);ax.xaxis.tick_top()
ax.set_yticks(range(len(records)));ax.set_yticklabels([f'{r["id"]:02d}  {r["name"]}' for r in records],fontsize=8.4)
ax.tick_params(length=0)
for sp in ax.spines.values():sp.set_visible(False)
ax.axvline(8.5,color='white',lw=3)
for yv in np.arange(4.5,30,5):ax.axhline(yv,color='white',lw=1.6)
save(fig,'qui_maitrise')
# Progression de chaque élève : maîtrise moyenne T1 → T2, et ce qui restera le 3 juillet.
fig,ax=plt.subplots(figsize=(10,4.3));srt=sorted(records,key=lambda r:r['m2'])
for k,r in enumerate(srt):
 col=TEAL if r['dm']>=2 else RED if r['dm']<=-2 else GREY
 ax.annotate('',xy=(k,r['m2']),xytext=(k,r['m1']),arrowprops=dict(arrowstyle='-|>',color=col,lw=1.7,mutation_scale=9),zorder=3)
 ax.scatter(k,r['m1'],s=14,color='white',edgecolor=col,lw=1,zorder=4);ax.plot([k-.3,k+.3],[r['dur']]*2,color=GOLD,lw=2,zorder=2)
 ax.text(k,max(r['m2'],r['m1'])+2.2,f'{r["id"]}',ha='center',fontsize=7.4,color=col,fontweight='bold')
for yv,lab in [(45,'seuil de soutien prioritaire'),(70,'seuil d’approfondissement')]:ax.axhline(yv,color=MUTED,lw=.8,ls=':');ax.text(-.6,yv+1.2,lab,ha='left',fontsize=7.8,color=MUTED)
ax.set(xlim=(-.8,29.8),ylim=(0,100),xticks=[],ylabel='Maîtrise moyenne des compétences (%)');ax.grid(axis='y',alpha=.15)
ax.legend(handles=[plt.Line2D([],[],marker='o',color=MUTED,mfc='white',ls='',label='fin du 1er trimestre'),plt.Line2D([],[],marker='>',color=TEAL,ls='-',label='progression jusqu’au 2e trimestre'),plt.Line2D([],[],color=RED,marker='>',ls='-',label='recul'),plt.Line2D([],[],color=GOLD,lw=2.5,label='ce qui restera le 3 juillet sans révision')],loc='upper left',frameon=False,fontsize=8.2,ncol=2)
save(fig,'progression')

# ---------- Mise en page ----------
W,H=595.2756,841.8898;TOTAL=19
c=canvas.Canvas(str(OUT),pagesize=(W,H));c.setTitle('MathPrint | Bilan de classe | 3e B | T2 2025-2026');c.setAuthor('MathPrint')
def rect(x,y,w,h,col,r=0,stroke=None,lw=.6):
 c.setFillColor(HexColor(col));c.setStrokeColor(HexColor(stroke or col));c.setLineWidth(lw);c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=bool(stroke))
def txt(x,y,t,fs=10,col=NAVY,font='R',align='l'):
 c.setFillColor(HexColor(col));c.setFont(font,fs);{'l':c.drawString,'c':c.drawCentredString,'r':c.drawRightString}[align](x,H-y-fs,t)
def fit(t,w,fs,font='R',mn=5):
 while pdfmetrics.stringWidth(t,font,fs)>w and fs>mn:fs-=.1
 return fs
def measure(t,w,fs=10,col=NAVY,leading=None):
 p=Paragraph(t,ParagraphStyle('p',fontName='R',fontSize=fs,leading=leading or fs*1.38,textColor=HexColor(col)));return p,p.wrap(w,3000)[1]
def para(x,y,t,w=523,fs=10,col=NAVY,leading=None,limit=794):
 p,h=measure(t,w,fs,col,leading);assert y+h<limit,(t[:70],y,h);p.drawOn(c,x,H-y-h);return h
def dot(x,y,r,col):c.setFillColor(HexColor(col));c.circle(x,H-y,r,fill=1,stroke=0)
PAGE=[0];TITLES={1:'L’essentiel du trimestre',2:'Les compétences de la classe',3:'Qui maîtrise quoi',4:'La progression de chaque élève',5:'Les notes du trimestre',6:'Groupes de besoin et plan d’action',7:'Bilans individuels',8:'Méthode'}
PLANNED={1:2,2:3,3:4,4:5,5:6,6:7,7:8,8:19};PAGEOF={}
def footer():txt(36,807,'3e B • Mathématiques • T2 2025-2026 • Bilan du professeur',7,MUTED);txt(559,807,f'{PAGE[0]:02d}/{TOTAL}',8,MUTED,'R','r')
def topbar(tag='REGARD PROFESSEUR  /  3e B'):
 PAGE[0]+=1;rect(0,0,W,7,TEAL);txt(36,22,'mathprint',18,NAVY,'B');txt(559,28,tag,8,TEAL,'B','r');c.setStrokeColor(HexColor(LINE));c.setLineWidth(.5);c.line(36,H-42,559,H-42);footer()
def header(num,what=None,use=None):
 topbar();PAGEOF[num]=PAGE[0];c.bookmarkPage(f'p{PAGE[0]}');c.addOutlineEntry(f'{num}. {TITLES[num]}',f'p{PAGE[0]}',0)
 txt(36,56,f'SECTION {num}',8,TEAL,'B');txt(36,68,TITLES[num],fit(TITLES[num],523,23,'B'),NAVY,'B')
 if not what:return 104
 body=f'<b>De quoi s’agit-il ?</b> {what}<br/><b>À quoi ça sert ?</b> {use}';_,h=measure(body,499,8.6,MUTED,11.4)
 rect(36,100,523,h+16,PALE,r=6);rect(36,106,3,h+4,TEAL,r=1);para(48,108,body,499,8.6,MUTED,11.4);return 100+h+30
def chart(name,x,y,w,h):c.drawImage(str(TMP/(name+'.png')),x,H-y-h,width=w,height=h,mask='auto',preserveAspectRatio=True,anchor='c')
def metric(x,y,w,value,label,detail,col=TEAL,h=90):
 rect(x,y,w,h,PALE,r=8);fs=fit(value,w-26,24,'B');txt(x+12,y+11,value,fs,col,'B');txt(x+12,y+42,label,9,NAVY,'B');para(x+12,y+58,detail,w-24,7.8,MUTED,10)
def section(y,title,body,col=TEAL,fs=9.3):txt(36,y,title,12,col,'B');return y+19+para(36,y+19,body,523,fs,leading=fs*1.36)
def twobar(x,y,w,h,m,d,t1=None):
 rect(x,y,w,h,TRACK,r=h/3);rect(x,y,w*m/100,h,LIGHT,r=h/3);rect(x,y,w*d/100,h,TEAL,r=h/3)
 if t1 is not None:c.setStrokeColor(HexColor(NAVY));c.setLineWidth(1.1);c.line(x+w*t1/100,H-y+1.5,x+w*t1/100,H-y-h-1.5)

# ---------- Page de garde ----------
PAGE[0]+=1
rect(0,0,W,388,NAVY);rect(0,388,W,8,TEAL)
txt(48,52,'mathprint',22,'#FFFFFF','B');txt(547,58,'REGARD PROFESSEUR',8.5,LIGHT,'B','r')
txt(48,150,'3E B  •  MATHÉMATIQUES',11,LIGHT,'B');txt(48,172,'Bilan de classe',36,'#FFFFFF','B')
txt(48,226,'2e trimestre 2025-2026',15,'#FFFFFF');txt(48,248,'compétences, progrès et plan d’action',15,LIGHT)
txt(48,340,f'Bilan arrêté au vendredi 13 mars 2026  •  Point de repère : le dernier jour de l’année, vendredi 3 juillet 2026',9,LIGHT)
for i,(v,l) in enumerate([(f'{len(records)}','élèves'),('12','évaluations au T2'),(f'{NC}','compétences suivies'),(f'{COPIES}','copies corrigées'),(f'≈ {COPIES*3//60} h','de correction évitées')]):
 x=48+i*102;txt(x,420,v,19,TEAL,'B');txt(x,444,l,8.5,MUTED)
txt(48,478,'SOMMAIRE',9,TEAL,'B');c.setStrokeColor(HexColor(LINE));c.line(48,H-494,547,H-494)
for num in range(1,9):
 col_x=48 if num<=4 else 306;yy=506+((num-1)%4)*28
 txt(col_x,yy,f'{num:02d}',9.5,TEAL,'B');txt(col_x+24,yy,TITLES[num],9.5,NAVY);txt(col_x+238,yy+.5,f'p. {PLANNED[num]}',8.5,MUTED,'R','r')
rect(48,752,499,30,PALE,r=6);txt(60,763,'Document personnel du professeur : il contient des données nominatives d’élèves. Ne pas diffuser en dehors de l’équipe pédagogique.',7.6,MUTED,'I')
c.showPage()

# ---------- 1. L'essentiel ----------
y=header(1,f'Les résultats de la 3e B au 2e trimestre : {len(records)} élèves, 12 évaluations, {NC} compétences suivies (9 du programme de 3e et 2 transversales). Les compétences passent avant les notes : elles disent précisément ce que les élèves savent faire, et ce qu’ils risquent d’oublier d’ici la fin de l’année.','Savoir en deux minutes où en est la classe, ce qui progresse, ce qui bloque, et par quoi commencer la semaine prochaine.')
metric(36,y,166,pct(known2),'Compétences acquises',f'élève par élève, contre {pct(known1)} au 1er trimestre ({pts(known2-known1)})')
metric(215,y,166,pct(solid),'Solides jusqu’au 3 juillet',f'le reste de ce qui est acquis s’effacera en partie sans révision ({pct(forget)} de perte prévue)',BLUE)
metric(394,y,165,fr(means.mean())+'/20','Moyenne des notes',f'1er trimestre : {fr(prevs.mean())}/20 • {signed(deltas.mean())} point')
y+=100
metric(36,y,166,f'{sum(r["dm"]>=5 for r in records)}/30','Élèves en nette progression','au moins +5 points de maîtrise moyenne des compétences depuis le 1er trimestre')
metric(215,y,166,f'{prof_count["Soutien prioritaire"]}/30','En soutien prioritaire','maîtrise moyenne sous 45 % : une priorité à la fois, en petit groupe',RED)
metric(394,y,165,f'{len(down)}/30','En recul','au moins 3 points de maîtrise perdus : un entretien court avant toute décision',GOLD)
y+=106;txt(36,y,'Ce qu’il faut retenir',13,TEAL,'B');y+=22
wk=order_weak[0];wk2=order_weak[1];bc=COMPS[best];rk=COMPS[risk_order[0]];rk2=COMPS[risk_order[1]]
dominant=COMPS[wk][5][int(np.argmax([sum(1 for i,r in enumerate(records) if r['comp'][wk]['status']=='build' and i%2==t) for t in range(2)]))]
facts=[
 f'<b>La classe progresse en compétences.</b> {pct(known2)} des compétences évaluées sont acquises, contre {pct(known1)} au 1er trimestre. La moyenne des notes suit le même mouvement ({fr(prevs.mean())} → {fr(means.mean())}/20).',
 f'<b>Point d’appui : {bc[2].lower()}.</b> {nsolid[best]} élèves sur 30 la maîtrisent durablement. C’est le bon support pour relancer les élèves fragiles sur des réussites.',
 f'<b>Priorité : {COMPS[wk][2].lower()}</b> ({nbuild[wk]} élèves à renforcer), puis {COMPS[wk2][2].lower()} ({nbuild[wk2]}). Erreur la plus fréquente : l’élève {dominant[0]}.',
 f'<b>Le risque d’oubli est réel.</b> Sans révision, {pct(forget)} de ce que les élèves savent aujourd’hui aura disparu le 3 juillet. Les plus exposées : {SL[rk[1]]} et {SL[rk2[1]]}, qui ne sont pas encore ancrées.',
 f'<b>Les élèves.</b> {prof_count["Soutien prioritaire"]} en soutien prioritaire, {len(down)} en recul. Les plus fortes progressions : {", ".join(f"{r["name"]} ({pts(r["dm"])})" for r in up)}.']
for i,f in enumerate(facts):
 rect(36,y,18,18,TEAL,r=9);txt(45,y+4,str(i+1),9,'#FFFFFF','B','c');h=para(62,y+1,f,497,8.9,leading=11.8);y+=max(h,18)+7
prio=f'Trois groupes de besoin de 15 minutes : {SL[COMPS[order_weak[0]][1]]}, {SL[COMPS[order_weak[1]][1]]} et {SL[COMPS[order_weak[2]][1]]} (section 6) • Une reprise espacée de {SL[rk[1]]} pour toute la classe, dans 1, 3 et 7 semaines • Un entretien de 5 minutes avec les {len(down)} élèves en recul • Une question de justification ou de vérification à chaque séance.'
y+=4;_,ph=measure(prio,495,8.9,'#FFFFFF',12);rect(36,y,523,ph+42,NAVY,r=8);txt(50,y+12,'PRIORITÉS DES PROCHAINES SEMAINES',9,'#FFFFFF','B');para(50,y+29,prio,495,8.9,'#FFFFFF',12)
c.showPage()

# ---------- 2. Les compétences de la classe ----------
y=header(2,'Pour chacune des 11 compétences suivies : la maîtrise moyenne de la classe aujourd’hui, la part qui restera le 3 juillet sans révision, le niveau de la fin du 1er trimestre (trait vertical), et le nombre d’élèves solides, à réviser ou à renforcer.','Voir ce qui est vraiment installé, ce qui est su mais fragile, et ce qui reste à construire, compétence par compétence.')
lx=36
for col,lab in [(TEAL,'Restera le 3 juillet'),(LIGHT,'Su aujourd’hui, oublié le 3 juillet sans révision'),(TRACK,'Reste à acquérir')]:
 rect(lx,y+1,18,8,col,r=2);txt(lx+23,y+1,lab,7.4,NAVY);lx+=23+pdfmetrics.stringWidth(lab,'R',7.4)+14
c.setStrokeColor(HexColor(NAVY));c.setLineWidth(1.1);c.line(lx+2,H-y,lx+2,H-y-10);txt(lx+8,y+1,'Fin du 1er trimestre',7.4,NAVY)
y+=20;rect(36,y,523,15,NAVY,r=3)
for xh,lab,al in [(44,'COMPÉTENCE','l'),(254,'MAÎTRISE DE LA CLASSE','l'),(428,'T2','c'),(462,'3 JUIL.','c'),(521,'ÉLÈVES','c')]:txt(xh,y+4.2,lab,6.6,'#FFFFFF','B',al)
y+=18
for j,(code,short,label,*_r) in enumerate(COMPS):
 if j==9:c.setStrokeColor(HexColor(LINE));c.setLineWidth(.8);c.line(36,H-y+2,559,H-y+2);y+=4
 rh=27
 if j%2==0:rect(36,y,523,rh,PALE,r=3)
 txt(44,y+5,label if len(label)<52 else label[:50]+'…',fit(label,200,8.2,'B'),NAVY,'B');txt(44,y+16,code,6.8,MUTED)
 twobar(254,y+9,160,9,CM2[:,j].mean(),CD[:,j].mean(),CM1[:,j].mean())
 txt(428,y+9,pct(CM2[:,j].mean()),8.4,NAVY,'B','c');txt(462,y+9,pct(CD[:,j].mean()),8.4,TEAL,'B','c')
 for k_,(n_,col) in enumerate([(nsolid[j],TEAL),(nreview[j],GOLD),(nbuild[j],RED)]):
  rect(487+k_*24,y+6,21,14,col,r=4);txt(497.5+k_*24,y+9.5,str(n_),7.6,'#FFFFFF','B','c')
 y+=rh+2
txt(556,y+2,'solides • à réviser • à renforcer',6.4,MUTED,'R','r');y+=18
gain=CM2.mean(0)-CM1.mean(0);gbest=int(np.argmax(gain[:9]))
y=section(y,'Analyse',f'La plus forte progression du trimestre porte sur <b>{COMPS[gbest][2].lower()}</b> ({pts(gain[gbest])}). <b>{COMPS[wk][2]}</b> reste la compétence la plus fragile : {nbuild[wk]} élèves sont sous 50 % de maîtrise. L’écart entre la barre claire et la barre foncée montre ce qui est su mais pas encore ancré : il est le plus grand pour <b>{SL[rk[1]]}</b> et <b>{SL[rk2[1]]}</b>, chapitres récents qu’il faudra reprendre de façon espacée. Les deux compétences transversales (justifier, vérifier) restent en retrait : une question de justification à chaque séance les fera progresser sur tous les chapitres.',fs=9)
c.showPage()

# ---------- 3. Qui maîtrise quoi ----------
y=header(3,'La maîtrise de chaque élève dans chaque compétence (en %). Rouge : à renforcer ; beige : en cours ; vert : maîtrisée. Le petit triangle signale une compétence maîtrisée aujourd’hui mais qui perdra au moins 15 points d’ici le 3 juillet sans révision.','Former des groupes de besoin en un coup d’œil (lire une colonne), repérer le profil d’un élève (lire une ligne), et savoir qui faire réviser avant l’oubli.')
chart('qui_maitrise',36,y,523,566);y+=570
nrisk=sum(1 for r in records for c_ in r['comp'] if c_['m2']>=50 and c_['lost']>=15)
para(36,y,f'<b>Lecture.</b> Une colonne rouge désigne un groupe de besoin : pour {SL[COMPS[wk][1]]}, {nbuild[wk]} élèves. {nrisk} triangles signalent des acquis à réactiver avant l’oubli. Les lignes séparées par 5 élèves facilitent la lecture.',523,8.4,MUTED)
c.showPage()

# ---------- 4. Progression de chaque élève ----------
y=header(4,'Chaque flèche est un élève (son numéro). Elle part de sa maîtrise moyenne des compétences à la fin du 1er trimestre et monte (ou descend) jusqu’au 2e trimestre. Le trait jaune indique ce qui restera le 3 juillet sans révision. Les élèves sont rangés du plus fragile au plus solide.','Voir qui progresse, qui recule, et pour qui l’écart entre aujourd’hui et le 3 juillet appelle des révisions.')
chart('progression',36,y,523,240);y+=250
y=section(y,'Lecture',f'<b>{sum(r["dm"]>=2 for r in records)} élèves progressent</b>, {(lambda n:f'{n} est stable' if n<=1 else f'{n} sont stables')(sum(abs(r['dm'])<2 for r in records))} et <b>{sum(r["dm"]<=-2 for r in records)} reculent</b>. Les plus fortes progressions sont celles de {", ".join(f"<b>{r["name"]}</b> ({pts(r["dm"])})" for r in up)}. L’écart entre la pointe de la flèche et le trait jaune est d’autant plus grand que l’élève est fragile : chez les élèves en soutien, une compétence tout juste acquise s’efface vite si elle n’est pas reprise.')+12
rows=[(p,[r for r in records if r['profile']==p]) for p in PCOL]
txt(36,y,'Les quatre profils de la classe',12,TEAL,'B');y+=20
for p,rs in rows:
 names=', '.join(f'{r["name"]} ({r["id"]})' for r in rs) or '—';_,h=measure(names,388,8,NAVY,10.4);rh=max(h+10,22)
 rect(36,y,523,rh,PALE,r=4);rect(36,y,4,rh,PCOL[p],r=2);txt(48,y+6,p,8.6,PCOL[p],'B');txt(150,y+6,f'{len(rs)} élèves',8,MUTED,'B');para(165+20,y+5,names,374,8,NAVY,10.4);y+=rh+5
y+=6
para(36,y,'Soutien prioritaire : maîtrise moyenne sous 45 %. Vigilance : recul d’au moins 3 points. Consolidation : de 45 à 70 %. Approfondissement : 70 % ou plus. Ces profils organisent l’aide ; ils ne résument pas un élève.',523,7.8,MUTED)
c.showPage()

# ---------- 5. Notes ----------
y=header(5,'Les notes des 12 évaluations du trimestre, comparées au 1er trimestre : moyenne de la classe à chaque évaluation, puis répartition des élèves par tranche de moyenne.','Garder un repère sur les notes, qui restent la référence des bulletins, en les lisant à la lumière des compétences.')
chart('trajectoire',36,y,523,212);y+=218
para(36,y,'<b>Lecture.</b> E1 à E6 et E7 à E12 reprennent les mêmes six domaines, dans le même ordre. Le ruban délimite la moitié centrale des notes.',523,8.4,MUTED);y+=26
chart('distribution',36,y,523,150);y+=160
y=section(y,'Ce que disent les notes',f'La moyenne passe de <b>{fr(prevs.mean())}</b> à <b>{fr(means.mean())}/20</b> et la médiane de {fr(np.median(prevs))} à {fr(np.median(means))}. {int((means<10).sum())} élèves restent sous 10/20. Les notes et les compétences racontent la même histoire, mais les compétences disent en plus <b>sur quoi</b> porte la difficulté et <b>ce qui risque d’être oublié</b> : c’est sur elles que s’appuie le plan d’action.')
c.showPage()

# ---------- 6. Groupes de besoin ----------
y=header(6,'Les élèves regroupés par compétence à renforcer, avec l’erreur la plus fréquente, le geste à proposer et la tâche qui vérifiera l’effet de la reprise. Puis la reprise espacée pour toute la classe et le calendrier jusqu’au 3 juillet.','Passer du constat à l’action : savoir qui prendre, sur quoi, comment, et quand vérifier.')
GROUPS=[]
for g,j in enumerate(order_weak[:3]):
 rs=[r for r in records if r['comp'][j]['status']=='build'];trace=COMPS[j][5][g%2]
 GROUPS.append((f'Groupe {g+1}',COMPS[j],rs,trace))
gcols=[RED,'#C9606B',GOLD]
for g,(gname,comp,rs,trace) in enumerate(GROUPS):
 body=f'<b>Élèves ({len(rs)}) :</b> {", ".join(r["name"] for r in rs)}.<br/><b>Erreur la plus fréquente :</b> l’élève {trace[0]}.<br/><b>Geste :</b> {trace[1][0].upper()+trace[1][1:]} ; {"deux sous-groupes de 15 minutes" if len(rs)>8 else "un groupe de 15 minutes"}, un exemple commenté, puis une tâche sans aide.<br/><b>Vérification le {day(CHECK)} :</b> {trace[2]}.'
 _,h=measure(body,446,8.2,NAVY,10.8);bh=h+30
 rect(36,y,523,bh,'#FFFFFF',r=8,stroke=LINE);rect(36,y,58,bh,gcols[g],r=8);rect(80,y,14,bh,gcols[g])
 txt(65,y+bh/2-12,gname.split()[1],18,'#FFFFFF','B','c');txt(65,y+bh/2+8,'GROUPE',6.5,'#FFFFFF','B','c')
 txt(104,y+8,comp[2],10,NAVY,'B');txt(551,y+9,f'{len(rs)} ÉLÈVES',7,gcols[g],'B','r');para(104,y+23,body,446,8.2,NAVY,10.8);y+=bh+8
top_students=[r for r in records if r['solid']>=8]
body=f'<b>Toute la classe — réviser avant l’oubli :</b> {rk[2].lower()} et {rk2[2].lower()}, sues mais pas encore ancrées. Une question de chaque dans les évaluations du {day(TODAY+timedelta(7))}, du {day(TODAY+timedelta(21))} et du {day(TODAY+timedelta(49))}.<br/><b>Pour aller plus loin ({len(top_students)} élèves solides sur au moins 8 compétences) :</b> {", ".join(r["name"] for r in top_students) or "—"}. Preuve, contre-exemple ou problème ouvert plutôt que des exercices répétitifs.'
_,h=measure(body,499,8.2,NAVY,10.8);rect(36,y,523,h+18,PALE,r=8);para(48,y+9,body,499,8.2,NAVY,10.8);y+=h+30
txt(36,y,'Calendrier des reprises jusqu’au 3 juillet',12,TEAL,'B');y+=22
x0,x1=150,545;span=(END-TODAY).days
def xd(d):return x0+(x1-x0)*(d-TODAY).days/span
c.setStrokeColor(HexColor(LINE));c.setLineWidth(1)
for mth in range(3,8):
 d=date(2026,mth,1)
 if TODAY<=d and mth<7:xx=xd(d);c.line(xx,H-y+4,xx,H-y-4*22+2);txt(xx+3,y-6,MONTHS[mth-1],7,MUTED)
txt(xd(END),y-6,'3 juillet',7,NAVY,'B','r')
lines=[(g[0]+' · '+g[1][1],gcols[k],[TODAY+timedelta(d) for d in (3,6,14,28,56)]) for k,g in enumerate(GROUPS)]+[('Toute la classe · oubli',TEAL,[TODAY+timedelta(d) for d in (7,21,49,84)])]
for k,(lab,col,ds) in enumerate(lines):
 yy=y+k*22;txt(36,yy+2,lab,fit(lab,108,7.6,'B'),col,'B');c.setStrokeColor(HexColor(TRACK));c.setLineWidth(3);c.line(x0,H-yy-6,x1,H-yy-6)
 last=-99
 for d in ds:
  dot(xd(d),yy+6,4.2,col)
  if xd(d)-last>=30:txt(xd(d),yy+12,day(d),6,MUTED,'R','c');last=xd(d)
y+=len(lines)*22+6
para(36,y,'Les écarts entre deux reprises s’allongent (quelques jours, puis une, deux, quatre semaines) : c’est l’espacement qui ancre une compétence dans la durée.',523,7.8,MUTED)
c.showPage()

# ---------- 7. Bilans individuels : 3 cartes par page ----------
CH=222;GAP=9;CW=523
def jauge(x,y,w,r):
 c.setStrokeColor(HexColor(TRACK));c.setLineWidth(4);c.line(x,H-y,x+w,H-y)
 txt(x-4,y-2.6,'0',5.8,GREY,'R','r');txt(x+w+4,y-2.6,'100',5.8,GREY,'R','l')
 p1,p2,p3=x+w*r['m1']/100,x+w*r['m2']/100,x+w*r['dur']/100;col=TEAL if r['dm']>=0 else RED
 c.setStrokeColor(HexColor(col));c.setLineWidth(1.6);c.line(p1,H-y,p2,H-y)
 c.setStrokeColor(HexColor(GOLD));c.setDash(2,2);c.setLineWidth(1.2);c.line(p2,H-y,p3,H-y);c.setDash()
 c.setFillColor(HexColor('#FFFFFF'));c.setStrokeColor(HexColor(MUTED));c.setLineWidth(1);c.circle(p1,H-y,3,fill=1,stroke=1)
 dot(p2,y,4.2,col);c.setFillColor(HexColor('#FFFFFF'));c.setStrokeColor(HexColor(GOLD));c.setLineWidth(1.4);c.circle(p3,H-y,3.2,fill=1,stroke=1)
 txt(p2,y-15,f'T2 {r["m2"]:.0f} %',7,col,'B','c')
 lab1,lab3=f'T1 {r["m1"]:.0f}',f'3 juil. {r["dur"]:.0f}'
 if abs(p1-p3)<34:txt(min(p1,p3)-4,y+9,lab3+' • '+lab1 if p3<p1 else lab1+' • '+lab3,6.2,MUTED,'R','r')
 else:txt(p1,y+9,lab1,6.2,MUTED,'R','c');txt(p3,y+9,lab3,6.2,GOLD,'B','c')
def card(y,r):
 x=36;rect(x,y,CW,CH,'#FFFFFF',r=9,stroke=LINE,lw=.8);rect(x,y,CW,34,PALE,r=9);rect(x,y+24,CW,10,PALE)
 col=PCOL[r['profile']];dot(x+20,y+17,11,NAVY);txt(x+20,y+12.6,f'{r["id"]:02d}',8.6,'#FFFFFF','B','c')
 txt(x+38,y+8,r['name'],12.5,NAVY,'B');nw=pdfmetrics.stringWidth(r['name'],'B',12.5);cw_=pdfmetrics.stringWidth(r['profile'],'B',7)+14
 rect(x+44+nw,y+9.5,cw_,13,col,r=6.5);txt(x+44+nw+cw_/2,y+12.6,r['profile'].upper() if False else r['profile'],7,'#FFFFFF','B','c')
 kx=x+CW-8
 for val,lab,vc in [(fr(r['mean'])+'/20',f'moyenne ({signed(r["delta"])})',NAVY),(f'{r["solid"]}/{NC}','solides au 3 juillet',BLUE),(f'{r["k2"]}/{NC}',f'compétences acquises ({"+" if r["k2"]>=r["k1"] else "−"}{abs(r["k2"]-r["k1"])})',TEAL)]:
  wv=max(pdfmetrics.stringWidth(val,'B',11.5),pdfmetrics.stringWidth(lab,'R',6.2))
  txt(kx,y+6,val,11.5,vc,'B','r');txt(kx,y+21,lab,6.2,MUTED,'R','r');kx-=wv+16
 # Compétences : barres à deux niveaux.
 bx=x+12;by=y+44;txt(bx,by,'COMPÉTENCES DU TRIMESTRE',6.8,TEAL,'B');txt(bx+246,by,'T2 → 3 JUIL.',6.2,MUTED,'B','r');by+=12
 for j,cc in enumerate(r['comp']):
  if j==9:by+=3
  dot(bx+2.5,by+4.2,2.4,STCOL[cc['status']]);txt(bx+9,by+1.3,cc['short'],fit(cc['short'],74,7,'R'),NAVY)
  twobar(bx+86,by+1,112,6.8,cc['m2'],cc['dur'])
  txt(bx+246,by+1,f'{cc["m2"]:.0f} → {cc["dur"]:.0f}',6.6,NAVY,'R','r');by+=13.3
 # Synthèse rédigée.
 rx=x+278;rw=CW-290;ry=y+44
 txt(rx,ry,'MAÎTRISE MOYENNE',6.8,TEAL,'B');jauge(rx+10,ry+28,rw-34,r);ry+=46
 cs=r['comp'];strong=max(cs,key=lambda c_:(c_['dur'],c_['m2']));weak=min(cs[:9],key=lambda c_:c_['m2'])
 known=[c_ for c_ in cs if c_['m2']>=50 and c_ is not strong];risk=max(known,key=lambda c_:c_['lost']) if known else None
 tr=weak['traces'][r['id']%2]
 appui=f'{strong["short"]} ({strong["m2"]:.0f} %'+(', solide jusqu’en juillet).' if strong['dur']>=60 else ', à consolider).')
 extra={'Soutien prioritaire':' Une seule priorité à la fois, en petit groupe.','Vigilance':' Commencer par un entretien de 5 minutes sur une copie récente.','Consolidation':' Retirer l’aide progressivement.','Approfondissement':' Puis une preuve ou un problème ouvert plutôt que des exercices répétitifs.'}[r['profile']]
 blocks=[('Point d’appui',TEAL,appui),('Priorité',RED,f'{weak["short"]} ({weak["m2"]:.0f} %) : l’élève {tr[0]}.')]
 if risk and risk['lost']>=12:blocks.append(('Avant l’oubli',GOLD,f'{risk["short"]} : {risk["m2"]:.0f} % aujourd’hui, {risk["dur"]:.0f} % le 3 juillet. La reprendre dans 1 et 3 semaines.'))
 blocks+= [('Geste',NAVY,tr[1][0].upper()+tr[1][1:]+'.'+extra),('À vérifier',BLUE,f'le {day(CHECK)}, sans aide : {tr[2]}.')]
 for lab,col_,body in blocks:
  h=para(rx,ry,f'<font color="{col_}"><b>{lab.upper()}</b></font>  {body}',rw,7.4,NAVY,9.4);ry+=h+4.5
 assert ry<=y+CH-4,(r['name'],ry-y)
def explain(y):
 x=36;rect(x,y,CW,CH,PALE,r=9,stroke=LINE,lw=.8);txt(x+14,y+12,'COMMENT LIRE UNE CARTE ÉLÈVE',9,TEAL,'B')
 # En-tête.
 yy=y+32;dot(x+24,yy+7,9,NAVY);txt(x+24,yy+3.6,'08',7.6,'#FFFFFF','B','c');txt(x+40,yy+2,'Numéro et nom de l’élève, puis son profil :',7.6,NAVY,'B')
 px=x+40
 for p,col in PCOL.items():
  w_=pdfmetrics.stringWidth(p,'B',6.6)+12;rect(px,yy+13,w_,11,col,r=5.5);txt(px+w_/2,yy+15.6,p,6.6,'#FFFFFF','B','c');px+=w_+5
 para(x+40,yy+29,'À droite de l’en-tête : compétences acquises (et évolution depuis le 1er trimestre), compétences solides jusqu’au 3 juillet, moyenne des notes.',225,7.3,NAVY,9.2)
 # Barres.
 yy=y+100;txt(x+14,yy,'LES BARRES DE COMPÉTENCES',7,TEAL,'B');yy+=13
 twobar(x+14,yy,150,9,68,42);txt(x+170,yy+1,'68 → 42',7,NAVY,'B')
 c.setStrokeColor(HexColor(NAVY));c.setLineWidth(.6)
 for xv,lab in [(x+14+150*.21,'foncé : ce qui restera le 3 juillet'),(x+14+150*.55,'clair : su aujourd’hui, oublié sans révision')]:pass
 para(x+14,yy+15,'<font color="#087F83"><b>Foncé</b></font> : ce qui restera le 3 juillet. <font color="#5EAAA8"><b>Clair</b></font> : su aujourd’hui mais oublié d’ici là sans révision. <b>Gris</b> : reste à acquérir. Les chiffres : maîtrise aujourd’hui → le 3 juillet.',240,7.3,NAVY,9.2)
 yy+=52
 for k_,(st,lab) in enumerate([('solid','solide'),('review','à réviser'),('build','à renforcer')]):dot(x+18+k_*70,yy+3,2.6,STCOL[st]);txt(x+24+k_*70,yy,lab,7,NAVY)
 # Jauge.
 rx=x+278;txt(rx,y+32,'LA JAUGE DE MAÎTRISE MOYENNE',7,TEAL,'B')
 jauge(rx+10,y+62,200,dict(m1=38,m2=61,dur=44,dm=23))
 para(rx,y+78,'<b>Rond vide</b> : fin du 1er trimestre. <b>Rond plein</b> : aujourd’hui (rouge si recul). <font color="#C58935"><b>Rond jaune</b></font> : ce qui restera le 3 juillet sans révision.',230,7.3,NAVY,9.2)
 txt(rx,y+116,'LA SYNTHÈSE RÉDIGÉE',7,TEAL,'B')
 para(rx,y+129,'<font color="#087F83"><b>Point d’appui</b></font> : la compétence la plus solide, pour redonner confiance. <font color="#BC4855"><b>Priorité</b></font> : la plus fragile, avec l’erreur observée sur les copies. <font color="#C58935"><b>Avant l’oubli</b></font> : ce qui est su mais va s’effacer. <b>Geste</b> : ce que le professeur peut proposer. <font color="#4F70AE"><b>À vérifier</b></font> : la tâche courte qui prouvera la réussite.',230,7.3,NAVY,9.2)
y=header(7);explain(y);y+=CH+GAP;queue=list(records)
for r in queue[:2]:card(y,r);y+=CH+GAP
c.showPage();rest=queue[2:]
while rest:
 topbar('REGARD PROFESSEUR  /  3e B  /  BILANS INDIVIDUELS');y=56
 for r in rest[:3]:card(y,r);y+=CH+GAP
 if len(rest)<3:
  # Dernière page : récapitulatif des profils dans l'espace restant.
  rect(36,y,CW,140,PALE,r=9);txt(50,y+12,'RÉCAPITULATIF DES PROFILS DE LA CLASSE',9,TEAL,'B');yy=y+32
  for p,col in PCOL.items():
   rs=[r_ for r_ in records if r_['profile']==p];dot(54,yy+4,3.5,col);txt(62,yy,f'{p} ({len(rs)})',8,col,'B');h=para(170,yy-1,', '.join(r_['name'] for r_ in rs) or '—',375,7.6,NAVY,9.6);yy+=max(h,11)+7
 rest=rest[3:];c.showPage()

# ---------- 8. Méthode ----------
y=header(8,'Comment sont calculés les indicateurs de ce bilan.','Lire les chiffres correctement et garder les mêmes définitions d’un trimestre à l’autre.')
for title,body in [
 ('Compétences suivies','Neuf compétences du programme de 3e (référentiel de la plateforme : fractions, puissances, calcul littéral, équations, Pythagore, Thalès) et deux compétences transversales (justifier, vérifier), observées dans les 12 évaluations du trimestre.'),
 ('Maîtrise d’une compétence','Probabilité que l’élève réussisse aujourd’hui une tâche nouvelle sur cette compétence, estimée à partir de toutes ses réponses, en donnant plus de poids aux plus récentes. Une compétence est <b>acquise</b> à partir de 50 % de maîtrise.'),
 ('Ce qui restera le 3 juillet','Maîtrise prévue le dernier jour de l’année sans aucune révision, calculée avec la courbe de l’oubli propre à chaque élève : plus une compétence a été réussie souvent et à intervalles espacés, plus elle résiste. <b>Solide</b> : au moins 60 % le 3 juillet. <b>À réviser</b> : acquise mais fragile. <b>À renforcer</b> : sous 50 % aujourd’hui.'),
 ('Profils','Soutien prioritaire : maîtrise moyenne sous 45 %. Vigilance : recul d’au moins 3 points depuis le 1er trimestre. Consolidation : de 45 à 70 %. Approfondissement : 70 % ou plus. Les profils organisent l’aide, ils ne résument pas un élève.'),
 ('Notes','Chaque évaluation vaut 20 points : 10 pour le domaine ciblé, 5 pour la justification, 5 pour la vérification. Moyenne trimestrielle : moyenne des 12 notes. Les deux trimestres suivent le même plan d’évaluation.'),
 ('Groupes de besoin et calendrier','Un groupe réunit les élèves sous 50 % sur une compétence. L’erreur citée est la plus fréquente sur leurs copies. Les dates de reprise s’espacent progressivement (quelques jours, puis une, deux et quatre semaines), ce que la recherche sur la mémoire désigne comme la pratique espacée.'),
 ('Limites','Les indicateurs signalent des situations à examiner ; ils ne remplacent ni la connaissance des élèves, ni l’échange avec eux. Aucune conclusion personnelle, familiale ou médicale ne peut être tirée de ces seuls résultats.')]:
 y=section(y,title,body,fs=9)+10
c.showPage();c.save()
assert PAGE[0]==TOTAL and PAGEOF==PLANNED,(PAGE[0],PAGEOF)
print(json.dumps(dict(pages=PAGE[0],known=[round(known1,1),round(known2,1)],solid=round(solid,1),forget=round(forget,1),weak=[COMPS[j][1] for j in order_weak[:3]],build=[int(nbuild[j]) for j in order_weak[:3]],best=COMPS[best][1],risk=[rk[1],rk2[1]],profiles=prof_count,down=[r['name'] for r in down],up=[(r['name'],round(r['dm'],1)) for r in up]),ensure_ascii=False))
