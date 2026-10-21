"""Bilan de l'établissement pour la direction des études : T3, progression de l'année, notes et compétences."""
import os
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/mathprint-mpl')
os.environ.setdefault('XDG_CACHE_HOME','/private/tmp/mathprint-cache')
from pathlib import Path
import json,sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
sys.path.insert(0,str(Path(__file__).resolve().parent))
from bilan_etablissement_data import build,COEF,CLASSES,LEVELS,SUBJECTS,SHORT,FREQ,CATALOGUE
ROOT=Path(__file__).resolve().parents[1];REPO=ROOT.parents[1];TMP=REPO/'tmp/pdfs/bilan_etablissement';TMP.mkdir(parents=True,exist_ok=True)
OUT=ROOT/'06_bilan_etablissement_T3_DIRECTION_ETUDES.pdf'
for n,f in [('R','Arial.ttf'),('B','Arial Bold.ttf'),('I','Arial Italic.ttf')]:pdfmetrics.registerFont(TTFont(n,'/System/Library/Fonts/Supplemental/'+f))
pdfmetrics.registerFontFamily('R',normal='R',bold='B',italic='I',boldItalic='B')
NAVY='#183449';TEAL='#087F83';BLUE='#4F70AE';MUTED='#637888';RED='#BC4855';GOLD='#C58935';PALE='#EFF5F7';LINE='#D8E3E8';GREY='#A8BAC5';LIGHT='#A9D9D6'
FCOL=[RED,GOLD,'#5EAAA8',TEAL]
SUBJCOL={'Mathématiques':TEAL,'Physique-Chimie':'#2E8C8F','SVT':'#5EAAA8','Français':BLUE,'Anglais':'#6C86BD','Espagnol':'#8FA6C9','Philosophie':'#3D5A8F','Histoire-Géographie':GOLD,'EDHC':'#D9A65E'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':LINE,'axes.labelcolor':NAVY,'xtick.color':MUTED,'ytick.color':MUTED,'text.color':NAVY,'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white'})
SCHOOL='Groupe scolaire Horizon';PERIOD='3e trimestre 2025-2026';STAMP='bilan arrêté au vendredi 29 mai 2026'
NATIONAL={'3e':('BEPC',51.41),'Tle':('BAC',40.15)}
SEC=dict(essentiel=1,annee=2,classes=3,matieres=4,competences=5,comp_matieres=6,profs=7,frequence=8,profs_comp=9,examens=10,meilleurs=11,difficulte=12,redoublement=13,priorites=14,methode=15)
TITLES={1:'L’essentiel du trimestre',2:'L’année en un coup d’œil',3:'Les classes',4:'Les matières',5:'La maîtrise des compétences',6:'Les compétences, matière par matière',7:'Les professeurs',8:'Évaluer souvent fait progresser',9:'Professeurs, évaluations et compétences',10:'La préparation aux examens',11:'Les meilleurs élèves',12:'Les élèves en grande difficulté',13:'Les propositions de redoublement',14:'Les priorités pour la rentrée',15:'Méthode et définitions'}
FLAB=['≤ 2','3 à 5','6 à 9','≥ 10']

def fr(n,d=1):return f'{n:.{d}f}'.replace('.',',')
def signed(n,d=1):return fr(0,d) if round(abs(n),d)==0 else ('+' if n>=0 else '−')+fr(abs(n),d)
def pct(n):return f'{n:.0f} %'
def pts(n):return ('+' if n>=0 else '−')+f'{abs(n):.0f} pts'
ART={'Français':'le français','Anglais':'l’anglais','Espagnol':'l’espagnol','Mathématiques':'les mathématiques','Physique-Chimie':'la physique-chimie','SVT':'les SVT','Histoire-Géographie':'l’histoire-géographie','EDHC':'l’EDHC','Philosophie':'la philosophie'}
def cap(s):return s[0].upper()+s[1:]

# ---------- Indicateurs : notes ----------
D=build();S=D['students'];T=D['teachers']
G=np.array([s['gen'] for s in S]);DELTA=G[:,2]-G[:,0]
for s in S:s['delta']=s['gen'][2]-s['gen'][0]
def core(s):return [x for x in s['marks'] if x!='EDHC']
def fund(s):return [x for x,c in COEF[s['grid']].items() if c>=3 or x in ('Français','Mathématiques')]
def weak(s):return [x for x in fund(s) if s['marks'][x][2]<8]
nonexam=[s for s in S if s['level'] not in ('3e','Tle')]
REDO=[s for s in nonexam if s['annual']<8.5 and s['delta']<.5 and len(weak(s))>=2]
LIMIT=[s for s in nonexam if s not in REDO and s['annual']<9.5 and (s['annual']<8.5 or len(weak(s))>=2)]
EXAMRISK=[s for s in S if s['level'] in ('3e','Tle') and s['annual']<9]
DROP=sorted([s for s in S if s['gen'][0]>=11 and s['delta']<=-2],key=lambda s:s['delta'])
classes=[c[0] for c in CLASSES]
def cls_stats(c):
 ss=[s for s in S if s['cls']==c];g=np.array([s['gen'] for s in ss])
 return dict(n=len(ss),t=g.mean(0),ok=(g[:,2]>=10).mean()*100,under8=int((g[:,2]<8).sum()),redo=sum(s['cls']==c for s in REDO),limit=sum(s['cls']==c for s in LIMIT))
CS={c:cls_stats(c) for c in classes}
LV={lv:np.array([s['gen'] for s in S if s['level']==lv]) for lv in LEVELS}
def subj_t(subj,cls=None):
 v=np.array([s['marks'][subj] for s in S if subj in s['marks'] and (cls is None or s['cls']==cls)]);return v.mean(0) if len(v) else None
SUBJ={x:subj_t(x) for x in SUBJECTS}
for t in T:t['delta']=t['t'][2]-t['t'][0];t['gain']=t['acq'][2]-t['acq'][0]
COPIES_Y=sum(t['copies'] for t in T);HOURS=COPIES_Y*3/60
def exam_index(s):
 cf={x:c for x,c in COEF[s['grid']].items() if x!='EDHC'};return sum(s['marks'][x][2]*c for x,c in cf.items())/sum(cf.values())
EXC=['3e A','3e B','3e C','Tle A','Tle C','Tle D'];EX={}
for c in EXC:
 ss=[s for s in S if s['cls']==c];idx=np.array([exam_index(s) for s in ss]);p=1/(1+np.exp(-(idx-10.4)/.75))
 EX[c]=dict(n=len(ss),rate=p.mean()*100,safe=int((idx>=11.5).sum()),edge=int(((idx>=9)&(idx<11.5)).sum()),fragile=int((idx<9).sum()),lvl=c.split()[0])
for lv in ('3e','Tle'):
 ss=[c for c in EXC if EX[c]['lvl']==lv];EX[lv]=dict(rate=sum(EX[c]['rate']*EX[c]['n'] for c in ss)/sum(EX[c]['n'] for c in ss),edge=sum(EX[c]['edge'] for c in ss),n=sum(EX[c]['n'] for c in ss))
BELOW=[c for c in EXC if EX[c]['rate']<NATIONAL[EX[c]['lvl']][1]]
def below_txt():return ' et '.join(f'la {c} ({pct(EX[c]["rate"])} de réussite estimée) est sous le taux national du {NATIONAL[EX[c]["lvl"]][0]} ({fr(NATIONAL[EX[c]["lvl"]][1],2)} %)' for c in BELOW)

# ---------- Indicateurs : compétences (pondérées par le nombre de compétences au programme) ----------
def comp_mix(ss,tr,subj=None):
 tot=acq=na=cov=0.
 for s in ss:
  for x,cpt in s['comp'].items():
   if subj and x!=subj:continue
   n=cpt['n'];tot+=n;acq+=cpt['acq'][tr]*n;na+=cpt['na'][tr]*n;cov+=cpt['cov'][tr]*n
 if not tot:return None
 a,b,cv=acq/tot,na/tot,cov/tot;return dict(acq=a,na=b,cours=cv-a-b,never=100-cv,cov=cv)
CE=[comp_mix(S,tr) for tr in range(3)]
CLV={lv:[comp_mix([s for s in S if s['level']==lv],tr) for tr in range(3)] for lv in LEVELS}
CSUB={x:[comp_mix(S,tr,x) for tr in range(3)] for x in SUBJECTS}
per_student=[sum(c['acq'][2]/100*c['n'] for c in s['comp'].values()) for s in S];per_total=[sum(c['n'] for c in s['comp'].values()) for s in S]
per_student1=[sum(c['acq'][0]/100*c['n'] for c in s['comp'].values()) for s in S]
def forget(ss):
 a=f=0.
 for s in ss:
  for c in s['comp'].values():w=c['acq'][2]*c['n'];a+=w;f+=w*c['forget']/100
 return f/a*100
FORGET={lv:forget([s for s in S if s['level']==lv]) for lv in LEVELS};FORGET_ALL=forget(S)
def cat_rate(subj,lv,off,tr):
 v=[s['comp'][subj]['acq'][tr]/100 for s in S if s['level']==lv and subj in s['comp']];return float(np.clip(np.mean(v)+off,.03,.97))*100
CAT=sorted([dict(subj=x,lv=lv,label=l,t1=cat_rate(x,lv,o,0),t3=cat_rate(x,lv,o,2)) for x,lv,l,o in CATALOGUE],key=lambda d:d['t3'])
GROUPS=[[t for t in T if t['group']==g] for g in range(4)]
GSTAT=[dict(n=len(ts),notes=np.mean([t['delta'] for t in ts]),gain=np.mean([t['gain'] for t in ts]),never=100-np.mean([t['cov'][2] for t in ts]),acq3=np.mean([t['acq'][2] for t in ts])) for ts in GROUPS]

# ---------- Graphiques ----------
def save(fig,name):fig.savefig(TMP/(name+'.png'),dpi=220,bbox_inches='tight',pad_inches=.14);plt.close(fig)
LVCOL=['#9BC9C6','#5EAAA8','#BC4855','#087F83','#4F70AE','#8FA6C9','#183449']
fig,ax=plt.subplots(figsize=(10,4.4));ends=[]
for i,lv in enumerate(LEVELS):
 m_=LV[lv].mean(0);ax.plot([1,2,3],m_,color=LVCOL[i],lw=2.6 if lv=='4e' else 1.8,marker='o',ms=5);ends.append([m_[2],m_[2],lv,LVCOL[i]])
ends.append([G.mean(0)[2],G.mean(0)[2],'Établissement',NAVY]);ends.sort(key=lambda e:-e[0])
for k in range(1,len(ends)):
 if ends[k-1][1]-ends[k][1]<.11:ends[k][1]=ends[k-1][1]-.11
for v,ly,lv,col in ends:ax.annotate(f'{lv}  {fr(v)}',xy=(3,v),xytext=(3.1,ly),va='center',fontsize=9,color=col,fontweight='bold',arrowprops=dict(arrowstyle='-',color=col,lw=.6,alpha=.6))
ax.plot([1,2,3],G.mean(0),color=NAVY,lw=3.2,ls='--',marker='D',ms=6,label='Établissement (moyenne générale)')
for k in range(2):ax.text(k+1,G.mean(0)[k]-.2,fr(G.mean(0)[k]),ha='center',fontsize=9,color=NAVY,fontweight='bold',bbox=dict(boxstyle='round,pad=.15',fc='white',ec='none'))
lo_,hi_=min(LV[lv].mean(0).min() for lv in LEVELS),max(LV[lv].mean(0).max() for lv in LEVELS)
ax.set(xticks=[1,2,3],xticklabels=['1er trimestre','2e trimestre','3e trimestre'],xlim=(.8,3.75),ylabel='Moyenne générale / 20',ylim=(lo_-.3,hi_+.3));ax.grid(axis='y',alpha=.2);ax.legend(frameon=False,loc='upper left');save(fig,'niveaux')
fig,ax=plt.subplots(figsize=(10,2.5));bins=[0,6,8,10,12,14,16,20.01];lab=['< 6','6 à < 8','8 à < 10','10 à < 12','12 à < 14','14 à < 16','≥ 16'];xx=np.arange(7)
for vals,off,col,l in [(G[:,0],-.19,GREY,'1er trimestre'),(G[:,2],.19,TEAL,'3e trimestre')]:
 cnt=np.histogram(vals,bins=bins)[0];bars=ax.bar(xx+off,cnt,.36,color=col,label=l)
 for b,n in zip(bars,cnt):ax.text(b.get_x()+b.get_width()/2,n+4,str(n),ha='center',fontsize=8.5)
ax.set(xticks=xx,xticklabels=lab,ylabel='Élèves',ylim=(0,max(np.histogram(G[:,2],bins=bins)[0].max(),np.histogram(G[:,0],bins=bins)[0].max())*1.22));ax.grid(axis='y',alpha=.15);ax.legend(frameon=False,ncol=2);save(fig,'distribution')
cmap=LinearSegmentedColormap.from_list('mp',['#BC4855','#E9B7A0','#F6E7C8','#BFE0DC','#087F83'])
def heat(Mx,rows,cols_,name,vmin,vmax,fmt,figsize,lowtxt,hightxt,rot=55,seps=(2.5,5.5,8.5,11.5,13.5,16.5)):
 fig,ax=plt.subplots(figsize=figsize);ax.imshow(Mx,cmap=cmap,vmin=vmin,vmax=vmax,aspect='auto')
 for i in range(Mx.shape[0]):
  for j in range(Mx.shape[1]):
   if not np.isnan(Mx[i,j]):ax.text(j,i,fmt(Mx[i,j]),ha='center',va='center',fontsize=7.4,color='white' if Mx[i,j]<lowtxt or Mx[i,j]>hightxt else NAVY)
 ax.set(xticks=range(len(cols_)),yticks=range(len(rows)),yticklabels=rows);ax.set_xticklabels(cols_,rotation=rot,ha='right' if rot else 'center',fontsize=8.5);ax.tick_params(axis='y',labelsize=8.5,length=0);ax.tick_params(axis='x',length=0)
 for sp in ax.spines.values():sp.set_visible(False)
 for b in seps:ax.axvline(b,color='white',lw=2.5)
 save(fig,name)
M=np.full((len(SUBJECTS),len(classes)),np.nan)
for i,x in enumerate(SUBJECTS):
 for j,cc in enumerate(classes):
  v=subj_t(x,cc);M[i,j]=v[2] if v is not None else np.nan
heat(M,SUBJECTS,classes,'heatmap',8,14,fr,(10,3.7),8.8,13.2)
order=sorted(SUBJECTS,key=lambda x:SUBJ[x][2]);fig,ax=plt.subplots(figsize=(10,2.9));y=np.arange(len(order))
for i,x in enumerate(order):
 a,b=SUBJ[x][0],SUBJ[x][2];ax.plot([a,b],[i,i],color='#B8CBD1',lw=4);ax.scatter(a,i,s=40,color=GREY,zorder=3);ax.scatter(b,i,s=60,color=TEAL if b-a>=.6 else RED if b-a<0 else GOLD if b-a<.3 else NAVY,zorder=4)
 ax.text(max(a,b)+.12,i,f'{fr(b)}  ({signed(b-a)})',va='center',fontsize=8.5)
ax.set(yticks=y,yticklabels=order,xlim=(9,14.5),xlabel='Moyenne de la matière / 20 (gris : 1er trimestre, couleur : 3e trimestre)');ax.tick_params(axis='y',labelsize=8.5);ax.grid(axis='x',alpha=.2);save(fig,'matieres')
# Compétences par niveau : 1er et 3e trimestre empilés.
fig,ax=plt.subplots(figsize=(10,4.3));yy=[];labels=[]
parts=[('acq',TEAL,'Acquises'),('cours',LIGHT,'En cours d’acquisition'),('na','#E9B7A0','Non acquises'),('never','#E3E9EC','Jamais évaluées')]
for i,lv in enumerate(LEVELS):
 for k,tr in enumerate((0,2)):
  yv=i*2.6+k*1.05;left=0;mix=CLV[lv][tr]
  for key,col,lab in parts:
   ax.barh(yv,mix[key],left=left,color=col,height=.9,edgecolor='white',lw=.6)
   if key=='acq' or (key=='never' and mix[key]>=8):ax.text(left+mix[key]/2,yv,pct(mix[key]),ha='center',va='center',fontsize=7.6,color='white' if key=='acq' else MUTED,fontweight='bold')
   left+=mix[key]
  yy.append(yv);labels.append(f'{lv}  ·  {"T1" if tr==0 else "T3"}')
ax.set(yticks=yy,yticklabels=labels,xlim=(0,100),xlabel='Part des compétences du programme de l’année (%)');ax.invert_yaxis();ax.tick_params(axis='y',labelsize=8.3)
for t_ in ax.get_yticklabels():
 if 'T3' in t_.get_text():t_.set_fontweight('bold');t_.set_color(NAVY)
ax.legend(handles=[Patch(color=c_,label=l) for _,c_,l in parts],loc='upper center',bbox_to_anchor=(.5,-.12),ncol=4,frameon=False,fontsize=8.6);save(fig,'comp_niveaux')
CM=np.full((len(SUBJECTS),len(LEVELS)),np.nan)
for i,x in enumerate(SUBJECTS):
 for j,lv in enumerate(LEVELS):
  mx=comp_mix([s for s in S if s['level']==lv],2,x);CM[i,j]=mx['acq'] if mx else np.nan
heat(CM,SUBJECTS,LEVELS,'comp_heat',20,65,pct,(10,2.9),27,58,rot=0,seps=())
fig,ax=plt.subplots(figsize=(10,2.2));xx=np.arange(7);vals=[FORGET[lv] for lv in LEVELS]
bars=ax.bar(xx,vals,color=[RED if v>=FORGET_ALL+2 else GOLD for v in vals],width=.55)
for b,v in zip(bars,vals):ax.text(b.get_x()+b.get_width()/2,v+.6,pct(v),ha='center',fontsize=9,fontweight='bold')
ax.axhline(FORGET_ALL,color=NAVY,lw=1,ls='--');ax.text(-.75,FORGET_ALL+.6,f'établissement : {pct(FORGET_ALL)}',ha='left',fontsize=8.3,color=NAVY)
ax.set(xticks=xx,xticklabels=LEVELS,xlim=(-.8,6.5),ylim=(0,max(vals)*1.3),ylabel='% oubliées à la rentrée');ax.grid(axis='y',alpha=.15);save(fig,'oubli')
# Groupes de fréquence d'évaluation : trois indicateurs.
fig,axs=plt.subplots(1,3,figsize=(10.5,3.2));names=[f'{lab}\n({FLAB[i]})' for i,(lab,_,_) in enumerate(FREQ)]
for ax,key,title,fmt in [(axs[0],'notes','Progression des notes\n(points sur 20, T1 → T3)',signed),(axs[1],'gain','Compétences acquises en plus\n(points de %, T1 → T3)',pts),(axs[2],'never','Programme jamais évalué\nau 3e trimestre (%)',pct)]:
 vals=[g[key] for g in GSTAT];bars=ax.bar(range(4),vals,color=FCOL,width=.62);span=max(vals)-min(min(vals),0)
 for b,v in zip(bars,vals):ax.text(b.get_x()+b.get_width()/2,v+(span*.04 if v>=0 else -span*.1),fmt(v),ha='center',fontsize=9.5,fontweight='bold',color=NAVY)
 ax.axhline(0,color=MUTED,lw=.8);ax.set_title(title,fontsize=9.5,color=NAVY,pad=8);ax.set_xticks(range(4));ax.set_xticklabels(names,fontsize=7.3);ax.set_yticks([]);ax.set_ylim(min(min(vals),0)-span*.18,max(vals)+span*.2)
fig.tight_layout(w_pad=2.2);save(fig,'frequence')
# Professeurs : compétences acquises du T1 au T3, selon les évaluations par trimestre.
fig,ax=plt.subplots(figsize=(10,5.2));zones=[(0,2.5),(2.5,5.5),(5.5,9.5),(9.5,12.7)]
for g,(a,b) in enumerate(zones):ax.axvspan(a,b,color=FCOL[g],alpha=.07,lw=0);ax.text((a+b)/2,77,FREQ[g][0].upper(),ha='center',fontsize=8.6,color=FCOL[g],fontweight='bold')
jit={}
for t in sorted(T,key=lambda t:t['gain']):
 k=jit.get(t['evals'],0);jit[t['evals']]=k+1;x=t['evals']+(k-(sum(u['evals']==t['evals'] for u in T)-1)/2)*.2;col=FCOL[t['group']]
 ax.annotate('',xy=(x,t['acq'][2]),xytext=(x,t['acq'][0]),arrowprops=dict(arrowstyle='-|>',color=col,lw=1.6,mutation_scale=9),zorder=3)
 ax.scatter(x,t['acq'][0],s=14,color='white',edgecolor=col,lw=1,zorder=4);ax.text(x,t['acq'][2]+(1.4 if k%2==0 else 4.4),str(t['id']),ha='center',fontsize=6.4,color=col,fontweight='bold',zorder=5)
for g,(a,b) in enumerate(zones):ax.plot([a+.15,b-.15],[GSTAT[g]['acq3']]*2,color=FCOL[g],lw=1.2,ls=':',alpha=.9)
ax.set(xlim=(0,12.7),ylim=(0,80),xticks=range(1,13),xlabel='Évaluations corrigées par classe et par trimestre',ylabel='Compétences acquises par ses élèves (%)');ax.grid(axis='y',alpha=.15)
ax.legend(handles=[plt.Line2D([],[],marker='o',color=MUTED,mfc='white',ls='',label='1er trimestre'),plt.Line2D([],[],marker='>',color=MUTED,ls='-',label='flèche jusqu’au 3e trimestre'),plt.Line2D([],[],color=MUTED,ls=':',label='moyenne du groupe au 3e trimestre')],loc='upper left',bbox_to_anchor=(0,.93),frameon=False,fontsize=8.3)
save(fig,'profs_comp')
fig,ax=plt.subplots(figsize=(10,2.9));yy=np.arange(6)
vals=[EX[c]['rate'] for c in EXC];ref=[NATIONAL[EX[c]['lvl']][1] for c in EXC]
ax.barh(yy,vals,color=[TEAL if v>=r+10 else GOLD if v>=r else RED for v,r in zip(vals,ref)],height=.6)
for i,(v,r) in enumerate(zip(vals,ref)):ax.plot([r,r],[i-.4,i+.4],color=NAVY,lw=2);ax.text(v+1,i,pct(v),va='center',fontsize=9.5,fontweight='bold')
ax.set(yticks=yy,yticklabels=EXC,xlim=(0,100),xlabel='Réussite estimée (%) • trait foncé : taux national de la dernière session');ax.invert_yaxis();ax.grid(axis='x',alpha=.2);save(fig,'examens')

# ---------- Mise en page ----------
W,H=595.2756,841.8898;TOTAL=17
c=canvas.Canvas(str(OUT),pagesize=(W,H));c.setTitle(f'MathPrint | Bilan de l’établissement | {PERIOD}');c.setAuthor('MathPrint')
def rect(x,y,w,h,col,r=0,stroke=None,lw=.6):
 c.setFillColor(HexColor(col));c.setStrokeColor(HexColor(stroke or col));c.setLineWidth(lw);c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=bool(stroke))
def txt(x,y,t,fs=10,col=NAVY,font='R',align='l'):
 c.setFillColor(HexColor(col));c.setFont(font,fs);{'l':c.drawString,'c':c.drawCentredString,'r':c.drawRightString}[align](x,H-y-fs,t)
def fit(t,w,fs,font='R',mn=5.2):
 while pdfmetrics.stringWidth(t,font,fs)>w and fs>mn:fs-=.1
 return fs
def measure(t,w,fs=10,col=NAVY,leading=None):
 p=Paragraph(t,ParagraphStyle('p',fontName='R',fontSize=fs,leading=leading or fs*1.38,textColor=HexColor(col)));return p,p.wrap(w,3000)[1]
def para(x,y,t,w=523,fs=10,col=NAVY,leading=None,limit=792):
 p,h=measure(t,w,fs,col,leading);assert y+h<limit,(t[:70],y,h);p.drawOn(c,x,H-y-h);return h
PAGE=[0];PAGEOF={}
def footer():txt(36,807,f'{SCHOOL} • Bilan de l’établissement • {PERIOD} • Document confidentiel de direction',7,MUTED);txt(559,807,f'{PAGE[0]:02d}/{TOTAL}',8,MUTED,'R','r')
def header(num,what,use,suffix=''):
 PAGE[0]+=1;n=PAGE[0];PAGEOF.setdefault(num,n);title=TITLES[num]+suffix
 rect(0,0,W,7,TEAL);txt(36,22,'mathprint',18,NAVY,'B');txt(559,28,'DIRECTION DES ÉTUDES  /  BILAN DE L’ÉTABLISSEMENT',8,TEAL,'B','r')
 c.setStrokeColor(HexColor(LINE));c.setLineWidth(.5);c.line(36,H-42,559,H-42)
 txt(36,56,f'SECTION {num}',8,TEAL,'B');txt(36,68,title,fit(title,523,23,'B'),NAVY,'B')
 body=f'<b>De quoi s’agit-il ?</b> {what}<br/><b>À quoi ça sert ?</b> {use}';_,h=measure(body,499,8.6,MUTED,11.4)
 rect(36,100,523,h+16,PALE,r=6);rect(36,106,3,h+4,TEAL,r=1);para(48,108,body,499,8.6,MUTED,11.4)
 if not suffix:c.bookmarkPage(f'p{n}');c.addOutlineEntry(f'{num}. {title}',f'p{n}',0)
 footer();return 100+h+16+14
def chart(name,x,y,w,h):c.drawImage(str(TMP/(name+'.png')),x,H-y-h,width=w,height=h,mask='auto',preserveAspectRatio=True,anchor='c')
def metric(x,y,w,value,label,detail,col=TEAL,h=92):
 rect(x,y,w,h,PALE,r=8);fs=fit(value,w-26,24,'B');txt(x+12,y+11,value,fs,col,'B');txt(x+12,y+42,label,9,NAVY,'B');para(x+12,y+58,detail,w-24,7.8,MUTED,10)
def section(y,title,body,col=TEAL,fs=9.4):txt(36,y,title,12,col,'B');return y+19+para(36,y+19,body,523,fs,leading=fs*1.36)
def table(y,cols,rows,rh=12.6,fs=7.2,head=NAVY,zebra=True):
 x=36;rect(36,y,523,15,head,r=3)
 for (lab,w,al) in cols:
  xx=x+4 if al=='l' else x+w/2 if al=='c' else x+w-4;txt(xx,y+4.2,lab,fit(lab,w-6,6.6,'B'),'#FFFFFF','B',al);x+=w
 y+=16
 for i,row in enumerate(rows):
  if zebra and i%2==0:rect(36,y,523,rh,PALE)
  x=36
  for (lab,w,al),v in zip(cols,row):
   font='R';col=NAVY
   if isinstance(v,tuple):v,col,font=v
   xx=x+4 if al=='l' else x+w/2 if al=='c' else x+w-4;f=fit(str(v),w-6,fs,font);txt(xx,y+(rh-f)/2+.6,str(v),f,col,font,al);x+=w
  y+=rh
 return y
def classes_txt(cl):
 out=[]
 for lv in LEVELS:
  mine=[x for x in cl if x.split()[0]==lv]
  if not mine:continue
  allc=[x for x in classes if x.split()[0]==lv]
  out.append(f'{lv} ({len(mine)})' if len(mine)==len(allc) and len(mine)>1 else ', '.join(mine))
 return ', '.join(out)
def compact(cl):
 # « 2nde C, 1ère C/D, Tle C/D » ; au-delà de six classes : « 6e à 3e (10 classes) ».
 if len(cl)>6:return f'{cl[0].split()[0]} à {cl[-1].split()[0]} ({len(cl)} classes)'
 out=[]
 for lv in LEVELS:
  mine=[x.split()[1] for x in cl if x.split()[0]==lv]
  if mine:out.append(f'{lv} '+'/'.join(mine))
 return ', '.join(out)
def dcol(v):return TEAL if v>=.3 else RED if v<=-.3 else MUTED

# ---------- Faits utilisés par l'analyse, et contrôles des affirmations écrites ----------
m=G.mean(0);ok=(G>=10).mean(0)*100;lvd={lv:LV[lv].mean(0)[2]-LV[lv].mean(0)[0] for lv in LEVELS}
best_lv=max(lvd,key=lvd.get);worst_lv=min(lvd,key=lvd.get);cd={cc:CS[cc]['t'][2]-CS[cc]['t'][0] for cc in classes}
best_c=sorted(classes,key=lambda x:-cd[x])[:3]
sd={x:SUBJ[x][2]-SUBJ[x][0] for x in SUBJECTS};weak_subj=sorted(SUBJECTS,key=lambda x:SUBJ[x][2])[:2]
prog_subj=sorted([x for x in SUBJECTS if x!='EDHC'],key=lambda x:-sd[x])[:2];flat_subj=min(SUBJECTS,key=lambda x:sd[x])
math4=next(t for t in T if t['subject']=='Mathématiques' and '4e A' in t['classes']);pc3=next(t for t in T if t['subject']=='Physique-Chimie' and '3e A' in t['classes'])
redo4=sum(s['level']=='4e' for s in REDO);math4e=subj_t('Mathématiques','4e A');lv4=LV['4e'].mean(0);g0,g3=GSTAT[0],GSTAT[3]
clvgain={lv:CLV[lv][2]['acq']-CLV[lv][0]['acq'] for lv in LEVELS};worst_cg=min(clvgain,key=clvgain.get);best_cg=max(clvgain,key=clvgain.get)
LEVAL={lv:np.mean([t['evals'] for t in T for x in t['classes'] if x.split()[0]==lv]) for lv in LEVELS}
low3e=min(('3e A','3e B','3e C'),key=lambda x:CS[x]['t'][2]);worst_f=max(FORGET,key=FORGET.get)
flat_teachers=[t for t in T if t['subject']==flat_subj and t['group']==0]
assert worst_lv=='4e' and lvd['4e']<.1 and all(v>.2 for k,v in lvd.items() if k!='4e'),lvd
assert BELOW and math4['delta']<0 and math4['group']==0 and pc3['group']==3 and g3['gain']>3*g0['gain']+10 and worst_cg=='4e',(BELOW,GSTAT,clvgain)
assert all(SUBJ[x][2]>SUBJ[x][0] for x in weak_subj) and 'Mathématiques' in weak_subj

# ---------- Page de garde ----------
PAGE[0]+=1
rect(0,0,W,388,NAVY);rect(0,388,W,8,TEAL)
txt(48,52,'mathprint',22,'#FFFFFF','B');txt(547,58,'DIRECTION DES ÉTUDES',8.5,LIGHT,'B','r')
txt(48,150,SCHOOL.upper(),11,LIGHT,'B');txt(48,172,'Bilan de',36,'#FFFFFF','B');txt(48,214,'l’établissement',36,'#FFFFFF','B')
txt(48,268,'3e trimestre 2025-2026',15,'#FFFFFF');txt(48,290,'et progrès de toute l’année scolaire',15,LIGHT)
txt(48,340,f'De la 6e à la Terminale  •  {cap(STAMP)}',9.5,LIGHT)
for i,(v,l) in enumerate([(f'{len(S)}','élèves'),(f'{len(classes)}','classes'),(f'{len(T)}','professeurs'),(f'{len(SUBJECTS)}','matières évaluées'),(f'{COPIES_Y:,}'.replace(',',' '),'copies corrigées')]):
 x=48+i*102;txt(x,420,v,19 if i<4 else 17,TEAL,'B');txt(x,444,l,8.5,MUTED)
txt(48,478,'SOMMAIRE',9,TEAL,'B');c.setStrokeColor(HexColor(LINE));c.line(48,H-494,547,H-494)
TOCY=506;PLANNED={n:n+1 for n in range(1,14)};PLANNED.update({14:16,15:17})
for num in range(1,16):
 col_x=48 if num<=8 else 306;yy=TOCY+((num-1)%8)*28
 txt(col_x,yy,f'{num:02d}',9.5,TEAL,'B');txt(col_x+24,yy,TITLES[num],9.5,NAVY);txt(col_x+238,yy+.5,f'p. {PLANNED[num]}',8.5,MUTED,'R','r')
rect(48,752,499,30,PALE,r=6);txt(60,763,'Document confidentiel : il contient des données nominatives d’élèves et de professeurs. Diffusion réservée à l’équipe de direction.',7.6,MUTED,'I')
footer();c.showPage()

# ---------- 1. L'essentiel ----------
y=header(1,f'{SCHOOL} : {len(S)} élèves, {len(classes)} classes de la 6e à la Terminale, {len(T)} professeurs, {len(SUBJECTS)} matières évaluées. {cap(PERIOD)}, {STAMP}. Comme c’est le dernier trimestre, chaque indicateur compare aussi le 3e trimestre au 1er pour mesurer les progrès de toute l’année.','Retenir en deux minutes ce qui a changé, ce qui va bien, ce qui inquiète, et les décisions à préparer pour le conseil de classe. Ce rapport garde la même structure à chaque trimestre.')
metric(36,y,166,fr(m[2])+'/20','Moyenne de l’établissement',f'1er trimestre : {fr(m[0])} • 2e : {fr(m[1])} • {signed(m[2]-m[0])} point sur l’année')
metric(215,y,166,pct(CE[2]['acq']),'Compétences acquises',f'contre {pct(CE[0]["acq"])} au 1er trimestre • {pct(CE[2]["never"])} du programme jamais évalué')
metric(394,y,165,pct(ok[2]),'Élèves à 10/20 ou plus',f'contre {pct(ok[0])} au 1er trimestre, soit {int(round((ok[2]-ok[0])*len(S)/100))} élèves de plus')
y+=102
metric(36,y,166,str(len(REDO)),'Redoublements proposés',f'+ {len(LIMIT)} cas à examiner • {len(EXAMRISK)} élèves de 3e et Tle à risque à l’examen',RED)
metric(215,y,166,f'{len(DROP)}','Décrochages récents','élèves au-dessus de 11 au 1er trimestre qui ont perdu au moins 2 points',GOLD)
metric(394,y,165,f'{COPIES_Y:,}'.replace(',',' '),'Copies corrigées sur l’année',f'≈ {HOURS:,.0f} heures de correction manuelle évitées aux professeurs'.replace(',',' '))
y+=110;txt(36,y,'Ce qu’il faut retenir',13,TEAL,'B');y+=22
facts=[
 f'<b>L’établissement progresse sur toute l’année.</b> La moyenne générale passe de {fr(m[0])} à {fr(m[2])}/20, et surtout la part des compétences acquises passe de {pct(CE[0]["acq"])} à {pct(CE[2]["acq"])} : les élèves savent réellement faire davantage de choses, et pas seulement mieux réussir les contrôles.',
 f'<b>Évaluer souvent fait la différence.</b> Chez les {g3["n"]} professeurs qui évaluent souvent (10 fois ou plus par trimestre), les compétences acquises progressent de <b>{pts(g3["gain"])}</b> et les notes de {signed(g3["notes"])}. Chez les {g0["n"]} qui n’évaluent presque jamais, de {pts(g0["gain"])} et {signed(g0["notes"])} ; {pct(g0["never"])} de leur programme n’a jamais été vérifié.',
 f'<b>La 4e est le seul niveau qui ne progresse pas</b> ({fr(lv4[0])} → {fr(lv4[2])}). Le recul se concentre en mathématiques, en 4e A en particulier ({fr(math4e[0])} → {fr(math4e[2])}), où les élèves n’ont été évalués que {math4["evals"]} fois par trimestre. La 4e regroupe {redo4} des {len(REDO)} propositions de redoublement, et ces élèves passent le BEPC dans un an.',
 f'<b>Examens : de forts écarts entre classes.</b> {cap(below_txt())}. {EX["3e"]["edge"]} élèves de 3e et {EX["Tle"]["edge"]} de Terminale sont à la frontière de la réussite : c’est sur eux que se joue le taux de l’établissement.',
 f'<b>{len(DROP)} élèves décrochent sans être encore parmi les plus faibles</b>, et <b>{pct(FORGET_ALL)} des compétences acquises cette année seront oubliées d’ici la rentrée</b> sans révision pendant les vacances.']
PREP=f'Valider ou amender les <b>{len(REDO)} propositions de redoublement</b> et trancher les <b>{len(LIMIT)} cas limites</b> (section {SEC["redoublement"]}) • Organiser avant les examens un stage de révision pour les <b>{EX["3e"]["edge"]+EX["Tle"]["edge"]} élèves à la frontière</b> (section {SEC["examens"]}) • Recevoir les <b>{len(DROP)} élèves en décrochage récent</b> et leurs familles (section {SEC["difficulte"]}) • Décider des cahiers de vacances personnalisés et accompagner les professeurs qui évaluent peu (section {SEC["priorites"]}).'
for i,f in enumerate(facts):
 rect(36,y,18,18,TEAL,r=9);txt(45,y+4,str(i+1),9,'#FFFFFF','B','c');h=para(62,y+1,f,497,8.9,leading=11.8);y+=max(h,18)+7
y+=4;_,ph=measure(PREP,495,8.9,'#FFFFFF',12);rect(36,y,523,ph+42,NAVY,r=8);txt(50,y+12,'À PRÉPARER POUR LE CONSEIL',9,'#FFFFFF','B');para(50,y+29,PREP,495,8.9,'#FFFFFF',12)
c.showPage()

# ---------- 2. L'année ----------
y=header(2,'L’évolution de la moyenne générale de chaque niveau sur les trois trimestres, puis la répartition de tous les élèves par tranche de moyenne au 1er et au 3e trimestre.','Vérifier que les progrès concernent tous les niveaux et tous les élèves, et pas seulement la moyenne. Un niveau qui stagne ou une tranche basse qui ne se vide pas signale un problème à traiter.')
chart('niveaux',36,y,523,236);y+=242
para(36,y,'<b>Lecture.</b> Chaque ligne est un niveau ; la ligne pointillée foncée est l’établissement. La pente indique la progression sur l’année.',523,8.4,MUTED);y+=24
chart('distribution',36,y,523,150);y+=160
low1=int((G[:,0]<8).sum());low3=int((G[:,2]<8).sum());top1=int((G[:,0]>=14).sum());top3=int((G[:,2]>=14).sum())
y=section(y,'Analyse',f'Tous les niveaux progressent sauf la 4e. Le meilleur parcours de l’année est celui de la <b>{best_lv}</b> ({signed(lvd[best_lv])} point) : l’entrée au collège est mieux réussie qu’au 1er trimestre ne le laissait craindre. Les élèves sous 8/20 passent de <b>{low1} à {low3}</b> et ceux à 14 ou plus de <b>{top1} à {top3}</b> : la progression touche les deux extrémités, et pas seulement les élèves moyens.')
c.showPage()

# ---------- 3. Les classes ----------
y=header(3,'Les 20 classes de l’établissement avec leur moyenne générale à chaque trimestre, leur progression sur l’année, la part d’élèves à 10 ou plus, le nombre d’élèves sous 8, et le nombre de redoublements proposés ou de cas à examiner.','Repérer les classes qui décrochent ou qui progressent, préparer chaque conseil de classe, et décider où placer l’aide (soutien, co-intervention, changement d’organisation).')
cols=[('CLASSE',52,'l'),('ÉLÈVES',44,'c'),('T1',44,'c'),('T2',44,'c'),('T3',44,'c'),('ÉVOL. ANNÉE',58,'c'),('≥ 10 AU T3',58,'c'),('SOUS 8 AU T3',62,'c'),('REDOUBLEMENT',64,'c'),('À EXAMINER',53,'c')]
rows=[]
for cc in classes:
 s_=CS[cc];d=s_['t'][2]-s_['t'][0];exam=cc.split()[0] in ('3e','Tle')
 rows.append([(cc,NAVY,'B'),s_['n'],fr(s_['t'][0]),fr(s_['t'][1]),(fr(s_['t'][2]),NAVY,'B'),(signed(d),dcol(d),'B'),pct(s_['ok']),(s_['under8'],RED if s_['under8']>=8 else NAVY,'R'),('après examen' if exam else s_['redo'],MUTED if exam else RED if s_['redo']>=4 else NAVY,'I' if exam else 'R'),('—' if exam else s_['limit'])])
y=table(y,cols,rows,rh=14.2,fs=7.8)+6
para(36,y,f'Couleurs : progression ≥ +0,3 point en vert, recul ≤ −0,3 en rouge. Pour la 3e et la Terminale, le passage dépend du BEPC et du BAC : voir section {SEC["examens"]}.',523,7.6,MUTED);y+=24
flat=[x for x in classes if cd[x]<.2]
y=section(y,'Analyse',f'Les plus fortes progressions de l’année sont en <b>{best_c[0]}</b> ({signed(cd[best_c[0]])}), <b>{best_c[1]}</b> ({signed(cd[best_c[1]])}) et <b>{best_c[2]}</b> ({signed(cd[best_c[2]])}). Les classes qui ne progressent pas sont {", ".join(f"<b>{x}</b> ({signed(cd[x])})" for x in flat)}. '+('Dans les trois 4e, le point commun est la même équipe de mathématiques, qui évalue très peu : c’est la piste à examiner en premier, avant de conclure à un « mauvais niveau » des élèves. ' if all(x in flat for x in ('4e A','4e B','4e C')) else '')+f'Parmi les 3e, la <b>{low3e}</b> a la moyenne la plus basse ({fr(CS[low3e]["t"][2])}).')
c.showPage()

# ---------- 4. Les matières ----------
y=header(4,'La moyenne de chaque matière dans chaque classe au 3e trimestre (carte de couleurs), puis l’évolution de chaque matière sur l’ensemble de l’établissement entre le 1er et le 3e trimestre.','Voir d’un coup d’œil où se situent les faiblesses (une matière, une classe, ou une matière dans une classe précise), et vérifier quelles matières ont réellement progressé dans l’année.')
chart('heatmap',36,y,523,226);y+=230
para(36,y,'<b>Lecture.</b> Rouge : moyenne de la classe sous 9 ; vert foncé : 13 ou plus. Une case vide signifie que la matière n’est pas enseignée dans cette classe.',523,8.2,MUTED);y+=22
chart('matieres',36,y,523,170);y+=178
trend_flat='est la seule matière en recul' if sd[flat_subj]<-.05 else 'est la seule matière quasiment immobile'
why_flat=f' : {flat_teachers[0]["name"]}, l’un de ses professeurs, n’évalue presque jamais (section {SEC["profs"]})' if flat_teachers else ''
y=section(y,'Analyse',f'Les deux matières les plus faibles de l’établissement sont <b>{ART[weak_subj[0]]}</b> ({fr(SUBJ[weak_subj[0]][2])}) et <b>{ART[weak_subj[1]]}</b> ({fr(SUBJ[weak_subj[1]][2])}), mais toutes deux progressent. <b>{cap(ART[prog_subj[0]])}</b> réalise la plus forte progression ({signed(sd[prog_subj[0]])}). <b>{cap(ART[flat_subj])}</b> {trend_flat} ({signed(sd[flat_subj])}){why_flat}. La carte montre aussi que la faiblesse en mathématiques n’est pas générale : elle se concentre en 4e et en série A.',fs=9.1)
c.showPage()

# ---------- 5. Les compétences ----------
y=header(5,'Une compétence est un savoir-faire précis du programme (par exemple « calculer des longueurs avec le théorème de Thalès »). Elle est <b>acquise</b> quand l’élève l’a réussie au moins trois fois sur des évaluations espacées dans le temps. Cette page montre, pour chaque niveau, la part des compétences du programme acquises, en cours, non acquises et jamais évaluées, au 1er et au 3e trimestre.','Mesurer ce que les élèves savent réellement faire, de façon plus juste et plus comparable que la moyenne des notes.')
cw=171
for i,(v,l,d_) in enumerate([(pct(CE[2]['acq']),'Compétences acquises',f'{pct(CE[0]["acq"])} au 1er trimestre, soit {pts(CE[2]["acq"]-CE[0]["acq"])}'),(f'{np.mean(per_student):.0f} / {np.mean(per_total):.0f}','Compétences par élève',f'acquises en moyenne au 3e trimestre, contre {np.mean(per_student1):.0f} au 1er'),(pct(CE[2]['never']),'Jamais évaluées',f'du programme de l’année, contre {pct(CE[0]["never"])} au 1er trimestre')]):
 metric(36+i*(cw+5),y,cw,v,l,d_,RED if i==2 else TEAL,h=84)
y+=96;chart('comp_niveaux',36,y,523,262);y+=268
box='<b>Pourquoi les compétences sont plus justes que les notes.</b> <b>1.</b> Une note mélange tout ; une compétence dit précisément ce qui est su et ce qui ne l’est pas. <b>2.</b> Une compétence n’est acquise qu’après plusieurs réussites espacées : un contrôle facile ou un barème généreux ne suffit pas. <b>3.</b> Les critères sont les mêmes pour toutes les classes et tous les professeurs : les résultats sont comparables.'
_,bh=measure(box,499,8.6,NAVY,11.4);rect(36,y,523,bh+16,PALE,r=6);para(48,y+8,box,499,8.6,NAVY,11.4);y+=bh+28
never2=sorted(LEVELS,key=lambda l:-CLV[l][2]['never'])[:2]
y=section(y,'Analyse',f'Les compétences acquises progressent le plus en <b>{best_cg}</b> ({pts(clvgain[best_cg])}) et le moins en <b>{worst_cg}</b> ({pts(clvgain[worst_cg])}), ce qui confirme le constat des notes. La part jamais évaluée est le signal le plus utile : en {" et en ".join(f"<b>{lv}</b> ({pct(CLV[lv][2]['never'])})" for lv in never2)}, une partie du programme n’a jamais été vérifiée. Ce n’est pas un problème d’élèves, mais de fréquence d’évaluation.',fs=9)
c.showPage()

# ---------- 6. Compétences par matière ----------
y=header(6,'La part des compétences acquises dans chaque matière et à chaque niveau au 3e trimestre, les compétences les plus fragiles de l’établissement, et la part de ce qui a été acquis cette année qui sera oubliée d’ici la rentrée sans révision.','Cibler les remédiations sur des savoir-faire précis plutôt que sur une matière entière, et préparer des révisions personnalisées pour les vacances.')
chart('comp_heat',36,y,523,168);y+=172
para(36,y,'<b>Lecture.</b> Part des compétences du programme de l’année acquises par les élèves au 3e trimestre. Case vide : matière non enseignée à ce niveau.',523,8,MUTED);y+=20
txt(36,y,'Les compétences les plus fragiles de l’établissement',11.5,TEAL,'B');y+=18
cols=[('COMPÉTENCE',230,'l'),('MATIÈRE',96,'l'),('NIVEAU',46,'c'),('ACQUISE AU T1',75,'c'),('ACQUISE AU T3',76,'c')]
frag=CAT[:7]
rows=[[(d_['label'],NAVY,'B'),d_['subj'],d_['lv'],pct(d_['t1'])+' des élèves',(pct(d_['t3'])+' des élèves',RED,'B')] for d_ in frag]
y=table(y,cols,rows,rh=13.4,fs=7.6)+12
txt(36,y,'Ce qui sera oublié d’ici la rentrée sans révision',11.5,TEAL,'B');y+=16
chart('oubli',36,y,523,118);y+=122
y=section(y,'Analyse',f'Les compétences les plus fragiles sont des savoir-faire de méthode : {frag[0]["label"].lower()} ({frag[0]["lv"]}), {frag[1]["label"].lower()} ({frag[1]["lv"]}), {frag[2]["label"].lower()} ({frag[2]["lv"]}). Chacune justifie une reprise ciblée, plus efficace qu’une révision du chapitre entier. Sans révision, <b>{pct(FORGET_ALL)} des compétences acquises cette année seront oubliées à la rentrée</b>, et jusqu’à {pct(FORGET[worst_f])} en {worst_f}{', où les évaluations ont été les moins fréquentes' if worst_f==min(LEVAL,key=LEVAL.get) else ''}. Un cahier de vacances personnalisé, construit sur les lacunes de chaque élève, en limite l’effet.',fs=8.9)
c.showPage()

# ---------- 7. Les professeurs ----------
y=header(7,'Pour chaque professeur, regroupé par matière : ses classes, le <b>rythme de ses évaluations</b> (un point par évaluation corrigée par classe et par trimestre) et l’<b>évolution des compétences acquises par ses élèves</b>, du 1er au 3e trimestre.','Voir en un coup d’œil qui évalue régulièrement et dont les élèves progressent, pour accompagner les équipes et diffuser les bonnes pratiques. Ces indicateurs servent à soutenir, jamais à classer.')
lx=40
for g in range(4):
 c.setFillColor(HexColor(FCOL[g]));c.circle(lx+3,H-y-5,2.6,fill=1,stroke=0);lab=f'{FREQ[g][0]} ({FLAB[g]})';txt(lx+9,y+1.3,lab,7.3,NAVY);lx+=pdfmetrics.stringWidth(lab,'R',7.3)+20
rect(lx+4,y+1.5,22,7,LIGHT,r=2);rect(lx+4,y+1.5,12,7,TEAL,r=2);txt(lx+31,y+1.3,'compétences : T1 foncé, gain au T3 clair',7.3,NAVY)
y+=18
LEFT=['Mathématiques','Physique-Chimie','SVT','Philosophie','EDHC'];RIGHT=['Français','Anglais','Espagnol','Histoire-Géographie']
PW=258;RH=17
def panel(x,y,subj):
 ts=[t for t in T if t['subject']==subj];mean_gain=np.mean([t['gain'] for t in ts])
 rect(x,y,PW,17,SUBJCOL[subj],r=4);txt(x+7,y+4.6,subj.upper(),7.8,'#FFFFFF','B');txt(x+PW-7,y+4.9,f'compétences acquises : {pts(mean_gain)} en moyenne',6.6,'#FFFFFF','R','r')
 y+=19
 for i,t in enumerate(ts):
  if i%2==0:rect(x,y,PW,RH,PALE,r=2)
  txt(x+5,y+5.2,f'{t["id"]}',6.4,MUTED,'B');txt(x+17,y+5,t['name'],fit(t['name'],76,7.4,'B'),NAVY,'B')
  ct=compact(t['classes']);txt(x+96,y+5.3,ct,fit(ct,58,6.3,'R',4.6),MUTED)
  for k in range(12):
   c.setFillColor(HexColor(FCOL[t['group']] if k<t['evals'] else '#DCE4E8'));c.circle(x+158+k*4.3,H-y-RH/2,1.6,fill=1,stroke=0)
  bx,bw=x+212,32;rect(bx,y+5.3,bw,6.4,'#E3E9EC',r=2);rect(bx,y+5.3,bw*t['acq'][2]/100,6.4,LIGHT,r=2);rect(bx,y+5.3,bw*t['acq'][0]/100,6.4,TEAL,r=2)
  txt(x+PW-3,y+5.2,pts(t['gain']).replace(' pts',''),7,TEAL if t['gain']>=15 else RED if t['gain']<5 else GOLD,'B','r')
  y+=RH
 return y+9
yl=y
for subj in LEFT:yl=panel(36,yl,subj)
yr=y
for subj in RIGHT:yr=panel(301,yr,subj)
y=max(yl,yr);assert y<700,y
best3=sorted(T,key=lambda t:-t['gain'])[:3]
y=section(y+2,'À retenir',f'Les élèves de <b>{best3[0]["name"]}</b> ({best3[0]["subject"].lower()}), <b>{best3[1]["name"]}</b> ({best3[1]["subject"].lower()}) et <b>{best3[2]["name"]}</b> ({best3[2]["subject"].lower()}) ont le plus progressé en compétences ({pts(best3[0]["gain"])}, {pts(best3[1]["gain"])}, {pts(best3[2]["gain"])}). Dans chaque matière, les lignes dont les points sont presque tous gris sont celles où la progression est la plus faible : la fréquence d’évaluation explique l’essentiel des écarts (sections {SEC["frequence"]} et {SEC["profs_comp"]}).',fs=8.8)
c.showPage()

# ---------- 8. Évaluer souvent ----------
y=header(8,'Les professeurs sont répartis en quatre groupes selon le nombre d’évaluations qu’ils corrigent par classe et par trimestre. Pour chaque groupe : la progression des notes de leurs élèves, la progression des compétences acquises, et la part du programme jamais évaluée.','Mesurer l’effet de la fréquence d’évaluation sur les progrès des élèves, et décider qui accompagner en priorité.')
chart('frequence',36,y,523,178);y+=188
rect(36,y,523,15,NAVY,r=3);xx=36
for lab,w,al in [('GROUPE',111,'l'),('PROF.',30,'c'),('NOMS (NUMÉROS DE LA SECTION 7)',382,'l')]:txt(xx+4 if al=='l' else xx+w/2,y+4.2,lab,6.6,'#FFFFFF','B',al);xx+=w
y+=17
for g in range(4):
 names=', '.join(f'{t["name"]} ({t["id"]})' for t in GROUPS[g]);_,h=measure(names,374,7.6,NAVY,10);rh=h+8
 if g%2==0:rect(36,y,523,rh,PALE)
 txt(40,y+4,f'{FREQ[g][0]} ({FLAB[g]})',7.8,FCOL[g],'B');txt(162,y+4,str(GSTAT[g]['n']),8,NAVY,'B','c');para(181,y+4,names,374,7.6,NAVY,10);y+=rh
y+=14
ratio=g3['gain']/max(g0['gain'],.5)
y=section(y,'Ce que montrent ces chiffres',f'L’écart est massif. Chez les professeurs qui évaluent <b>souvent</b>, les compétences acquises progressent de <b>{pts(g3["gain"])}</b> sur l’année, soit environ {ratio:.0f} fois plus que chez ceux qui n’évaluent <b>presque jamais</b> ({pts(g0["gain"])}). Les notes suivent la même pente : {signed(g3["notes"])} contre {signed(g0["notes"])}. Et dans les classes du groupe « presque jamais », <b>{pct(g0["never"])} du programme n’a jamais été vérifié</b> : personne ne sait si ces compétences sont acquises.')+12
y=section(y,'Pourquoi','Ce résultat rejoint la recherche en sciences cognitives : se tester régulièrement ancre les apprentissages bien mieux que relire ou passer un gros contrôle par mois, et chaque évaluation corrigée permet de reprendre une erreur avant qu’elle ne s’installe. La fréquence d’évaluation n’est pas une affaire de bonne volonté : sans outil, 8 évaluations par trimestre et par classe représentent des centaines de copies. C’est précisément la charge que la plateforme retire aux professeurs.',fs=9)
c.showPage()

# ---------- 9. Professeurs, évaluations, compétences ----------
y=header(9,'Chaque flèche est un professeur (numéro de la section 7). Elle part de la part de compétences acquises par ses élèves au 1er trimestre et monte jusqu’au 3e trimestre. Sa position horizontale indique le nombre d’évaluations corrigées par classe et par trimestre.','Relier en un seul graphique les professeurs, les progrès réels de leurs élèves et leur rythme d’évaluation, puis organiser l’entraide entre collègues d’une même matière.')
chart('profs_comp',36,y,523,272);y+=278
y=section(y,'Lecture',f'Plus on va vers la droite, plus les flèches sont longues : les élèves des professeurs qui évaluent souvent acquièrent beaucoup plus de compétences dans l’année. À gauche, les flèches sont presque plates. <b>{pc3["name"]}</b> (n° {pc3["id"]}, physique-chimie en 3e, {pc3["evals"]} évaluations par trimestre) fait passer ses élèves de {pct(pc3["acq"][0])} à {pct(pc3["acq"][2])} de compétences acquises. <b>{math4["name"]}</b> (n° {math4["id"]}, mathématiques en 4e, {math4["evals"]} évaluations par trimestre) : de {pct(math4["acq"][0])} à {pct(math4["acq"][2])}. C’est le service à accompagner en priorité, avec des évaluations préparées et corrigées par la plateforme : un soutien, pas un jugement.',fs=8.9)+10
txt(36,y,'Binômes d’entraide proposés',11.5,TEAL,'B');y+=17
pairs=[]
for subj in SUBJECTS:
 ts=[t for t in T if t['subject']==subj]
 if len(ts)<2:continue
 lo_t=min(ts,key=lambda t:t['gain']);hi_t=max(ts,key=lambda t:t['gain'])
 if hi_t['gain']-lo_t['gain']>=8:pairs.append((subj,lo_t,hi_t))
pairs.sort(key=lambda p:p[1]['gain'])
cols=[('MATIÈRE',96,'l'),('PROFESSEUR ACCOMPAGNÉ',140,'l'),('PROFESSEUR RÉFÉRENT',140,'l'),('À PARTAGER EN PRIORITÉ',147,'l')]
rows=[[subj,f'{lo["name"]} ({pts(lo["gain"])} • {lo["evals"]} éval.)',(f'{hi["name"]} ({pts(hi["gain"])} • {hi["evals"]} éval.)',TEAL,'B'),f'rythme d’évaluation : {lo["evals"]} → {hi["evals"]} par trimestre' if hi['evals']-lo['evals']>=2 else 'méthodes de remédiation'] for subj,lo,hi in pairs[:5]]
y=table(y,cols,rows,rh=12.8,fs=7.3)+5
para(36,y,'Binôme proposé quand, dans une même matière, l’écart de progression des compétences entre deux collègues dépasse 8 points. Entre parenthèses : compétences acquises en plus sur l’année, évaluations par trimestre.',523,7.4,MUTED)
c.showPage()

# ---------- 10. Examens ----------
y=header(10,'Pour chaque classe de 3e et de Terminale, la probabilité de réussite à l’examen estimée à partir des résultats du 3e trimestre, comparée au taux national de la dernière session (2025 : BEPC 51,41 %, BAC 40,15 %). Les élèves sont répartis en trois groupes : sécurisés, à la frontière, fragiles.','Savoir où l’établissement se situe avant l’examen et cibler les révisions sur les élèves pour qui elles changeront le résultat, c’est-à-dire ceux qui sont à la frontière.')
chart('examens',36,y,523,185);y+=192
cols=[('CLASSE',70,'l'),('EXAMEN',60,'c'),('ÉLÈVES',55,'c'),('RÉUSSITE ESTIMÉE',90,'c'),('TAUX NATIONAL',78,'c'),('SÉCURISÉS',56,'c'),('À LA FRONTIÈRE',62,'c'),('FRAGILES',52,'c')]
rows=[]
for cc in EXC:
 e=EX[cc];nat=NATIONAL[e['lvl']]
 rows.append([(cc,NAVY,'B'),nat[0],e['n'],(pct(e['rate']),TEAL if e['rate']>=nat[1]+10 else GOLD if e['rate']>=nat[1] else RED,'B'),fr(nat[1],2)+' %',e['safe'],(e['edge'],GOLD,'B'),(e['fragile'],RED,'R')])
y=table(y,cols,rows,rh=15,fs=8)+6
para(36,y,f'Sécurisés : indice du 3e trimestre ≥ 11,5. À la frontière : de 9 à 11,5. Fragiles : sous 9. L’indice est la moyenne coefficientée des matières d’examen (méthode en section {SEC["methode"]}).',523,7.6,MUTED);y+=26
gain=100/EX['3e']['n'];tle=('Tle A','Tle C','Tle D');best_t=max(tle,key=lambda k:EX[k]['rate']);worst_t=min(tle,key=lambda k:EX[k]['rate'])
def vs(lv):return 'au-dessus du taux national' if EX[lv]['rate']>=NATIONAL[lv][1] else 'sous le taux national'
y=section(y,'Analyse',f'Sur l’ensemble des 3e, la réussite estimée au BEPC est de <b>{pct(EX["3e"]["rate"])}</b>, {vs("3e")} ; en Terminale, au BAC, de <b>{pct(EX["Tle"]["rate"])}</b>, {vs("Tle")}. Mais les écarts entre classes sont forts : {below_txt()}, et la <b>{worst_t}</b> a {fr(EX[best_t]["rate"]-EX[worst_t]["rate"],0)} points de retard sur la {best_t}. <b>{EX["3e"]["edge"]} élèves de 3e</b> et <b>{EX["Tle"]["edge"]} de Terminale</b> sont à la frontière. En 3e, chaque élève qui bascule du bon côté ajoute environ {fr(gain)} point au taux de réussite du niveau.')+12
def edge_subjects(lv):
 ss=[s_ for s_ in S if s_['level']==lv and 9<=exam_index(s_)<11.5];cnt={}
 for s_ in ss:
  for x in core(s_):cnt.setdefault(x,[0,0]);cnt[x][1]+=1;cnt[x][0]+=s_['marks'][x][2]<10
 return sorted(((x,v[0]/v[1]*100) for x,v in cnt.items() if v[1]>=5),key=lambda kv:-kv[1])[:4]
EDGE={lv:edge_subjects(lv) for lv in ('3e','Tle')}
y=section(y,'Sur quoi faire réviser les élèves à la frontière','Part des élèves à la frontière qui sont sous 10 dans chaque matière au 3e trimestre. <b>En 3e :</b> '+', '.join(f'{ART[x]} ({pct(v)})' for x,v in EDGE['3e'])+'. <b>En Terminale :</b> '+', '.join(f'{ART[x]} ({pct(v)})' for x,v in EDGE['Tle'])+f'. Le stage doit commencer par {ART[EDGE["3e"][0][0]]} en 3e et par {ART[EDGE["Tle"][0][0]]} en Terminale, avec des copies différentes pour chaque élève selon ses propres lacunes.',fs=9)
c.showPage()

# ---------- 11. Meilleurs élèves ----------
y=header(11,'Les 15 meilleures moyennes annuelles de l’établissement, tous niveaux confondus, puis les 10 plus fortes progressions de l’année. Les distinctions sont proposées selon les seuils habituels de l’établissement.','Préparer les félicitations et les distinctions du conseil, valoriser aussi les élèves qui ont le plus progressé, et repérer les profils à orienter vers les filières exigeantes.')
top=sorted(S,key=lambda s:-s['annual'])[:15]
def best2(s):return ', '.join([SHORT[x] for x in sorted(core(s),key=lambda x:-s['marks'][x][2])][:2])
def distinction(a):return 'Félicitations' if a>=16 else 'Encouragements' if a>=14 else 'Tableau d’honneur' if a>=12 else ''
def acq_all(s,tr):return sum(c_['acq'][tr]*c_['n'] for c_ in s['comp'].values())/sum(c_['n'] for c_ in s['comp'].values())
cols=[('RANG',30,'c'),('ÉLÈVE',116,'l'),('CLASSE',46,'c'),('T1',32,'c'),('T3',32,'c'),('ANNUELLE',50,'c'),('COMPÉTENCES ACQUISES',72,'c'),('POINTS FORTS',75,'l'),('DISTINCTION',70,'l')]
rows=[[i+1,(s['name'],NAVY,'B'),s['cls'],fr(s['gen'][0]),fr(s['gen'][2]),(fr(s['annual']),TEAL,'B'),pct(acq_all(s,2)),best2(s),distinction(s['annual'])] for i,s in enumerate(top)]
y=table(y,cols,rows,rh=13.2,fs=7.6)+14
txt(36,y,'Les plus fortes progressions de l’année',12,TEAL,'B');y+=20
prog=sorted(S,key=lambda s:-s['delta'])[:10]
cols=[('ÉLÈVE',136,'l'),('CLASSE',52,'c'),('T1',42,'c'),('T2',42,'c'),('T3',42,'c'),('PROGRESSION',62,'c'),('COMPÉTENCES',62,'c'),('PLUS FORTE HAUSSE',85,'l')]
rows=[[(s['name'],NAVY,'B'),s['cls'],fr(s['gen'][0]),fr(s['gen'][1]),fr(s['gen'][2]),(signed(s['delta']),TEAL,'B'),pts(acq_all(s,2)-acq_all(s,0)),(lambda x:f'{SHORT[x]} ({signed(s["marks"][x][2]-s["marks"][x][0])})')(max(core(s),key=lambda x:s['marks'][x][2]-s['marks'][x][0]))] for s in prog]
y=table(y,cols,rows,rh=13.2,fs=7.6)+12
nf=sum(s['annual']>=16 for s in S);ne=sum(14<=s['annual']<16 for s in S);nt=sum(12<=s['annual']<14 for s in S);lv_top={}
for s in top:lv_top[s['level']]=lv_top.get(s['level'],0)+1
y=section(y,'Analyse',f'Distinctions proposées : <b>{nf} félicitations</b> (16 ou plus), <b>{ne} encouragements</b> (14 à 16) et <b>{nt} inscriptions au tableau d’honneur</b> (12 à 14). Les meilleures moyennes se concentrent en {", ".join(f"{k} ({v})" for k,v in sorted(lv_top.items(),key=lambda kv:-kv[1])[:2])}. Les fortes progressions méritent la même reconnaissance que les fortes moyennes : {sum(s["gen"][0]<10 for s in prog)} de ces 10 élèves étaient sous 10 au 1er trimestre, et la félicitation de l’effort est ce qui les fera continuer.',fs=9.1)
c.showPage()

# ---------- 12. Difficulté ----------
y=header(12,'Les 15 moyennes annuelles les plus basses de l’établissement, tous niveaux confondus, avec leur évolution, les matières principales sous 8 et le type de difficulté. Puis les élèves en décrochage récent, qui ne sont pas encore parmi les plus faibles.','Organiser le suivi individuel avant la fin de l’année et dès la rentrée : qui recevoir, avec quelle priorité, et pour travailler quoi.')
low=sorted(S,key=lambda s:s['annual'])[:15]
def profile(s):
 if s['delta']>=1:return ('En remontée',TEAL)
 if s['delta']<=-1:return ('En aggravation',RED)
 return ('Difficulté installée',GOLD)
cols=[('ÉLÈVE',118,'l'),('CLASSE',44,'c'),('T1',32,'c'),('T2',32,'c'),('T3',32,'c'),('ANNUELLE',48,'c'),('MATIÈRES PRINCIPALES SOUS 8',130,'l'),('SITUATION',87,'l')]
rows=[[(s['name'],NAVY,'B'),s['cls'],fr(s['gen'][0]),fr(s['gen'][1]),fr(s['gen'][2]),(fr(s['annual']),RED,'B'),', '.join(SHORT[x] for x in weak(s)) or '—',(profile(s)[0],profile(s)[1],'B')] for s in low]
y=table(y,cols,rows,rh=12.9,fs=7.4)+12
DROP_SHOWN=DROP[:18]
txt(36,y,f'Décrochages récents ({len(DROP)} élèves'+(f', les {len(DROP_SHOWN)} plus marqués)' if len(DROP)>len(DROP_SHOWN) else ')'),12,GOLD,'B');y+=20
cols=[('ÉLÈVE',130,'l'),('CLASSE',50,'c'),('T1',44,'c'),('T2',44,'c'),('T3',44,'c'),('ÉVOLUTION',62,'c'),('MATIÈRES LES PLUS EN BAISSE',149,'l')]
rows=[[(s['name'],NAVY,'B'),s['cls'],fr(s['gen'][0]),fr(s['gen'][1]),fr(s['gen'][2]),(signed(s['delta']),RED,'B'),', '.join(f'{SHORT[x]} ({signed(s["marks"][x][2]-s["marks"][x][0])})' for x in sorted(core(s),key=lambda x:s['marks'][x][2]-s['marks'][x][0])[:2])] for s in DROP_SHOWN]
y=table(y,cols,rows,rh=12.4,fs=7.4)+12
inst=sum(profile(s)[0]=='Difficulté installée' for s in low);agg=sum(profile(s)[0]=='En aggravation' for s in low);rem=sum(profile(s)[0]=='En remontée' for s in low)
mf='Les mathématiques ou le français sont en cause pour chacun d’eux.' if all(('Mathématiques' in weak(s)) or ('Français' in weak(s)) for s in low) else 'Les mathématiques et le français sont le plus souvent en cause.'
y=section(y,'Analyse',f'Parmi les 15 élèves les plus en difficulté, <b>{inst}</b> sont en difficulté installée (pas d’évolution sur l’année), <b>{agg}</b> s’aggravent et <b>{rem}</b> remontent. {mf} Les décrochages récents sont le signal le plus urgent : une chute de plus de 2 points en deux trimestres, chez des élèves jusque-là solides, a souvent une cause extérieure à la classe. Un entretien avec l’élève et la famille doit précéder toute décision.',fs=8.8)
c.showPage()

# ---------- 13. Redoublements ----------
y=header(13,'Les élèves pour qui le redoublement est proposé, selon des critères identiques pour tous : moyenne annuelle sous 8,5, pas de progression sur l’année (moins de +0,5 point entre le 1er et le 3e trimestre) et au moins deux matières principales sous 8. Aux 1er et 2e trimestres, cette section liste les élèves à risque de redoublement.','Préparer le conseil de classe avec une liste objective et argumentée. La décision finale reste celle du conseil, qui tient compte de ce que les chiffres ne disent pas : santé, situation familiale, assiduité, attitude.')
REDO_S=sorted(REDO,key=lambda s:(classes.index(s['cls']),s['annual']))
def obs(s):
 w=weak(s)
 if s['delta']<=-.5:return f'Recul sur l’année ({signed(s["delta"])})'
 if len(w)>=4:return f'Lacunes dans {len(w)} matières principales'
 return 'Aucune progression sur l’année'
rh=12.2 if len(REDO_S)<=40 else max(9.4,556/len(REDO_S))
cols=[('ÉLÈVE',122,'l'),('CLASSE',48,'c'),('T1',34,'c'),('T2',34,'c'),('T3',34,'c'),('ANNUELLE',50,'c'),('MATIÈRES PRINCIPALES SOUS 8',100,'l'),('OBSERVATION',101,'l')]
rows=[[(s['name'],NAVY,'B'),s['cls'],fr(s['gen'][0]),fr(s['gen'][1]),fr(s['gen'][2]),(fr(s['annual']),RED,'B'),', '.join(SHORT[x] for x in weak(s)),obs(s)] for s in REDO_S]
y=table(y,cols,rows,rh=rh,fs=min(7.2,rh*.62))+8
byl={}
for s in REDO:byl[s['level']]=byl.get(s['level'],0)+1
para(36,y,'<b>Répartition :</b> '+' • '.join(f'{lv} : {byl[lv]}' for lv in LEVELS if lv in byl)+f'. <b>Total : {len(REDO)} élèves</b>, soit {fr(len(REDO)/len(nonexam)*100)} % des élèves des classes hors examen.',523,8.6,limit=800)
c.showPage()
y=header(13,'Les élèves proches du seuil, qui ne remplissent pas tous les critères de la page précédente : moyenne annuelle sous 9,5 avec des lacunes dans au moins deux matières principales, ou moyenne sous 8,5 avec une progression réelle. La recommandation dépend de la tendance de l’année.','Trancher au cas par cas. Un élève en nette remontée peut passer avec un contrat de soutien ; un élève sans progression relève plutôt d’un redoublement accompagné.',suffix=' (suite)')
LIM=sorted(LIMIT,key=lambda s:(classes.index(s['cls']),s['annual']))
def reco(s):return ('Passage + soutien',TEAL) if s['delta']>=1 else ('À débattre',GOLD)
half=(len(LIM)+1)//2;rh=min(11.4,540/half)
txt(36,y,'Cas à examiner en conseil',11.5,TEAL,'B');y+=18
for col_i,chunk in enumerate([LIM[:half],LIM[half:]]):
 x0=36+col_i*265;yy=y;rect(x0,yy,258,14,NAVY,r=3)
 for lab,xo,al in [('ÉLÈVE',5,'l'),('CLASSE',123,'c'),('ANN.',156,'c'),('ÉVOL.',184,'c'),('AVIS',228.5,'c')]:txt(x0+xo,yy+4,lab,6.4,'#FFFFFF','B',al)
 yy+=15
 for i,s in enumerate(chunk):
  if i%2==0:rect(x0,yy,258,rh,PALE)
  fs_=min(6.9,rh*.6);ty=yy+(rh-fs_)/2
  txt(x0+5,ty,s['name'],fit(s['name'],100,fs_,'B'),NAVY,'B');txt(x0+123,ty,s['cls'],fs_,NAVY,'R','c');txt(x0+156,ty,fr(s['annual']),fs_,NAVY,'B','c')
  txt(x0+184,ty,signed(s['delta']),fs_,dcol(s['delta']),'B','c');lab,cl=reco(s);rect(x0+201,yy+1.2,55,rh-2.4,cl,r=4);txt(x0+228.5,yy+(rh-5.6)/2,lab,fit(lab,51,5.9,'B',4.8),'#FFFFFF','B','c')
  yy+=rh
y=y+15+half*rh+10
npass=sum(reco(s)[0]=='Passage + soutien' for s in LIMIT)
y=section(y,'Classes d’examen et synthèse',f'<b>{npass} élèves</b> sont en nette remontée et peuvent passer avec un contrat de soutien ; <b>{len(LIMIT)-npass}</b> sont à débattre. En 3e et en Terminale, aucune proposition n’est faite avant les résultats : <b>{len(EXAMRISK)} élèves</b> ont une moyenne annuelle sous 9 et sont les plus exposés à l’échec au BEPC ou au BAC (section {SEC["examens"]}).',fs=8.8)
c.showPage()

# ---------- 14. Priorités ----------
y=header(14,'Les actions recommandées à partir de ce bilan, classées par urgence, avec pour chacune le constat qui la justifie, l’action proposée et l’indicateur qui permettra de vérifier son effet au prochain bilan. Aux 1er et 2e trimestres, cette section porte sur le trimestre suivant.','Transformer le bilan en décisions, et mesurer au trimestre suivant si elles ont fonctionné.')
few=GROUPS[0]+GROUPS[1]
frA=next(t for t in T if t['subject']=='Français' and 'Tle A' in t['classes']);phT=next(t for t in T if t['subject']=='Philosophie' and 'Tle A' in t['classes']);ph1=next(t for t in T if t['subject']=='Philosophie' and '1ère A' in t['classes'])
assert '1ère A' in frA['classes'] and frA['group']==0 and phT['evals']<ph1['evals']
prios=[
 ('Avant les examens','Stage de révision ciblé pour les élèves à la frontière',f'<b>Constat :</b> {EX["3e"]["edge"]} élèves de 3e et {EX["Tle"]["edge"]} de Terminale sont entre 9 et 11,5 ; {below_txt()}. <b>Action :</b> deux semaines de copies de révision personnalisées sur les lacunes de chacun, en commençant par {ART[EDGE["3e"][0][0]]} en 3e et {ART[EDGE["Tle"][0][0]]} en Terminale. <b>Indicateur :</b> taux de réussite réel au BEPC et au BAC, comparé à l’estimation de ce bilan.'),
 ('Avant la fin de l’année',f'Recevoir les {len(DROP)} élèves en décrochage récent',f'<b>Constat :</b> {len(DROP)} élèves solides au 1er trimestre ont perdu au moins 2 points. <b>Action :</b> entretien avec l’élève et la famille, puis un professeur référent pour chacun. <b>Indicateur :</b> moyenne de ces élèves au 1er trimestre de l’an prochain.'),
 ('Avant les vacances','Cahiers de vacances personnalisés',f'<b>Constat :</b> sans révision, {pct(FORGET_ALL)} des compétences acquises cette année seront oubliées à la rentrée, jusqu’à {pct(FORGET[worst_f])} en {worst_f}. <b>Action :</b> un cahier imprimé pour chaque élève, construit sur ses propres compétences fragiles, en priorité pour les {len(REDO)} redoublants proposés et les élèves de 4e. <b>Indicateur :</b> compétences acquises au diagnostic de rentrée, comparées à celles de fin d’année.'),
 ('Dès la rentrée','Plan mathématiques pour la future 3e',f'<b>Constat :</b> la 4e est le seul niveau sans progrès, avec seulement {math4["evals"]} évaluations par trimestre en mathématiques, et ses élèves passent le BEPC dans un an. <b>Action :</b> diagnostic en septembre, groupes de besoin, binôme entre {math4["name"]} et un collègue dont les élèves progressent. <b>Indicateur :</b> compétences acquises en mathématiques en 3e au 1er trimestre.'),
 ('Dès la rentrée','Évaluer au moins 8 fois par trimestre dans chaque classe',f'<b>Constat :</b> {pts(g3["gain"])} de compétences acquises chez les professeurs qui évaluent souvent, {pts(g0["gain"])} chez ceux qui n’évaluent presque jamais. <b>Action :</b> objectif commun de 8 évaluations courtes par classe et par trimestre, préparées et corrigées par la plateforme ; accompagnement individuel des {len(few)} professeurs qui évaluent 5 fois ou moins. <b>Indicateur :</b> évaluations par professeur et part du programme jamais évaluée.'),
 ('Au 1er trimestre','Français et philosophie en série A',f'<b>Constat :</b> la Tle A a la réussite estimée la plus basse au BAC ({pct(EX["Tle A"]["rate"])}) ; le français n’y est évalué que {frA["evals"]} fois par trimestre, et la philosophie {phT["evals"]} fois en Tle A, contre {ph1["evals"]} en 1ère A. <b>Action :</b> un entraînement court et régulier à la dissertation et au commentaire, corrigé avec une grille commune. <b>Indicateur :</b> compétences acquises en français et en philosophie en 1ère A et Tle A.')]
for i,(when,title,body) in enumerate(prios):
 _,h=measure(body,452,8.4,NAVY,11);bh=h+33;col=RED if i<3 else TEAL
 rect(36,y,523,bh,'#FFFFFF',r=8,stroke=LINE);rect(36,y,40,bh,col,r=8);rect(60,y,16,bh,col)
 txt(56,y+bh/2-9,str(i+1),15,'#FFFFFF','B','c');txt(88,y+8,title,10.3,NAVY,'B');txt(551,y+9,when.upper(),7,col,'B','r')
 para(88,y+24,body,452,8.4,NAVY,11);y+=bh+8
c.showPage()

# ---------- 15. Méthode ----------
y=header(15,'Comment sont calculés les indicateurs de ce rapport, et quels seuils sont utilisés.','Lire les chiffres correctement, et garder les mêmes définitions d’un trimestre à l’autre pour pouvoir comparer.')
for title,body in [
 ('Moyennes','Moyenne trimestrielle d’une matière : moyenne des évaluations du trimestre. Moyenne générale : moyenne des matières évaluées pondérée par les coefficients de l’établissement, propres à chaque niveau et à chaque série. Moyenne annuelle : moyenne des trois moyennes générales trimestrielles. Valeurs affichées arrondies au dixième.'),
 ('Compétences','Savoir-faire précis tirés des programmes de chaque matière. <b>Acquise</b> : réussie au moins trois fois sur des évaluations espacées d’au moins une semaine, dont la dernière réussite a moins de six semaines. <b>En cours</b> : réussie au moins une fois. <b>Non acquise</b> : évaluée sans réussite. <b>Jamais évaluée</b> : au programme de l’année mais absente de toutes les évaluations. Les parts sont pondérées par le nombre de compétences de chaque matière.'),
 ('Oubli d’ici la rentrée','Part des compétences acquises dont la probabilité de rappel, estimée par la courbe de l’oubli de chaque élève, passera sous le seuil de maîtrise après dix semaines sans révision.'),
 ('Progression et fréquence d’évaluation','Progression : différence entre le 3e et le 1er trimestre (au 2e trimestre : entre le 2e et le 1er). Pour un professeur : progression de ses élèves dans sa matière. Fréquence : évaluations corrigées par classe et par trimestre ; groupes « presque jamais » (2 ou moins), « peu » (3 à 5), « régulièrement » (6 à 9), « souvent » (10 ou plus).'),
 ('Redoublement','Proposé si les trois critères sont réunis : moyenne annuelle sous 8,5 ; progression inférieure à +0,5 point ; au moins deux matières principales (français, mathématiques, et toute matière de coefficient 3 ou plus) sous 8 au 3e trimestre. Cas à examiner : moyenne sous 9,5 avec deux matières principales sous 8, ou sous 8,5 avec une progression réelle. Aucune proposition en 3e et en Terminale avant le BEPC et le BAC.'),
 ('Décrochage récent','Élève dont la moyenne générale était d’au moins 11 au 1er trimestre et qui a perdu au moins 2 points au 3e.'),
 ('Préparation aux examens','Indice : moyenne coefficientée du 3e trimestre dans les matières d’examen. La probabilité de réussite de chaque élève est estimée à partir de cet indice ; la réussite estimée d’une classe est la moyenne de ces probabilités. Repère : taux national de la dernière session publiée (2025 : BEPC 51,41 %, BAC 40,15 %).'),
 ('Distinctions et temps de correction','Tableau d’honneur à partir de 12 de moyenne annuelle, encouragements à partir de 14, félicitations à partir de 16. Temps de correction évité : 3 minutes par copie corrigée par la plateforme, hors validation des réponses douteuses.'),
 ('Limites','Les indicateurs signalent des situations à examiner ; ils ne remplacent ni la connaissance des élèves par les équipes, ni l’échange avec les familles. Aucune conclusion sur une cause personnelle, familiale ou médicale ne peut être tirée de ces seuls résultats.')]:
 y=section(y,title,body,fs=8.7)+8
c.showPage();c.save()
assert PAGE[0]==TOTAL,PAGE[0]

assert PAGEOF==PLANNED,(PAGEOF,PLANNED)
print(json.dumps(dict(pages=PAGE[0],mean=[round(v,2) for v in m],acq=[round(x['acq'],1) for x in CE],never=round(CE[2]['never'],1),redo=len(REDO),limit=len(LIMIT),drop=len(DROP),below=BELOW,groups=[(g['n'],round(g['notes'],2),round(g['gain'],1),round(g['never'],1)) for g in GSTAT],forget=round(FORGET_ALL,1),worst_f=worst_f,ex={k_:round(v['rate'],1) for k_,v in EX.items()},frag=[(d_['label'],d_['lv'],round(d_['t3'])) for d_ in CAT[:7]],best_c=best_c,flat=[x for x in classes if cd[x]<.2],flat_subj=flat_subj,weak=weak_subj,prog=prog_subj,clvgain={k_:round(v,1) for k_,v in clvgain.items()}),ensure_ascii=False))
