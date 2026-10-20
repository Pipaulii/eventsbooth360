"use strict";
const video=document.getElementById('hero-demo');
const toggle=document.getElementById('demo-toggle');
toggle.addEventListener('click',()=>{if(video.paused){video.play();toggle.textContent='Mettre en pause';}else{video.pause();toggle.textContent='Lire la vidéo';}});
document.querySelectorAll('button,input,select,textarea').forEach(el=>{if(el!==toggle)el.disabled=true;});
document.querySelectorAll('form').forEach(form=>form.addEventListener('submit',e=>e.preventDefault()));
const notice=document.querySelector('.availability .notice');
if(notice)notice.textContent='Aperçu des couleurs uniquement. Pour réserver, rendez-vous sur la page principale du site.';
