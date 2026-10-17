/* Progressive booking flow: the underlying payment and availability checks stay authoritative. */
(()=>{
const root=document.getElementById('disponibilites'),grid=root.querySelector('.booking-grid');
const calendar=grid.querySelector('.calendar'),format=grid.querySelector('.booking-left .selection'),details=grid.querySelector('.customer-details'),payment=grid.querySelector('.payment-summary');
const checkoutMessage=document.getElementById('checkout-message'),checkout=document.getElementById('continue');
const names=['Date','Formule et horaire','Coordonnées','Récapitulatif et paiement'];
const nav=document.createElement('nav');nav.className='booking-progress';nav.setAttribute('aria-label','Étapes de réservation');
const panels=names.map((name,i)=>{const panel=document.createElement('div');panel.className='booking-step selection';panel.id='booking-step-'+(i+1);const title=document.createElement('h3');title.tabIndex=-1;title.textContent=(i+1)+'. '+name;panel.append(title);return panel});
const tabs=names.map((name,i)=>{const button=document.createElement('button');button.type='button';button.textContent=(i+1)+'. '+name;button.onclick=()=>{if(i>furthest)return;if(i>0&&!readyDate())return;if(i>1&&!readyTime())return;if(i===3&&!validateDetails())return;go(i)};nav.append(button);return button});
let current=0,furthest=0;panels[0].append(calendar);panels[1].append(format);panels[2].append(details);
const travelButton=document.getElementById('travel-calculate'),travelMessage=document.getElementById('travel-message'),travelConfirm=document.getElementById('travel-confirm').closest('label');
panels[2].append(travelButton,travelMessage,travelConfirm);
const recap=document.createElement('dl');recap.className='booking-recap';panels[3].append(recap,payment,checkoutMessage,checkout);
const feedback=document.createElement('p');feedback.className='flow-feedback';feedback.setAttribute('role','status');
const controls=document.createElement('div');controls.className='flow-controls';
const back=document.createElement('button'),next=document.createElement('button');back.type=next.type='button';back.className='button outline';next.className='button';back.textContent='Retour';back.onclick=()=>go(current-1);
function readyDate(){return selected&&selected>today&&planning?.ready}
function readyTime(){return readyDate()&&chosenTime}
function validateDetails(){for(const field of details.querySelectorAll('input[required],select[required]')){if(!field.value.trim()||!field.reportValidity()){field.focus();return false}}
const phone=document.getElementById('customer-phone');if(!/^\+?[\d\s().-]+$/.test(phone.value.trim())||!/^.{9,15}$/.test(phone.value.replace(/\D/g,''))){feedback.textContent='Renseignez un numéro de téléphone valide.';phone.focus();return false}
if(!travelQuote||!document.getElementById('travel-confirm').checked){feedback.textContent='Calculez les frais et confirmez l’adresse reconnue avant de continuer.';return false}if(!prices){feedback.textContent='Les tarifs sont momentanément indisponibles.';return false}return true}
function fillRecap(){recap.replaceChildren();const values=[['Date',new Intl.DateTimeFormat('fr-FR',{dateStyle:'full'}).format(selected)],['Horaire',document.getElementById('time-recap')?.textContent||chosenTime],['Formule',duration.value+' heures'],['Client',document.getElementById('customer-first-name').value+' '+document.getElementById('customer-last-name').value],['E-mail',document.getElementById('customer-email').value],['Téléphone',document.getElementById('customer-phone').value],['Lieu',fullEventAddress()],['Événement',document.getElementById('event-type').value],['Personnalisation vidéo',document.getElementById('video-name').value||'À préciser']];for(const [name,value] of values){const term=document.createElement('dt'),definition=document.createElement('dd');term.textContent=name;definition.textContent=value;recap.append(term,definition)}updateAmounts();refreshPayment()}
function sync(){next.disabled=current===0?!readyDate():current===1?!readyTime():false;back.hidden=current===0;next.hidden=current===3;next.textContent=current===0?'Choisir ma formule et mon horaire':current===1?'Renseigner mes coordonnées':'Vérifier ma réservation';tabs.forEach((button,i)=>{button.disabled=i>furthest;if(i===current)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current')})}
function go(index){current=Math.max(0,Math.min(3,index));furthest=Math.max(furthest,current);panels.forEach((panel,i)=>panel.hidden=i!==current);feedback.textContent='';if(current===3)fillRecap();sync();panels[current].querySelector('h3').focus({preventScroll:true});root.scrollIntoView({behavior:'smooth',block:'start'})}
next.onclick=()=>{if(current===0&&!readyDate())return;if(current===1&&!readyTime())return;if(current===2&&!validateDetails())return;go(current+1)};
controls.append(back,next);grid.replaceChildren(nav,...panels,feedback,controls);grid.classList.add('booking-wizard');
root.addEventListener('click',()=>queueMicrotask(sync));root.addEventListener('change',()=>queueMicrotask(sync));new MutationObserver(sync).observe(document.getElementById('slots'),{childList:true,subtree:true});
document.querySelectorAll('[data-duration]').forEach(button=>button.addEventListener('click',()=>go(0)));
panels.forEach((panel,i)=>panel.hidden=i!==0);sync();
})();
