const reviewBoard = document.querySelector('[data-review-board]');

if (reviewBoard) {
  const content = reviewBoard.querySelector('[data-review-content]');
  const refreshLink = reviewBoard.querySelector('[data-review-refresh]');
  const status = reviewBoard.querySelector('[data-review-status]');
  const interval = 60000;
  const trendInterval = 5 * interval;
  const idleLimit = 5 * interval;
  let lastTrend = Date.now();
  let lastActivity = Date.now();
  let timer;
  let pending = false;
  let accessLost = false;
  let delay = interval;

  const active = () => !document.hidden && Date.now() - lastActivity < idleLimit;
  const schedule = () => {
    clearTimeout(timer);
    timer = undefined;
    if (active() && !pending && !accessLost) {
      timer = setTimeout(() => {
        timer = undefined;
        if (active()) refresh();
      }, delay);
    }
  };

  function updatePreview(nextList) {
    const list = content.querySelector('[data-review-list]');

    const focus = document.activeElement;
    const focusedKey = list.contains(focus) ? focus.closest('[data-review-key]')?.dataset.reviewKey : null;
    const focusedOnLink = focus?.hasAttribute('data-review-link');

    // Keep the focused/hovered nodes for rows still in the preview.
    const existing = new Map([...list.querySelectorAll('[data-review-key]')].map(
      row => [row.dataset.reviewKey, row],
    ));
    let current = list.firstElementChild;
    for (const next of [...nextList.children]) {
      const key = next.dataset.reviewKey;
      let row = existing.get(key);
      if (!row) {
        row = next;
      } else {
        existing.delete(key);
        for (const selector of ['[data-review-link]', '.review-stage', '.workbench-help-note']) {
          const element = row.querySelector(selector);
          const updated = next.querySelector(selector);
          if (element.innerHTML !== updated.innerHTML) element.innerHTML = updated.innerHTML;
          if (element.className !== updated.className) element.className = updated.className;
        }
      }
      if (row !== current) list.insertBefore(row, current);
      current = row.nextElementSibling;
    }
    while (current) {
      const next = current.nextElementSibling;
      current.remove();
      current = next;
    }
    if (focusedKey && document.activeElement !== focus) {
      const row = [...list.children].find(item => item.dataset.reviewKey === focusedKey);
      const selector = focusedOnLink ? '[data-review-link]' : 'summary';
      (row?.querySelector(selector) ?? refreshLink).focus({ preventScroll: true });
    }

  }

  function updateSnapshot(next) {
    const metrics = [...next.querySelectorAll('[data-review-metric]')];
    const nextTime = next.querySelector('[data-review-time]');
    const nextList = next.querySelector('[data-review-list]');
    const nextHandover = next.querySelector('[data-handover-feed]');
    if (metrics.length !== 3 || !nextTime || !nextList || !nextHandover) throw new Error('Invalid snapshot.');
    for (const item of metrics) {
      const metric = content.querySelector('[data-review-metric="' + item.dataset.reviewMetric + '"]');
      const number = metric.querySelector('[data-review-value]');
      const nextValue = item.querySelector('[data-review-value]').textContent;
      const bar = metric.querySelector('[data-review-bar]');
      if (number.textContent !== nextValue) number.textContent = nextValue;
      if (bar) bar.style.width = item.querySelector('[data-review-bar]').style.width;
    }
    const time = content.querySelector('[data-review-time]');
    time.dateTime = nextTime.dateTime;
    time.textContent = nextTime.textContent;
    updatePreview(nextList);
    const handover = content.querySelector('[data-handover-feed]');
    if (handover.innerHTML !== nextHandover.innerHTML) {
      const key = handover.contains(document.activeElement) ? document.activeElement.dataset.handoverFocus : null;
      handover.replaceWith(nextHandover);
      if (key) {
        const retained = [...nextHandover.querySelectorAll('[data-handover-focus]')]
          .find(link => link.dataset.handoverFocus === key);
        (retained ?? refreshLink).focus({ preventScroll: true });
      }
    }
  }

  function updateTrend(next) {
    const trend = document.querySelector('[data-review-restricted]');
    const updated = next.querySelector('[data-review-restricted]');
    if (!trend || !updated) throw new Error('Trend unavailable.');
    if (trend.innerHTML !== updated.innerHTML) {
      const focusedURL = trend.contains(document.activeElement) ? document.activeElement.href : null;
      trend.replaceWith(updated);
      if (focusedURL) {
        const retained = [...updated.querySelectorAll('a')].find(link => link.href === focusedURL);
        (retained ?? updated.querySelector('[data-review-trend-refresh]')).focus({ preventScroll: true });
      }
    }
    lastTrend = Date.now();
  }

  async function refresh(manual = false, forceTrend = false) {
    if (pending || accessLost || document.hidden) return;
    clearTimeout(timer);
    timer = undefined;
    pending = true;
    if (manual) status.textContent = '正在刷新待审数据…';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const endpoint = new URL(reviewBoard.dataset.reviewBoard, location.origin);
      const withTrend = forceTrend || Date.now() - lastTrend >= trendInterval;
      if (withTrend) endpoint.searchParams.set('trend', '1');
      const response = await fetch(endpoint, {
        cache: 'no-store',
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403 || response.redirected) {
        accessLost = true;
        if (content.contains(document.activeElement)) refreshLink.focus({ preventScroll: true });
        content.replaceChildren();
        document.querySelectorAll('[data-review-restricted]').forEach(section => section.remove());
        status.textContent = '待审数据已隐藏，请刷新页面重新确认登录和权限。';
        return;
      }
      if (!response.ok) throw new Error('Snapshot request failed.');
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      updateSnapshot(next);
      if (withTrend) updateTrend(next);
      status.textContent = manual ? '待审数据已更新。' : '';
      delay = interval;
    } catch (_) {
      status.textContent = '暂时无法刷新，正在显示上次成功取得的数据。';
      delay = Math.min(delay * 2, 4 * interval);
    } finally {
      clearTimeout(timeout);
      pending = false;
      schedule();
    }
  }

  function resume() {
    if (document.hidden) {
      clearTimeout(timer);
      timer = undefined;
      return;
    }
    lastActivity = Date.now();
    refresh();
  }

  function recordActivity() {
    const wasIdle = !active();
    lastActivity = Date.now();
    if (wasIdle) refresh();
  }

  refreshLink.addEventListener('click', event => {
    if (accessLost || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    lastActivity = Date.now();
    refresh(true);
  });
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-review-trend-refresh]');
    if (!trigger || accessLost || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    lastActivity = Date.now();
    refresh(true, true);
  });
  document.addEventListener('visibilitychange', resume);
  document.addEventListener('pointerdown', recordActivity);
  document.addEventListener('keydown', recordActivity);
  window.addEventListener('scroll', recordActivity, { passive: true });
  window.addEventListener('pageshow', event => { if (event.persisted) resume(); });
  schedule();
}
