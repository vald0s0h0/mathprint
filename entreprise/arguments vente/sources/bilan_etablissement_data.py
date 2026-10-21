"""Données du bilan d'établissement : 20 classes de la 6e à la Terminale, trois trimestres, notes et compétences."""
import numpy as np

SEED=31052026
SUBJECTS=['Français','Anglais','Espagnol','Mathématiques','Physique-Chimie','SVT','Histoire-Géographie','EDHC','Philosophie']
SHORT={'Français':'Fr.','Anglais':'Ang.','Espagnol':'Esp.','Mathématiques':'Maths','Physique-Chimie':'PC','SVT':'SVT','Histoire-Géographie':'HG','EDHC':'EDHC','Philosophie':'Philo'}
LEVELS=['6e','5e','4e','3e','2nde','1ère','Tle']
# Coefficients de l'établissement par niveau ou série (matières évaluées par la plateforme).
COEF={
 'col1':{'Français':4,'Anglais':2,'Mathématiques':4,'SVT':2,'Histoire-Géographie':2,'EDHC':1},
 'col2':{'Français':4,'Anglais':2,'Espagnol':1,'Mathématiques':4,'Physique-Chimie':2,'SVT':2,'Histoire-Géographie':2,'EDHC':1},
 '2A':{'Français':4,'Anglais':3,'Espagnol':2,'Mathématiques':2,'Physique-Chimie':1,'SVT':1,'Histoire-Géographie':3,'EDHC':1},
 '2C':{'Français':3,'Anglais':2,'Mathématiques':5,'Physique-Chimie':4,'SVT':2,'Histoire-Géographie':2,'EDHC':1},
 'A':{'Français':4,'Philosophie':4,'Anglais':3,'Espagnol':2,'Mathématiques':2,'SVT':1,'Histoire-Géographie':3,'EDHC':1},
 'C':{'Français':3,'Philosophie':2,'Anglais':2,'Mathématiques':5,'Physique-Chimie':5,'SVT':2,'Histoire-Géographie':2,'EDHC':1},
 'D':{'Français':3,'Philosophie':2,'Anglais':2,'Mathématiques':4,'Physique-Chimie':4,'SVT':4,'Histoire-Géographie':2,'EDHC':1},
}
# Nombre de compétences au programme de l'année (mathématiques : référentiels de la plateforme).
NCOMP={'Français':32,'Anglais':28,'Espagnol':24,'Physique-Chimie':30,'SVT':30,'Histoire-Géographie':28,'EDHC':16,'Philosophie':20}
NMATH={'6e':43,'5e':66,'4e':69,'3e':42}
def ncomp(subj,level):return (NMATH.get(level,40) if subj=='Mathématiques' else NCOMP[subj])
# Classe : niveau, grille, effectif, niveau moyen relatif.
CLASSES=[('6e A','6e','col1',54,.05),('6e B','6e','col1',56,-.10),('6e C','6e','col1',52,.00),
 ('5e A','5e','col1',53,.15),('5e B','5e','col1',55,-.05),('5e C','5e','col1',51,.05),
 ('4e A','4e','col2',55,-.05),('4e B','4e','col2',56,-.20),('4e C','4e','col2',54,.00),
 ('3e A','3e','col2',50,.30),('3e B','3e','col2',52,.00),('3e C','3e','col2',53,-.25),
 ('2nde A','2nde','2A',48,-.10),('2nde C','2nde','2C',46,.35),
 ('1ère A','1ère','A',44,-.15),('1ère C','1ère','C',32,.45),('1ère D','1ère','D',45,.10),
 ('Tle A','Tle','A',46,-.30),('Tle C','Tle','C',30,.60),('Tle D','Tle','D',44,.15)]
BASE={'Français':10.3,'Anglais':11.0,'Espagnol':11.5,'Mathématiques':9.5,'Physique-Chimie':9.7,'SVT':11.0,'Histoire-Géographie':10.8,'EDHC':12.6,'Philosophie':9.3}
LEVEL_SLOPE={'6e':.30,'5e':.15,'4e':-.30,'3e':.30,'2nde':.20,'1ère':.05,'Tle':.15}
FIRST=['Aya','Adjoua','Affoué','Akissi','Amenan','Ahou','Aminata','Fatoumata','Mariam','Awa','Kadiatou','Salimata','Grâce','Ruth','Esther','Prisca','Christelle','Murielle','Laetitia','Nadège','Inès','Yasmine','Rokia','Assita','Bintou','Kouadio','Konan','Koffi','Yao','Kouamé','N’Dri','Ibrahim','Moussa','Seydou','Souleymane','Drissa','Lassina','Jean-Marc','Serge','Hervé','Arsène','Emmanuel','Didier','Franck','Cédric','Ange','Christ','Fabrice','Wilfried','Junior','Ismaël','Abdoulaye','Mamadou','Bakary','Olivier','Josué','Samuel','Élie','Noël','Paul']
LAST=['Kouassi','Kouamé','Koné','Traoré','Coulibaly','Ouattara','Yao','N’Guessan','Konan','Bamba','Diabaté','Touré','Silué','Yéo','Tanoh','Aka','Ahoua','Kouadio','Assi','Brou','Gnagne','Amani','Diallo','Cissé','Fofana','Sangaré','Dembélé','Doumbia','Kamagaté','Soumahoro','Tra','Gbané','Zadi','Gnahoré','Dosso','Sylla','Kacou','Ehui','Adou','Boka','Esmel','Lago','Néné','Achi','Kra','Loukou','Tiémoko','Meité','Sanogo','Kamara']
TEACHER_FIRST=['Kouakou','Adama','Clémentine','Hyacinthe','Marcelin','Edwige','Bernadette','Lazare','Sidiki','Nathalie','Béatrice','Augustin','Rosine','Ferdinand','Mathias','Odile','Félix','Viviane','Raoul','Sylvie','Gaston','Antoinette','Désiré','Pélagie','Ismaïla','Honorine','Firmin','Agathe','Valentin','Brigitte','Siaka','Germaine','Théodore','Armelle','Zié','Philomène','Évariste','Solange']
# Services : classes (indices dans CLASSES) et évaluations corrigées par classe et par trimestre.
SERVICES={'Français':[([0,1,2],9),([3,4,5],6),([6,7,8],11),([9,10,11],3),([12,14,17],2),([13,15,16,18,19],7)],
 'Anglais':[([0,1,2,3],8),([4,5,6,7],5),([8,9,10,11],10),([12,13,14,15],7),([16,17,18,19],4)],
 'Espagnol':[([6,7,8,9],6),([10,11,12,14,17],2)],
 'Mathématiques':[([0,1,2],7),([3,4,5],9),([6,7,8],2),([9,10,11],12),([12,13,14],5),([15,18],10),([16,17,19],8)],
 'Physique-Chimie':[([6,7,8],6),([9,10,11],12),([12,13,16],10),([15,18,19],8)],
 'SVT':[([0,1,2,3],11),([4,5,6,7],7),([8,9,10,11],9),([12,13,14,15],4),([16,17,18,19],6)],
 'Histoire-Géographie':[([0,1,2,3],10),([4,5,6,7],3),([8,9,10,11],8),([12,13,14,15],6),([16,17,18,19],12)],
 'EDHC':[([0,1,2,3,4,5,6,7,8,9],5),([10,11,12,13,14,15,16,17,18,19],1)],
 'Philosophie':[([14,15,16],9),([17,18,19],3)]}
FREQ=[('Presque jamais',0,2),('Peu',3,5),('Régulièrement',6,9),('Souvent',10,99)]
def freq_group(e):return next(i for i,(_,a,b) in enumerate(FREQ) if a<=e<=b)

def build():
 rng=np.random.default_rng(SEED);teachers=[];k=0
 for subj,services in SERVICES.items():
  for g,e in services:
   slope=.15*(e-6.5)+rng.normal(0,.1)
   teachers.append(dict(id=k+1,name=f'{TEACHER_FIRST[k]} {LAST[(k*7+3)%len(LAST)]}',subject=subj,classes=[CLASSES[i][0] for i in g],evals=e,slope=slope,group=freq_group(e)));k+=1
 # Cas suivis : le service de maths de 4e en recul, le service de PC de 3e en forte progression.
 for t in teachers:
  if t['subject']=='Mathématiques' and t['classes'][0]=='4e A':t['slope']-=.3
  if t['subject']=='Physique-Chimie' and t['classes'][0]=='3e A':t['slope']+=.2
 teacher_of={(t['subject'],c):t for t in teachers for c in t['classes']}
 students=[];sid=0;used=set()
 for cname,level,grid,size,offset in CLASSES:
  for _ in range(size):
   while True:
    nm=f'{rng.choice(FIRST)} {rng.choice(LAST)}'
    if nm not in used:used.add(nm);break
   a=float(np.clip(rng.normal(offset,.88),-2.3,2.6));trend=rng.normal(.18,.42);profile='standard';u=rng.random()
   if u<.025 and a>-.2:profile='décrochage';trend=-1.35-rng.random()*.6;a+=.55
   elif u<.06 and a<-.4:profile='remontée';trend=1.0+rng.random()*.5
   aff={s:rng.normal(0,.85) for s in COEF[grid]};marks={};comp={}
   for s in COEF[grid]:
    t=teacher_of[(s,cname)];e=t['evals'];row=[]
    for tr in range(3):
     v=BASE[s]+2.35*a+aff[s]+tr*(trend+LEVEL_SLOPE[level]*.6+t['slope']*.55)+rng.normal(0,.85)
     if cname=='2nde C' and s in ('Mathématiques','Physique-Chimie'):v-=[1.5,.7,0][tr]
     if level=='6e' and tr==0:v-=.4
     row.append(float(np.clip(round(v*4)/4,1,19.5)))
    marks[s]=row
    # Compétences : une compétence n'est acquise qu'après des réussites répétées et espacées ;
    # sans évaluations fréquentes, une partie du programme n'est jamais vérifiée.
    cov=[min(1,.18+.07*e),min(1,.27+.075*e),min(1,.35+.08*e)];kf=[.33+.025*e,.33+.0375*e,min(.97,.33+.05*e)];acq=[];na=[]
    for tr in range(3):
     p=1/(1+np.exp(-(row[tr]-10)/1.8));x=float(np.clip(p*kf[tr]+rng.normal(0,.03),0,cov[tr]-.01))
     acq.append(x*100);na.append(min(cov[tr]-x,(cov[tr]-x)*(1-p)*1.15)*100)
    comp[s]=dict(acq=acq,cov=[c*100 for c in cov],na=na,forget=float(np.clip(.36-.02*e+rng.normal(0,.03),.06,.42))*100,n=ncomp(s,level))
   gen=[sum(marks[s][tr]*c for s,c in COEF[grid].items())/sum(COEF[grid].values()) for tr in range(3)]
   students.append(dict(id=sid+1,name=nm,cls=cname,level=level,grid=grid,marks=marks,comp=comp,gen=gen,annual=float(np.mean(gen)),profile=profile));sid+=1
 for t in teachers:
  ss=[s for s in students if s['cls'] in t['classes']];sub=t['subject']
  t['n']=len(ss);t['t']=[float(np.mean([s['marks'][sub][tr] for s in ss])) for tr in range(3)]
  t['acq']=[float(np.mean([s['comp'][sub]['acq'][tr] for s in ss])) for tr in range(3)]
  t['cov']=[float(np.mean([s['comp'][sub]['cov'][tr] for s in ss])) for tr in range(3)]
  t['ok']=float(np.mean([s['marks'][sub][2]>=10 for s in ss]))*100;t['under8']=int(sum(s['marks'][sub][2]<8 for s in ss))
  t['copies']=int(t['evals']*t['n']*3)
 return dict(seed=SEED,students=students,teachers=teachers)

# Compétences repérées dans les copies (intitulés des référentiels ; mathématiques : plateforme).
CATALOGUE=[
 ('Mathématiques','6e','Additionner et soustraire avec des fractions',-.10),('Mathématiques','6e','Utiliser une échelle, un pourcentage',-.06),
 ('Mathématiques','5e','Additionner et soustraire des fractions',-.08),('Mathématiques','5e','Résoudre une équation',-.12),
 ('Mathématiques','4e','Multiplier et diviser des nombres relatifs',.03),('Mathématiques','4e','Développer et factoriser avec la distributivité',-.05),('Mathématiques','4e','Utiliser le théorème de Pythagore et sa réciproque',-.02),
 ('Mathématiques','3e','Calculer des longueurs avec le théorème de Thalès',-.13),('Mathématiques','3e','Factoriser une somme ou une différence',-.15),('Mathématiques','3e','Résoudre une équation produit',-.04),
 ('Mathématiques','2nde','Résoudre une inéquation du premier degré',-.08),('Mathématiques','1ère','Étudier les variations d’une fonction par la dérivée',-.10),('Mathématiques','Tle','Calculer une limite de fonction',-.11),
 ('Français','5e','Accorder le participe passé',-.12),('Français','3e','Rédiger un texte argumentatif',-.10),('Français','4e','Analyser une phrase complexe',-.05),('Français','Tle','Rédiger une dissertation littéraire',-.15),('Français','1ère','Réaliser un commentaire de texte',-.08),
 ('Philosophie','Tle','Problématiser un sujet de dissertation',-.08),('Philosophie','1ère','Expliquer un texte philosophique',-.07),
 ('Anglais','3e','Comprendre un document oral authentique',-.09),('Anglais','4e','Utiliser le present perfect',-.06),('Anglais','Tle','Rédiger un paragraphe argumenté',-.05),
 ('Espagnol','3e','Conjuguer au pretérito indefinido',-.10),('Espagnol','4e','Comprendre un texte écrit court',.04),
 ('Physique-Chimie','3e','Équilibrer une équation de réaction',-.09),('Physique-Chimie','4e','Utiliser la loi d’Ohm',-.07),('Physique-Chimie','Tle','Exploiter les lois de Newton',-.10),
 ('SVT','3e','Interpréter un résultat expérimental',-.06),('SVT','Tle','Expliquer la transmission des caractères héréditaires',-.08),
 ('Histoire-Géographie','Tle','Réaliser un croquis de synthèse',-.11),('Histoire-Géographie','2nde','Analyser un document historique',-.04),('Histoire-Géographie','6e','Se repérer sur une carte',.05),
 ('EDHC','5e','Expliquer un droit fondamental de l’enfant',.06),
 ('Mathématiques','6e','Calculer une aire',.08),('Français','6e','Comprendre un texte narratif',.07),('Anglais','6e','Se présenter et présenter sa famille',.12),('SVT','5e','Décrire le fonctionnement d’un organe',.06),
]

if __name__=='__main__':
 D=build();S=D['students'];T=D['teachers']
 g=np.array([s['gen'] for s in S]);ann=np.array([s['annual'] for s in S])
 print('élèves',len(S),'profs',len(T),'moy',g.mean(0).round(2),'>=10',(g>=10).mean(0).round(3),'annual<8.5',(ann<8.5).sum())
 for lv in LEVELS:print(lv,np.array([s['gen'] for s in S if s['level']==lv]).mean(0).round(2),np.array([s['comp'][x]['acq'] for s in S if s['level']==lv for x in s['comp']]).mean(0).round(1))
 for gi,(lab,_,_) in enumerate(FREQ):
  ts=[t for t in T if t['group']==gi];print(lab,len(ts),'notes',round(np.mean([t['t'][2]-t['t'][0] for t in ts]),2),'acq',round(np.mean([t['acq'][0] for t in ts]),1),round(np.mean([t['acq'][2] for t in ts]),1),'cov3',round(np.mean([t['cov'][2] for t in ts]),1))
 for s in ['Mathématiques','Philosophie','Espagnol','Physique-Chimie']:print(s,np.mean([x['marks'][s] for x in S if s in x['marks']],0).round(2))
