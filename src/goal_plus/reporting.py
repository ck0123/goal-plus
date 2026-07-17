from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from math import isclose, isfinite
from pathlib import Path
from typing import Any

from goal_plus.agent_hosts import get_agent_host_adapter
from goal_plus.goal_plus import FileGoalPlusRuntime
from goal_plus.models import (
    AgentSessionRecord,
    CandidateRecord,
    FrozenSpec,
    GoalPlusRecord,
    RunRecord,
    SearchPlan,
)
from goal_plus.monitor import goal_plus_monitor_snapshot
from goal_plus.runtime import load_json


REPORT_SCHEMA_VERSION = 1


_REPORT_CSS = """
:root {
  color-scheme: light;
  --page: #f4f6f8;
  --surface: #ffffff;
  --surface-subtle: #f8fafb;
  --text: #17212b;
  --muted: #5b6977;
  --border: #dce2e8;
  --border-strong: #b8c2cc;
  --accent: #176b87;
  --accent-soft: #e7f2f5;
  --success: #18794e;
  --success-soft: #e8f5ee;
  --warning: #a15c00;
  --warning-soft: #fff3d6;
  --failure: #b42318;
  --failure-soft: #fdecea;
  --worker: #6b5aa6;
  --parent: #b35c00;
  --metric-1: #dceff2;
  --metric-2: #9bcdd5;
  --metric-3: #4f9fb0;
  --metric-4: #176b87;
  --radius: 8px;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--page);
  color: var(--text);
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: 0;
}
button, input, select { font: inherit; }
button { letter-spacing: 0; }
a { color: var(--accent); }
code, pre, .mono, .metric-value, .timeline-time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}
.wrap { width: min(1440px, 100%); margin: 0 auto; padding: 0 24px; }
.masthead {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.masthead-inner {
  min-height: 82px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.identity { min-width: 0; }
.eyebrow {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: 4px; font-size: 28px; line-height: 36px; }
h2 { margin-bottom: 20px; font-size: 20px; line-height: 28px; }
h3 { margin-bottom: 12px; font-size: 14px; line-height: 20px; }
.identity-line { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.id-line { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.masthead-actions { display: flex; align-items: center; gap: 18px; flex: 0 0 auto; }
.generated { color: var(--muted); font-size: 11px; text-align: right; }
.generated strong { display: block; color: var(--text); font-size: 12px; }
.button {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 7px 12px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-weight: 650;
}
.button:hover { background: var(--surface-subtle); }
.button svg { width: 16px; height: 16px; }
.status {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-subtle);
  color: var(--muted);
  font-size: 11px;
  font-weight: 750;
  text-transform: uppercase;
  white-space: nowrap;
}
.status.success { color: var(--success); border-color: #b8dfca; background: var(--success-soft); }
.status.warning { color: var(--warning); border-color: #ead298; background: var(--warning-soft); }
.status.failure { color: var(--failure); border-color: #efbbb6; background: var(--failure-soft); }
.section-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.97);
  border-bottom: 1px solid var(--border);
}
.section-nav .wrap { display: flex; gap: 26px; overflow-x: auto; }
.section-nav a {
  padding: 13px 0 11px;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  font-size: 13px;
  font-weight: 650;
  text-decoration: none;
  white-space: nowrap;
}
.section-nav a:hover { color: var(--accent); border-color: var(--accent); }
main { padding-top: 30px; padding-bottom: 72px; }
.report-section { padding: 0 0 40px; margin: 0 0 40px; border-bottom: 1px solid var(--border); }
.section-kicker { margin-bottom: 14px; color: var(--muted); font-size: 11px; font-weight: 750; text-transform: uppercase; }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--border);
}
.kpi { min-width: 0; min-height: 105px; padding: 16px; background: var(--surface); }
.kpi-label { color: var(--muted); font-size: 11px; font-weight: 650; }
.metric-value { margin: 8px 0 2px; font-size: 22px; line-height: 28px; font-weight: 750; overflow-wrap: anywhere; }
.metric-value.success { color: var(--success); }
.metric-value.warning { color: var(--warning); }
.kpi-detail { color: var(--muted); font-size: 11px; }
.two-column { display: grid; grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr); gap: 24px; }
.panel { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.panel-body { padding: 20px; }
.panel + .panel { margin-top: 16px; }
.objective { margin-bottom: 0; color: var(--text); font-size: 15px; line-height: 24px; overflow-wrap: anywhere; }
.raw-goal { color: var(--muted); white-space: pre-wrap; overflow-wrap: anywhere; }
.fact-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.fact { min-width: 0; padding-top: 12px; border-top: 1px solid var(--border); }
.fact dt { color: var(--muted); font-size: 11px; font-weight: 650; }
.fact dd { margin: 4px 0 0; font-weight: 650; overflow-wrap: anywhere; }
.completion-note { border-left: 3px solid var(--success); }
.completion-note p { margin-bottom: 0; color: var(--muted); }
.metric-gap-list { margin: 0; padding: 0; list-style: none; }
.metric-gap-list li { display: grid; grid-template-columns: minmax(190px, 0.8fr) 110px minmax(260px, 2fr); gap: 14px; padding: 9px 0; border-top: 1px solid var(--border); }
.metric-gap-list li:first-child { border-top: 0; }
.metric-gap-list code { color: var(--text); overflow-wrap: anywhere; }
.metric-gap-kind { color: var(--muted); font-size: 10px; font-weight: 750; text-transform: uppercase; }
.metric-gap-reason { color: var(--muted); font-size: 12px; }
.coverage { margin-top: 16px; }
.coverage-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 12px; }
.coverage-bar { height: 5px; margin-top: 7px; overflow: hidden; border-radius: 3px; background: var(--border); }
.coverage-bar > span { display: block; height: 100%; background: var(--accent); }
.timeline-shell { overflow: hidden; }
.timeline-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.timeline-head h2 { margin: 0; }
.metric-lens-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-subtle);
}
.metric-scale { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 10px; }
.metric-scale-bar { display: grid; grid-template-columns: repeat(4, 18px); height: 10px; overflow: hidden; border: 1px solid var(--border-strong); border-radius: 3px; }
.metric-scale-bar i:nth-child(1) { background: var(--metric-1); }
.metric-scale-bar i:nth-child(2) { background: var(--metric-2); }
.metric-scale-bar i:nth-child(3) { background: var(--metric-3); }
.metric-scale-bar i:nth-child(4) { background: var(--metric-4); }
.metric-control { display: inline-flex; overflow-x: auto; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface); }
.metric-control button {
  min-height: 30px;
  padding: 5px 9px;
  border: 0;
  border-right: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}
.metric-control button:last-child { border-right: 0; }
.metric-control button:hover { background: var(--accent-soft); color: var(--accent); }
.metric-control button[aria-pressed="true"] { background: var(--accent); color: #fff; }
.timeline-scroll { overflow-x: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.timeline { width: var(--timeline-width, 980px); min-width: 980px; }
.score-row { display: grid; grid-template-columns: 190px 1fr; min-height: 64px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); }
.score-label { position: sticky; left: 0; z-index: 5; padding: 11px 14px; background: var(--surface-subtle); box-shadow: 1px 0 0 var(--border); }
.score-label strong, .score-label span { display: block; }
.score-label strong { font-size: 11px; }
.score-label span { margin-top: 2px; color: var(--muted); font-size: 9px; }
.score-track { position: relative; min-height: 64px; overflow: hidden; background: var(--surface); }
.score-track svg { display: block; width: 100%; height: 64px; }
.score-reference { stroke: var(--border-strong); stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
.score-step { fill: none; stroke: var(--success); stroke-width: 2; vector-effect: non-scaling-stroke; }
.score-point { fill: var(--surface); stroke: var(--success); stroke-width: 2; vector-effect: non-scaling-stroke; }
.score-ref-label { position: absolute; left: 8px; z-index: 2; padding: 0 3px; background: var(--surface); color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 9px; line-height: 12px; white-space: nowrap; }
.timeline-rows { max-height: min(62vh, 680px); overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.timeline-row { display: grid; grid-template-columns: 190px 1fr; min-height: 48px; border-bottom: 1px solid var(--border); }
.timeline-row:first-child { position: sticky; top: 0; z-index: 4; }
.timeline-label { position: sticky; left: 0; z-index: 3; padding: 13px 14px; background: var(--surface-subtle); box-shadow: 1px 0 0 var(--border); color: var(--muted); font-size: 10px; font-weight: 750; text-transform: uppercase; overflow-wrap: anywhere; }
.timeline-label strong, .timeline-label small { display: block; }
.timeline-label small { margin-top: 2px; color: var(--muted); font-size: 9px; font-weight: 600; line-height: 12px; text-transform: none; }
.timeline-row.redispatched .timeline-label { box-shadow: inset 3px 0 0 var(--accent), 1px 0 0 var(--border); }
.timeline-track { position: relative; min-height: 48px; border-left: 1px solid var(--border); background: var(--surface); }
.timeline-track::before, .timeline-track::after { content: ""; position: absolute; inset: 0 auto 0 33.333%; border-left: 1px solid var(--border); }
.timeline-track::after { left: 66.666%; }
.timeline-event {
  position: absolute;
  top: 12px;
  min-width: 8px;
  height: 24px;
  padding: 4px 7px;
  overflow: hidden;
  border-radius: 4px;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
  z-index: 2;
}
.timeline-event.main { background: var(--accent); }
.timeline-event.worker { background: var(--worker); }
.timeline-event.parent { background: var(--parent); }
.timeline-event.success { background: var(--success); }
.timeline-event.failure { background: var(--failure); }
.timeline-event.worker-session { display: flex; align-items: center; gap: 5px; border: 1px solid transparent; }
.timeline-event.worker-session.metric-level-1 { background: var(--metric-1); color: #17434b; }
.timeline-event.worker-session.metric-level-2 { background: var(--metric-2); color: #143e46; }
.timeline-event.worker-session.metric-level-3 { background: var(--metric-3); color: #fff; }
.timeline-event.worker-session.metric-level-4 { background: var(--metric-4); color: #fff; }
.timeline-shell[data-metric-mode="status"] .timeline-event.worker-session { background: var(--worker); color: #fff; }
.timeline-event.worker-session.session-failure { border: 2px solid var(--failure); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.65); }
.session-state-icon { width: 13px; height: 13px; flex: 0 0 auto; color: var(--failure); }
.timeline-shell[data-metric-mode="status"] .session-state-icon { color: #fff; }
.metric-readout { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.retry-badge { display: inline-block; margin-left: 5px; padding: 0 4px; border: 1px solid var(--border-strong); border-radius: 3px; color: var(--accent); font-size: 8px; line-height: 13px; }
.timeline-idle { position: absolute; inset: 0 auto 0 0; z-index: 1; border-right: 1px dashed var(--border-strong); border-left: 1px dashed var(--border-strong); background: #f0f2f4; }
.timeline-idle-label { position: absolute; inset: 50% auto auto 50%; transform: translate(-50%, -50%); color: var(--muted); font-size: 9px; font-weight: 700; white-space: nowrap; }
.timeline-event.point { top: 4px; width: 10px !important; min-width: 10px; height: 10px; padding: 0; border: 2px solid var(--surface); border-radius: 50%; }
.timeline-axis { display: flex; justify-content: space-between; padding: 8px 10px 9px 200px; color: var(--muted); font-size: 10px; }
.timeline-key { display: flex; flex-wrap: wrap; gap: 14px; padding: 12px 20px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; }
.key-dot { display: inline-block; width: 9px; height: 9px; margin-right: 5px; border-radius: 50%; background: var(--accent); }
.key-dot.worker { background: var(--worker); }
.key-dot.parent { background: var(--parent); }
.event-log { margin-top: 16px; }
.event-list { margin: 0; padding: 0; list-style: none; }
.event-list li { display: grid; grid-template-columns: 155px 90px 1fr; gap: 12px; padding: 8px 0; border-top: 1px solid var(--border); }
.lane { color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase; }
.task-tabs { display: flex; gap: 6px; margin-bottom: 18px; overflow-x: auto; }
.task-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
}
.task-tab[aria-selected="true"] { color: var(--accent); border-color: var(--accent); box-shadow: inset 0 -2px 0 var(--accent); }
.task-panel { margin-bottom: 26px; }
.js .task-panel[hidden] { display: none; }
.task-head { padding: 20px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); }
.task-title-line { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.task-title-line h3 { margin: 0; font-size: 16px; }
.task-objective { max-width: 960px; margin: 10px 0 0; color: var(--muted); overflow-wrap: anywhere; }
.task-metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; background: var(--border); border-bottom: 1px solid var(--border); }
.task-metric { min-height: 78px; padding: 12px 16px; background: var(--surface); }
.task-metric strong { display: block; margin-top: 4px; font-size: 16px; overflow-wrap: anywhere; }
.subsection { padding: 20px; border-top: 1px solid var(--border); }
.subsection:first-child { border-top: 0; }
details.summary-block > summary { cursor: pointer; list-style: none; }
details.summary-block > summary::-webkit-details-marker { display: none; }
.table-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }
table { width: 100%; border-collapse: collapse; background: var(--surface); font-size: 12px; }
th { background: var(--surface-subtle); color: var(--muted); font-size: 10px; text-align: left; text-transform: uppercase; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; overflow-wrap: anywhere; }
tbody tr:last-child td { border-bottom: 0; }
.selected-row td:first-child { box-shadow: inset 3px 0 0 var(--success); }
.stats-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.stats-table { border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.stats-table h3 { margin: 0; padding: 11px 12px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); }
.stat-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 8px 12px; border-top: 1px solid var(--border); font-size: 11px; }
.stat-row:first-of-type { border-top: 0; }
.stat-row span:first-child { color: var(--muted); overflow-wrap: anywhere; }
.stat-row strong { text-align: right; overflow-wrap: anywhere; }
details.summary-block { margin-top: 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }
details.summary-block > summary { padding: 11px 13px; color: var(--accent); font-weight: 700; }
details.summary-block > div, details.summary-block > pre { margin: 0; padding: 14px; border-top: 1px solid var(--border); }
pre { max-height: 600px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; }
.warning-list { margin: 0; padding-left: 18px; }
.warning-list li + li { margin-top: 6px; }
.footnote { margin: 18px 0 0; color: var(--muted); font-size: 11px; }
@media (max-width: 1100px) {
  .kpi-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .two-column { grid-template-columns: 1fr; }
  .task-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .fact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .wrap { padding: 0 14px; }
  .masthead-inner { align-items: flex-start; flex-direction: column; padding: 15px 0; }
  .masthead-actions { width: 100%; justify-content: space-between; }
  .generated { text-align: left; }
  h1 { font-size: 24px; line-height: 31px; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .task-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stats-grid { grid-template-columns: 1fr; }
  .event-list li { grid-template-columns: 1fr; gap: 2px; }
  .metric-gap-list li { grid-template-columns: 1fr; gap: 2px; }
  .timeline-head, .metric-lens-toolbar { align-items: flex-start; flex-direction: column; }
  .metric-control { width: 100%; }
  .metric-control button { min-width: 0; flex: 1 1 0; padding-right: 4px; padding-left: 4px; font-size: 10px; }
}
@media (max-width: 480px) {
  .kpi-grid, .task-metrics, .fact-grid { grid-template-columns: 1fr; }
  .kpi { min-height: auto; }
  .masthead-actions { align-items: flex-start; flex-direction: column; }
  .button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
@media print {
  @page { margin: 12mm; }
  body { background: #fff; font-size: 11px; }
  .no-print, .section-nav, .task-tabs { display: none !important; }
  .wrap { width: 100%; max-width: none; padding: 0; }
  .masthead-inner { min-height: auto; padding: 0 0 12px; }
  main { padding: 16px 0 0; }
  .report-section { margin-bottom: 22px; padding-bottom: 22px; }
  .js .task-panel[hidden], .task-panel { display: block !important; }
  .panel, .table-scroll, .stats-table, .timeline-shell { break-inside: avoid; }
  details > * { display: block !important; }
  .timeline-scroll, .timeline-rows { max-height: none; overflow: visible; }
  .timeline { width: 100% !important; min-width: 0; }
  .timeline-row:first-child, .timeline-label { position: static; }
  .metric-lens-toolbar { display: none; }
  .kpi-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .kpi { min-height: 70px; padding: 10px; }
  .metric-value { font-size: 16px; }
}
"""


_REPORT_SCRIPT = """
(function () {
  var buttons = Array.prototype.slice.call(document.querySelectorAll('[data-task-target]'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.task-panel'));
  function activate(runId, updateHash) {
    buttons.forEach(function (button) {
      button.setAttribute('aria-selected', button.dataset.taskTarget === runId ? 'true' : 'false');
    });
    panels.forEach(function (panel) { panel.hidden = panel.dataset.runId !== runId; });
    if (updateHash && history.replaceState) history.replaceState(null, '', '#task-' + runId);
  }
  buttons.forEach(function (button) {
    button.addEventListener('click', function () { activate(button.dataset.taskTarget, true); });
  });
  if (buttons.length) {
    var requested = location.hash.indexOf('#task-') === 0 ? location.hash.slice(6) : null;
    var initial = buttons.some(function (button) { return button.dataset.taskTarget === requested; })
      ? requested : buttons.find(function (button) { return button.getAttribute('aria-selected') === 'true'; }).dataset.taskTarget;
    activate(initial, false);
  }

  var metricFormatters = {
    'score-gain': function (value) { return (value >= 0 ? '+' : '') + value.toFixed(4); },
    'tokens-per-minute': function (value) { return Math.round(value).toLocaleString() + '/min'; },
    'cost-per-minute': function (value) { return '$' + value.toFixed(4) + '/min'; },
    'verifier-density': function (value) { return value.toFixed(1) + '/min'; }
  };
  Array.prototype.forEach.call(document.querySelectorAll('[data-metric-lens]'), function (lens) {
    var metricButtons = Array.prototype.slice.call(lens.querySelectorAll('[data-metric-mode]'));
    var workerEvents = Array.prototype.slice.call(lens.querySelectorAll('.worker-session'));
    var lowLabel = lens.querySelector('[data-metric-low]');
    var highLabel = lens.querySelector('[data-metric-high]');
    var scaleBar = lens.querySelector('.metric-scale-bar');

    function setMode(mode) {
      var values = workerEvents.map(function (event) {
        var raw = event.getAttribute('data-metric-' + mode);
        return raw === null || raw === '' ? NaN : Number(raw);
      }).filter(Number.isFinite);
      var low = values.length ? Math.min.apply(Math, values) : null;
      var high = values.length ? Math.max.apply(Math, values) : null;
      lens.dataset.metricMode = mode;
      metricButtons.forEach(function (button) {
        button.setAttribute('aria-pressed', button.dataset.metricMode === mode ? 'true' : 'false');
      });
      workerEvents.forEach(function (event) {
        event.classList.remove('metric-level-1', 'metric-level-2', 'metric-level-3', 'metric-level-4');
        var readout = event.querySelector('.metric-readout');
        if (mode === 'status') {
          if (readout) readout.textContent = event.dataset.terminalState || 'unknown';
          return;
        }
        var raw = event.getAttribute('data-metric-' + mode);
        var value = raw === null || raw === '' ? NaN : Number(raw);
        if (!Number.isFinite(value)) {
          if (readout) readout.textContent = 'Not observed';
          return;
        }
        var ratio = high === low ? 0.5 : (value - low) / (high - low);
        var level = Math.min(4, Math.floor(ratio * 4) + 1);
        event.classList.add('metric-level-' + level);
        if (readout) readout.textContent = metricFormatters[mode](value);
      });
      if (mode === 'status') {
        if (lowLabel) lowLabel.textContent = 'Completed';
        if (highLabel) highLabel.textContent = 'Timed out';
        if (scaleBar) scaleBar.hidden = true;
      } else {
        if (lowLabel) lowLabel.textContent = low === null ? 'Not observed' : metricFormatters[mode](low);
        if (highLabel) highLabel.textContent = high === null ? 'Not observed' : metricFormatters[mode](high);
        if (scaleBar) scaleBar.hidden = false;
      }
    }

    metricButtons.forEach(function (button) {
      button.addEventListener('click', function () { setMode(button.dataset.metricMode); });
    });
    setMode(lens.dataset.metricMode || 'tokens-per-minute');
  });
})();
"""


def _text(value: Any) -> str:
    if value is None or value == "":
        return "Not observed"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _html(value: Any) -> str:
    return escape(_text(value), quote=True)


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _timestamp(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _duration(value: Any) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "Not observed"
    seconds = float(value)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m"


def _number(value: Any, *, digits: int = 2) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "Not observed"
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{float(value):,.{digits}f}".rstrip("0").rstrip(".")


def _percent(value: Any) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "Not observed"
    return f"{float(value) * 100:.1f}%"


def _cost(value: Any) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "Not observed"
    return f"${float(value):,.6f}".rstrip("0").rstrip(".")


def _status_class(value: Any) -> str:
    normalized = str(value or "").lower()
    if normalized in {"complete", "completed", "promoted", "passed", "evaluated", "success"}:
        return "success"
    if normalized in {"failed", "failure", "aborted", "blocked", "timed_out", "timeout"}:
        return "failure"
    if normalized in {"active", "running", "waiting_for_workers", "ready_to_promote", "planned", "started"}:
        return "warning"
    return ""


def _status(value: Any) -> str:
    return f'<span class="status {_status_class(value)}">{_html(value)}</span>'


def _finite_float(value: Any) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _per_minute(value: Any, duration_seconds: Any) -> float | None:
    amount = _finite_float(value)
    duration = _finite_float(duration_seconds)
    if amount is None or duration is None or duration <= 0:
        return None
    return amount / duration * 60.0


def _is_better_score(value: float, current: float, direction: str) -> bool:
    if isclose(value, current, rel_tol=1e-9, abs_tol=1e-12):
        return False
    return value < current if direction == "minimize" else value > current


def _session_scores(task: dict[str, Any], direction: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for candidate in task.get("candidates", []):
        for iteration in candidate.get("iterations", []):
            session_id = iteration.get("agent_session_id")
            score = _finite_float(iteration.get("score"))
            if not isinstance(session_id, str) or score is None:
                continue
            current = scores.get(session_id)
            if current is None or _is_better_score(score, current, direction):
                scores[session_id] = score
    return scores


def _timeline_performance(task: dict[str, Any], timeline: dict[str, Any]) -> dict[str, Any]:
    start_epoch = _epoch(timeline.get("start_at"))
    duration = _finite_float(timeline.get("duration_seconds"))
    if start_epoch is None or duration is None:
        return {}

    statistics = task.get("statistics") or {}
    scores = statistics.get("scores") or {}
    direction = str(scores.get("direction") or (task.get("frozen_spec") or {}).get("metric_direction") or "maximize")
    baseline = _finite_float(scores.get("baseline"))
    selected = _finite_float(scores.get("selected"))
    checkpoints: list[dict[str, Any]] = []
    for candidate in task.get("candidates", []):
        for iteration in candidate.get("iterations", []):
            score = _finite_float(iteration.get("score"))
            created_epoch = _epoch(iteration.get("created_at"))
            if score is None or created_epoch is None:
                continue
            checkpoints.append(
                {
                    "at": iteration.get("created_at"),
                    "epoch": created_epoch,
                    "score": score,
                    "candidate_id": candidate.get("candidate_id"),
                    "session_id": iteration.get("agent_session_id"),
                }
            )
    checkpoints.sort(key=lambda item: item["epoch"])
    current = baseline
    best_points: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        score = checkpoint["score"]
        if current is not None and not _is_better_score(score, current, direction):
            continue
        current = score
        best_points.append({key: value for key, value in checkpoint.items() if key != "epoch"})

    worker_events = [
        event for event in timeline.get("events", []) if event.get("kind") == "worker_session"
    ]
    metric_keys = (
        "score_gain",
        "tokens_per_minute",
        "cost_per_minute",
        "verifier_density",
    )
    metric_ranges: dict[str, dict[str, float | int]] = {}
    for key in metric_keys:
        values = [
            value
            for event in worker_events
            if (value := _finite_float(event.get(key))) is not None
        ]
        if values:
            metric_ranges[key] = {
                "min": min(values),
                "max": max(values),
                "observed": len(values),
            }

    spans = sorted(
        (start, end)
        for event in worker_events
        if (start := _epoch(event.get("start_at"))) is not None
        and (end := _epoch(event.get("end_at"))) is not None
        and end > start
    )
    merged: list[list[float]] = []
    for span_start, span_end in spans:
        if not merged or span_start > merged[-1][1]:
            merged.append([span_start, span_end])
        else:
            merged[-1][1] = max(merged[-1][1], span_end)
    idle_threshold = max(60.0, duration * 0.05)
    idle_intervals = [
        {
            "start_at": _timestamp(previous[1]),
            "end_at": _timestamp(following[0]),
            "duration_seconds": following[0] - previous[1],
        }
        for previous, following in zip(merged, merged[1:])
        if following[0] - previous[1] >= idle_threshold
    ]
    return {
        "metric_name": scores.get("metric_name") or (task.get("frozen_spec") or {}).get("metric_name"),
        "metric_direction": direction,
        "score": {
            "baseline": baseline,
            "selected": selected,
            "points": best_points,
        },
        "metric_ranges": metric_ranges,
        "idle_intervals": idle_intervals,
        "max_parallel": ((task.get("frozen_spec") or {}).get("budget") or {}).get("max_parallel"),
    }


def _load_models(path: Path, pattern: str, model: Any) -> list[Any]:
    if not path.exists():
        return []
    return [model.model_validate(load_json(item)) for item in sorted(path.glob(pattern))]


def _find_goal_record(root: Path, run_id: str) -> GoalPlusRecord | None:
    runtime = FileGoalPlusRuntime(root)
    for path in sorted((root / "goal-plus").glob("*/goal.json")):
        try:
            record = runtime.status(path.parent.name)
        except (OSError, ValueError):
            continue
        if any(task.run_id == run_id for task in record.search_tasks):
            return record
    return None


def _collect_observability(session: AgentSessionRecord) -> dict[str, Any]:
    try:
        return get_agent_host_adapter(session.host).collect_observability(session)
    except Exception as exc:
        return {
            "source": "collection_failed",
            "execution": {
                "terminal_state": "unknown",
                "started_at": session.created_at,
                "ended_at": session.updated_at,
                "duration_seconds": None,
                "timed_out": bool(session.host_handle.metadata.get("timed_out")),
                "runner_failed": bool(session.host_handle.metadata.get("runner_failed")),
            },
            "usage": {},
            "context": {},
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def _task_details(root: Path, task_summary: dict[str, Any], report_run_id: str) -> dict[str, Any]:
    run_id = task_summary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return {**task_summary, "is_report_run": False, "plans": [], "candidates": [], "sessions": []}
    run_dir = root / "runs" / run_id
    run = RunRecord.model_validate(load_json(run_dir / "run.json"))
    frozen = FrozenSpec.model_validate(
        load_json(root / "specs" / run.frozen_spec_id / "frozen_spec.json")
    )
    plans = _load_models(run_dir / "plans", "plan_*.json", SearchPlan)
    candidates = _load_models(run_dir / "candidates", "*/candidate.json", CandidateRecord)
    sessions = _load_models(run_dir / "agent_sessions", "agent_*.json", AgentSessionRecord)
    observations = {
        session.agent_session_id: _collect_observability(session) for session in sessions
    }
    session_ids_by_candidate: dict[str, list[str]] = {}
    for session in sessions:
        session_ids_by_candidate.setdefault(session.candidate_id, []).append(
            session.agent_session_id
        )

    candidate_payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        scored = [iteration for iteration in candidate.iterations if iteration.score is not None]
        best = None
        if scored:
            reverse = frozen.spec.metric_direction == "maximize"
            best = sorted(scored, key=lambda item: item.score, reverse=reverse)[0]
        candidate_payloads.append(
            {
                "candidate_id": candidate.candidate_id,
                "status": candidate.status,
                "plan_id": candidate.task.plan_id,
                "parent_id": candidate.task.parent_id,
                "parent_candidate_ids": candidate.task.parent_candidate_ids,
                "base_candidate_id": candidate.task.base_candidate_id,
                "hypothesis": candidate.task.hypothesis,
                "selected": candidate.candidate_id == run.selected_candidate_id,
                "score": (
                    candidate.score_report.aggregate_score
                    if candidate.score_report is not None
                    else None
                ),
                "process_passed": (
                    candidate.score_report.process_passed
                    if candidate.score_report is not None
                    else None
                ),
                "best_iteration": best.iteration if best is not None else None,
                "best_score": best.score if best is not None else None,
                "iterations_total": len(candidate.iterations),
                "session_ids": session_ids_by_candidate.get(candidate.candidate_id, []),
                "changed_files": candidate.detected_changed_files,
                "promotion_passed": (
                    candidate.promotion_report.promotion_passed
                    if candidate.promotion_report is not None
                    else None
                ),
                "promotion_evidence_at": (
                    candidate.promotion_evidence.created_at
                    if candidate.promotion_evidence is not None
                    else None
                ),
                "iterations": [
                    {
                        "iteration": iteration.iteration,
                        "agent_session_id": iteration.agent_session_id,
                        "score": iteration.score,
                        "process_passed": iteration.process_passed,
                        "hypothesis": iteration.hypothesis,
                        "summary": iteration.summary,
                        "failure_class": iteration.failure_class,
                        "git_head": iteration.git_head,
                        "created_at": iteration.created_at,
                        "changed_files": iteration.changed_files,
                    }
                    for iteration in candidate.iterations
                ],
            }
        )

    session_payloads: list[dict[str, Any]] = []
    for session in sessions:
        observation = observations[session.agent_session_id]
        execution = observation.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        usage = observation.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        context = observation.get("context")
        context = context if isinstance(context, dict) else {}
        session_payloads.append(
            {
                "agent_session_id": session.agent_session_id,
                "candidate_id": session.candidate_id,
                "host": session.host,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "provider": execution.get("provider"),
                "model": execution.get("model"),
                "terminal_state": execution.get("terminal_state"),
                "started_at": execution.get("started_at") or session.created_at,
                "ended_at": execution.get("ended_at"),
                "duration_seconds": execution.get("duration_seconds"),
                "timed_out": bool(execution.get("timed_out")),
                "runner_failed": bool(execution.get("runner_failed")),
                "processed_tokens": usage.get("processed_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "tool_calls": usage.get("tool_calls"),
                "context_tokens": context.get("tokens"),
                "context_percent": context.get("percent"),
                "verifier_runs": sum(
                    iteration.agent_session_id == session.agent_session_id
                    for candidate in candidates
                    for iteration in candidate.iterations
                ),
                "observability_source": observation.get("source"),
                "errors": observation.get("errors") or [],
            }
        )

    plan_payloads = [
        {
            "plan_id": plan.plan_id,
            "status": plan.status,
            "created_at": plan.created_at,
            "strategy": plan.strategy.name,
            "driver": plan.strategy.driver,
            "worker_mode": plan.worker_policy.get("mode", plan.strategy.worker_mode),
            "requested_k": plan.requested_k,
            "planned_k": plan.planned_k,
            "remaining_budget": plan.remaining_budget,
            "started_candidate_ids": plan.started_candidate_ids,
            "work_orders_total": len(plan.work_orders),
            "trace": (
                plan.strategy_trace.get("reason")
                or plan.strategy_trace.get("selection_rule")
                or plan.strategy_trace
            ),
        }
        for plan in plans
    ]
    return {
        **task_summary,
        "is_report_run": run_id == report_run_id,
        "run": run.model_dump(mode="json"),
        "frozen_spec": {
            "frozen_spec_id": frozen.frozen_spec_id,
            "spec_hash": frozen.spec_hash,
            "objective": frozen.spec.objective,
            "metric_name": frozen.spec.metric_name,
            "metric_direction": frozen.spec.metric_direction,
            "strategy": frozen.spec.strategy.model_dump(mode="json", exclude_none=True),
            "budget": frozen.spec.budget.model_dump(mode="json", exclude_none=True),
        },
        "plans": plan_payloads,
        "candidates": candidate_payloads,
        "sessions": session_payloads,
    }


_GOAL_EVENT_LABELS = {
    "created": "Goal created",
    "session_activated": "Main-agent session attached",
    "triage_recorded": "Triage recorded",
    "spec_draft_saved": "Search specification drafted",
    "frozen_verifier_confirmed": "Verifier frozen",
    "search_linked": "Search task linked",
    "search_result_recorded": "Search result recorded",
    "goal_updated": "Goal revision created",
    "status_changed": "Goal status changed",
    "final_check_requested": "Final check requested",
    "final_check_submitted": "Final check completed",
}


def _goal_event_label(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "event")
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    base = _GOAL_EVENT_LABELS.get(event_type, event_type.replace("_", " ").title())
    if event_type in {"search_linked", "search_result_recorded"} and payload.get("run_id"):
        return f"{base}: {payload['run_id']}"
    if event_type == "status_changed" and payload.get("status"):
        return f"{base}: {payload['status']}"
    if event_type == "goal_updated" and payload.get("goal_revision"):
        return f"{base}: revision {payload['goal_revision']}"
    return base


def _build_timeline(
    goal: GoalPlusRecord | None,
    goal_events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    goal_timeline_events: list[dict[str, Any]] = []
    if goal is not None:
        goal_timeline_events.append(
            {
                "lane": "main",
                "kind": "main_span",
                "label": "Goal record activity window",
                "start_at": goal.created_at,
                "end_at": goal.updated_at,
                "inferred_end": False,
                "run_id": None,
            }
        )
    for event in goal_events:
        if event.get("event_type") not in _GOAL_EVENT_LABELS:
            continue
        created_at = event.get("created_at")
        if not isinstance(created_at, str):
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        goal_timeline_events.append(
            {
                "lane": "main",
                "kind": "milestone",
                "label": _goal_event_label(event),
                "start_at": created_at,
                "end_at": None,
                "inferred_end": False,
                "run_id": payload.get("run_id"),
            }
        )
    for task in tasks:
        run_id = task.get("run_id")
        task_events: list[dict[str, Any]] = []
        task_scores = (task.get("statistics") or {}).get("scores") or {}
        metric_direction = str(
            task_scores.get("direction")
            or (task.get("frozen_spec") or {}).get("metric_direction")
            or "maximize"
        )
        baseline_score = _finite_float(task_scores.get("baseline"))
        session_scores = _session_scores(task, metric_direction)
        sessions_by_candidate: dict[str, list[str]] = {}
        for session in task.get("sessions", []):
            sessions_by_candidate.setdefault(str(session.get("candidate_id") or "unknown"), []).append(
                str(session.get("agent_session_id") or "unknown")
            )
        for session in task.get("sessions", []):
            start_at = session.get("started_at") or session.get("created_at")
            end_at = session.get("ended_at")
            duration_seconds = _finite_float(session.get("duration_seconds"))
            inferred = False
            if end_at is None and duration_seconds is not None:
                start_epoch = _epoch(start_at)
                if start_epoch is not None:
                    end_at = _timestamp(start_epoch + duration_seconds)
                    inferred = True
            if end_at is None:
                end_at = session.get("updated_at")
                inferred = True
            if duration_seconds is None:
                start_epoch = _epoch(start_at)
                end_epoch = _epoch(end_at)
                if start_epoch is not None and end_epoch is not None and end_epoch >= start_epoch:
                    duration_seconds = end_epoch - start_epoch
            terminal = session.get("terminal_state") or "unknown"
            session_id = str(session["agent_session_id"])
            candidate_id = str(session.get("candidate_id") or "unknown")
            candidate_sessions = sessions_by_candidate.get(candidate_id, [session_id])
            attempt_index = candidate_sessions.index(session_id) + 1
            score = session_scores.get(session_id)
            score_improvement = None
            if score is not None and baseline_score is not None:
                score_improvement = (
                    baseline_score - score
                    if metric_direction == "minimize"
                    else score - baseline_score
                )
            task_events.append(
                {
                    "lane": "worker",
                    "kind": "worker_session",
                    "label": f"{session_id} / {terminal}",
                    "start_at": start_at,
                    "end_at": end_at,
                    "inferred_end": inferred,
                    "run_id": run_id,
                    "session_id": session_id,
                    "candidate_id": candidate_id,
                    "terminal_state": terminal,
                    "duration_seconds": duration_seconds,
                    "processed_tokens": session.get("processed_tokens"),
                    "cost_usd": session.get("cost_usd"),
                    "tool_calls": session.get("tool_calls"),
                    "verifier_runs": session.get("verifier_runs"),
                    "tokens_per_minute": _per_minute(session.get("processed_tokens"), duration_seconds),
                    "cost_per_minute": _per_minute(session.get("cost_usd"), duration_seconds),
                    "verifier_density": _per_minute(session.get("verifier_runs"), duration_seconds),
                    "score": score,
                    "score_gain": score_improvement,
                    "attempt_index": attempt_index,
                    "attempt_count": len(candidate_sessions),
                }
            )
        for candidate in task.get("candidates", []):
            for iteration in candidate.get("iterations", []):
                parent_owned = iteration.get("agent_session_id") is None
                task_events.append(
                    {
                        "lane": "verifier",
                        "kind": "parent_verifier" if parent_owned else "worker_verifier",
                        "label": (
                            f"Parent verifier {candidate['candidate_id']} #{iteration['iteration']}"
                            if parent_owned
                            else f"Worker verifier {candidate['candidate_id']} #{iteration['iteration']}"
                        ),
                        "start_at": iteration.get("created_at"),
                        "end_at": None,
                        "inferred_end": False,
                        "run_id": run_id,
                        "session_id": iteration.get("agent_session_id"),
                        "score": iteration.get("score"),
                    }
                )
        for candidate in task.get("candidates", []):
            if not candidate.get("promotion_passed"):
                continue
            run = task.get("run") or {}
            promotion_at = (
                candidate.get("promotion_evidence_at")
                or task.get("result_recorded_at")
                or run.get("created_at")
            )
            task_events.append(
                {
                    "lane": "verifier",
                    "kind": "promotion",
                    "label": f"Promotion passed: {candidate['candidate_id']}",
                    "start_at": promotion_at,
                    "end_at": None,
                    "inferred_end": candidate.get("promotion_evidence_at") is None,
                    "run_id": run_id,
                }
            )

        run = task.get("run") or {}
        run_started_at = run.get("created_at")
        task_epochs = [
            value
            for event in task_events
            for value in (_epoch(event.get("start_at")), _epoch(event.get("end_at")))
            if value is not None
        ]
        run_started_epoch = _epoch(run_started_at)
        task_end_epoch = max(task_epochs) if task_epochs else run_started_epoch
        if run_started_epoch is not None and task_end_epoch is not None:
            task_events.insert(
                0,
                {
                    "lane": "main",
                    "kind": "main_span",
                    "label": "Search orchestration",
                    "start_at": run_started_at,
                    "end_at": _timestamp(max(run_started_epoch + 1.0, task_end_epoch)),
                    "inferred_end": False,
                    "run_id": run_id,
                },
            )
        task_timeline = _timeline_payload(task_events)
        task_timeline["performance"] = _timeline_performance(task, task_timeline)
        task["timeline"] = task_timeline

    gate_counts = Counter(
        str(event.get("event_type"))
        for event in goal_events
        if event.get("event_type") in {"gate_allowed", "gate_blocked"}
    )
    return _timeline_payload(
        goal_timeline_events,
        gate_events=dict(sorted(gate_counts.items())),
    )


def _timeline_payload(
    events: list[dict[str, Any]],
    *,
    gate_events: dict[str, int] | None = None,
) -> dict[str, Any]:
    epochs = [
        value
        for event in events
        for value in (_epoch(event.get("start_at")), _epoch(event.get("end_at")))
        if value is not None
    ]
    start_epoch = min(epochs) if epochs else None
    end_epoch = max(epochs) if epochs else None
    duration = (
        max(1.0, end_epoch - start_epoch)
        if start_epoch is not None and end_epoch is not None
        else None
    )
    events.sort(key=lambda event: _epoch(event.get("start_at")) or float("inf"))
    return {
        "start_at": _timestamp(start_epoch),
        "end_at": _timestamp(end_epoch),
        "duration_seconds": duration,
        "events": events,
        "gate_events": gate_events or {},
    }


def build_html_report_data(root_dir: Path | str, run_id: str) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    goal = _find_goal_record(root, run_id)
    goal_id = goal.goal_plus_id if goal is not None else None
    snapshot = goal_plus_monitor_snapshot(
        root,
        goal_plus_id=goal_id,
        run_id=run_id,
    )
    task_summaries = snapshot.get("search_tasks")
    task_summaries = task_summaries if isinstance(task_summaries, list) else []
    tasks = [
        _task_details(root, summary, run_id)
        for summary in task_summaries
        if isinstance(summary, dict) and summary.get("run_exists")
    ]
    goal_runtime = FileGoalPlusRuntime(root)
    goal_events = goal_runtime.list_events(goal_id) if goal_id is not None else []
    timeline = _build_timeline(goal, goal_events, tasks)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": snapshot.get("snapshot_at"),
        "goal_plus_id": goal_id,
        "report_run_id": run_id,
        "snapshot": snapshot,
        "search_tasks": tasks,
        "timeline": timeline,
    }


def _metric_card(label: str, value: str, detail: str = "", tone: str = "") -> str:
    return (
        '<div class="kpi">'
        f'<div class="kpi-label">{escape(label)}</div>'
        f'<div class="metric-value {tone}">{escape(value)}</div>'
        f'<div class="kpi-detail">{escape(detail)}</div>'
        "</div>"
    )


_METRIC_GAP_INFO = {
    "target_score": (
        "Not configured",
        "No score threshold was declared. This does not mean the Goal Plus task failed.",
    ),
    "baseline_score": ("Not configured", "No baseline score was supplied for improvement analysis."),
    "orchestrator_cost_usd": (
        "Not collected",
        "The attached main-agent evidence does not expose a reliable orchestrator cost.",
    ),
    "orchestrator_token_usage": (
        "Not collected",
        "No readable main-agent transcript usage record was attached.",
    ),
    "orchestrator_usage_breakdown": (
        "Not collected",
        "Detailed main-agent input, cache, output, and tool usage are not available.",
    ),
    "hardware_utilization": (
        "Not collected",
        "Worker host CPU, GPU, memory, and accelerator telemetry are outside the current evidence contract.",
    ),
    "worker_time_to_first_token": (
        "Not collected",
        "The worker host did not publish time-to-first-token for every session.",
    ),
    "worker_processed_tokens": ("Partial coverage", "Processed-token usage is missing for one or more worker sessions."),
    "worker_cost_usd": ("Partial coverage", "Cost usage is missing for one or more worker sessions."),
    "worker_duration": ("Partial coverage", "Observed duration is missing for one or more worker sessions."),
    "semantic_candidate_coverage": (
        "Not computed",
        "Candidate semantic diversity is not currently scored.",
    ),
    "redundant_attempt_rate": (
        "Not computed",
        "The report does not classify semantically duplicate attempts.",
    ),
    "temporal_collision_rate": (
        "Not computed",
        "The report does not classify concurrent workers as colliding or duplicating work.",
    ),
    "research_rollup_quality": (
        "Not computed",
        "No verifier-backed quality metric exists for the final research synthesis.",
    ),
    "normalized_score": (
        "Not computed",
        "The verifier score is reported in its native scale; no cross-task normalization is declared.",
    ),
    "promotion_attempt_history": (
        "Not retained",
        "The durable report has final promotion evidence, not a complete history of every attempted promotion.",
    ),
}


def _render_metric_availability(items: list[Any]) -> str:
    names = list(dict.fromkeys(str(item) for item in items if item))
    if not names:
        return '<p>No known metric availability gaps.</p>'
    rows = []
    for name in names:
        kind, reason = _METRIC_GAP_INFO.get(
            name,
            ("Not observed", "This value was not present in the durable report evidence."),
        )
        rows.append(
            '<li>'
            f'<code>{escape(name)}</code>'
            f'<span class="metric-gap-kind">{escape(kind)}</span>'
            f'<span class="metric-gap-reason">{escape(reason)}</span>'
            '</li>'
        )
    return (
        '<details class="summary-block">'
        f'<summary>Metric availability ({len(names)} gaps)</summary>'
        f'<div><ul class="metric-gap-list">{"".join(rows)}</ul></div>'
        '</details>'
    )


def _stat_rows(values: dict[str, Any], formatters: dict[str, Any] | None = None) -> str:
    formatters = formatters or {}
    rows = []
    for key, value in values.items():
        formatter = formatters.get(key, _text)
        rows.append(
            '<div class="stat-row">'
            f'<span>{escape(key.replace("_", " ").title())}</span>'
            f'<strong class="mono">{escape(formatter(value))}</strong>'
            "</div>"
        )
    return "".join(rows)


def _timeline_position(event: dict[str, Any], start_epoch: float, duration: float) -> tuple[float, float]:
    event_start = _epoch(event.get("start_at")) or start_epoch
    event_end = _epoch(event.get("end_at"))
    left = max(0.0, min(99.0, (event_start - start_epoch) / duration * 100))
    if event_end is None:
        return left, 0.8
    width = max(1.0, (event_end - event_start) / duration * 100)
    return left, min(width, 100.0 - left)


def _timeline_width(duration_seconds: float) -> int:
    duration_minutes = max(0.0, duration_seconds / 60.0)
    return max(980, min(20_000, int(round(190 + duration_minutes * 80))))


def _metric_level(value: Any, metric_range: dict[str, Any]) -> int | None:
    number = _finite_float(value)
    low = _finite_float(metric_range.get("min"))
    high = _finite_float(metric_range.get("max"))
    if number is None or low is None or high is None:
        return None
    ratio = 0.5 if high == low else (number - low) / (high - low)
    return min(4, max(1, int(ratio * 4) + 1))


def _metric_readout(metric: str, value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "Not observed"
    if metric == "score_gain":
        return f"{number:+.4f}".rstrip("0").rstrip(".")
    if metric == "tokens_per_minute":
        return f"{_number(number, digits=0)}/min"
    if metric == "cost_per_minute":
        return f"${number:.4f}/min"
    if metric == "verifier_density":
        return f"{number:.1f}/min"
    return _number(number)


def _render_score_chart(
    performance: dict[str, Any],
    start_epoch: float,
    duration: float,
) -> str:
    score_data = performance.get("score") or {}
    baseline = _finite_float(score_data.get("baseline"))
    selected = _finite_float(score_data.get("selected"))
    points = [
        point
        for point in score_data.get("points", [])
        if _finite_float(point.get("score")) is not None and _epoch(point.get("at")) is not None
    ]
    values = [value for value in (baseline, selected) if value is not None]
    values.extend(float(point["score"]) for point in points)
    if not values:
        return ""

    low = min(values)
    high = max(values)
    padding = max((high - low) * 0.15, abs(high) * 0.02, 0.01)
    plot_low = low - padding
    plot_high = high + padding

    def y_position(value: float) -> float:
        return 54.0 - (value - plot_low) / (plot_high - plot_low) * 44.0

    current = baseline if baseline is not None else float(points[0]["score"])
    path_parts = [f"M 0 {y_position(current):.2f}"]
    point_marks = []
    for point in points:
        point_epoch = _epoch(point.get("at"))
        score = float(point["score"])
        if point_epoch is None:
            continue
        x = max(0.0, min(1000.0, (point_epoch - start_epoch) / duration * 1000.0))
        y = y_position(score)
        path_parts.extend((f"H {x:.2f}", f"V {y:.2f}"))
        tooltip = (
            f"{point.get('candidate_id') or 'candidate'}: {_number(score, digits=4)} "
            f"at {point.get('at')}"
        )
        point_marks.append(
            f'<circle class="score-point" cx="{x:.2f}" cy="{y:.2f}" r="3" '
            f'<title>{escape(tooltip)}</title></circle>'
        )
    path_parts.append("H 1000")
    reference_lines = []
    reference_labels = []
    for label, value in (("Baseline", baseline), ("Selected", selected)):
        if value is None:
            continue
        y = y_position(value)
        reference_lines.append(
            f'<line class="score-reference" x1="0" y1="{y:.2f}" x2="1000" y2="{y:.2f}" />'
        )
        label_top = min(51.0, max(1.0, y - 11.0))
        reference_labels.append(
            f'<span class="score-ref-label" style="top:{label_top:.2f}px">'
            f'{escape(label)} {_html(_number(value, digits=4))}</span>'
        )
    metric_name = str(performance.get("metric_name") or "score")
    summary = f"{_number(baseline, digits=4)} to {_number(selected, digits=4)}"
    return (
        '<div class="score-row">'
        '<div class="score-label">'
        '<strong>Best score</strong>'
        f'<span>{escape(metric_name)} / {escape(summary)}</span>'
        '</div>'
        f'<div class="score-track" role="img" aria-label="Best score progression: {escape(summary, quote=True)}">'
        '<svg viewBox="0 0 1000 64" preserveAspectRatio="none" aria-hidden="true">'
        f'{"".join(reference_lines)}'
        f'<path class="score-step" d="{" ".join(path_parts)}" />'
        f'{"".join(point_marks)}'
        f'</svg>{"".join(reference_labels)}</div></div>'
    )


def _render_metric_toolbar(performance: dict[str, Any], default_metric: str) -> str:
    metric_ranges = performance.get("metric_ranges") or {}
    selected_range = metric_ranges.get(default_metric) or {}
    low = _metric_readout(default_metric, selected_range.get("min"))
    high = _metric_readout(default_metric, selected_range.get("max"))
    options = (
        ("score-gain", "Score gain"),
        ("tokens-per-minute", "Tokens/min"),
        ("cost-per-minute", "Cost/min"),
        ("verifier-density", "Verifier/min"),
    )
    buttons = "".join(
        f'<button type="button" data-metric-mode="{key}" aria-pressed="{str(key == default_metric.replace("_", "-")).lower()}">{label}</button>'
        for key, label in options
    )
    return (
        '<div class="metric-lens-toolbar no-print">'
        '<div class="metric-scale" aria-label="Selected metric range">'
        f'<span data-metric-low>{escape(low)}</span>'
        '<span class="metric-scale-bar" aria-hidden="true"><i></i><i></i><i></i><i></i></span>'
        f'<span data-metric-high>{escape(high)}</span>'
        '</div>'
        f'<div class="metric-control" role="group" aria-label="Worker session color metric">{buttons}</div>'
        '</div>'
    )


_SESSION_ALERT_ICON = (
    '<svg class="session-state-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>'
)


def _render_timeline(
    timeline: dict[str, Any],
    *,
    title: str,
    span_label: str = "Observed span",
) -> str:
    events = timeline.get("events") or []
    start_epoch = _epoch(timeline.get("start_at"))
    duration = timeline.get("duration_seconds")
    if start_epoch is None or not isinstance(duration, int | float):
        return '<div class="panel panel-body">No durable timeline timestamps were observed.</div>'

    main_events = [event for event in events if event.get("lane") == "main"]
    verifier_events = [event for event in events if event.get("lane") == "verifier"]
    worker_events = [event for event in events if event.get("lane") == "worker"]
    performance = timeline.get("performance") or {}
    metric_ranges = performance.get("metric_ranges") or {}
    default_metric = "score_gain" if "score_gain" in metric_ranges else (
        "tokens_per_minute" if "tokens_per_minute" in metric_ranges else next(iter(metric_ranges), "status")
    )
    tracks: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("Main Agent", "main", main_events),
    ]
    for event in worker_events:
        tracks.append((str(event.get("session_id") or "Worker session"), "worker", [event]))
    if verifier_events:
        tracks.append(("Verifier activity", "parent", verifier_events))
    timeline_width = _timeline_width(float(duration))
    idle_intervals = performance.get("idle_intervals") or []

    rows = []
    worker_track_index = 0
    for label, lane_class, track_events in tracks:
        marks = []
        if lane_class == "worker":
            for idle in idle_intervals:
                left, width = _timeline_position(idle, start_epoch, float(duration))
                idle_label = ""
                if worker_track_index == 0:
                    idle_label = (
                        f'<span class="timeline-idle-label">Idle {_html(_duration(idle.get("duration_seconds")))}</span>'
                    )
                marks.append(
                    f'<span class="timeline-idle" style="left:{left:.3f}%;width:{width:.3f}%;" '
                    f'title="No active worker sessions for {escape(_duration(idle.get("duration_seconds")), quote=True)}">'
                    f'{idle_label}</span>'
                )
        for event in track_events:
            left, width = _timeline_position(event, start_epoch, float(duration))
            point = event.get("end_at") is None
            kind = event.get("kind")
            css_class = lane_class
            if kind == "worker_verifier":
                css_class = "worker"
            elif kind == "promotion":
                css_class = "success"
            elif kind != "worker_session" and str(event.get("terminal_state")) in {"timed_out", "failed"}:
                css_class = "failure"
            tooltip = str(event.get("label") or "event")
            if event.get("inferred_end"):
                tooltip += " (end inferred)"
            style = f"left:{left:.3f}%;width:{width:.3f}%;"
            event_attributes = ""
            label_html = "" if point else escape(str(event.get("label") or ""))
            if kind == "worker_session":
                terminal_state = str(event.get("terminal_state") or "unknown")
                failed = terminal_state in {"timed_out", "failed"}
                level = _metric_level(event.get(default_metric), metric_ranges.get(default_metric) or {})
                css_class = "worker worker-session"
                if level is not None:
                    css_class += f" metric-level-{level}"
                if failed:
                    css_class += " session-failure"
                metric_attributes = []
                for metric_name in (
                    "score_gain",
                    "tokens_per_minute",
                    "cost_per_minute",
                    "verifier_density",
                ):
                    metric_value = _finite_float(event.get(metric_name))
                    if metric_value is not None:
                        metric_attributes.append(
                            f'data-metric-{metric_name.replace("_", "-")}="{metric_value:.9f}"'
                        )
                event_attributes = (
                    f'data-terminal-state="{escape(terminal_state, quote=True)}" '
                    + " ".join(metric_attributes)
                    + " "
                )
                metric_value = event.get(default_metric)
                label_html = (
                    (_SESSION_ALERT_ICON if failed else "")
                    + f'<span class="metric-readout">{escape(_metric_readout(default_metric, metric_value))}</span>'
                )
                details = [
                    f"candidate {event.get('candidate_id')}",
                    f"duration {_duration(event.get('duration_seconds'))}",
                    f"score gain {_metric_readout('score_gain', event.get('score_gain'))}",
                    f"tokens/min {_metric_readout('tokens_per_minute', event.get('tokens_per_minute'))}",
                    f"cost/min {_metric_readout('cost_per_minute', event.get('cost_per_minute'))}",
                    f"verifier density {_metric_readout('verifier_density', event.get('verifier_density'))}",
                ]
                if _finite_float(event.get("score")) is not None:
                    details.append(f"score {_number(event.get('score'), digits=4)}")
                tooltip += " | " + " | ".join(details)
            marks.append(
                f'<span class="timeline-event {css_class}{" point" if point else ""}" {event_attributes}'
                f'style="{style}" title="{escape(tooltip, quote=True)}">{label_html}</span>'
            )
        label_html = escape(label)
        row_class = "timeline-row"
        if lane_class == "worker" and track_events:
            event = track_events[0]
            session_id = str(event.get("session_id") or label)
            suffix = session_id.rsplit("_", 1)[-1]
            candidate_id = str(event.get("candidate_id") or "unknown")
            attempt_index = int(event.get("attempt_index") or 1)
            attempt_count = int(event.get("attempt_count") or 1)
            score = _finite_float(event.get("score"))
            retry = (
                f'<span class="retry-badge">retry {attempt_index}/{attempt_count}</span>'
                if attempt_count > 1
                else ""
            )
            score_text = f" / score {_number(score, digits=4)}" if score is not None else ""
            label_html = (
                f'<strong title="{escape(session_id, quote=True)}">agent_{escape(suffix)}{retry}</strong>'
                f'<small>{escape(candidate_id)}{escape(score_text)}</small>'
            )
            if attempt_count > 1:
                row_class += " redispatched"
            worker_track_index += 1
        rows.append(
            f'<div class="{row_class}">'
            f'<div class="timeline-label">{label_html}</div>'
            f'<div class="timeline-track">{"".join(marks)}</div>'
            "</div>"
        )
    end_epoch = start_epoch + float(duration)
    event_items = []
    for event in events:
        event_items.append(
            "<li>"
            f'<time class="timeline-time">{_html(event.get("start_at"))}</time>'
            f'<span class="lane">{_html(event.get("lane"))}</span>'
            f'<span>{_html(event.get("label"))}'
            f'{" <em>(end inferred)</em>" if event.get("inferred_end") else ""}</span>'
            "</li>"
        )
    metric_lens = bool(worker_events and metric_ranges)
    score_chart = _render_score_chart(performance, start_epoch, float(duration)) if worker_events else ""
    toolbar = _render_metric_toolbar(performance, default_metric) if metric_lens else ""
    default_mode = default_metric.replace("_", "-") if metric_lens else "status"
    return (
        f'<div class="panel timeline-shell" data-metric-mode="{default_mode}"'
        f'{" data-metric-lens" if metric_lens else ""}>'
        '<div class="timeline-head">'
        f'<h2>{escape(title)}</h2>'
        f'<span class="mono">{escape(span_label)}: {escape(_duration(duration))}</span>'
        "</div>"
        f'{toolbar}'
        f'<div class="timeline-scroll" tabindex="0" aria-label="{escape(title, quote=True)} scroll area">'
        f'<div class="timeline" style="--timeline-width:{timeline_width}px">'
        f'{score_chart}'
        f'<div class="timeline-rows" data-track-count="{len(tracks)}">{"".join(rows)}</div>'
        '<div class="timeline-axis">'
        f'<span>{escape(_timestamp(start_epoch) or "")}</span>'
        f'<span>+{escape(_duration(float(duration) / 2))}</span>'
        f'<span>{escape(_timestamp(end_epoch) or "")}</span>'
        "</div></div></div>"
        '<div class="timeline-key">'
        '<span><i class="key-dot"></i>Main agent</span>'
        '<span><i class="key-dot worker"></i>Worker session / worker verifier</span>'
        '<span><i class="key-dot parent"></i>Parent verifier</span>'
        f'{"<span>Fill intensity = selected metric</span><span>Red outline = timed out / failed</span><span>Retry n/N = same candidate redispatch</span>" if metric_lens else ""}'
        "</div></div>"
        '<details class="summary-block event-log"><summary>Chronological event evidence</summary>'
        f'<div><ul class="event-list">{"".join(event_items)}</ul></div></details>'
    )


def _render_candidates(task: dict[str, Any]) -> str:
    candidates = task.get("candidates") or []
    if not candidates:
        return "<p>No candidates were persisted.</p>"
    rows = []
    for candidate in candidates:
        rows.append(
            f'<tr class="{"selected-row" if candidate.get("selected") else ""}">'
            f'<td class="mono"><strong>{_html(candidate.get("candidate_id"))}</strong></td>'
            f'<td>{_status("selected" if candidate.get("selected") else candidate.get("status"))}</td>'
            f'<td class="mono">{_html(_number(candidate.get("score")))}</td>'
            f'<td class="mono">{_html(_number(candidate.get("best_score")))}</td>'
            f'<td>{_html(candidate.get("process_passed"))}</td>'
            f'<td class="mono">{_html(candidate.get("iterations_total"))}</td>'
            f'<td class="mono">{_html(", ".join(candidate.get("session_ids") or []) or None)}</td>'
            f'<td>{_html(", ".join(candidate.get("changed_files") or []) or None)}</td>'
            f'<td>{_html(candidate.get("hypothesis"))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-scroll"><table><thead><tr>'
        "<th>Candidate</th><th>Status</th><th>Final score</th><th>Best score</th>"
        "<th>Process pass</th><th>Iterations</th><th>Sessions</th><th>Changed files</th><th>Hypothesis</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _render_sessions(task: dict[str, Any]) -> str:
    sessions = task.get("sessions") or []
    if not sessions:
        return "<p>No worker sessions were persisted.</p>"
    rows = []
    for session in sessions:
        terminal = session.get("terminal_state") or (
            "timed_out" if session.get("timed_out") else "unknown"
        )
        rows.append(
            "<tr>"
            f'<td class="mono"><strong>{_html(session.get("agent_session_id"))}</strong></td>'
            f'<td class="mono">{_html(session.get("candidate_id"))}</td>'
            f'<td>{_html(session.get("host"))}</td>'
            f'<td>{_html(session.get("provider"))}</td>'
            f'<td>{_html(session.get("model"))}</td>'
            f'<td>{_status(terminal)}</td>'
            f'<td class="mono">{_html(_duration(session.get("duration_seconds")))}</td>'
            f'<td class="mono">{_html(_number(session.get("processed_tokens")))}</td>'
            f'<td class="mono">{_html(_cost(session.get("cost_usd")))}</td>'
            f'<td class="mono">{_html(session.get("verifier_runs"))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-scroll"><table><thead><tr>'
        "<th>Session</th><th>Candidate</th><th>Host</th><th>Provider</th><th>Model</th>"
        "<th>Terminal state</th><th>Duration</th><th>Processed tokens</th><th>Known cost</th><th>Verifier runs</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _render_statistics(task: dict[str, Any]) -> str:
    statistics = task.get("statistics")
    if not isinstance(statistics, dict):
        return "<p>Unified statistics were not available for this Search task.</p>"
    timing = statistics.get("timing") or {}
    workers = statistics.get("workers") or {}
    verifiers = statistics.get("verifiers") or {}
    usage = statistics.get("usage") or {}
    efficiency = statistics.get("efficiency") or {}
    lineage = statistics.get("lineage") or {}
    tables = [
        ("Timing", timing, {key: _duration for key in timing}),
        ("Workers", workers, {key: _percent for key in workers if key.endswith("_rate")}),
        ("Verifiers", verifiers, {key: _percent for key in verifiers if key.endswith("_rate")}),
        (
            "Usage",
            {key: usage.get(key) for key in ("processed_tokens", "input_tokens", "cached_input_tokens", "output_tokens", "cost_usd", "tool_calls")},
            {"cost_usd": _cost},
        ),
        (
            "Efficiency",
            efficiency,
            {key: _cost for key in efficiency if key.endswith("_usd")},
        ),
        ("Lineage", lineage, {}),
    ]
    return '<div class="stats-grid">' + "".join(
        '<div class="stats-table">'
        f'<h3>{escape(title)}</h3>{_stat_rows(values, formatters)}'
        "</div>"
        for title, values, formatters in tables
    ) + "</div>"


def _render_task(task: dict[str, Any], index: int) -> str:
    run = task.get("run") or {}
    frozen = task.get("frozen_spec") or {}
    stats = task.get("statistics") or {}
    scores = stats.get("scores") or {}
    timing = stats.get("timing") or {}
    score_target = scores.get("target")
    score_target_text = (
        "Not configured" if score_target is None else _number(score_target, digits=4)
    )
    run_id = str(task.get("run_id") or f"task-{index}")
    selected = task.get("is_report_run")
    return (
        f'<article class="panel task-panel" data-run-id="{escape(run_id, quote=True)}" '
        f'id="task-{escape(run_id, quote=True)}" {"" if selected else "hidden"}>'
        '<header class="task-head">'
        '<div class="task-title-line">'
        f'<h3>Search Task {index:02d}: <span class="mono">{escape(run_id)}</span></h3>'
        f'{_status(run.get("state") or task.get("state"))}'
        "</div>"
        f'<p class="task-objective">{_html(frozen.get("objective"))}</p>'
        "</header>"
        '<div class="task-metrics">'
        f'<div class="task-metric"><span class="kpi-label">Goal revision</span><strong>{_html(task.get("goal_revision"))}</strong></div>'
        f'<div class="task-metric"><span class="kpi-label">Strategy</span><strong>{_html((task.get("strategy") or {}).get("name"))}</strong></div>'
        f'<div class="task-metric"><span class="kpi-label">Baseline</span><strong class="mono">{_html(_number(scores.get("baseline")))}</strong></div>'
        f'<div class="task-metric"><span class="kpi-label">Score target</span><strong class="mono">{escape(score_target_text)}</strong></div>'
        f'<div class="task-metric"><span class="kpi-label">Best / selected</span><strong class="mono">{_html(_number(scores.get("best")))} / {_html(_number(scores.get("selected")))}</strong></div>'
        f'<div class="task-metric"><span class="kpi-label">First improvement</span><strong class="mono">{_html(_duration(timing.get("time_to_first_improvement_seconds")))}</strong></div>'
        "</div>"
        '<section class="subsection">'
        f'{_render_timeline(task.get("timeline") or {}, title="Search Execution Timeline")}'
        '<p class="footnote">This axis is scoped to this Search run. Worker bars show actual host execution, not the configured maximum or an aspirational exploration window.</p>'
        "</section>"
        '<section class="subsection"><h3>Candidate Evidence</h3>'
        f'{_render_candidates(task)}</section>'
        '<section class="subsection"><h3>Worker Sessions</h3>'
        f'{_render_sessions(task)}</section>'
        '<section class="subsection"><h3>Complete Statistical View</h3>'
        f'{_render_statistics(task)}'
        '<details class="summary-block"><summary>Raw normalized Search statistics</summary>'
        f'<pre>{escape(json.dumps(stats, indent=2, ensure_ascii=False, sort_keys=True))}</pre></details>'
        "</section></article>"
    )


def render_html_report(data: dict[str, Any]) -> str:
    snapshot = data.get("snapshot") or {}
    goal = snapshot.get("goal_plus") or {}
    aggregate = snapshot.get("search_task_aggregate") or {}
    aggregate_stats = aggregate.get("statistics") or {}
    aggregate_usage = aggregate_stats.get("usage") or {}
    total_statistics = snapshot.get("statistics") or {}
    total_usage = total_statistics.get("total_usage") or {}
    orchestrator = total_statistics.get("orchestrator") or {}
    orchestrator_usage = orchestrator.get("usage") or {}
    tasks = data.get("search_tasks") or []
    report_run_id = str(data.get("report_run_id") or "unknown")
    selected_task = next(
        (task for task in tasks if task.get("is_report_run")),
        tasks[-1] if tasks else {},
    )
    selected_frozen = selected_task.get("frozen_spec") or {}
    selected_stats = selected_task.get("statistics") or {}
    selected_scores = selected_stats.get("scores") or {}
    unavailable = total_statistics.get("unavailable_metrics") or []
    missing = (selected_stats.get("data_quality") or {}).get("missing") or []
    warnings = snapshot.get("warnings") or []
    goal_id = data.get("goal_plus_id")
    title_id = str(goal_id or report_run_id)
    state = goal.get("status") or (selected_task.get("run") or {}).get("state")
    search_count = aggregate.get("search_tasks_total", len(tasks))
    run_state = (selected_task.get("run") or {}).get("state") or selected_task.get("state")
    score_target = selected_scores.get("target")
    score_gain = selected_scores.get("selected_improvement_from_baseline")
    score_detail = (
        f"baseline {_number(selected_scores.get('baseline'), digits=4)} / "
        f"gain {_metric_readout('score_gain', score_gain)}"
    )
    completion_detail = f"run {str(run_state or 'unknown').lower()}"
    if score_target is None:
        completion_detail += " / no score threshold"
        completion_semantics = (
            "No score threshold was configured for this Search task. Complete means the Goal Plus "
            "workflow finished and the selected candidate survived the required verification and promotion; "
            "it does not claim that an unspecified threshold was reached."
        )
        score_target_text = "Not configured"
    else:
        target_reached = selected_scores.get("target_reached")
        target_result = (
            "reached"
            if target_reached is True
            else "not reached"
            if target_reached is False
            else "not evaluated"
        )
        completion_detail += f" / target {target_result}"
        completion_semantics = (
            f"A score threshold of {_number(score_target, digits=4)} was configured and was "
            f"{target_result} by the selected result."
        )
        score_target_text = _number(score_target, digits=4)

    kpis = "".join(
        [
            _metric_card("Goal status", _text(state).title(), completion_detail, _status_class(state)),
            _metric_card("Search tasks", _number(search_count), "GP-level tasks"),
            _metric_card("Selected score", _number(selected_scores.get("selected"), digits=4), score_detail, "success"),
            _metric_card("Candidates", _number(aggregate.get("candidates_total")), f"{_number(aggregate.get('candidates_evaluated'))} evaluated"),
            _metric_card("Worker sessions", _number(aggregate.get("worker_sessions_total")), f"{_number((aggregate_stats.get('workers') or {}).get('timed_out'))} timed out"),
            _metric_card("Verifier runs", _number(aggregate.get("verifier_runs_total")), f"{_number((aggregate_stats.get('verifiers') or {}).get('parent_process_runs'))} parent-owned"),
            _metric_card("Processed tokens", _number(aggregate_usage.get("processed_tokens")), "worker sessions"),
            _metric_card("Known worker cost", _cost(aggregate_usage.get("cost_usd")), "coverage-aware"),
        ]
    )

    task_tabs = "".join(
        f'<button class="task-tab" type="button" data-task-target="{escape(str(task.get("run_id")), quote=True)}" '
        f'aria-selected="{"true" if task.get("is_report_run") else "false"}">'
        f'<span>Task {index:02d}</span><span class="mono">r{_html(task.get("goal_revision"))}</span>'
        f'{_status((task.get("run") or {}).get("state"))}</button>'
        for index, task in enumerate(tasks, start=1)
    )
    task_panels = "".join(
        _render_task(task, index) for index, task in enumerate(tasks, start=1)
    )
    warning_items = "".join(
        f'<li><span class="mono">{_html(item.get("kind") if isinstance(item, dict) else "warning")}</span>: '
        f'{_html(json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else item)}</li>'
        for item in warnings
    ) or "<li>No monitor warnings.</li>"
    metric_availability = _render_metric_availability(unavailable + missing)

    worker_sources = int(aggregate_usage.get("sources_total") or 0)
    worker_coverage = (aggregate_usage.get("coverage") or {}).get("processed_tokens") or 0
    coverage_percent = min(100.0, worker_coverage / worker_sources * 100) if worker_sources else 0.0
    raw_payload = {
        "schema_version": data.get("schema_version"),
        "generated_at": data.get("generated_at"),
        "snapshot": snapshot,
        "search_tasks": tasks,
        "timeline": data.get("timeline"),
    }

    print_icon = (
        '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
        '<rect width="12" height="8" x="6" y="14"/></svg>'
    )

    return f"""<!doctype html>
<html lang="en" data-report-schema="goal-plus-report/v{REPORT_SCHEMA_VERSION}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Goal Plus Execution Report: {escape(title_id)}</title>
  <script>document.documentElement.classList.add('js');</script>
  <style>{_REPORT_CSS}</style>
</head>
<body data-goal-plus-id="{escape(str(goal_id or ''), quote=True)}" data-run-id="{escape(report_run_id, quote=True)}">
  <header class="masthead">
    <div class="wrap masthead-inner">
      <div class="identity">
        <div class="eyebrow">Goal Plus Execution Report</div>
        <div class="identity-line"><h1>{escape(title_id)}</h1>{_status(state)}</div>
        <div class="id-line mono">Report run: {escape(report_run_id)}</div>
      </div>
      <div class="masthead-actions">
        <div class="generated">Generated<strong class="mono">{_html(data.get("generated_at"))}</strong></div>
        <button class="button no-print" type="button" onclick="window.print()" title="Print report">{print_icon}<span>Print</span></button>
      </div>
    </div>
  </header>
  <nav class="section-nav no-print" aria-label="Report sections">
    <div class="wrap">
      <a href="#aggregate">Summary</a><a href="#goal">Goal</a>
      <a href="#tasks">Search tasks ({escape(_number(search_count))})</a><a href="#audit">Audit</a>
    </div>
  </nav>
  <main class="wrap">
    <section id="aggregate" class="report-section">
      <div class="section-kicker">Goal Plus Summary</div>
      <div class="kpi-grid">{kpis}</div>
    </section>
    <section id="goal" class="report-section">
      <h2>Goal And Completion</h2>
      <div class="panel panel-body">
        <h3>Selected Search Objective</h3>
        <p class="objective">{_html(selected_frozen.get("objective"))}</p>
      </div>
      <div class="panel panel-body completion-note">
        <h3>Completion semantics</h3>
        <p>{escape(completion_semantics)}</p>
      </div>
      <div class="panel panel-body">
        <h3>Goal Record</h3>
        <dl class="fact-grid">
          <div class="fact"><dt>Goal revision</dt><dd>{_html(goal.get("goal_revision"))}</dd></div>
          <div class="fact"><dt>Phase</dt><dd>{_html(goal.get("phase"))}</dd></div>
          <div class="fact"><dt>Selection survival</dt><dd>{_html(_percent(aggregate_stats.get("selection_survival_rate")))}</dd></div>
          <div class="fact"><dt>Score target</dt><dd>{escape(score_target_text)}</dd></div>
        </dl>
        <details class="summary-block"><summary>Original raw goal</summary><div class="raw-goal">{_html(goal.get("raw_goal"))}</div></details>
      </div>
      <div class="panel panel-body">
        <h3>Usage Coverage</h3>
        <div class="coverage-row"><span>Worker processed-token coverage</span><strong>{worker_coverage}/{worker_sources} sessions</strong></div>
        <div class="coverage-bar" aria-label="Worker usage coverage"><span style="width:{coverage_percent:.2f}%"></span></div>
        <dl class="fact-grid coverage">
          <div class="fact"><dt>Worker processed tokens</dt><dd class="mono">{_html(_number(aggregate_usage.get("processed_tokens")))}</dd></div>
          <div class="fact"><dt>Orchestrator processed tokens</dt><dd class="mono">{_html(_number(orchestrator_usage.get("processed_tokens")))}</dd></div>
          <div class="fact"><dt>Combined known tokens</dt><dd class="mono">{_html(_number(total_usage.get("processed_tokens")))}</dd></div>
          <div class="fact"><dt>Combined known cost</dt><dd class="mono">{_html(_cost(total_usage.get("cost_usd")))}</dd></div>
        </dl>
      </div>
    </section>
    <section id="tasks" class="report-section">
      <div class="section-kicker">Per-Task Evidence</div>
      <h2>Search Tasks</h2>
      <div class="task-tabs no-print" role="tablist" aria-label="Search tasks">{task_tabs}</div>
      {task_panels or '<div class="panel panel-body">No linked Search tasks were found.</div>'}
    </section>
    <section id="audit" class="report-section">
      <h2>Report Audit</h2>
      <div class="two-column">
        <div class="panel panel-body"><h3>Monitor Warnings</h3><ul class="warning-list">{warning_items}</ul></div>
        <div class="panel panel-body"><h3>Timeline Gate Summary</h3>{_stat_rows((data.get("timeline") or {}).get("gate_events") or {}) or '<p>No gate events observed.</p>'}</div>
      </div>
      {metric_availability}
      <details class="summary-block"><summary>Complete normalized report data</summary><pre>{escape(json.dumps(raw_payload, indent=2, ensure_ascii=False, sort_keys=True))}</pre></details>
      <p class="footnote">Schema goal-plus-report/v{REPORT_SCHEMA_VERSION}. This file is self-contained and generated from durable Goal Plus/Search state. Host-native transcripts remain external evidence and are summarized only through normalized observability.</p>
    </section>
  </main>
  <script>{_REPORT_SCRIPT}</script>
</body>
</html>
"""


def write_html_report(
    root_dir: Path | str,
    run_id: str,
    output_path: Path | None = None,
) -> Path:
    root = Path(root_dir).resolve()
    destination = output_path or root / "runs" / run_id / "report.html"
    data = build_html_report_data(root, run_id)
    destination.write_text(render_html_report(data), encoding="utf-8")
    return destination
