"""Branded, non-binding estimates, separate from booking confirmations."""
from io import BytesIO
from pathlib import Path
from html import escape
from datetime import datetime

def render(number,data):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    styles=getSampleStyleSheet();styles.add(ParagraphStyle('Detail',fontSize=10,leading=15));styles.add(ParagraphStyle('Fine',fontSize=9,leading=13,textColor=colors.HexColor('#59616c')))
    def p(v,style='Detail'):return Paragraph(escape(str(v)).replace('\n','<br/>'),styles[style])
    def money(c):return f'{c/100:,.2f}'.replace(',',' ').replace('.',',')+' EUR'
    def table(rows):
        t=Table([[p(k),p(v)] for k,v in rows],colWidths=[210,301]);commands=[('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,-1),(-1,-1),.5,colors.HexColor('#d2aa60'))]
        for i,(key,_) in enumerate(rows):
            if key=='TOTAL PROPOSÉ':commands.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#ffe2a4')))
        t.setStyle(TableStyle(commands));return t
    buf=BytesIO();doc=SimpleDocTemplate(buf,pagesize=(595.28,841.89),leftMargin=42,rightMargin=42,topMargin=32,bottomMargin=45)
    story=[Image(str(Path(__file__).parent/'public/logo-signature.png'),width=240,height=56),Spacer(1,15),p(f'Proposition de devis DEV-{number:04d}','Title'),p(f"Émise le {datetime.fromisoformat(data['issued']).strftime('%d/%m/%Y')} - valable jusqu'au {datetime.fromisoformat(data['valid_until']).strftime('%d/%m/%Y')}",'Fine'),Spacer(1,12),p('Votre événement','Heading2'),table([('Client',data['name']),('E-mail',data['email']),('Téléphone',data['phone']),('Lieu',data['address']),('Événement',data['event']),('Début (heure de Paris)',datetime.fromisoformat(data['start']).strftime('%d/%m/%Y à %H h %M')),('Fin',datetime.fromisoformat(data['end']).strftime('%d/%m/%Y à %H h %M')),('Durée',data['hours']+' heures')]),Spacer(1,12)]
    rows=[('Location VideoBooth 360',money(data['base'])),('Remise sur la location ('+data['discount']+' %)', '- '+money(data['reduction'])),('Déplacement ('+str(data['travel_km'])+' km A/R)',money(data['travel_fee'])),('TOTAL PROPOSÉ',money(data['total'])),('Acompte prévu (30 %)',money(data['deposit'])),('Solde après acompte',money(data['balance']))]
    story.append(KeepTogether([p('Détail des montants','Heading2'),table(rows)]))
    story+=[Spacer(1,10),p('Marseille : déplacement inclus. Hors Marseille : 0,18 EUR/km routier aller-retour. Remise appliquée uniquement à la location.','Fine')]
    if data['notes']:story +=[p('Précisions','Heading2'),p(data['notes'])]
    story +=[Spacer(1,14),p('Estimation commerciale non contractuelle. Aucun paiement reçu et aucun créneau réservé. Les informations légales de l’entreprise seront à compléter pour établir un devis définitif. Ce document ne constitue pas une facture.','Fine'),Spacer(1,8),p('eventsbooth360.fr - eventsbooth360@gmail.com - 06 11 57 46 64','Fine')]
    def footer(c,d):c.setFont('Helvetica',8);c.drawString(42,24,f'EventsBooth360 - DEV-{number:04d}');c.drawRightString(553,24,str(d.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer);return buf.getvalue()
