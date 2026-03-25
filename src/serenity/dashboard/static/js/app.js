/**
 * Serenity QA Live Dashboard — WebSocket Client & DOM Controller
 *
 * Standalone module (mirrors the inline <script> in index.html).
 * Can be loaded via <script src="/static/js/app.js"> when developing
 * with separate files instead of the self-contained SPA.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------
  // State
  // ---------------------------------------------------------------
  const state = {
    ws: null,
    connected: false,
    reconnectAttempts: 0,
    maxReconnectAttempts: 50,
    reconnectDelay: 1000,
    startTime: null,
    timerInterval: null,
    pingInterval: null,
    activeFilter: 'all',
    findings: [],
    pageStatuses: {},
    domainScores: {},
    overallScore: 0,
    pagesAnalyzed: 0,
    totalPages: 0,
    scanComplete: false,
    severityCounts: { critical: 0, high: 0, medium: 0, low: 0 },
    domainChart: null,
    alertTimeout: null,
  };

  // ---------------------------------------------------------------
  // DOM Helpers
  // ---------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    targetUrl:         $('#target-url'),
    scanStatus:        $('#scan-status'),
    elapsedTime:       $('#elapsed-time'),
    wsIndicator:       $('#ws-indicator'),
    gaugeNeedle:       $('#gauge-needle'),
    gaugeScore:        $('#gauge-score'),
    verdictText:       $('#verdict-text'),
    domainBars:        $('#domain-bars'),
    progressPages:     $('#progress-pages'),
    progressPercent:   $('#progress-percent'),
    progressEta:       $('#progress-eta'),
    progressFill:      $('#progress-fill'),
    findingsCount:     $('#findings-count'),
    findingsList:      $('#findings-list'),
    heatmapGrid:       $('#heatmap-grid'),
    statPages:         $('#stat-pages'),
    statFindings:      $('#stat-findings'),
    statCritical:      $('#stat-critical'),
    statDomains:       $('#stat-domains'),
    sevCritical:       $('#sev-critical'),
    sevHigh:           $('#sev-high'),
    sevMedium:         $('#sev-medium'),
    sevLow:            $('#sev-low'),
    alertBanner:       $('#alert-banner'),
    alertTitle:        $('#alert-title'),
    alertDesc:         $('#alert-desc'),
    connectionOverlay: $('#connection-overlay'),
    scanCompleteBadge: $('#scan-complete-badge'),
    domainChartCanvas: $('#domain-chart'),
  };

  // ---------------------------------------------------------------
  // Utility Functions
  // ---------------------------------------------------------------

  /**
   * Format seconds into HH:MM:SS or MM:SS string.
   */
  function formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  /**
   * Shorten a URL for display.
   */
  function shortUrl(url) {
    if (!url) return '';
    try {
      const u = new URL(url);
      let p = u.pathname;
      if (p.length > 30) p = p.substring(0, 27) + '...';
      return u.hostname + p;
    } catch {
      return url.substring(0, 40);
    }
  }

  /**
   * Format an ISO timestamp into HH:MM:SS.
   */
  function formatTimestamp(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return '';
    }
  }

  /**
   * Return a CSS color based on a 0-100 score.
   */
  function scoreColor(score) {
    if (score >= 91) return 'var(--green)';
    if (score >= 70) return 'var(--sea-mid)';
    if (score >= 60) return 'var(--yellow)';
    if (score >= 40) return 'var(--orange)';
    return 'var(--red)';
  }

  /**
   * HTML-escape a string to prevent XSS.
   */
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ---------------------------------------------------------------
  // Gauge (SVG Speedometer)
  // ---------------------------------------------------------------

  function updateGauge(score) {
    score = Math.max(0, Math.min(100, score));
    state.overallScore = score;

    // Needle: -90deg (0) to +90deg (100)
    const angle = -90 + (score / 100) * 180;
    dom.gaugeNeedle.style.transform = `rotate(${angle}deg)`;
    dom.gaugeScore.textContent = Math.round(score);

    let verdict = '---';
    let cls = '';
    if (score >= 91) {
      verdict = 'EXCELENTE';
      cls = 'verdict-excellent';
    } else if (score >= 70) {
      verdict = 'APROVADO';
      cls = 'verdict-approved';
    } else if (score > 0) {
      verdict = 'REPROVADO';
      cls = 'verdict-failed';
    }

    dom.verdictText.textContent = verdict;
    dom.verdictText.className = 'verdict-text' + (cls ? ' ' + cls : '');
  }

  // ---------------------------------------------------------------
  // Domain Score Bars
  // ---------------------------------------------------------------

  function updateDomainBars(scores) {
    state.domainScores = scores || {};
    const domains = Object.entries(state.domainScores);

    if (domains.length === 0) {
      dom.domainBars.innerHTML =
        '<div style="color:var(--text-muted);font-size:0.8rem;text-align:center;padding:12px 0;">No domain scores yet</div>';
      return;
    }

    dom.domainBars.innerHTML = domains
      .map(([name, score]) => {
        const pct = Math.max(0, Math.min(100, score));
        const color = scoreColor(pct);
        return `
        <div class="domain-bar-item">
          <div class="domain-bar-header">
            <span class="domain-bar-name">${escapeHtml(name)}</span>
            <span class="domain-bar-score">${pct.toFixed(1)}</span>
          </div>
          <div class="domain-bar-track">
            <div class="domain-bar-fill" style="width:${pct}%;background:${color}"></div>
          </div>
        </div>`;
      })
      .join('');

    dom.statDomains.textContent = domains.length;
    updateDoughnutChart();
  }

  // ---------------------------------------------------------------
  // Doughnut Chart (Chart.js)
  // ---------------------------------------------------------------

  function initDoughnutChart() {
    if (typeof Chart === 'undefined') {
      console.warn('[Serenity] Chart.js not loaded — skipping doughnut chart');
      return;
    }
    const ctx = dom.domainChartCanvas.getContext('2d');
    state.domainChart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: [],
        datasets: [
          {
            data: [],
            backgroundColor: [],
            borderColor: 'rgba(255,255,255,0.8)',
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        cutout: '60%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              font: { family: 'Inter', size: 10 },
              color: '#5A5A5A',
              padding: 8,
              usePointStyle: true,
              pointStyleWidth: 8,
            },
          },
          tooltip: {
            titleFont: { family: 'Inter' },
            bodyFont: { family: 'Inter' },
            callbacks: {
              label: function (ctx) {
                return ' ' + ctx.label + ': ' + ctx.parsed.toFixed(1);
              },
            },
          },
        },
      },
    });
  }

  function updateDoughnutChart() {
    if (!state.domainChart) return;
    const entries = Object.entries(state.domainScores);
    const chartColors = [
      '#1A3A5C', '#2E6B9E', '#5B9BD5', '#C9A84C', '#E8D48B',
      '#27AE60', '#E67E22', '#C0392B', '#8E44AD', '#2C3E50',
    ];

    state.domainChart.data.labels = entries.map(
      ([n]) => n.charAt(0).toUpperCase() + n.slice(1)
    );
    state.domainChart.data.datasets[0].data = entries.map(([, v]) => v);
    state.domainChart.data.datasets[0].backgroundColor = entries.map(
      (_, i) => chartColors[i % chartColors.length]
    );
    state.domainChart.update('none');
  }

  // ---------------------------------------------------------------
  // Progress Bar
  // ---------------------------------------------------------------

  function updateProgress(current, total, eta) {
    state.pagesAnalyzed = current || 0;
    state.totalPages = total || 0;

    const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
    dom.progressPages.textContent = `${current} / ${total} pages`;
    dom.progressPercent.textContent = `${pct.toFixed(0)}%`;
    dom.progressFill.style.width = pct + '%';

    if (eta && eta > 0) {
      dom.progressEta.textContent = `ETA: ${formatTime(eta)}`;
    } else if (pct >= 100) {
      dom.progressEta.textContent = 'Complete';
    } else {
      dom.progressEta.textContent = '';
    }

    dom.statPages.textContent = current;
  }

  // ---------------------------------------------------------------
  // Findings Log
  // ---------------------------------------------------------------

  function addFinding(finding) {
    if (state.findings.some((f) => f.id === finding.id)) return;
    state.findings.unshift(finding);

    const sev = (finding.severity || 'low').toLowerCase();
    if (state.severityCounts.hasOwnProperty(sev)) {
      state.severityCounts[sev]++;
    }

    updateSeverityDisplay();
    renderFindings();
  }

  function updateSeverityDisplay() {
    dom.sevCritical.textContent = state.severityCounts.critical;
    dom.sevHigh.textContent = state.severityCounts.high;
    dom.sevMedium.textContent = state.severityCounts.medium;
    dom.sevLow.textContent = state.severityCounts.low;
    dom.statFindings.textContent = state.findings.length;
    dom.statCritical.textContent = state.severityCounts.critical;
    dom.findingsCount.textContent = `${state.findings.length} issue${state.findings.length !== 1 ? 's' : ''} found`;
  }

  function renderFindings() {
    const filtered =
      state.activeFilter === 'all'
        ? state.findings
        : state.findings.filter(
            (f) => (f.severity || '').toLowerCase() === state.activeFilter
          );

    if (filtered.length === 0) {
      dom.findingsList.innerHTML = `
        <div style="text-align:center;color:var(--text-muted);padding:40px 0;font-size:0.85rem;">
          ${state.findings.length === 0 ? 'Waiting for findings...' : 'No findings match this filter'}
        </div>`;
      return;
    }

    dom.findingsList.innerHTML = filtered
      .map((f) => {
        const sev = (f.severity || 'low').toLowerCase();
        return `
        <div class="finding-entry severity-${sev}">
          <span class="finding-time">${formatTimestamp(f.timestamp)}</span>
          <span class="finding-badge badge-${sev}">${sev}</span>
          <span class="finding-domain">${escapeHtml(f.domain || '?')}</span>
          <span class="finding-title">${escapeHtml(f.title || 'Untitled finding')}</span>
        </div>`;
      })
      .join('');
  }

  // ---------------------------------------------------------------
  // Page Heatmap
  // ---------------------------------------------------------------

  function updateHeatmap(statuses) {
    state.pageStatuses = { ...state.pageStatuses, ...statuses };
    const entries = Object.entries(state.pageStatuses);

    dom.heatmapGrid.innerHTML = entries
      .map(([url, status]) => {
        const s = (status || 'pending').toLowerCase();
        const cls = 'heatmap-cell status-' + s;
        return `<div class="${cls}" title="${escapeHtml(url)}"><span class="tooltip">${escapeHtml(shortUrl(url))}</span></div>`;
      })
      .join('');
  }

  function setPageStatus(url, status) {
    state.pageStatuses[url] = status;
    updateHeatmap({});
  }

  // ---------------------------------------------------------------
  // Alert Banner
  // ---------------------------------------------------------------

  window.hideAlert = function () {
    dom.alertBanner.classList.remove('visible');
    if (state.alertTimeout) {
      clearTimeout(state.alertTimeout);
      state.alertTimeout = null;
    }
  };

  function showAlert(title, description) {
    dom.alertTitle.textContent = title || 'Critical Alert';
    dom.alertDesc.textContent = description || '';
    dom.alertBanner.classList.add('visible');

    if (state.alertTimeout) clearTimeout(state.alertTimeout);
    state.alertTimeout = setTimeout(() => {
      dom.alertBanner.classList.remove('visible');
    }, 10000);
  }

  // ---------------------------------------------------------------
  // Filter Buttons
  // ---------------------------------------------------------------

  window.setFilter = function (filter, btn) {
    state.activeFilter = filter;
    $$('.filter-btn').forEach((b) => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderFindings();
  };

  // ---------------------------------------------------------------
  // Elapsed Timer
  // ---------------------------------------------------------------

  function startTimer() {
    if (state.timerInterval) return;
    state.startTime = state.startTime || Date.now();
    state.timerInterval = setInterval(() => {
      const elapsed = (Date.now() - state.startTime) / 1000;
      dom.elapsedTime.textContent = formatTime(elapsed);
    }, 1000);
  }

  function stopTimer() {
    if (state.timerInterval) {
      clearInterval(state.timerInterval);
      state.timerInterval = null;
    }
  }

  // ---------------------------------------------------------------
  // Message Handlers
  // ---------------------------------------------------------------

  const handlers = {
    'state.snapshot': (payload) => {
      if (payload.target_url) dom.targetUrl.textContent = payload.target_url;
      if (payload.overall_score != null) updateGauge(payload.overall_score);
      if (payload.domain_scores) updateDomainBars(payload.domain_scores);
      if (payload.page_statuses) updateHeatmap(payload.page_statuses);
      if (payload.findings && payload.findings.length) {
        payload.findings.forEach((f) => addFinding(f));
      }
      if (payload.pages_analyzed != null) {
        updateProgress(
          payload.pages_analyzed,
          payload.max_pages || payload.discovered_count || 0,
          null
        );
      }
      startTimer();
    },

    'scan.started': (payload) => {
      const url = payload.target_url || payload.url || payload.data;
      if (url) dom.targetUrl.textContent = url;
      dom.scanStatus.textContent = 'Scanning';
      startTimer();
    },

    'scan.progress': (payload) => {
      const current = payload.pages_analyzed || payload.current || payload.analyzed || 0;
      const total = payload.total_pages || payload.total || payload.max_pages || 0;
      const eta = payload.eta || payload.eta_seconds || null;
      updateProgress(current, total, eta);
      dom.scanStatus.textContent = 'Scanning';
    },

    'finding.new': (payload) => {
      addFinding(payload);
    },

    'score.update': (payload) => {
      if (payload.overall_score != null || payload.overall != null) {
        updateGauge(payload.overall_score || payload.overall || 0);
      }
      if (payload.domain_scores || payload.domains) {
        updateDomainBars(payload.domain_scores || payload.domains);
      }
    },

    'page.heatmap': (payload) => {
      if (payload.statuses) updateHeatmap(payload.statuses);
      else if (payload.url && payload.status) {
        setPageStatus(payload.url, payload.status);
      }
    },

    'alert.critical': (payload) => {
      const title = payload.title || payload.message || 'Critical Issue Detected';
      const desc = payload.description || payload.detail || payload.details || '';
      showAlert(title, desc);
    },

    'scan.completed': (payload) => {
      dom.scanStatus.textContent = 'Complete';
      state.scanComplete = true;
      stopTimer();
      dom.scanCompleteBadge.classList.add('visible');

      if (payload.overall_score != null) updateGauge(payload.overall_score);
      if (payload.domain_scores) updateDomainBars(payload.domain_scores);

      dom.progressFill.style.animation = 'none';
      dom.progressFill.style.background = 'var(--gold)';
    },

    'page.analyzing': (payload) => {
      const url = payload.url || payload.data;
      if (url) setPageStatus(url, 'analyzing');
    },

    'page.done': (payload) => {
      const url = payload.url || payload.data;
      const score = payload.score || payload.page_score;
      if (url) {
        const status = score != null && score < 50 ? 'failed' : 'passed';
        setPageStatus(url, status);
      }
    },

    pong: () => {
      // Keep-alive acknowledgement — no action needed
    },
  };

  /**
   * Parse and dispatch an incoming WebSocket message.
   */
  function handleMessage(data) {
    try {
      const msg = JSON.parse(data);
      const handler = handlers[msg.type];
      if (handler) {
        handler(msg.payload || {});
      } else {
        console.log('[Serenity] Unknown message type:', msg.type);
      }
    } catch (e) {
      console.error('[Serenity] Failed to parse message:', e);
    }
  }

  // ---------------------------------------------------------------
  // WebSocket Client
  // ---------------------------------------------------------------

  function connectWebSocket() {
    const wsUrl = `ws://${window.location.host}/ws`;
    dom.connectionOverlay.classList.add('visible');

    try {
      state.ws = new WebSocket(wsUrl);
    } catch (e) {
      console.error('[Serenity] WebSocket creation failed:', e);
      scheduleReconnect();
      return;
    }

    state.ws.onopen = () => {
      state.connected = true;
      state.reconnectAttempts = 0;
      dom.wsIndicator.classList.remove('disconnected');
      dom.connectionOverlay.classList.remove('visible');
      console.log('[Serenity] WebSocket connected');

      // Keep-alive ping every 30 seconds
      state.pingInterval = setInterval(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          state.ws.send('ping');
        }
      }, 30000);
    };

    state.ws.onmessage = (event) => {
      handleMessage(event.data);
    };

    state.ws.onclose = (event) => {
      state.connected = false;
      dom.wsIndicator.classList.add('disconnected');
      if (state.pingInterval) {
        clearInterval(state.pingInterval);
        state.pingInterval = null;
      }
      console.log('[Serenity] WebSocket closed:', event.code, event.reason);

      if (!state.scanComplete) {
        scheduleReconnect();
      }
    };

    state.ws.onerror = (error) => {
      console.error('[Serenity] WebSocket error:', error);
    };
  }

  /**
   * Schedule a reconnection attempt with exponential backoff.
   */
  function scheduleReconnect() {
    if (state.reconnectAttempts >= state.maxReconnectAttempts) {
      dom.connectionOverlay.classList.add('visible');
      const card = dom.connectionOverlay.querySelector('.connection-card');
      if (card) {
        card.innerHTML = `
          <h3 style="color:var(--red)">Connection Lost</h3>
          <p>Could not reconnect to the dashboard server.</p>
          <button onclick="location.reload()" style="
            margin-top:16px;padding:8px 24px;background:var(--sea-deep);
            color:white;border:none;border-radius:6px;cursor:pointer;
            font-family:var(--font-body);font-size:0.85rem;">
            Retry
          </button>`;
      }
      return;
    }

    state.reconnectAttempts++;
    const delay = Math.min(
      state.reconnectDelay * Math.pow(1.5, state.reconnectAttempts - 1),
      10000
    );
    console.log(
      `[Serenity] Reconnecting in ${Math.round(delay)}ms (attempt ${state.reconnectAttempts})`
    );
    setTimeout(connectWebSocket, delay);
  }

  // ---------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------

  function init() {
    initDoughnutChart();
    updateGauge(0);
    connectWebSocket();

    // Fetch initial state snapshot via REST as fallback
    fetch('/api/state')
      .then((r) => r.json())
      .then((data) => {
        if (data.error) return;
        if (data.target_url) dom.targetUrl.textContent = data.target_url;
        if (data.overall_score) updateGauge(data.overall_score);
        if (data.domain_scores) updateDomainBars(data.domain_scores);
        if (data.findings) data.findings.forEach((f) => addFinding(f));
        if (data.pages_analyzed != null) {
          updateProgress(data.pages_analyzed, data.max_pages || 0, null);
        }
        // Build heatmap from URL sets
        const statuses = {};
        (data.discovered_urls || []).forEach((u) => (statuses[u] = 'pending'));
        (data.analyzed_urls || []).forEach((u) => (statuses[u] = 'passed'));
        (data.failed_urls || []).forEach((u) => (statuses[u] = 'failed'));
        if (Object.keys(statuses).length) updateHeatmap(statuses);
      })
      .catch(() => {
        /* Server may not be ready yet */
      });
  }

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
