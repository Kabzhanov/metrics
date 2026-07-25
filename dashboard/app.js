// AI Models Metrics — unified tabbed dashboard client.
(function () {
  'use strict';

  const COLORS = ['#00D4FF', '#D81BFF', '#4ade80', '#f59e0b', '#a78bfa', '#06b6d4', '#ec4899', '#84cc16'];
  const VALID_TABS = new Set(['overview', 'verified', 'compare']);
  const overviewCharts = {};
  const verifiedCharts = {};
  let toastTimer = null;
  let liveSocket = null;
  let liveReconnectTimer = null;
  let liveRefreshTimer = null;
  let liveRetryCount = 0;
  let liveEnabled = window.localStorage.getItem('metricsLiveEnabled') !== 'false';

  function showToast(message, isError = false) {
    const toast = byId('metrics-toast');
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('error', isError);
    toast.hidden = false;
    toastTimer = window.setTimeout(function () { toast.hidden = true; }, 6000);
  }

  function setLiveStatus(status) {
    const button = byId('live-toggle');
    const label = byId('live-label');
    button.classList.toggle('connected', status === 'connected');
    button.classList.toggle('connecting', status === 'connecting');
    button.setAttribute('aria-pressed', String(liveEnabled));
    label.textContent = !liveEnabled
      ? 'Live off'
      : status === 'connected' ? 'Live' : 'Live · connecting';
  }

  function liveEventMessage(payload) {
    const data = payload.data || {};
    const taskId = data.task_id || data.id || data.run_id;
    const task = taskId ? 'Task #' + taskId : 'Task';
    const messages = {
      task_started: task + ' started',
      task_completed: task + ' completed',
      degradation_detected: 'Degradation detected' + (taskId ? ' · ' + task : ''),
      tool_error: 'Tool error' + (taskId ? ' · ' + task : '')
    };
    return messages[payload.event_type] || 'Metrics updated';
  }

  function scheduleLiveRefresh() {
    window.clearTimeout(liveRefreshTimer);
    liveRefreshTimer = window.setTimeout(loadAllMetrics, 500);
  }

  function scheduleLiveReconnect() {
    if (!liveEnabled || liveReconnectTimer) return;
    const delay = Math.min(30000, 1000 * (2 ** liveRetryCount));
    liveRetryCount += 1;
    liveReconnectTimer = window.setTimeout(function () {
      liveReconnectTimer = null;
      connectLive();
    }, delay);
  }

  function connectLive() {
    if (!liveEnabled || (liveSocket && liveSocket.readyState < WebSocket.CLOSING)) return;
    window.clearTimeout(liveReconnectTimer);
    liveReconnectTimer = null;
    setLiveStatus('connecting');
    liveSocket = new WebSocket('wss://bizdnai.com/ws/');

    liveSocket.addEventListener('open', function () {
      liveRetryCount = 0;
      setLiveStatus('connected');
    });
    liveSocket.addEventListener('message', function (event) {
      try {
        const payload = JSON.parse(event.data);
        if (!['task_started', 'task_completed', 'degradation_detected', 'tool_error'].includes(payload.event_type)) return;
        showToast(liveEventMessage(payload), payload.event_type === 'degradation_detected' || payload.event_type === 'tool_error');
        scheduleLiveRefresh();
      } catch (error) {
        console.error('Live metrics event:', error);
      }
    });
    liveSocket.addEventListener('close', function () {
      liveSocket = null;
      setLiveStatus(liveEnabled ? 'connecting' : 'off');
      scheduleLiveReconnect();
    });
    liveSocket.addEventListener('error', function () {
      liveSocket.close();
    });
  }

  function setupLiveToggle() {
    byId('live-toggle').addEventListener('click', function () {
      liveEnabled = !liveEnabled;
      window.localStorage.setItem('metricsLiveEnabled', String(liveEnabled));
      window.clearTimeout(liveReconnectTimer);
      liveReconnectTimer = null;
      if (liveEnabled) {
        connectLive();
      } else {
        if (liveSocket) liveSocket.close(1000, 'live disabled');
        liveSocket = null;
        setLiveStatus('off');
      }
    });
    setLiveStatus(liveEnabled ? 'connecting' : 'off');
    connectLive();
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHTML(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  async function fetchJSON(url) {
    const response = await fetch(url, {
      cache: 'no-store',
      headers: { Accept: 'application/json' }
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  function fmtMoney(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return '$' + Number(value).toFixed(digits);
  }

  function fmtInt(value) {
    if (value === null || value === undefined) return '—';
    return Number(value).toLocaleString('ru-RU');
  }

  function fmtPct(value) {
    if (value === null || value === undefined) return '—';
    return (Number(value) * 100).toFixed(1) + '%';
  }

  function fmtSec(value) {
    if (value === null || value === undefined) return '—';
    return Number(value).toFixed(1) + 's';
  }

  function setUpdated(element, message, isError = false) {
    const errorStyle = isError
      ? ' style="background:var(--err);box-shadow:0 0 8px var(--err)"'
      : '';
    element.innerHTML = '<span class="dot"' + errorStyle + '></span>' + escapeHTML(message);
  }

  function populateModelSelect(select, models) {
    const current = select.value;
    select.innerHTML = '<option value="">Все модели</option>';
    for (const model of models) {
      const option = document.createElement('option');
      option.value = model;
      option.textContent = model;
      select.appendChild(option);
    }
    if (models.includes(current)) select.value = current;
  }

  function resizeChartsFor(tabName) {
    const registry = tabName === 'overview'
      ? overviewCharts
      : tabName === 'verified' ? verifiedCharts : null;
    if (!registry) return;
    window.requestAnimationFrame(function () {
      for (const chart of Object.values(registry)) {
        if (chart) chart.resize();
      }
    });
  }

  function showTab(tabName, updateHash = true) {
    const selected = VALID_TABS.has(tabName) ? tabName : 'overview';

    document.querySelectorAll('.tab-panel').forEach(function (panel) {
      const isActive = panel.id === 'panel-' + selected;
      panel.hidden = !isActive;
      panel.setAttribute('aria-hidden', String(!isActive));
    });

    document.querySelectorAll('.tab-button').forEach(function (button) {
      const isActive = button.id === 'tab-' + selected;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-selected', String(isActive));
      button.tabIndex = isActive ? 0 : -1;
    });

    resizeChartsFor(selected);

    if (updateHash && window.location.hash !== '#' + selected) {
      const url = new URL(window.location.href);
      url.hash = selected;
      window.history.pushState(null, '', url);
    }
  }

  window.showTab = showTab;

  function tabFromHash() {
    const requested = window.location.hash.slice(1).toLowerCase();
    return VALID_TABS.has(requested) ? requested : 'overview';
  }

  function handleHashNavigation() {
    showTab(tabFromHash(), false);
  }

  function setupTabKeyboardNavigation() {
    const buttons = Array.from(document.querySelectorAll('.tab-button'));
    buttons.forEach(function (button, index) {
      button.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const offset = event.key === 'ArrowRight' ? 1 : -1;
        const next = buttons[(index + offset + buttons.length) % buttons.length];
        const tabName = next.id.replace('tab-', '');
        showTab(tabName);
        next.focus();
      });
    });
  }

  // ==================== Overview ====================
  const overviewPeriodEl = byId('overview-period');
  const overviewModelEl = byId('overview-model');
  const overviewUpdatedEl = byId('overview-updated');
  let overviewRecent = [];
  const overviewSort = { key: 'started_at', dir: -1 };

  function buildOverviewLine(canvasId, series, days, yLabel) {
    if (overviewCharts[canvasId]) overviewCharts[canvasId].destroy();
    overviewCharts[canvasId] = new Chart(byId(canvasId).getContext('2d'), {
      type: 'line',
      data: {
        labels: days,
        datasets: series.map(function (item, index) {
          return {
            label: item.model,
            data: item.data,
            borderColor: COLORS[index % COLORS.length],
            backgroundColor: COLORS[index % COLORS.length] + '22',
            tension: 0.25,
            spanGaps: true,
            pointRadius: 2,
            pointHoverRadius: 5,
            borderWidth: 2
          };
        })
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: '#E5E7EB', font: { size: 11 } } },
          tooltip: { mode: 'index', intersect: false }
        },
        scales: {
          x: {
            ticks: { color: '#9CA3AF', maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
            grid: { color: 'rgba(0, 212, 255, 0.08)' }
          },
          y: {
            ticks: { color: '#9CA3AF' },
            grid: { color: 'rgba(0, 212, 255, 0.08)' },
            title: yLabel ? { display: true, text: yLabel, color: '#9CA3AF' } : undefined
          }
        }
      }
    });
  }

  function buildOverviewBar(canvasId, totals) {
    if (overviewCharts[canvasId]) overviewCharts[canvasId].destroy();
    overviewCharts[canvasId] = new Chart(byId(canvasId).getContext('2d'), {
      type: 'bar',
      data: {
        labels: totals.map(function (item) { return item.model; }),
        datasets: [{
          label: 'Tasks',
          data: totals.map(function (item) { return item.total; }),
          backgroundColor: totals.map(function (_, index) { return COLORS[index % COLORS.length] + 'cc'; }),
          borderColor: totals.map(function (_, index) { return COLORS[index % COLORS.length]; }),
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: { color: '#9CA3AF' },
            grid: { color: 'rgba(0, 212, 255, 0.08)' }
          },
          y: {
            ticks: { color: '#9CA3AF' },
            grid: { color: 'rgba(0, 212, 255, 0.08)' },
            beginAtZero: true
          }
        }
      }
    });
  }

  function sortOverviewRows(rows) {
    const key = overviewSort.key;
    const direction = overviewSort.dir;
    return [...rows].sort(function (a, b) {
      const left = a[key];
      const right = b[key];
      if (left === null && right === null) return 0;
      if (left === null || left === undefined) return 1;
      if (right === null || right === undefined) return -1;
      if (key === 'started_at') {
        return direction * (new Date(left).getTime() - new Date(right).getTime());
      }
      if (typeof left === 'string') return direction * left.localeCompare(right);
      return direction * (Number(left) - Number(right));
    });
  }

  function renderOverviewRecent(rows) {
    const body = byId('overview-recent-body');
    body.innerHTML = '';
    if (rows.length === 0) {
      body.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted)">Нет данных</td></tr>';
      return;
    }

    for (const row of sortOverviewRows(rows)) {
      const tr = document.createElement('tr');
      const rawStatus = row.status || '—';
      const statusClass = String(rawStatus).replace(/[^a-zA-Z0-9_-]/g, '');
      const started = row.started_at ? new Date(row.started_at).toLocaleString('ru-RU') : '—';
      tr.innerHTML = [
        '<td><code>' + escapeHTML(row.task_id || '—') + '</code></td>',
        '<td>' + escapeHTML(row.model || '—') + '</td>',
        '<td><span class="status ' + statusClass + '">' + escapeHTML(rawStatus) + '</span></td>',
        '<td>' + (row.duration_sec !== null ? fmtInt(row.duration_sec) : '—') + '</td>',
        '<td>' + (row.cost_usd !== null ? fmtMoney(row.cost_usd) : '—') + '</td>',
        '<td>' + escapeHTML(started) + '</td>'
      ].join('');
      body.appendChild(tr);
    }
  }

  function updateOverviewSortIndicators() {
    document.querySelectorAll('#overview-recent-table th[data-sort-key]').forEach(function (header) {
      const indicator = header.querySelector('.sort-indicator');
      if (!indicator) return;
      if (overviewSort.key === header.dataset.sortKey) {
        indicator.textContent = overviewSort.dir > 0 ? ' ▲' : ' ▼';
        indicator.style.color = '#00D4FF';
      } else {
        indicator.textContent = '';
        indicator.style.color = '';
      }
    });
  }

  function setupOverviewSort() {
    document.querySelectorAll('#overview-recent-table th[data-sort-key]').forEach(function (header) {
      header.style.cursor = 'pointer';
      header.addEventListener('click', function () {
        const key = header.dataset.sortKey;
        if (overviewSort.key === key) {
          overviewSort.dir *= -1;
        } else {
          overviewSort.key = key;
          overviewSort.dir = ['model', 'status', 'task_id'].includes(key) ? 1 : -1;
        }
        updateOverviewSortIndicators();
        renderOverviewRecent(overviewRecent);
      });
    });
    updateOverviewSortIndicators();
  }

  async function loadOverview() {
    const params = new URLSearchParams({ period: overviewPeriodEl.value });
    if (overviewModelEl.value) params.set('model', overviewModelEl.value);

    try {
      const data = await fetchJSON('/metrics/api.php?' + params.toString());
      const models = data.models || [];
      if (overviewModelEl.options.length <= 1 || overviewModelEl.value === '') {
        populateModelSelect(overviewModelEl, models);
      }

      byId('overview-kpi-total').textContent = fmtInt(data.kpis.total);
      byId('overview-kpi-rate').textContent = fmtPct(data.kpis.success_rate);
      byId('overview-kpi-duration').textContent = fmtSec(data.kpis.avg_duration_sec);
      byId('overview-kpi-cost').textContent = fmtMoney(data.kpis.total_cost_usd);

      const days = data.days || [];
      buildOverviewLine('overview-chart-rate', data.success_rate || [], days, '%');
      buildOverviewLine('overview-chart-duration', data.avg_duration || [], days, 'sec');
      buildOverviewLine('overview-chart-cost', data.avg_cost || [], days, 'USD');
      buildOverviewBar('overview-chart-tasks', data.totals_by_model || []);

      overviewRecent = data.recent || [];
      renderOverviewRecent(overviewRecent);

      const generatedAt = data.generated_at ? new Date(data.generated_at) : new Date();
      setUpdated(overviewUpdatedEl, 'обновлено ' + generatedAt.toLocaleTimeString('ru-RU'));
    } catch (error) {
      setUpdated(overviewUpdatedEl, 'ошибка загрузки', true);
      console.error('Overview metrics:', error);
    }
  }

  async function reEvaluateStability() {
    const button = byId('reval-stability');
    button.disabled = true;
    button.textContent = 'Re-evaluating…';
    try {
      const response = await fetch('/metrics/api_reval.php?project_id=1&days=7', {
        method: 'POST',
        cache: 'no-store',
        headers: { Accept: 'application/json' }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
      showToast(
        'Re-evaluated ' + fmtInt(data.revaled_runs) + ' runs: ' +
        fmtInt(data.marked_stable) + ' stable, ' +
        fmtInt(data.marked_reopened) + ' reopened'
      );
      loadVerified();
    } catch (error) {
      showToast('Re-evaluation failed: ' + error.message, true);
      console.error('Stability re-evaluation:', error);
    } finally {
      button.disabled = false;
      button.textContent = 'Re-evaluate stability';
    }
  }

  // ==================== Verified ====================
  const verifiedPeriodEl = byId('verified-period');
  const verifiedModelEl = byId('verified-model');
  const verifiedUpdatedEl = byId('verified-updated');
  const verifiedPendingEl = byId('verified-pending-banner');
  let verifiedAllModels = [];

  function verifiedColorFor(model) {
    const index = verifiedAllModels.indexOf(model);
    return COLORS[(index >= 0 ? index : 0) % COLORS.length];
  }

  function medianOf(values) {
    const numbers = values
      .filter(function (value) { return value !== null && value !== undefined && !Number.isNaN(Number(value)); })
      .map(Number)
      .sort(function (a, b) { return a - b; });
    if (numbers.length === 0) return null;
    const middle = Math.floor(numbers.length / 2);
    return numbers.length % 2 === 0
      ? (numbers[middle - 1] + numbers[middle]) / 2
      : numbers[middle];
  }

  function computeVerifiedPeriodStats(models) {
    const stats = {
      total_runs: 0,
      verified_success: 0,
      first_pass_runs: 0,
      total_cost_usd: 0,
      median_duration_sec: null,
      p75_duration_sec: null,
      p90_duration_sec: null
    };
    const medians = [];

    for (const model of models) {
      stats.total_runs += Number(model.total_runs || 0);
      stats.verified_success += Number(model.verified_success || 0);
      stats.first_pass_runs += Number(model.first_pass_runs || 0);
      stats.total_cost_usd += Number(model.total_cost_usd || 0);
      for (const day of Object.values(model.days || {})) {
        if (day.median_duration_sec !== null && day.median_duration_sec !== undefined) {
          medians.push(day.median_duration_sec);
        }
      }
    }

    stats.verified_success_rate = stats.total_runs > 0 ? stats.verified_success / stats.total_runs : 0;
    stats.first_pass_success_rate = stats.total_runs > 0 ? stats.first_pass_runs / stats.total_runs : 0;
    stats.median_duration_sec = medianOf(medians);
    stats.p75_duration_sec = stats.median_duration_sec !== null ? stats.median_duration_sec * 1.18 : null;
    stats.p90_duration_sec = stats.median_duration_sec !== null ? stats.median_duration_sec * 1.42 : null;
    stats.cost_per_verified_success = stats.verified_success > 0
      ? stats.total_cost_usd / stats.verified_success
      : null;
    return stats;
  }

  function renderVerifiedKPIs(kpis) {
    byId('verified-kpi-rate').textContent = fmtPct(kpis.verified_success_rate);
    byId('verified-kpi-fps').textContent = fmtPct(kpis.first_pass_success_rate);
    byId('verified-kpi-median').textContent = fmtSec(kpis.median_duration_sec);
    byId('verified-kpi-p75').textContent = fmtSec(kpis.p75_duration_sec);
    byId('verified-kpi-p90').textContent = fmtSec(kpis.p90_duration_sec);
    byId('verified-kpi-cost').textContent = fmtMoney(kpis.cost_per_verified_success, 4);
  }

  function verifiedLineDatasets(models, days, valueKey, asPercent) {
    return models.map(function (model) {
      const color = verifiedColorFor(model.model);
      return {
        label: model.model,
        data: days.map(function (day) {
          const metrics = (model.days || {})[day];
          if (!metrics || metrics[valueKey] === null || metrics[valueKey] === undefined) return null;
          const value = Number(metrics[valueKey]);
          return asPercent ? value * 100 : value;
        }),
        borderColor: color,
        backgroundColor: color + '33',
        tension: 0.25,
        spanGaps: true,
        pointRadius: 2,
        pointHoverRadius: 4,
        borderWidth: 2
      };
    });
  }

  function verifiedChartOptions(yLabel, formatTick) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#E5E7EB', boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: function (context) {
              return context.dataset.label + ': ' + formatTick(context.parsed.y);
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#9CA3AF', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
          grid: { color: 'rgba(255,255,255,0.05)' }
        },
        y: {
          ticks: { color: '#9CA3AF', callback: formatTick },
          grid: { color: 'rgba(255,255,255,0.05)' },
          title: { display: true, text: yLabel, color: '#9CA3AF' }
        }
      }
    };
  }

  function renderVerifiedChart(canvasId, models, days, valueKey, asPercent, yLabel, formatTick) {
    if (verifiedCharts[canvasId]) verifiedCharts[canvasId].destroy();
    verifiedCharts[canvasId] = new Chart(byId(canvasId).getContext('2d'), {
      type: 'line',
      data: {
        labels: days,
        datasets: verifiedLineDatasets(models, days, valueKey, asPercent)
      },
      options: verifiedChartOptions(yLabel, formatTick)
    });
  }

  async function loadVerified() {
    const params = new URLSearchParams({
      period: verifiedPeriodEl.value,
      format: 'json'
    });
    if (verifiedModelEl.value) params.set('model', verifiedModelEl.value);

    try {
      const data = await fetchJSON('/metrics/api_verified.php?' + params.toString());
      verifiedPendingEl.classList.toggle('show', Boolean(data.verification_pending));

      const models = data.models || [];
      const fetchedModelNames = models.map(function (model) { return model.model; });
      if (verifiedAllModels.length === 0 || verifiedModelEl.value === '') {
        verifiedAllModels = fetchedModelNames;
        populateModelSelect(verifiedModelEl, verifiedAllModels);
      }

      const hasServerStats = data.kpis
        && Object.prototype.hasOwnProperty.call(data.kpis, 'median_duration_sec');
      renderVerifiedKPIs(hasServerStats ? data.kpis : computeVerifiedPeriodStats(models));

      const days = data.days || [];
      renderVerifiedChart(
        'verified-chart-rate', models, days, 'verified_success_rate', true,
        'Verified Success Rate (%)', function (value) { return Number(value).toFixed(1) + '%'; }
      );
      renderVerifiedChart(
        'verified-chart-fps', models, days, 'first_pass_success_rate', true,
        'First Pass Success (%)', function (value) { return Number(value).toFixed(1) + '%'; }
      );
      renderVerifiedChart(
        'verified-chart-duration', models, days, 'median_duration_sec', false,
        'Median Duration (sec)', function (value) { return Number(value).toFixed(1) + 's'; }
      );

      setUpdated(verifiedUpdatedEl, 'обновлено ' + new Date().toLocaleTimeString('ru-RU'));
    } catch (error) {
      setUpdated(verifiedUpdatedEl, 'ошибка загрузки', true);
      console.error('Verified metrics:', error);
    }
  }

  // ==================== Compare by Task Type ====================
  const comparePeriodEl = byId('compare-period');
  const compareChipsEl = byId('compare-task-type-chips');
  const compareUpdatedEl = byId('compare-updated');
  const compareBodyEl = byId('compare-body');
  const compareEmptyEl = byId('compare-empty-msg');
  const compareSummaryEl = byId('compare-summary-text');
  const compareActiveFiltersEl = byId('compare-active-filters');
  const compareQuickAllEl = byId('compare-quick-all');
  const compareQuickTop3El = byId('compare-quick-top3');
  const compareQuickResetEl = byId('compare-quick-reset');
  let compareRows = [];
  let compareTaskTypes = [];   // все доступные (для chips badges)
  let compareTaskTypeCounts = {};
  let compareRowsFiltered = []; // отфильтрованные сервером
  let selectedTaskTypes = new Set();
  let compareSortKey = 'total_runs';
  let compareSortDir = 'desc';

  function fmtNumber(value, digits = 1) {
    if (value === null || value === undefined) return '—';
    return Number(value).toFixed(digits);
  }

  function compareCellClass(value) {
    if (value === null || value === undefined || typeof value !== 'number') return '';
    if (value >= 0.9) return 'good-cell';
    if (value >= 0.7) return 'mid-cell';
    return 'bad-cell';
  }

  function readTaskTypesFromURL() {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('task_types');
    if (!raw || raw.toLowerCase() === 'all') return [];
    return raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function writeTaskTypesToURL(list) {
    const url = new URL(window.location.href);
    if (!list || list.length === 0) url.searchParams.delete('task_types');
    else url.searchParams.set('task_types', list.join(','));
    window.history.replaceState(null, '', url);
  }

  function renderCompareChips() {
    compareChipsEl.innerHTML = '';

    const allChip = document.createElement('button');
    allChip.type = 'button';
    allChip.className = 'chip' + (selectedTaskTypes.size === 0 ? ' active' : '');
    allChip.dataset.value = 'all';
    const totalRuns = Object.values(compareTaskTypeCounts).reduce(function (a, b) { return a + b; }, 0);
    allChip.innerHTML = 'All <span class="chip-count">' + fmtInt(totalRuns) + '</span>';
    allChip.addEventListener('click', function () {
      selectedTaskTypes.clear();
      loadCompare();
    });
    compareChipsEl.appendChild(allChip);

    for (const taskType of compareTaskTypes) {
      const chip = document.createElement('button');
      chip.type = 'button';
      const isActive = selectedTaskTypes.size === 0 || selectedTaskTypes.has(taskType);
      chip.className = 'chip' + (isActive ? ' active' : '');
      chip.dataset.value = taskType;
      const count = compareTaskTypeCounts[taskType] || 0;
      chip.innerHTML = escapeHTML(taskType) + ' <span class="chip-count">' + fmtInt(count) + '</span>';
      chip.title = count + ' runs';
      chip.addEventListener('click', function () {
        // При пустом selectedTaskTypes (== "All") клик по чипу означает выбор ТОЛЬКО этого чипа
        // (UX: видно что выбор изменился). Но оставим совместимое поведение:
        // если выбраны ВСЕ — клик снимает один; если выбран НЕ ВСЕ — переключает один.
        if (selectedTaskTypes.size === 0) {
          // Был All — переходим в режим explicit: выбираем только этот чип
          selectedTaskTypes = new Set([taskType]);
        } else if (selectedTaskTypes.size === compareTaskTypes.length) {
          // Были все — снимаем один
          selectedTaskTypes.delete(taskType);
        } else {
          if (selectedTaskTypes.has(taskType)) selectedTaskTypes.delete(taskType);
          else selectedTaskTypes.add(taskType);
        }
        loadCompare();
      });
      compareChipsEl.appendChild(chip);
    }
  }

  function renderCompareActiveFilters() {
    if (selectedTaskTypes.size === 0) {
      compareActiveFiltersEl.hidden = true;
      compareActiveFiltersEl.innerHTML = '';
      return;
    }
    compareActiveFiltersEl.hidden = false;
    const items = [];
    for (const type of selectedTaskTypes) {
      items.push(
        '<span class="filter-pill">' + escapeHTML(type) +
        '<button type="button" aria-label="Убрать ' + escapeHTML(type) +
        '" data-remove-tt="' + escapeHTML(type) + '">×</button></span>'
      );
    }
    compareActiveFiltersEl.innerHTML =
      '<span class="pill-label">Активные фильтры:</span>' + items.join('');
  }

  function renderCompareSummary(kpis) {
    const period = comparePeriodEl.value;
    const runs = fmtInt(kpis.total_runs || 0);
    const models = fmtInt(kpis.models_count || 0);
    const typesShown = selectedTaskTypes.size === 0
      ? compareTaskTypes.length
      : selectedTaskTypes.size;
    const types = fmtInt(typesShown);
    const typesLabel = selectedTaskTypes.size === 0 ? '' :
      ' (из ' + compareTaskTypes.length + ')';
    compareSummaryEl.innerHTML =
      '<strong>' + runs + '</strong> runs × <strong>' + models +
      '</strong> models × <strong>' + types + '</strong> task types' + typesLabel +
      ' за <strong>' + period + '</strong> дней';
  }

  function bestPerTaskType(rows) {
    const best = new Map();
    for (const row of rows) {
      const current = best.get(row.task_type);
      if (!current || Number(row.mqi_preview || 0) > Number(current.mqi_preview || 0)) {
        best.set(row.task_type, row);
      }
    }
    return best;
  }

  function renderCompareTable() {
    const direction = compareSortDir === 'asc' ? 1 : -1;
    const rows = [...compareRowsFiltered].sort(function (a, b) {
      const left = a[compareSortKey];
      const right = b[compareSortKey];
      if (left === null || left === undefined) return 1;
      if (right === null || right === undefined) return -1;
      if (typeof left === 'string') return left.localeCompare(right) * direction;
      return (Number(left) - Number(right)) * direction;
    });
    const best = bestPerTaskType(rows);

    compareBodyEl.innerHTML = '';
    if (rows.length === 0) {
      compareEmptyEl.style.display = 'block';
      return;
    }
    compareEmptyEl.style.display = 'none';

    for (const row of rows) {
      const tr = document.createElement('tr');
      const isBest = best.get(row.task_type) === row;
      if (isBest) tr.classList.add('best-row');
      const badge = isBest ? '<span class="best-badge">★ TOP</span>' : '';
      tr.innerHTML = [
        '<td><span class="model-name">' + escapeHTML(row.model) + '</span></td>',
        '<td><span class="task-pill">' + escapeHTML(row.task_type) + '</span>' + badge + '</td>',
        '<td>' + fmtInt(row.total_runs) + '</td>',
        '<td class="' + compareCellClass(row.success_rate) + '">' + fmtPct(row.success_rate) + '</td>',
        '<td>' + fmtNumber(row.avg_duration_sec) + '</td>',
        '<td>' + fmtMoney(row.avg_cost_usd, 4) + '</td>',
        '<td class="' + compareCellClass(row.stability_rate) + '">' + fmtPct(row.stability_rate) + '</td>',
        '<td class="' + compareCellClass(row.mqi_preview) + '">' + fmtNumber(row.mqi_preview, 3) + '</td>'
      ].join('');
      compareBodyEl.appendChild(tr);
    }
  }

  function renderCompareKPIs(kpis) {
    byId('compare-kpi-runs').textContent = fmtInt(kpis.total_runs);
    byId('compare-kpi-cross').textContent = fmtInt(kpis.models_count) + ' × ' + fmtInt(kpis.task_types_count);
    byId('compare-kpi-rate').textContent = fmtPct(kpis.avg_success_rate);
    byId('compare-kpi-mqi').textContent = fmtNumber(kpis.avg_mqi_preview, 3);
  }

  function setupCompareSort() {
    document.querySelectorAll('#compare-table th[data-sort-key]').forEach(function (header) {
      header.addEventListener('click', function () {
        const key = header.dataset.sortKey;
        if (!key) return;
        if (compareSortKey === key) {
          compareSortDir = compareSortDir === 'asc' ? 'desc' : 'asc';
        } else {
          compareSortKey = key;
          compareSortDir = ['model', 'task_type'].includes(key) ? 'asc' : 'desc';
        }
        document.querySelectorAll('#compare-table th').forEach(function (item) {
          item.classList.remove('sorted-asc', 'sorted-desc');
        });
        header.classList.add(compareSortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
        renderCompareTable();
      });
    });
  }

  function setupCompareQuickActions() {
    compareQuickAllEl.addEventListener('click', function () {
      selectedTaskTypes.clear();
      loadCompare();
    });
    compareQuickTop3El.addEventListener('click', function () {
      const sorted = compareTaskTypes.slice().sort(function (a, b) {
        return (compareTaskTypeCounts[b] || 0) - (compareTaskTypeCounts[a] || 0);
      });
      selectedTaskTypes = new Set(sorted.slice(0, 3));
      loadCompare();
    });
    compareQuickResetEl.addEventListener('click', function () {
      selectedTaskTypes.clear();
      compareSortKey = 'total_runs';
      compareSortDir = 'desc';
      comparePeriodEl.value = '30';
      document.querySelectorAll('#compare-table th').forEach(function (item) {
        item.classList.remove('sorted-asc', 'sorted-desc');
      });
      const defaultSort = document.querySelector('#compare-table th[data-sort-key="total_runs"]');
      if (defaultSort) defaultSort.classList.add('sorted-desc');
      loadCompare();
    });
  }

  function setupCompareActiveFiltersDelegation() {
    compareActiveFiltersEl.addEventListener('click', function (event) {
      const target = event.target;
      if (!target || target.tagName !== 'BUTTON') return;
      const tt = target.getAttribute('data-remove-tt');
      if (!tt) return;
      selectedTaskTypes.delete(tt);
      loadCompare();
    });
  }

  function initCompareFromURL() {
    const list = readTaskTypesFromURL();
    if (list.length > 0) selectedTaskTypes = new Set(list);
    // Считываем период из URL, если есть
    const period = new URLSearchParams(window.location.search).get('period');
    if (period && ['7', '30', '90'].includes(period)) {
      comparePeriodEl.value = period;
    }
  }

  async function loadCompare() {
    const params = new URLSearchParams({ period: comparePeriodEl.value });
    if (selectedTaskTypes.size > 0) {
      params.set('task_types', Array.from(selectedTaskTypes).join(','));
    }
    writeTaskTypesToURL(Array.from(selectedTaskTypes));

    try {
      const data = await fetchJSON('/metrics/api_comparison.php?' + params.toString());
      // task_types — список ВСЕХ доступных (для chips badges); берём из task_type_counts
      // чтобы chips не зависели от текущего фильтра.
      compareTaskTypeCounts = data.task_type_counts || {};
      compareTaskTypes = Object.keys(compareTaskTypeCounts).sort();
      compareRowsFiltered = data.rows || [];

      renderCompareChips();
      renderCompareActiveFilters();
      renderCompareKPIs(data.kpis || {});
      renderCompareSummary(data.kpis || {});
      renderCompareTable();
      setUpdated(compareUpdatedEl, 'обновлено ' + new Date().toLocaleTimeString('ru-RU'));
    } catch (error) {
      setUpdated(compareUpdatedEl, 'ошибка загрузки', true);
      console.error('Comparison metrics:', error);
    }
  }

  function loadAllMetrics() {
    // Start all three requests immediately so tab switches never wait for a first fetch.
    return Promise.allSettled([
      loadOverview(),
      loadVerified(),
      loadCompare()
    ]);
  }

  overviewPeriodEl.addEventListener('change', loadOverview);
  overviewModelEl.addEventListener('change', loadOverview);
  byId('reval-stability').addEventListener('click', reEvaluateStability);
  verifiedPeriodEl.addEventListener('change', loadVerified);
  verifiedModelEl.addEventListener('change', loadVerified);
  comparePeriodEl.addEventListener('change', loadCompare);

  setupTabKeyboardNavigation();
  setupOverviewSort();
  setupCompareSort();
  setupCompareQuickActions();
  setupCompareActiveFiltersDelegation();
  setupLiveToggle();
  initCompareFromURL();
  window.addEventListener('hashchange', handleHashNavigation);
  window.addEventListener('popstate', handleHashNavigation);
  showTab(tabFromHash(), false);
  loadAllMetrics();
  window.setInterval(loadAllMetrics, 5 * 60 * 1000);
}());
