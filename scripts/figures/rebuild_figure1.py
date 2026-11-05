"""Build Figure 1 once from explicit geometry, chart data, and isolated logos.

No existing figure pages are embedded, cropped, covered, or recolored.
Only the four original standalone logo resources are retained as assets.
"""
from pathlib import Path
import json
import math
import pymupdf as pdf

HERE = Path(__file__).resolve().parent
ASSETS = HERE / 'figure1_clean_assets'
DATA = ASSETS / 'chart_data.json'
W,H = 840.4,397.5
doc = pdf.open()
p = doc.new_page(width=W,height=H)
def rgb(s):
    return tuple(int(s[i:i+2],16)/255 for i in (0,2,4))
INK=rgb('30343B'); TEXT=rgb('263345'); MUTED=rgb('596678')
GRAY=rgb('F0F2F4'); BORDER=rgb('B8BDC4'); WHITE=(1,1,1)
PURPLE=rgb('C9B8E2'); GREEN=rgb('B6DDC5')
BLUE=rgb('2E6BB3'); ORANGE=rgb('E96921')

def text(x,y,s,size=8.2,color=TEXT,bold=False,align='left'):
    font='hebo' if bold else 'helv'
    width=pdf.get_text_length(s,fontname=font,fontsize=size)
    if align=='center': x-=width/2
    elif align=='right': x-=width
    p.insert_text((x,y),s,fontname=font,fontsize=size,color=color)

def box(x,y,w,h,fill=None,edge=None,lw=.8,r=0):
    p.draw_rect(pdf.Rect(x,y,x+w,y+h),color=edge,fill=fill,width=lw,radius=r or None)

def line(x,y,a,b,color=INK,lw=1):
    p.draw_line((x,y),(a,b),color=color,width=lw)

def path(points,fill=None,edge=INK,lw=1,close=False):
    sh=p.new_shape();sh.draw_polyline(points)
    sh.finish(color=edge,fill=fill,width=lw,closePath=close);sh.commit()

def arrow(x,y,a,b,lw=1.6):
    line(x,y,a,b,lw=lw)
    angle=math.atan2(b-y,a-x);length=4.5;span=1.8
    path([(a,b),(a-length*math.cos(angle)+span*math.sin(angle),b-length*math.sin(angle)-span*math.cos(angle)),
          (a-length*math.cos(angle)-span*math.sin(angle),b-length*math.sin(angle)+span*math.cos(angle))],fill=INK,edge=None,close=True)

def robot(x,y,fill,scale=1,face_only=False):
    # x,y is the center of the face; all parts are drawn once.
    s=scale
    box(x-8*s,y-5.5*s,16*s,11*s,fill,INK,1.25*s)
    if not face_only:
        box(x-6*s,y+8.5*s,12*s,6*s,fill,INK,1.25*s)
        line(x,y-5.5*s,x,y-10*s,lw=.9*s)
        p.draw_circle((x,y-10.5*s),1.1*s,color=None,fill=INK)
    for dx in (-3,3):p.draw_circle((x+dx*s,y-.8*s),.9*s,color=None,fill=INK)
    path([(x-3*s,y+1.5*s),(x,y+3.4*s),(x+3*s,y+1.5*s)],lw=.8*s)

def coins(x,bottom,n,fill,r=8):
    # One outer silhouette plus internal elliptical seams, no covered coins.
    top=bottom-(n-1)*3.1-5
    sh=p.new_shape()
    sh.draw_bezier((x-r,top+2),(x-r,top-.7),(x+r,top-.7),(x+r,top+2))
    sh.draw_line((x+r,top+2),(x+r,bottom))
    sh.draw_bezier((x+r,bottom),(x+r,bottom+2.6),(x-r,bottom+2.6),(x-r,bottom))
    sh.draw_line((x-r,bottom),(x-r,top+2))
    sh.finish(color=INK,fill=fill,width=.55,closePath=True);sh.commit()
    p.draw_oval(pdf.Rect(x-r,top,x+r,top+4),color=INK,width=.55)
    for j in range(n-1):
        yy=top+5.1+j*3.1
        sh=p.new_shape();sh.draw_bezier((x-r,yy),(x-r,yy+2.6),(x+r,yy+2.6),(x+r,yy))
        sh.finish(color=INK,width=.55,closePath=False);sh.commit()

def bubble(x,y,w,h,label,tail='left',size=10):
    pts=[(x,y),(x+w,y),(x+w,y+h),(x+12,y+h),(x-6,y+h+6),(x,y+h-4)] if tail=='left' else [
        (x,y),(x+w,y),(x+w,y+h-4),(x+w+6,y+h+6),(x+w-12,y+h),(x,y+h)]
    path(pts,WHITE,INK,1.25,True)
    text(x+w/2,y+h*.65,label,size,align='center')

# Panel outlines and titles.
box(.7,.7,W-1.4,136,WHITE,BORDER,1.6,.016)
text(8.2,17,'A',14,bold=True)
for x,title in [(136,'Information policy'),(425,'LLM agents'),(712.5,'Possible responses')]:
    text(x,17,title,11.5,bold=True,align='center')
box(22.9,28,228,91,GRAY,r=.06)
box(312,28,228,91,GRAY,r=.06)
box(600,28,225,91,GRAY,r=.06)

# Information cards.
for x,label,fill,edge in [(31.9,'Least',rgb('E6E8EB'),rgb('6B7580')),
                        (104.9,'Coarse',rgb('EDF5FC'),rgb('2B66AD')),
                        (177.9,'Full',rgb('FFF2E6'),rgb('CC5917'))]:
    box(x,39,64,69,fill,edge,1.1,.022)
    text(x+32,55,label,10.5,color=edge,bold=True,align='center')
    line(x+6,61,x+58,61,rgb('E5E9EE'),.7)
    if label=='Least':text(x+32,92,'?',24,INK,align='center')
    elif label=='Coarse':
        for yy in (71,83,95):
            p.draw_circle((x+13,yy),1.5,color=None,fill=INK)
            line(x+23,yy,x+51,yy,lw=1.9)
    else:
        box(x+8,68,48,30,None,INK,.8)
        for dx in (24,40):line(x+dx,68,x+dx,98,lw=.8)
        for yy in (78,88):line(x+8,yy,x+56,yy,lw=.8)
        for xx in (x+16,x+32,x+48):
            for yy in (73,83,93):line(xx-2,yy,xx+2,yy,lw=.9)
text(61.7,132.2,'Gains:',8.2,MUTED)
for x,c,label in [(91.9,PURPLE,'agent'),(138.9,GREEN,'counter-party')]:
    box(x,125.7,7,6.5,c,INK,.45);text(x+12,132.2,label)
arrow(257,72,299,72)

# Interaction diagram.
p.draw_oval(pdf.Rect(361,54,510,96),color=INK,fill=WHITE,width=1.4)
robot(428,40,PURPLE);robot(342,74,PURPLE);robot(513,74,GREEN);robot(428,102,GREEN)
bubble(366,32,29,11,'...',size=8)
bubble(467,93,29,11,'...',tail='right',size=8)
for pts,end in [(((399,71),(421,57),(438,62),(456,72)),(456,72)),
                (((458,79),(437,93),(418,90),(399,78)),(399,78))]:
    sh=p.new_shape();sh.draw_bezier(*pts);sh.finish(color=INK,width=1,closePath=False);sh.commit()
    angle=math.atan2(pts[3][1]-pts[2][1],pts[3][0]-pts[2][0])
    a,b=end;path([(a,b),(a-4*math.cos(angle)+1.5*math.sin(angle),b-4*math.sin(angle)-1.5*math.cos(angle)),
                   (a-4*math.cos(angle)-1.5*math.sin(angle),b-4*math.sin(angle)+1.5*math.cos(angle))],INK,None,close=True)
text(426,132.2,'Strategic interaction',9.5,align='center')
for yy in (51,107):
    sh=p.new_shape();sh.draw_bezier((541,72),(566,66 if yy==51 else 79),(579,yy),(598,yy))
    sh.finish(color=INK,width=1.6,closePath=False);sh.commit()
    path([(599,yy),(595,yy-1.8),(595,yy+1.8)],INK,None,close=True)
line(610,73.5,815,73.5,rgb('CACFD4'),.55)
for top,bottom,title,desc,counts in [(28,55,'Find better trades','More total gains available',(2,2,3,3)),
    (75,102,'Demand a larger share','Different split; possible disagreement',(3,3,5,1))]:
    text(610,top+12,title,9.2,bold=True)
    for x,n,c in zip((658,677,745,764),counts,(PURPLE,GREEN,PURPLE,GREEN)):coins(x,bottom,n,c)
    arrow(693,bottom-5,722,bottom-5,.8)
    text(610,top+38,desc,7.8,MUTED)

# Bottom settings: all illustration geometry is native, not imported figure crops.
for col,title in enumerate(['Multi-resource trade','Single-good bargaining','Double auction']):
    x=col*850/3;w=820/3
    box(x+.7,143.2,w-1,253.5,WHITE,BORDER,1.6,.016)
    text(x+8,157.2,'BCD'[col],14,bold=True)
    text(x+w/2,158.5,title,11.2,bold=True,align='center')
    line(x+12,215,x+w-12,215,rgb('BFCBDA'),.7)

def bag(x,y,c,labels):
    path([(x-5,y-15),(x-7,y-21),(x+7,y-21),(x+4,y-15)],c,INK,1.1,True)
    sh=p.new_shape();sh.draw_bezier((x-5,y-15),(x-22,y-7),(x-21,y+14),(x-8,y+14))
    sh.draw_line((x-8,y+14),(x+8,y+14));sh.draw_bezier((x+8,y+14),(x+21,y+14),(x+22,y-7),(x+4,y-15))
    sh.draw_line((x+4,y-15),(x-5,y-15));sh.finish(color=INK,fill=c,width=1.1,closePath=True);sh.commit()
    for i,label in enumerate(labels):
        xx=x-10+(i%2)*11; yy=y-10+(i//2)*11
        box(xx,yy,9,9,rgb('E8EDF2'),INK,.8);text(xx+4.5,yy+7,label,7,bold=True,align='center')
bag(39,194,PURPLE,['X','X','X','Y']);bag(240,194,GREEN,['X','Y','Y','Y'])
robot(72,190,PURPLE);robot(202,190,GREEN)
text(72,170,'Agent A',9.5,align='center');text(204,170,'Agent B',9.5,align='center')
arrow(98,183,176,183);arrow(176,202,98,202)
text(137,180,'X',10,bold=True,align='center');text(137,198,'Y',10,bold=True,align='center')
robot(306,186,PURPLE);robot(536,186,GREEN)
text(306,170,'Seller',9.5,align='center');text(536,170,'Buyer',9.5,align='center')
text(306,210,'Cost c',9,MUTED,align='center');text(536,210,'Value v',9,MUTED,align='center')
bubble(348,165,89,17,'Offer p');bubble(402,188,110,17,"Counteroffer p'",tail='right')
box(324,178,11,13,rgb('FFF1C7'),rgb('BB8613'),.9);line(329.5,178,329.5,182,rgb('BB8613'),.8)
coins(508,185,3,rgb('FFF1C7'),6)
box(658,164,93,47,rgb('F8FAFB'),INK,1.3,r=.025)
text(704.5,174,'orders',10,align='center')
for yy in (183,192,201):line(681,yy,727,yy,lw=1.6)
for yy in (179,192,205):
    robot(598,yy,GREEN,.85,face_only=True);robot(810,yy,PURPLE,.85,face_only=True)
    arrow(611,yy,650,yy+4,1.5);arrow(797,yy,760,yy+4,1.5)

# Results are regenerated from numerical records, including exact CI endpoints.
rows=json.loads(DATA.read_text())['rows']
cells={(r['arena'],r['model']):r for r in rows if r['conditioning']=='all'}
models=['GPT-5 mini','GPT-6 Luna','GPT-5.1','GPT-6 Sol','Gemini 3.7 Flash','Gemini 3.1 Pro',
        'Claude Sonnet 5','Claude Opus 5.5','Grok 4.3','Grok 4.7']
for col,arena in enumerate(['Multi-resource trade','Single-good bargaining','Double auction']):
    x=col*850/3;w=820/3;left=x+110;right=x+w-11;bottom=364.5
    lower,upper,ticks=[(-.2,.35,[-.2,-.1,0,.1,.2,.3]),(-.7,.45,[-.6,-.3,0,.3]),(-.6,.3,[-.6,-.4,-.2,0,.2])][col]
    def xx(v):return left+(v-lower)/(upper-lower)*(right-left)
    box(left,248,right-left,bottom-248,rgb('F5F5F5'))
    metric='Change in normalized Nash score' if col==1 else 'Change in total gains (% of maximum)'
    text(x+w/2,226,metric,8.2,bold=True,align='center')
    text(left,240,'Coarse better',8,BLUE);text(right,240,'Full better',8,ORANGE,align='right')
    for t in ticks:
        line(xx(t),248,xx(t),bottom,rgb('A1B0BF') if t==0 else rgb('D4DBE3'),.75 if t==0 else .4)
        label='0' if t==0 else (f'{t:+.1f}' if col==1 else f'{100*t:+.0f}')
        text(xx(t),bottom+13,label,8.2,MUTED,align='center')
    for row,model in enumerate(models):
        r=cells[arena,model];delta=r['estimate'];lo=r['ci_lower'];hi=r['ci_upper'];y=254+11.5*row
        assert lower<=lo<=delta<=hi<=upper
        uncertain=lo<=0<=hi;color=rgb('87919C') if uncertain else (ORANGE if delta>0 else BLUE)
        pale=tuple(c+(1-c)*.77 for c in color)
        a,b=sorted([xx(0),xx(delta)]);box(a,y-3.2,b-a,6.4,pale)
        line(xx(lo),y,xx(hi),y,color,1.1);p.draw_circle((xx(delta),y),2.7,color=color,fill=WHITE if uncertain else color)
        label=f'{delta:+.2f}' if col==1 else f'{100*delta:+.1f}'
        anchor=max(xx(0),xx(hi))+5 if delta<0 else min(xx(0),xx(lo))-5
        text(anchor,y+2.8,label,bold=not uncertain,align='left' if delta<0 else 'right')
        text(x+104,y+2.8,model.replace('Claude ',''),8.1,align='right')
    for boundary in (3.5,5.5,7.5):line(x+12,254+11.5*boundary,right,254+11.5*boundary,rgb('B8C4CF'),.6)
    for logo,center in [('openai',1.5),('gemini',4.5),('claude',6.5),('grok',8.5)]:
        cy=254+11.5*center
        with pdf.open(ASSETS/(logo+'.pdf')) as asset:
            p.show_pdf_page(pdf.Rect(x+8,cy-6,x+20,cy+6),asset,0)
    detail='Full minus coarse (normalized Nash score)' if col==1 else 'Full minus coarse (percentage points)'
    text(x+w/2,390.5,detail,7.6,MUTED,align='center')

OUTPUT = HERE.parents[1] / 'figures'
target=OUTPUT/'figure1.pdf'
p.clean_contents()
doc.save(target,garbage=4,deflate=True,use_objstms=1,compression_effort=100)
p.get_pixmap(matrix=pdf.Matrix(2400/W,2400/W)).save(OUTPUT/'figure1.png')
print(f'{target}: {target.stat().st_size:,} bytes; {len(p.get_drawings())} paths; {len(p.get_texttrace())} text spans')
