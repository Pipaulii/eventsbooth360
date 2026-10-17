"""Branded reservation summaries, deliberately not accounting invoices."""
from io import BytesIO
from pathlib import Path
from html import escape
from datetime import datetime

def render(number,data):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    buffer=BytesIO();styles=getSampleStyleSheet()
    styles.add(ParagraphStyle('SmallPrint',fontName='Helvetica',fontSize=9,leading=13,textColor=colors.HexColor('#59616c')))
    styles.add(ParagraphStyle('Detail',fontName='Helvetica',fontSize=10,leading=15))
    def text(value,style='Detail'):return Paragraph(escape(str(value or 'Non renseigné')).replace('\n','<br/>'),styles[style])
    def money(c):return f'{c/100:,.2f}'.replace(',',' ').replace('.',',')+' EUR'
    document=SimpleDocTemplate(buffer,pagesize=(595.28,841.89),rightMargin=42,leftMargin=42,topMargin=32,bottomMargin=45)
    logo=Image(str(Path(__file__).parent/'public/logo-signature.png'),width=240,height=56)
    story=[logo,Spacer(1,8),text('Récapitulatif de réservation','Title'),text(f'N° {number:04d} — Émis le '+datetime.fromisoformat(data['issued']).strftime('%d/%m/%Y'),'SmallPrint'),Spacer(1,10)]
    for title,rows in [('Votre réservation',[('Référence',data['booking_id']),('Client',data['name']),('E-mail',data['email']),('Téléphone',data['phone']),('Lieu de prestation',data['address'])]),('Votre événement',[('Date',datetime.fromisoformat(data['start']).strftime('%d/%m/%Y')),('Début',datetime.fromisoformat(data['start']).strftime('%H h %M')),('Fin',datetime.fromisoformat(data['end']).strftime('%d/%m/%Y à %H h %M')),('Durée',data['duration']+' heures — heure de Paris'),('Événement',data['event']),('Filtres vidéo',data['video_name'] or 'À préciser')])]:
        story+=[text(title,'Heading2')]
        table=Table([[text(k,'SmallPrint'),text(v)] for k,v in rows],colWidths=[125,386],hAlign='LEFT')
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),0)]));story+=[table,Spacer(1,8)]
    amounts=[('Prestation',money(data['total']-data['travel_fee'])),('Déplacement',money(data['travel_fee'])),('Total de la réservation',money(data['total'])),('Payé en ligne',money(data['due'])),('Solde restant',money(data['total']-data['due']))]
    table=Table([[text(k),text(v)] for k,v in amounts],colWidths=[355,156])
    table.setStyle(TableStyle([('BACKGROUND',(0,2),(-1,2),colors.HexColor('#ffe2a4')),('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,1),(-1,1),.5,colors.HexColor('#edc87f'))]))
    story+=[KeepTogether([text('Détail des montants','Heading2'),table]),Spacer(1,8)]
    if data['travel_km'] is not None:story.append(text('Déplacement inclus dans Marseille. Hors Marseille : 0,18 EUR/km routier aller-retour. Distance retenue : '+str(data['travel_km'])+' km.','SmallPrint'))
    story+=[Spacer(1,14),text('Votre réservation est confirmée. Pour toute modification, contactez-nous avec votre référence.','SmallPrint'),text('eventsbooth360.fr — eventsbooth360@gmail.com — 06 11 57 46 64','SmallPrint')]
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#59616c'));canvas.drawString(42,24,'Récapitulatif de réservation — ne constitue pas une facture.');canvas.drawRightString(553,24,str(doc.page))
    document.build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
