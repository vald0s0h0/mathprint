from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from pypdf import PdfReader, PdfWriter
ROOT=Path(__file__).resolve().parents[1]
F='/System/Library/Fonts/Supplemental/'
for n,f in [('R','Arial.ttf'),('B','Arial Bold.ttf'),('I','Arial Italic.ttf')]: pdfmetrics.registerFont(TTFont(n,F+f))
pdfmetrics.registerFontFamily('R',normal='R',bold='B',italic='I',boldItalic='B')
W,H=595.2756,841.8898
NAVY='#142D45'; TEAL='#087F83'; MUTED='#597084'; LINE='#D6E1E8'; PALE='#EFF6F8'; RED='#BE2639'; GOLD='#DB9F39'
def rect(c,x,y,w,h,fill,stroke=None,r=0):
 c.setFillColor(HexColor(fill)); c.setStrokeColor(HexColor(stroke or fill)); c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=bool(stroke))
def line(c,x,y,x2,y2,color=LINE,width=.6,dash=None):
 c.setStrokeColor(HexColor(color)); c.setLineWidth(width); c.setDash(dash or []); c.line(x,H-y,x2,H-y2); c.setDash([])
def txt(c,x,y,s,size=10,color=NAVY,font='R'):
 c.setFillColor(HexColor(color)); c.setFont(font,size); c.drawString(x,H-y-size,s)
def para(c,x,y,s,w=483,size=11,color=NAVY,font='R',leading=None):
 st=ParagraphStyle('p',fontName=font,fontSize=size,leading=leading or size*1.38,textColor=HexColor(color))
 p=Paragraph(s,st); _,h=p.wrap(w,1000); p.drawOn(c,x,H-y-h); return h

def header(c,name,grade,title,sub,page,accent=TEAL):
 rect(c,0,0,W,10,accent); txt(c,36,28,'mathprint',23,NAVY,'B'); txt(c,403,37,'APPRENDRE. PROGRESSER.',8,accent,'B')
 txt(c,36,78,grade.upper()+'  /  MATHÉMATIQUES',9,accent,'B'); txt(c,36,99,title,26,NAVY,'B'); txt(c,36,135,sub,10,MUTED)
 rect(c,36,164,523,35,PALE,r=7); txt(c,48,174,name,11,NAVY,'B'); txt(c,300,176,'Date : ____ / ____ / ______',9,MUTED); txt(c,495,175,f'{page} / 2',10,accent,'B')
 line(c,36,799,559,799); txt(c,36,811,'MATHPRINT  •  '+name,8,MUTED); txt(c,450,811,f'Mathématiques  /  {page}',8,MUTED)
def ex(c,y,n,title,tag):
 rect(c,36,y,26,26,TEAL,r=6); txt(c,43,y+5,n,12,'#FFFFFF','B'); txt(c,73,y+1,title,15,NAVY,'B'); txt(c,73,y+21,tag,8,MUTED)
def writing(c,y,rows=5):
 for i in range(rows): line(c,48,y+i*19,547,y+i*19)
def feedback(c,y,h=86,label='RETOUR SUR MON RAISONNEMENT'):
 rect(c,36,y,523,h,'#FAF5F3',r=8); txt(c,48,y+9,label,7.5,'#96756B','B')
def red(c,y,s,h=86):
 para(c,48,y+26,s,499,10,RED,leading=13.1)
def guide(c,y,s):
 rect(c,48,y,499,36,PALE,r=6); para(c,59,y+7,'<b>Un coup de pouce</b>  '+s,477,9,TEAL,leading=11.5)
def options(c,y,labels):
 for i,s in enumerate(labels):
  x=54+i*167; c.setStrokeColor(HexColor(MUTED)); c.setLineWidth(.8); c.rect(x,H-y-10,10,10); txt(c,x+17,y-2,s,11)
def check(c,x,y):
 line(c,x-2,y+4,x+4,y+10,RED,1.5); line(c,x+4,y+10,x+14,y-4,RED,1.5)
def cross(c,x,y):
 line(c,x-2,y-2,x+12,y+12,RED,1.2); line(c,x-2,y+12,x+12,y-2,RED,1.2)

def strong(c,o,p):
 if not o:
  header(c,'Camille Moreau','3e • approfondissement','Aller au bout de la preuve','45 min  •  Calculatrice autorisée  •  Chaque conclusion doit être justifiée.',p)
 if p==1:
  if not o:
   ex(c,217,'01','Une division qui change tout','ALGÈBRE  /  Résoudre sans perdre de solution')
   para(c,48,261,'Résous dans les nombres réels : <b>x(x − 3) = 2x</b>. Explique pourquoi les transformations utilisées conservent toutes les solutions.')
   writing(c,319,5); feedback(c,411,99)
   ex(c,532,'02','Deux évolutions successives','POURCENTAGES  /  Distinguer variation et coefficient')
   para(c,48,576,'Un abonnement coûte 20 000 F CFA. Son prix augmente de 20 %, puis baisse de 20 %. Retrouve-t-il son prix initial ? Calcule le prix final et justifie ta conclusion.')
   writing(c,639,4); feedback(c,714,72)
  else:
   red(c,411,'<b>Ta division par x suppose x ≠ 0.</b> Elle conserve la solution 5, mais fait disparaître 0, qui vérifie aussi l’équation. Ramène tout à gauche : x(x − 5) = 0. Un produit nul donne <b>x = 0 ou x = 5</b>. Avant de diviser par une expression, vérifie si elle peut être nulle.')
   red(c,714,'<b>Les deux 20 % ne portent pas sur le même prix.</b> La baisse vaut 4 800 F, après une hausse de 4 000 F. Les coefficients se multiplient : 1,20 × 0,80 = 0,96. Prix final : <b>19 200 F CFA, soit −4 %</b>.')
 else:
  if not o:
   ex(c,217,'03','Choisir la bonne population','PROBABILITÉS  /  Lire une information conditionnelle')
   para(c,48,261,'Dans un club de 30 élèves, il y a 18 filles, dont 12 pratiquent le basket. Parmi les 12 garçons, 3 pratiquent le basket. On choisit au hasard <b>un élève parmi les basketteurs</b>. Quelle est la probabilité que ce soit une fille ? Justifie le numérateur et le dénominateur.')
   writing(c,344,4); feedback(c,420,86)
   ex(c,526,'04','Un cas suffit-il à prouver ?','ARITHMÉTIQUE  /  Passer de l’exemple à la démonstration')
   para(c,48,570,'Affirmation : « La somme de deux entiers impairs consécutifs est toujours un multiple de 4. » Démontre cette affirmation pour <b>tous</b> les entiers impairs consécutifs.')
   writing(c,632,3); feedback(c,686,100,'MON PROCHAIN PAS')
  else:
   red(c,420,'<b>12 est le bon numérateur, mais 30 n’est plus le bon total.</b> Le tirage se fait seulement parmi les 12 + 3 = 15 basketteurs. Donc P = 12/15 = <b>4/5</b>. Ton 12/30 répondrait à une autre question : choisir une fille basketteuse parmi tous les élèves du club.')
   red(c,686,'<b>Tes exemples sont exacts, mais ne couvrent pas tous les cas.</b> Écris les deux impairs consécutifs 2n + 1 et 2n + 3 : leur somme vaut 4n + 4 = 4(n + 1), donc un multiple de 4. <b>Ton cap :</b> tu sais calculer ; sécurise maintenant ce que tu supposes et ce que tu prouves. Pour chaque preuve, indique pourquoi elle vaut pour tous les cas.')

def weak(c,o,p):
 if not o: header(c,'Ibrahim Koné','6e • parcours accompagné','Un pas après l’autre','30 min  •  Sans calculatrice  •  Une seule réponse par QCM.',p)
 if p==1:
  if not o:
   ex(c,217,'01','Partager une même unité','FRACTIONS  /  Observer, puis choisir')
   para(c,48,258,'Une tablette est partagée en 8 parts égales. Trois parts sont colorées. Quelle fraction de la tablette est colorée ?')
   for i in range(8): rect(c,120+i*43,303,43,29,TEAL if i<3 else '#FFFFFF',TEAL)
   for i in [1,2]: line(c,120+i*43,303,120+i*43,332,'#FFFFFF',.8)
   guide(c,345,'En bas : toutes les parts. En haut : les parts colorées.')
   options(c,397,['3/8','8/3','3/5']); feedback(c,424,77)
   ex(c,523,'02','Comparer des nombres décimaux','NUMÉRATION  /  Regarder la valeur des chiffres')
   para(c,48,566,'Quel est le plus grand nombre ?')
   guide(c,593,'Tu peux ajouter un zéro à droite : 4,7 = 4,70. Compare les centièmes.')
   options(c,646,['4,35','4,7','Ils sont égaux']); feedback(c,681,105,'CE QUE JE PEUX RETENIR')
  else:
   cross(c,388,397); check(c,54,397); red(c,424,'<b>Tu as compté les 5 parts blanches.</b> Elles ne forment pas l’unité entière : il faut compter les 8 parts, colorées ou non. La fraction cherchée est <b>3/8</b>. Le dénominateur décrit le partage de toute la tablette.')
   cross(c,54,646); check(c,221,646); red(c,681,'<b>35 est plus grand que 7, mais les positions comptent.</b> Dans 4,35, il y a 35 centièmes ; dans 4,7, il y en a 70. Donc <b>4,7 &gt; 4,35</b>. Aligne les virgules et complète avec un zéro : tu compares alors des quantités de même valeur.')
 else:
  if not o:
   ex(c,217,'03','Relier les longueurs égales','MESURES  /  Deux écritures pour une longueur')
   para(c,48,259,'Relie chaque longueur de gauche à sa valeur de droite. Utilise chaque réponse une seule fois.')
   guide(c,300,'1 m = 100 cm. Pour passer de mètres à centimètres, multiplie par 100.')
   for i,(a,b) in enumerate(zip(['1,2 m','0,5 m','2 m'],['50 cm','200 cm','120 cm'])):
    y=357+i*30; txt(c,96,y,a,12); txt(c,430,y,b,12)
    for x in [180,405]: c.setFillColor(HexColor(TEAL)); c.circle(x,H-y-8,2.5,fill=1,stroke=0)
   feedback(c,450,78)
   ex(c,548,'04','Faire le tour du rectangle','GÉOMÉTRIE  /  Distinguer contour et surface')
   rect(c,65,596,105,45,'#FFFFFF',TEAL); txt(c,104,580,'7 cm',10); txt(c,178,610,'3 cm',10)
   para(c,255,581,'Le périmètre est la longueur du <b>contour</b>. Additionne les quatre côtés.',280,10)
   options(c,659,['21 cm','10 cm','20 cm']); feedback(c,691,95,'MON BILAN ET MON PETIT DÉFI')
  else:
   for a,b in [(0,2),(1,0)]: line(c,180,365+a*30,405,365+b*30,RED,1.4)
   red(c,450,'<b>2 m = 200 cm : ce lien est juste.</b> Pour les deux autres, garde le même facteur × 100 : <b>1,2 m = 120 cm</b> et <b>0,5 m = 50 cm</b>. Un demi-mètre mesure la moitié de 100 cm ; ce repère permet de vérifier.')
   cross(c,54,659); check(c,388,659); red(c,691,'<b>7 × 3 = 21 mesure la surface, en cm².</b> Pour le contour : 7 + 3 + 7 + 3 = <b>20 cm</b>. Tu sais convertir des mètres entiers. À renforcer : le rôle du dénominateur, la valeur des décimales et le sens du périmètre. <b>Petit défi :</b> compare 2,6 et 2,45, puis trace du doigt les 4 côtés d’un rectangle.')

# Repères locaux : origine en bas à gauche, carreaux de 12 points.
def grid(c,x,y,nx,ny,step=12):
 for i in range(nx+1): line(c,x+i*step,y,x+i*step,y+ny*step,'#DCE7EB',.45)
 for j in range(ny+1): line(c,x,y+j*step,x+nx*step,y+j*step,'#DCE7EB',.45)
def point(c,x,y,name,color=NAVY,dx=4,dy=-13):
 line(c,x-2,y,x+2,y,color,1); line(c,x,y-2,x,y+2,color,1); txt(c,x+dx,y+dy,name,9,color,'B')
def geo(c,o,p):
 if not o: header(c,'Léa Kouamé','4e • constructions','La précision fait la preuve','40 min  •  Règle, équerre, compas  •  Laisse tes traits de construction.',p)
 if p==1:
  # grid origin screen (48,287), 28x12, axis x=216. rectangle original x=96..156 y323..383
  if not o:
   ex(c,215,'01','Un reflet, côté par côté','SYMÉTRIE AXIALE  /  1 carreau = 1 unité')
   para(c,48,255,'Construis A′B′C′D′, image du rectangle ABCD par la symétrie d’axe (d).',499,10)
   grid(c,48,284,28,12); line(c,216,280,216,432,TEAL,1.4); txt(c,220,282,'(d)',9,TEAL)
   pts=[(96,320,'A'),(156,320,'B'),(156,380,'C'),(96,380,'D')]
   for i,(x,y,n) in enumerate(pts): point(c,x,y,n); q=pts[(i+1)%4]; line(c,x,y,q[0],q[1],NAVY,1.4)
   para(c,408,305,'<b>À vérifier</b><br/>L’axe est la médiatrice de [AA′].<br/><br/>Respecte l’ordre des sommets.',135,10,MUTED)
   feedback(c,443,66,'RETOUR SUR MON TRACÉ')
   ex(c,524,'02','Un demi-tour autour de O','SYMÉTRIE CENTRALE  /  1 carreau = 1 unité')
   para(c,48,563,'Construis le triangle A′B′C′, image de ABC par la symétrie de centre O.',499,10)
   grid(c,48,589,28,10); point(c,216,649,'O',TEAL)
   pts=[(108,613,'A'),(168,613,'B'),(132,637,'C')]
   for i,(x,y,n) in enumerate(pts): point(c,x,y,n); q=pts[(i+1)%3]; line(c,x,y,q[0],q[1],NAVY,1.4)
   para(c,408,614,'<b>À vérifier</b><br/>O doit être le milieu de [AA′], de [BB′] et de [CC′].',135,10,MUTED)
   feedback(c,722,64,'RETOUR SUR MON TRACÉ')
  else:
   line(c,336,380,336,320,RED,1.8); txt(c,342,348,'[D′A′]',9,RED,'B')
   red(c,443,'<b>Les quatre images sont bien placées.</b> Tu as relié D′ à B′ : c’est une diagonale. Le côté [DA] devient <b>[D′A′]</b>. Seul ce côté est à remplacer ; conserve tes trois autres côtés.')
   line(c,300,661,324,685,RED,1.8); txt(c,319,665,'[C′A′]',9,RED,'B')
   red(c,722,'<b>Tes points images et deux côtés sont justes.</b> Le dernier trait rejoint A, le sommet initial. Relie C′ à <b>A′</b> pour fermer l’image du triangle. O reste le milieu de chaque paire de points homologues.')
 else:
  if not o:
   ex(c,217,'03','Construire, puis contrôler','THALÈS ET PYTHAGORE  /  1 carreau = 1 cm')
   para(c,48,260,'Le triangle ABC est rectangle en A, avec AB = 8 cm et AC = 6 cm. M est le milieu de [AB]. Place N sur [AC] pour que <b>(MN) soit parallèle à (BC)</b>, puis trace [MN]. Calcule MN et justifie ta démarche.',499,11)
   # grid 20 pts per cm, origin A=(100,490), B=(260,490), C=(100,370)
   grid(c,60,350,16,9,20)
   pts=[(100,490,'A'),(260,490,'B'),(100,370,'C')]
   for i,(x,y,n) in enumerate(pts): point(c,x,y,n); q=pts[(i+1)%3]; line(c,x,y,q[0],q[1],NAVY,1.5)
   point(c,180,490,'M',TEAL,dy=7)
   para(c,405,355,'<b>Longueurs indiquées</b><br/>AB = 8 cm<br/>AC = 6 cm<br/>AM = MB<br/><br/>Le dessin est réduit.<br/>Utilise les carreaux, pas une mesure en cm sur la feuille.',140,10,MUTED)
   writing(c,557,4); feedback(c,638,85,'RETOUR SUR LA CONSTRUCTION ET LE CALCUL'); feedback(c,735,51,'MON PROCHAIN RÉFLEXE')
  else:
   line(c,180,490,100,430,RED,1.8); point(c,100,430,'N',RED,dx=-17,dy=-14)
   red(c,638,'<b>M est bien placé, mais ton N est trop proche de A.</b> AM/AB = 1/2 : il faut aussi AN/AC = 1/2, donc AN = <b>3 cm</b>, et non 2 cm. Le segment rouge remplace seulement [MN]. Ton calcul de BC = 10 cm est juste ; Thalès donne alors <b>MN = 5 cm</b>.')
   para(c,48,756,'Tu sais placer des images. Avant de relier, vérifie les noms des sommets ; avant de valider un parallèle, compare les deux rapports.',499,9.5,RED,leading=12)

def build(key,fn):
 a=ROOT/(key+'_sujet.pdf'); b=ROOT/(key+'_overlay_rouge.pdf')
 for path,o in [(a,False),(b,True)]:
  c=canvas.Canvas(str(path),pagesize=(W,H)); c.setTitle(key.replace('_',' ') + (' • Corrections' if o else ' • Sujet')); c.setAuthor('MathPrint')
  for p in [1,2]: fn(c,o,p); c.showPage()
  c.save()
 r=PdfReader(a); ov=PdfReader(b); wr=PdfWriter()
 for pg,op in zip(r.pages,ov.pages): pg.merge_page(op); wr.add_page(pg)
 with open(ROOT/(key+'_sujet_corrige.pdf'),'wb') as f: wr.write(f)
for key,fn in [('01_raisonnement',strong),('02_parcours_guide',weak),('03_geometrie',geo)]: build(key,fn)

# Guide de mise en scène, volontairement séparé des copies.
c=canvas.Canvas(str(ROOT/'00_mode_emploi_et_reponses.pdf'),pagesize=(W,H)); c.setTitle('MathPrint • Guide de démonstration'); c.setAuthor('MathPrint')
def notehead(p,title,sub):
 rect(c,0,0,W,10,TEAL); txt(c,36,30,'mathprint',24,NAVY,'B'); txt(c,36,87,'GUIDE DE DÉMONSTRATION  /  USAGE INTERNE',9,TEAL,'B'); txt(c,36,112,title,25,NAVY,'B'); para(c,36,151,sub,523,11,MUTED)
 line(c,36,799,559,799); txt(c,36,812,'PRÉPARATION DES COPIES  •  Ne pas distribuer aux élèves',8,MUTED); txt(c,522,812,str(p),8,MUTED)
def block(y,title,body):
 txt(c,36,y,title,13,TEAL,'B'); h=para(c,36,y+24,body,523,11); return y+24+h+24
notehead(1,'Trois copies, trois façons d’aider','Un scénario éditorial du potentiel futur de MathPrint. Les corrections sont préparées pour les réponses ci-dessous : aucun moteur de reconnaissance n’a été exécuté.')
y=216
y=block(y,'01  •  Choisir le bon fichier','Chaque dossier contient trois PDF de deux pages : <b>_sujet</b> pour écrire les réponses ; <b>_overlay_rouge</b> pour imprimer uniquement les retours ; <b>_sujet_corrige</b> pour imprimer directement le sujet et les corrections, puis ajouter les réponses au stylo.')
y=block(y,'02  •  Préparer une démonstration avant / après','Imprime le sujet et remplis-le au stylo bleu avec les réponses des pages suivantes. Photocopie ou numérise cette copie remplie pour garder un état « avant ». Réimprime ensuite l’overlay rouge sur la copie originale : tu obtiens l’état « après » avec la même écriture.')
y=block(y,'03  •  Garder un alignement précis','Format <b>A4 portrait, taille réelle / 100 %</b>, sans « ajuster à la page ». Imprime en recto simple. Teste d’abord le sens d’alimentation et le repérage sur une feuille brouillon. Utilise exactement le même réglage pour sujet et overlay. Un léger décalage mécanique reste possible ; le PDF combiné évite une seconde passe.')
y=block(y,'04  •  Rendre les réponses lisibles','Écris uniquement sur les lignes ou dans les espaces de construction. Laisse les blocs de retour libres. Les overlays ne contiennent aucune réponse manuscrite : c’est ton écriture qui donnera corps à la copie. Les exercices sont des créations originales conçues pour ces scénarios.')
block(y,'05  •  Ce que la démonstration met en valeur','Le retour part d’une erreur précise, explique son mécanisme et propose une vérification réutilisable. En géométrie, les côtés corrects ne sont pas redessinés. Aucune note globale n’est attribuée : le retour porte sur les acquis et le prochain travail utile.')
c.showPage()
notehead(2,'Camille • un raisonnement à affiner','Écris ces réponses dans les espaces prévus. Les erreurs sont volontaires ; conserve les formulations pour que les retours rouges correspondent exactement.')
y=213
y=block(y,'Page 1 / Exercice 1','« Je divise les deux membres par x : x − 3 = 2.<br/>Donc x = 5. Vérification : 5 × (5 − 3) = 10 et 2 × 5 = 10.<br/>L’unique solution est donc 5. »<br/><br/><b>Erreur ciblée :</b> division par une quantité qui peut être nulle ; la solution 0 disparaît.')
y=block(y,'Page 1 / Exercice 2','« Une hausse de 20 % suivie d’une baisse de 20 % donne<br/>+20 − 20 = 0 %. Le prix final est donc 20 000 F CFA.<br/>Le prix revient à sa valeur de départ. »<br/><br/><b>Erreur ciblée :</b> addition de taux appliqués à des bases différentes.')
y=block(y,'Page 2 / Exercice 3','« Il y a 12 filles qui font du basket sur 30 élèves.<br/>La probabilité est donc 12/30 = 2/5 = 40 %.<br/>12 compte les cas favorables et 30 tous les cas possibles. »<br/><br/><b>Erreur ciblée :</b> le total du club remplace à tort la population des basketteurs.')
block(y,'Page 2 / Exercice 4','« 3 + 5 = 8 et 7 + 9 = 16. Les deux sommes sont divisibles<br/>par 4. La somme de deux impairs consécutifs est donc<br/>toujours un multiple de 4. »<br/><br/><b>Erreur ciblée :</b> deux exemples exacts utilisés comme preuve universelle. Le retour conserve ce qui est juste et introduit une preuve générale.')
c.showPage()
notehead(3,'Ibrahim • rendre l’erreur utile','Coche nettement les cases au stylo bleu et trace les liens à la règle. Aucune longue réponse manuscrite n’est nécessaire.')
y=216
y=block(y,'Page 1 / Exercice 1','Coche <b>3/5</b>, la troisième case. L’élève compte les parts blanches au dénominateur. L’overlay barre cette case et indique 3/8, puis explique le rôle de l’unité entière.')
y=block(y,'Page 1 / Exercice 2','Coche <b>4,35</b>, la première case. L’élève compare 35 et 7 comme des entiers. La correction revient à 4,70 et fait comparer les centièmes.')
y=block(y,'Page 2 / Exercice 3','Trace exactement ces trois liens bleus :<br/><b>1,2 m → 50 cm</b> (faux, ligne du haut vers la ligne du haut).<br/><b>0,5 m → 120 cm</b> (faux, ligne du milieu vers la ligne du bas).<br/><b>2 m → 200 cm</b> (juste, ligne du bas vers la ligne du milieu).<br/><br/>Les deux liens correctifs apparaîtront en rouge. Le lien juste reste bleu. Le texte explique le facteur × 100 et fournit le repère du demi-mètre.')
y=block(y,'Page 2 / Exercice 4','Coche <b>21 cm</b>, la première case. Le calcul 7 × 3 est celui de l’aire, mais la question porte sur le contour. Le retour montre la somme des quatre côtés et rappelle l’unité de surface.')
block(y,'Le bilan à montrer à l’établissement','Le bilan s’appuie sur les réponses réellement prévues : conversion d’un nombre entier de mètres réussie ; fractions, décimaux et périmètre à reprendre. Les guides présents sur le sujet réduisent la charge de lecture. Les corrections donnent ensuite une autre manière de vérifier, sans jugement sur l’élève.')
c.showPage()
notehead(4,'Léa • corriger le trait nécessaire','Trace en bleu à la règle. Les positions ci-dessous sont données en carreaux : ne mesure pas les centimètres sur le papier. N’ajoute pas de tracé autre que ceux indiqués.')
y=210
y=block(y,'Page 1 / Exercice 1 • Symétrie axiale','Place <b>A′ à 10 carreaux à droite de l’axe</b>, sur la ligne de A ; <b>B′ à 5 carreaux à droite</b>, sur la ligne de B. Place <b>C′ à 5 carreaux à droite</b>, sur la ligne de C ; <b>D′ à 10 carreaux à droite</b>, sur la ligne de D. Inscris les noms près des points.<br/>Trace A′B′, B′C′ et C′D′ correctement. Pour fermer, trace volontairement <b>D′B′</b> au lieu de D′A′. Ne trace pas D′A′ : c’est le seul côté ajouté en rouge.')
y=block(y,'Page 1 / Exercice 2 • Symétrie centrale','Depuis O, place <b>A′ : 9 carreaux à droite, 3 en bas</b> ; <b>B′ : 4 à droite, 3 en bas</b> ; <b>C′ : 7 à droite, 1 en bas</b>. Nomme les points, en laissant le milieu du segment C′A′ libre pour son étiquette rouge.<br/>Trace A′B′ et B′C′ correctement. Trace ensuite volontairement <b>C′A</b>, jusqu’au sommet A du triangle initial, au lieu de C′A′. L’overlay ne rétablit que C′A′.')
y=block(y,'Page 2 / Exercice 3 • Thalès','Sur [AC], place volontairement <b>N à 2 carreaux au-dessus de A</b>, puis trace [MN] en bleu. Écris sur les lignes :<br/>« BC² = 8² + 6² = 100, donc BC = 10 cm.<br/>Je place N à 2 cm de A. MN paraît parallèle à BC.<br/>Par Thalès, MN = (2/6) × 10 = 10/3 cm. »<br/><br/>Le calcul de BC est juste. L’overlay place le bon N à <b>3 carreaux au-dessus de A</b> et trace seulement le bon [MN]. Il explique que les rapports 4/8 et 2/6 ne conviennent pas.')
block(y,'Contrôle avant présentation','Les côtés et points justes restent bleus. Les traits faux restent visibles : les phrases indiquent explicitement lesquels remplacer. Le rouge est réservé au côté manquant ou au segment corrigé, avec le point N déplacé lorsque nécessaire. Les quadrillages et les figures d’origine ne sont jamais redessinés par l’overlay.')
c.showPage(); c.save()
print('10 PDF créés dans',ROOT)
