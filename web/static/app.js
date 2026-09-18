(() => {
  const ROOT = document.body.dataset.root || '';
  const EMBED = document.body.dataset.embed === '1';
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dec = (x, d = 2) => Number(x).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
  const tokens = (n) => n >= 1e9 ? dec(n / 1e9, n >= 1e11 ? 0 : 1) + ' Md' : n >= 1e6 ? dec(n / 1e6, 0) + ' M' : dec(n / 1e3, 0) + ' k';
  const ctx = (n) => !n ? '—' : n >= 1e6 ? dec(n / 1e6, n % 1e6 ? 1 : 0) + ' M' : Math.round(n / 1e3) + ' k';
  const SHORT = { aa_intelligence: 'AA', aa_coding: 'AA', aa_agentic: 'AA', lmarena_text: 'LMArena', lmarena_coding: 'LMArena code', lmarena_webdev: 'LMArena WebDev', lmarena_agent: 'LMArena agent', epoch_eci: 'Epoch',
    lmarena_creative_writing: 'LMArena rédaction', lmarena_instruction_following: 'LMArena consignes',
    lmarena_french: 'LMArena français', lmarena_longer_query: 'LMArena requêtes longues' };

  if (EMBED && window.parent !== window) {
    // Contrat du shortcode creapulse_tool (autoheight) : type 'vsg-height'
    const send = () => window.parent.postMessage({ type: 'vsg-height', height: Math.ceil(document.body.getBoundingClientRect().height) }, '*');
    new ResizeObserver(send).observe(document.body);
  }

  const buttons = () => document.querySelectorAll('button');
  function busy(on, text) {
    $('#status').hidden = !on;
    if (text) $('#status-text').textContent = text;
    buttons().forEach((b) => { b.disabled = on; });
    if (on) $('#error').hidden = true;
  }
  function fail(message) {
    $('#error').textContent = message;
    $('#error').hidden = false;
    $('#error').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  async function call(url, options) {
    busy(true, options && options.method === 'POST' ? 'Je traduis votre question en filtres…' : 'Calcul en cours…');
    try {
      const resp = await fetch(url, options);
      const data = await resp.json().catch(() => ({ ok: false, message: 'Réponse illisible du serveur.' }));
      if (!data.ok) return fail(data.message || 'Une erreur est survenue.');
      render(data);
    } catch (e) {
      fail('Connexion impossible. Réessayez dans un instant.');
    } finally {
      busy(false);
    }
  }

  function fillForm(q) {
    const f = $('#filters');
    f.task.value = q.task; f.sort.value = q.sort;
    f.author.value = (q.authors && q.authors.length === 1) ? q.authors[0] : '';
    f.min_context.value = [128000, 200000, 400000, 1000000].includes(q.min_context) ? String(q.min_context) : '';
    f.max_price.value = q.max_price ?? '';
    f.min_quality.value = [50, 60, 70, 80].includes(q.min_quality) ? String(q.min_quality) : '';
    f.open_weights.checked = !!q.open_weights; f.tools.checked = !!q.tools;
    f.input.checked = (q.input_modalities || []).includes('image');
    f.min_sources.checked = q.min_sources >= 2;
    f.max_latency_ms.value = [500, 1000, 2000, 5000].includes(q.max_latency_ms) ? String(q.max_latency_ms) : '';
    f.min_throughput.value = [30, 50, 80].includes(q.min_throughput) ? String(q.min_throughput) : '';
  }

  function render(data) {
    const res = data.result;
    fillForm(data.query);
    $('#summary').textContent = data.summary;
    $('#understood').innerHTML = '<span>Compris comme :</span>' + data.understood.map((u) => `<span class="chip">${esc(u)}</span>`).join('');
    const FAST = data.query.sort === 'fast';
    const scoreOf = (r) => (FAST ? r.fast_index : r.value_index) || 0;
    $('#score-head').textContent = FAST ? 'Score vitesse-qualité-prix' : 'Score qualité-prix';
    $('#rows').innerHTML = res.results.length ? res.results.map((r) => {
      const detail = Object.entries(r.quality_detail).map(([s, v]) => `${SHORT[s] || s} ${dec(v.percentile, 0)}`).join(' · ');
      const u = r.usage ? `${tokens(r.usage.tokens_per_day)}<small>${r.usage.rank}ᵉ</small>` : '—';
      const sp = r.speed || {};
      const lat = sp.latency_ms != null ? `${dec(sp.latency_ms / 1000, 1)} s` : '—';
      const fp = sp.fastest_provider;
      const best = fp && fp.throughput_tps != null && sp.throughput_tps != null && fp.throughput_tps > sp.throughput_tps * 1.15
        ? `<small title="Hébergeur le plus rapide">max ${dec(fp.throughput_tps, 0)} (${esc(fp.provider)})</small>` : '';
      const tps = sp.throughput_tps != null ? `${dec(sp.throughput_tps, 0)} t/s${best}` : '—';
      return `<tr>
        <td>${r.position}</td>
        <td class="model"><strong>${esc(r.name)}${r.open_weights ? '<span class="ow" title="Poids ouverts">open</span>' : ''}</strong><small>${esc(r.author)}</small></td>
        <td class="q">${dec(r.quality, 0)}<small>${r.n_sources}/${r.n_sources_possible} sources : ${esc(detail)}</small></td>
        <td class="num">${dec(r.blended_price_per_m)}</td>
        <td class="num">${dec(r.price_in_per_m)} / ${dec(r.price_out_per_m)}</td>
        <td class="num">${ctx(r.context_length)}</td>
        <td class="num">${lat}</td>
        <td class="num">${tps}</td>
        <td class="num">${u}</td>
        <td class="num">${scoreOf(r)}<small>/100</small><div class="bar"><span style="width:${scoreOf(r)}%"></span></div></td>
      </tr>`;
    }).join('') : '<tr><td colspan="10">Aucun modèle ne passe ces filtres. Essayez d\'en relâcher un.</td></tr>';

    const f = res.formula;
    const us = res.usage_source;
    const span = us.days > 1 ? `moyenne du ${esc(us.first_date)} au ${esc(us.date)} (${us.days} jours)` : `journée du ${esc(us.date)}`;
    $('#formula').innerHTML = `
      <p><strong>Formule</strong> : score = <code>Q × (1 + ${dec(f.alpha)} × A / 100) ÷ max(P ; 0,05)^${dec(f.beta, 1)}</code></p>
      <ul>
        <li>Q = ${esc(f.Q)}</li>
        <li>A = ${esc(f.A)}</li>
        <li>P = ${esc(f.P)} (${esc(f.P_note).replace(`« ${esc(f.P_usage)} »`, `« <strong>${esc(f.P_usage)}</strong> »`)})</li>
        ${f.fast ? `<li>Tri rapide : ${esc(f.fast)}, avec R = ${esc(f.R)}</li>` : ''}
        <li>${esc(f.index.charAt(0).toUpperCase() + f.index.slice(1))}</li>
      </ul>
      <p style="margin-top:10px"><strong>Sources</strong> (${res.total_candidates} modèles classés après filtres)</p>
      <ul>${res.sources.map((s) => `<li>${esc(s.label)}, données du ${esc(s.date)}</li>`).join('')}
        <li>${esc(us.label)}, ${span}</li>
        <li>${esc(res.prices_source.label)}, prix du ${esc(res.prices_source.date)}</li>
        ${res.speed_source && res.speed_source.days ? `<li>${esc(res.speed_source.label)}, ${res.speed_source.days > 1 ? `moyenne du ${esc(res.speed_source.first_date)} au ${esc(res.speed_source.date)}` : `mesure du ${esc(res.speed_source.date)}`}</li>` : ''}
      </ul>`;
    const un = res.unscored_popular || [];
    $('#unscored').hidden = !un.length;
    $('#unscored').textContent = un.length ? 'Très utilisés mais pas encore évalués pour cette tâche (donc non classés) : ' + un.map((m) => `${m.name} (${m.usage_rank}ᵉ en usage)`).join(', ') + '.' : '';
    $('#caveats').innerHTML = (res.warnings || []).map((w) => `<p><strong>${esc(w)}</strong></p>`).join('')
      + res.caveats.map((c) => `<p>${esc(c)}</p>`).join('');
    $('#result').hidden = false;
    $('#result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function filtersQuery() {
    const params = new URLSearchParams();
    new FormData($('#filters')).forEach((v, k) => { if (v !== '') params.append(k, v); });
    return params;
  }

  $('#filters').addEventListener('submit', (e) => {
    e.preventDefault();
    call(`${ROOT}/api/recommend?${filtersQuery()}`);
  });

  const askForm = $('#ask-form');
  if (askForm) {
    askForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const question = $('#question').value.trim();
      if (!question) return fail('Écrivez votre question, ou réglez les filtres ci-dessous.');
      call(`${ROOT}/api/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) });
    });
    // Exemples : requêtes prédéfinies, aucun appel LLM
    document.querySelectorAll('.example').forEach((btn) => btn.addEventListener('click', () => {
      $('#question').value = btn.dataset.question;
      const q = JSON.parse(btn.dataset.query);
      const params = new URLSearchParams();
      Object.entries(q).forEach(([k, v]) => params.append(k, String(v)));
      call(`${ROOT}/api/recommend?${params}`);
    }));
  }
})();
