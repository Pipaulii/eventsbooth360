'use strict';
const quoteForm=document.getElementById('quote-form');
let quoteRequestKey=crypto.randomUUID(),quoteTrip=null;
const qfield=name=>quoteForm.elements.namedItem(name);
const qaddress=()=>[qfield('street').value.trim(),qfield('postcode').value.trim(),qfield('city').value.trim()].join(', ');
function quoteSummary(){
  const h=Number(qfield('hours').value),discount=Number(qfield('discount').value),base=({2:22900,3:29900,4:35900})[h]??Math.round(h*8975),reduction=Math.round(base*discount/100);
  el('quote-summary').textContent='Location : '+money(base)+' · Remise : '+money(reduction)+' · Déplacement : '+(quoteTrip?money(quoteTrip.fee_cents):'à calculer')+(quoteTrip?' · Total : '+money(base-reduction+quoteTrip.fee_cents):'');
}
for(const name of ['hours','discount'])qfield(name).addEventListener('input',quoteSummary);
for(const name of ['street','city','postcode'])qfield(name).addEventListener('input',()=>{quoteTrip=null;quoteSummary();});
el('quote-travel').onclick=async()=>{const b=el('quote-travel');b.disabled=true;try{quoteTrip=await api('quotes/travel',{address:qaddress()});el('quote-notice').textContent='Adresse reconnue : '+quoteTrip.label+' · '+quoteTrip.round_trip_km+' km aller-retour.';quoteSummary();}catch(e){quoteTrip=null;el('quote-notice').textContent=e.message;}finally{b.disabled=false;}};
async function loadQuotes(){
  const data=await api('quotes');el('quote-list').replaceChildren();
  for(const row of data.quotes){const card=document.createElement('article');paragraph(card,'DEV-'+String(row.number).padStart(4,'0')+' · '+row.name+' · '+money(row.total));
    const link=document.createElement('a');link.href='/api/admin/quotes/'+row.id+'/pdf';link.textContent='Télécharger le PDF';card.append(link);
    for(const mail of row.emails)paragraph(card,mail.recipient+' : '+({sent:'envoyé',pending:'en attente de connexion Gmail',sending:'envoi à vérifier dans Gmail',unknown:'résultat incertain, vérifier Gmail avant tout nouvel envoi'})[mail.status],'muted');
    if(row.emails.some(m=>m.status==='pending')){const button=document.createElement('button');button.textContent='Envoyer les mails en attente';button.onclick=async()=>{button.disabled=true;try{await api('quotes/'+row.id+'/send',{});await loadQuotes();}catch(e){el('quote-notice').textContent=e.message;}finally{button.disabled=false;}};card.append(button);}
    el('quote-list').append(card);
  }
}
quoteForm.onsubmit=async event=>{event.preventDefault();if(!quoteTrip){el('quote-notice').textContent='Calculez les frais de déplacement avant de créer le devis.';return;}
  const b=event.submitter;b.disabled=true;el('quote-notice').textContent='Création du PDF et envoi…';
  try{const data=Object.fromEntries(new FormData(quoteForm));data.address=qaddress();data.travel_token=quoteTrip.token;data.request_key=quoteRequestKey;
    const result=await api('quotes',data);quoteRequestKey=crypto.randomUUID();quoteForm.reset();quoteTrip=null;quoteSummary();el('quote-notice').textContent='Proposition DEV-'+String(result.number).padStart(4,'0')+' créée. Consultez les statuts d’envoi ci-dessous.';await loadQuotes();
  }catch(e){el('quote-notice').textContent=e.message;}finally{b.disabled=false;}
};
el('quote-refresh').onclick=()=>loadQuotes().catch(e=>{el('quote-notice').textContent=e.message;});
quoteSummary();
