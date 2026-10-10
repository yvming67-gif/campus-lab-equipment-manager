document.querySelectorAll('nav a').forEach(a=>{if(a.pathname==='/'?location.pathname==='/':location.pathname.startsWith(a.pathname))a.classList.add('active')});
document.querySelectorAll('.business-form').forEach(form=>form.addEventListener('submit',()=>{const button=form.querySelector('button[type=submit]');if(button){button.disabled=true;button.textContent='正在保存…'}}));
