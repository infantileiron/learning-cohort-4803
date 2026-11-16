import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {field, select} from '../components/common/fields.js';
import {TRIAL_STATE_LABEL, TRIAL_GROUP_LABEL} from '../constants/enums.js';

const pct = x => (x === null || x === undefined) ? '未成熟' : (x * 100).toFixed(1) + '%';
const tbadge = s => `<span class=tag>${esc(TRIAL_STATE_LABEL[s] || s)}</span>`;

function memberTable(entries) {
  return `<details class="members-box"><summary>逐人分组依据（${entries.length}）</summary>
  <div class=scroll><table><thead><tr><th>学员</th><th>分组</th><th>排序号</th><th>SHA-256 依据（前16位）</th></tr></thead><tbody>
  ${entries.map(e => `<tr><td>${esc(e.learner)}</td><td>${esc(TRIAL_GROUP_LABEL[e.group] || e.group)}</td>
   <td>${e.rank}</td><td><code>${esc(e.hash.slice(0, 16))}…</code></td></tr>`).join('')}
  </tbody></table></div><p class="eyebrow">依据串：${esc(entries[0]?.basis || '')}</p></details>`;
}

function conflictBlock(t) {
  const preview = t.state === 'pending' ? t.result?.preview : null;
  if (!preview) return '';
  const conflicts = preview.conflicts || {};
  const approved = preview.approved_members || [];
  const keys = Object.keys(conflicts);
  return `<div class="conflict">
    <h4>发布前隔离核对</h4>
    <p>已签核辅导成员：${approved.length ? approved.map(esc).join('、') : '无'}</p>
    <p>观察期重叠冲突：${keys.length ? keys.map(k => `${esc(k)}→试验#${conflicts[k].map(x => x.trial_id).join(',')}`).join('；') : '无'}</p>
  </div>`;
}

function settleBlock(t) {
  const r = t.result;
  if (t.state !== 'settled' || !r.treatment) return '';
  const tr = r.treatment, cr = r.control;
  return `<div class="settle">
    <h4>结算结果（截至 ${esc(r.as_of)}，分母保留原名单）</h4>
    <div class=scroll><table><thead><tr><th>观察周</th><th>试验组回访率</th><th>对照组回访率</th><th>差值(试验-对照)</th></tr></thead>
    <tbody>${r.weekly_diffs.map(d => `<tr><td>${esc(d.target_week)}</td><td>${pct(d.treatment_rate)} (${tr.weekly.find(w => w.target_week === d.target_week).returned}/${tr.weekly.find(w => w.target_week === d.target_week).denominator})</td>
      <td>${pct(d.control_rate)} (${cr.weekly.find(w => w.target_week === d.target_week).returned}/${cr.weekly.find(w => w.target_week === d.target_week).denominator})</td>
      <td><strong>${d.diff === null ? '—' : (d.diff * 100).toFixed(1) + 'pp'}</strong></td></tr>`).join('')}
      <tr><td><strong>观察期累计</strong></td><td><strong>${pct(tr.cumulative.rate)} (${tr.cumulative.returned}/${tr.cumulative.denominator})</strong></td>
      <td><strong>${pct(cr.cumulative.rate)} (${cr.cumulative.returned}/${cr.cumulative.denominator})</strong></td>
      <td><strong>${(r.cumulative_diff * 100).toFixed(1)}pp</strong></td></tr>
    </tbody></table></div>
    <p class="notice">${esc(r.method)}</p>
  </div>`;
}

function trialCard(t) {
  const a = t.assignment;
  const r = t.result || {};
  return `<article class="rec" data-trial="${t.id}">
    <h3>#${t.id} ${esc(t.name)} ${tbadge(t.state)} ${badge(`队列#${t.cohort_id} ${t.cohort_week}/W${t.week_index}`)}</h3>
    <p>试验组 ${a.treatment.length} 人 · 对照组 ${a.control.length} 人 · 观察 ${t.observe_weeks} 个发布后完整周
      ${r.publish_week ? `· 发布周 ${esc(r.publish_week)} · 期末 ${esc(r.observation_end)}` : ''}</p>
    ${conflictBlock(t)}
    ${settleBlock(t)}
    <div class="trial-groups">
      <div><h4>试验组（生成干预）</h4><p class="members">${a.treatment.map(e => esc(e.learner)).join(' · ')}</p></div>
      <div><h4>对照组（观察期禁签核）</h4><p class="members">${a.control.map(e => esc(e.learner)).join(' · ')}</p></div>
    </div>
    ${memberTable(a.treatment.concat(a.control))}
    <p class="actions">
      ${t.state === 'pending' ? `<button data-publish="${t.id}" class="primary">发布进入观察</button>` : ''}
      ${t.state !== 'pending' && t.state !== 'settled' ? `<button data-settle="${t.id}">结算（输入截至日）</button>` : ''}
      ${t.state === 'settled' ? `<button data-export-trial="${t.id}">导出试验 JSON</button>` : ''}
    </p>
  </article>`;
}

export function render() {
  const e = state.selected;
  const choices = e?.state === 'done'
    ? e.result.rows.flatMap(row => row.cells
        .filter(c => c.week_index > 0 && c.mature && c.missing.length)
        .map(c => [`${row.cohort_week}|${c.week_index}`,
          `${row.iso_label} / W${c.week_index} / 缺席${c.missing.length}人`]))
    : [];
  return `<div class="split">
    <section class="panel">
      <h2>对照试验：把干预效果从自然回访里分出来</h2>
      <p class="notice">从成熟周缺席成员建立试验，按首学周随机均分（奇数差一人，试验组多一人），名单与逐人分组依据立即冻结；失败重试不改换分组。发布排除已签核成员，同一学员观察期重叠的试验互斥；试验组生成干预、对照组观察期禁止签核。按发布后完整周结算两组回访率与差值，分母保留原名单，无事件算未回访，未成熟不结算。</p>
      ${state.trials.map(trialCard).join('') || '<p>尚无对照试验。完成队列后在右侧选择成熟缺席单元格。</p>'}
    </section>
    <aside class="side">
      <h2>建立对照试验</h2>
      <p>当前冻结方案：${esc(e?.name || '无')}</p>
      <form id="trial">
        ${field('试验名称', 'name', 'text', 'required minlength=2 maxlength=100 value="缺席成员辅导对照"')}
        <label>成熟缺席单元格<select name="cell" required>${choices.map(([v, t]) => `<option value="${esc(v)}" ${v === state.cell ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
        ${field('发布后观察完整周数', 'observe_weeks', 'number', 'required min=1 max=8 value=2')}
        <button class="primary" ${choices.length ? '' : 'disabled'}>冻结名单并随机分组</button>
      </form>
      <form id="settle" class="settle-form">
        <h3>结算观察中试验</h3>
        ${select('试验', 'trial_id', state.trials.filter(t => t.state === 'observing').map(t => [String(t.id), `#${t.id} ${t.name}`]))}
        ${field('截至完整业务日', 'as_of', 'date', 'required value="2026-11-14"')}
        <button>计算两组回访率与差值</button>
      </form>
    </aside>
  </div>`;
}
