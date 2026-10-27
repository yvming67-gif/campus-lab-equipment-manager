/* Keep the small-screen navigation compact; native details remains keyboard usable. */
const appearanceKey = 'campus-interface-glass';
const transparencyPreference = window.matchMedia('(prefers-reduced-transparency: reduce)');
let appearanceChoice;
try { appearanceChoice = localStorage.getItem(appearanceKey); } catch (_) { /* Optional preference storage. */ }
const glassControls = document.querySelectorAll('[data-glass-toggle]');
function setGlass(enabled) {
  document.documentElement.dataset.glass = enabled ? 'on' : 'off';
  glassControls.forEach(button => {
    button.hidden = false;
    button.setAttribute('aria-pressed', String(enabled));
    button.querySelector('[data-glass-state]').textContent = enabled ? '开' : '关';
  });
}
setGlass(appearanceChoice === 'on' || (appearanceChoice !== 'off' && !transparencyPreference.matches));
glassControls.forEach(button => button.addEventListener('click', () => {
  appearanceChoice = document.documentElement.dataset.glass === 'on' ? 'off' : 'on';
  setGlass(appearanceChoice === 'on');
  try { localStorage.setItem(appearanceKey, appearanceChoice); } catch (_) { /* Page-local choice still works. */ }
}));
if (transparencyPreference.addEventListener) transparencyPreference.addEventListener('change', event => {
  if (appearanceChoice !== 'on' && appearanceChoice !== 'off') setGlass(!event.matches);
});

const navigation = document.querySelector('details.navigation');
if (navigation) {
  const compact = window.matchMedia('(max-width: 760px)');
  if (compact.matches) navigation.open = false;
  // Only a breakpoint change resets the menu; preserve the user's manual toggle.
  const syncNavigation = event => { navigation.open = !event.matches; };
  if (compact.addEventListener) compact.addEventListener('change', syncNavigation);
  else if (compact.addListener) compact.addListener(syncNavigation);
}

// Soft reflected light follows the pointer only on interactive surfaces.
if (window.matchMedia('(hover: hover) and (pointer: fine)').matches &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.querySelectorAll('.task-card,.resource-card,a.stat,.management-grid>a').forEach(card => {
    let frame;
    card.addEventListener('pointermove', event => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = card.getBoundingClientRect();
        card.style.setProperty('--light-x', `${event.clientX - box.left}px`);
        card.style.setProperty('--light-y', `${event.clientY - box.top}px`);
      });
    }, { passive: true });
    card.addEventListener('pointerleave', () => { cancelAnimationFrame(frame); });
  });
}

// Visual acknowledgement only: do not disable named submit controls or alter form data.
document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const button = event.submitter;
    if (!button || !button.classList.contains('button') || button.formTarget === '_blank') return;
    button.classList.add('is-submitting');
    button.setAttribute('aria-busy', 'true');
    window.setTimeout(() => {
      button.classList.remove('is-submitting');
      button.removeAttribute('aria-busy');
    }, 12000);
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('.is-submitting').forEach(button => {
    button.classList.remove('is-submitting'); button.removeAttribute('aria-busy');
  });
});
