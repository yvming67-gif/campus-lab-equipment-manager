/* Keep the small-screen navigation compact; native details remains keyboard usable. */
const appearanceKey = 'campus-interface-glass';
// Asset failure is local to the photograph, never a reservation error.
document.querySelectorAll('[data-reference-photo] img').forEach(image => {
  const unavailable = () => {
    image.hidden = true;
    const frame=image.closest('[data-reference-photo]');
    frame.classList.add('is-unavailable');
    frame.querySelector('.photo-fallback').hidden=false;
    const imageLink=frame.querySelector('.reference-photo-image');
    imageLink.removeAttribute('href');
    imageLink.setAttribute('aria-disabled','true');
    imageLink.setAttribute('aria-label','实景图片暂不可用');
  };
  image.addEventListener('error',unavailable,{once:true});
  if(image.complete && !image.naturalWidth)unavailable();
});
// Device-local appearance only; non-staff pages always keep comfortable spacing.
const densityControls = document.querySelectorAll('[data-density-toggle]');
let densityChoice = 'comfortable';
try { if (densityControls.length && localStorage.getItem('campus-table-density') === 'compact') densityChoice = 'compact'; } catch (_) {}
function setDensity(choice) {
  densityChoice = densityControls.length && choice === 'compact' ? 'compact' : 'comfortable';
  document.documentElement.dataset.density = densityChoice;
  densityControls.forEach(button => {
    button.hidden = false;
    button.setAttribute('aria-pressed', String(densityChoice === 'compact'));
    button.textContent = densityChoice === 'compact' ? '舒适视图' : '紧凑视图';
    button.title = '仅调整宽屏表格与筛选区的留白';
  });
}
setDensity(densityChoice);
densityControls.forEach(button => button.addEventListener('click', () => {
  setDensity(densityChoice === 'compact' ? 'comfortable' : 'compact');
  try { localStorage.setItem('campus-table-density', densityChoice); } catch (_) {}
}));
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

// Echo the actual form values; choosing a suggestion never submits the booking.
const reservationSummary = document.querySelector('[data-reservation-summary]');
if (reservationSummary) {
  const start = document.querySelector('#reservation-form input[name=starts_at]');
  const end = document.querySelector('#reservation-form input[name=ends_at]');
  const purpose = document.querySelector('#reservation-form textarea[name=purpose]');
  const participants = document.querySelector('#reservation-form input[name=participants]');
  const output = reservationSummary.querySelector('[data-time-summary]');
  const purposeOutput = reservationSummary.querySelector('[data-purpose-summary]');
  const participantsOutput = reservationSummary.querySelector('[data-participants-summary]');
  const update = () => {
    output.textContent = start.value && end.value
      ? `${start.value.replace('T', ' ')} — ${end.value.replace('T', ' ')}（北京时间）`
      : '请填写完整起止时间。';
    purposeOutput.textContent = purpose.value.trim() || '尚未填写';
    if (participantsOutput) participantsOutput.textContent = participants.value || '尚未填写';
  };
  if (start && end && purpose && output && purposeOutput) {
    for (const field of [start, end, purpose, participants]) field?.addEventListener('input', update);
    update();
  }
}

const recovery = document.querySelector('[data-reservation-recovery]');
const reservationResult = document.querySelector('[data-reservation-result]');
if (reservationResult) {
  try {
    const key = 'reservation-receipt:' + reservationResult.dataset.context;
    const pending = JSON.parse(sessionStorage.getItem(key));
    if (pending?.request_key === reservationResult.dataset.reservationResult) sessionStorage.removeItem(key);
  } catch (_) { /* The form still works when browser storage is unavailable. */ }
}
if (recovery) {
  const form = document.querySelector('#reservation-form form');
  const storageKey = 'reservation-receipt:' + recovery.dataset.context;
  const fields = ['starts_at', 'ends_at', 'purpose', 'participants', 'request_key', 'expected_approval_mode'];
  const readForm = () => Object.fromEntries(fields.map(name => [name, form.elements[name].value]));
  const showReceipt = key => {
    const url = new URL(recovery.dataset.url, location.origin);
    url.searchParams.set('key', key);
    recovery.querySelector('a').href = url;
    recovery.hidden = false;
  };
  const lockOriginal = () => {
    for (const field of ['starts_at', 'ends_at', 'purpose', 'participants']) form.elements[field].readOnly = true;
    form.querySelector('[type=submit]').textContent = '按原内容重试';
  };
  let pending = null;
  try {
    pending = JSON.parse(sessionStorage.getItem(storageKey));
    if (pending && recovery.dataset.rejected === pending.request_key) {
      sessionStorage.removeItem(storageKey);
      pending = null;
    }
    if (pending && fields.every(name => typeof pending[name] === 'string')) {
      showReceipt(pending.request_key);
      if (form) {
        for (const name of fields) form.elements[name].value = pending[name];
        form.elements.purpose.dispatchEvent(new Event('input'));
        form.querySelector('[data-mode-summary]').textContent = pending.expected_approval_mode === 'MANUAL'
          ? '原申请由实验室管理员审核，待审核不占时段'
          : '原申请规则通过后自动确认';
        lockOriginal();
      }
    } else {
      pending = null;
    }
  } catch (_) { /* Receipt link can still appear on submit. */ }
  window.addEventListener('pageshow', event => {
    if (event.persisted && pending) location.reload();
  });
  form?.addEventListener('submit', event => {
    const values = readForm();
    if (pending && fields.some(name => values[name] !== pending[name])) {
      event.preventDefault();
      return;
    }
    if (!values.request_key) return;
    pending = values;
    lockOriginal();
    showReceipt(values.request_key);
    try { sessionStorage.setItem(storageKey, JSON.stringify(values)); } catch (_) { /* Optional recovery after reload. */ }
  });
}
