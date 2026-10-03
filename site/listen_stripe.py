"""Forward Stripe test webhooks, storing the signing secret without printing it."""
import os,subprocess,re
from pathlib import Path
import payments
root=Path(__file__).resolve().parent
key=payments.config().get('STRIPE_SECRET_KEY','')
if not key.startswith(('sk_test_','rk_test_')):raise SystemExit('Cle de test absente.')
environment=os.environ.copy();environment['STRIPE_API_KEY']=key
command=[str(root/'.tools/stripe.exe'),'--config',str(root/'.tools/stripe.toml'),'listen','--forward-to','http://127.0.0.1:3600/api/stripe/webhook','--events','checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired','--color','off']
process=subprocess.Popen(command,env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
try:
 for line in process.stdout:
  match=re.search(r'whsec_[A-Za-z0-9]+',line)
  if match:
   path=root/'.env';content=path.read_text(encoding='utf-8-sig')
   content=re.sub(r'^STRIPE_WEBHOOK_SECRET=.*$',lambda _: 'STRIPE_WEBHOOK_SECRET='+match.group(0),content,flags=re.M)
   path.write_text(content,encoding='utf-8')
   print('Confirmations Stripe connectees. Secret enregistre localement sans affichage.',flush=True)
  elif '[200]' in line:print('Notification Stripe traitee : OK',flush=True)
  elif re.search(r'\[(400|403|500|503)\]',line):print('Notification Stripe : erreur de traitement a verifier.',flush=True)
  elif 'ERROR' in line or 'Error' in line or 'error' in line:print('Connexion Stripe : erreur, aucun secret affiche.',flush=True)
 process.wait()
 print('Ecoute Stripe arretee. Code :',process.returncode,flush=True)
finally:
 process.terminate()
