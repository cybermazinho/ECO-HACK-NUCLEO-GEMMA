(() => {
  "use strict";

  const RISK_META = {
    critico: { label: "Crítico", color: "var(--critico)", bg: "var(--critico-dim)" },
    alto: { label: "Alto", color: "var(--alto)", bg: "var(--alto-dim)" },
    moderado: { label: "Moderado", color: "var(--moderado)", bg: "var(--moderado-dim)" },
    baixo: { label: "Baixo", color: "var(--baixo)", bg: "var(--baixo-dim)" },
  };
  const RISK_ORDER = ["critico", "alto", "moderado", "baixo"];

  const REFRESH_MS = 30000;

  const state = {
    pendencias: [],
    alertas: [],
    sugestoesNutricionais: [],
    openReview: new Set(), // classificacao_id com painel de revisão aberto
    openCases: new Map(), // alerta_id -> array de fichas (cache) ou null enquanto carrega
    openValidarNutri: new Set(), // sugestao_id com painel de validação aberto
    pedindoNutricao: new Set(), // ficha_id com pedido de sugestão em andamento
    lastFilaJSON: "",
    lastAlertasJSON: "",
    lastNutriJSON: "",
  };

  const el = (id) => document.getElementById(id);

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function nl2br(escapedStr) {
    return escapedStr.replace(/\n/g, "<br>");
  }

  function formatRelative(isoStr) {
    const then = new Date(isoStr + (isoStr.endsWith("Z") ? "" : "Z"));
    const diffMs = Date.now() - then.getTime();
    const min = Math.round(diffMs / 60000);
    if (min < 1) return "agora";
    if (min < 60) return `há ${min} min`;
    const h = Math.round(min / 60);
    if (h < 24) return `há ${h} h`;
    const d = Math.round(h / 24);
    return `há ${d} d`;
  }

  // ---------------- data fetching ----------------

  async function fetchJSON(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText} — ${body}`);
    }
    return res.json();
  }

  async function refresh() {
    try {
      const [pendencias, alertas, sugestoesNutricionais] = await Promise.all([
        fetchJSON("/pendencias"),
        fetchJSON("/alertas"),
        fetchJSON("/sugestoes-nutricionais"),
      ]);

      const filaJSON = JSON.stringify(pendencias);
      const alertasJSON = JSON.stringify(alertas);
      const nutriJSON = JSON.stringify(sugestoesNutricionais);

      if (filaJSON !== state.lastFilaJSON) {
        state.pendencias = pendencias;
        state.lastFilaJSON = filaJSON;
        renderFila();
        renderStats();
        renderTabs();
      }
      if (alertasJSON !== state.lastAlertasJSON) {
        state.alertas = alertas;
        state.lastAlertasJSON = alertasJSON;
        renderAlertas();
        renderTabs();
      }
      if (nutriJSON !== state.lastNutriJSON) {
        state.sugestoesNutricionais = sugestoesNutricionais;
        state.lastNutriJSON = nutriJSON;
        renderNutricao();
        renderTabs();
      }
    } catch (err) {
      showConnectionError(err);
    }
  }

  function showConnectionError(err) {
    const msg =
      '<div class="state-msg error">Não foi possível falar com o servidor local. ' +
      "Verifique se o NÚCLEO está ligado e conectado à rede." +
      `<br /><span style="font-family:var(--font-mono);font-size:11px">${escapeHtml(
        String(err.message || err)
      )}</span></div>`;
    el("lista-fila").innerHTML = msg;
    el("lista-alertas").innerHTML = msg;
    el("lista-nutricao").innerHTML = msg;
  }

  // ---------------- stats + tabs ----------------

  function renderStats() {
    const counts = { critico: 0, alto: 0, moderado: 0, baixo: 0 };
    for (const p of state.pendencias) {
      if (counts[p.risco] !== undefined) counts[p.risco]++;
    }
    el("stat-critico").textContent = counts.critico;
    el("stat-alto").textContent = counts.alto;
    el("stat-moderado").textContent = counts.moderado;
    el("stat-baixo").textContent = counts.baixo;
  }

  function renderTabs() {
    el("tab-count-fila").textContent = state.pendencias.length;
    el("tab-count-alertas").textContent = state.alertas.length;
    const hasNovo = state.alertas.some((a) => a.status === "novo");
    el("tab-badge-alertas").hidden = !hasNovo;

    el("tab-count-nutricao").textContent = state.sugestoesNutricionais.length;
    const hasPendente = state.sugestoesNutricionais.some((s) => s.status === "pendente");
    el("tab-badge-nutricao").hidden = !hasPendente;
  }

  function switchView(view) {
    document
      .querySelectorAll(".tab")
      .forEach((t) => t.classList.toggle("active", t.dataset.view === view));
    document
      .querySelectorAll(".view")
      .forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  }

  // ---------------- fila de pendência ----------------

  function filtrarPendencias() {
    const termo = (el("busca-fila")?.value || "").trim().toLowerCase();
    if (!termo) return state.pendencias;
    return state.pendencias.filter((p) =>
      [p.paciente_nome, p.comunidade, p.queixa_texto, p.sintomas, p.especialidade_sugerida, p.risco]
        .some((campo) => (campo || "").toLowerCase().includes(termo))
    );
  }

  function renderFila() {
    const container = el("lista-fila");
    if (state.pendencias.length === 0) {
      container.innerHTML =
        '<div class="state-msg">Fila vazia. Nenhum caso aguardando revisão no momento.</div>';
      return;
    }

    const lista = filtrarPendencias();
    if (lista.length === 0) {
      container.innerHTML = '<div class="state-msg">Nenhum caso encontrado para essa busca.</div>';
      return;
    }

    container.innerHTML = lista.map(cardFilaHTML).join("");

    for (const p of lista) {
      const card = container.querySelector(`[data-card="${p.classificacao_id}"]`);
      if (!card) continue;

      card.querySelector(".btn-revisar")?.addEventListener("click", () => {
        toggleReview(p.classificacao_id);
      });

      card.querySelector(".btn-nutricao")?.addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        state.pedindoNutricao.add(p.ficha_id);
        btn.disabled = true;
        btn.textContent = "Gerando sugestão...";
        try {
          await fetchJSON(`/fichas/${p.ficha_id}/sugestao-nutricional`, { method: "POST" });
          await refresh();
          switchView("nutricao");
        } catch (err) {
          btn.disabled = false;
          btn.textContent = "Sugestão nutricional";
          alert("Não foi possível gerar a sugestão nutricional: " + err.message);
        } finally {
          state.pedindoNutricao.delete(p.ficha_id);
        }
      });

      const panel = card.querySelector(".review-panel");
      if (panel) {
        let selecionado = p.risco;
        panel.querySelectorAll(".pill-option").forEach((btn) => {
          btn.addEventListener("click", () => {
            selecionado = btn.dataset.risco;
            panel
              .querySelectorAll(".pill-option")
              .forEach((b) => b.setAttribute("data-active", String(b === btn)));
          });
        });
        panel.querySelector(".btn-cancelar")?.addEventListener("click", () => {
          state.openReview.delete(p.classificacao_id);
          renderFila();
        });
        panel.querySelector(".btn-salvar")?.addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = "Salvando...";
          try {
            await fetchJSON(`/pendencias/${p.classificacao_id}/revisar`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                risco_corrigido: selecionado,
                obs_medico: panel.querySelector("textarea").value,
              }),
            });
            state.openReview.delete(p.classificacao_id);
            state.pendencias = state.pendencias.filter(
              (x) => x.classificacao_id !== p.classificacao_id
            );
            state.lastFilaJSON = JSON.stringify(state.pendencias);
            renderFila();
            renderStats();
            renderTabs();
          } catch (err) {
            btn.disabled = false;
            btn.textContent = "Salvar revisão";
            alert("Não foi possível salvar a revisão: " + err.message);
          }
        });
      }
    }
  }

  function toggleReview(id) {
    if (state.openReview.has(id)) state.openReview.delete(id);
    else state.openReview.add(id);
    renderFila();
  }

  function cardFilaHTML(p) {
    const risk = RISK_META[p.risco] || RISK_META.moderado;
    const isCritico = p.risco === "critico";
    const isOpen = state.openReview.has(p.classificacao_id);

    return `
      <article class="card${isCritico ? " pulse" : ""}"
                data-card="${p.classificacao_id}"
                style="--risk-color:${risk.color}; --risk-bg:${risk.bg}">
        <div class="card-top">
          <span class="risk-badge" style="--risk-color:${risk.color}; --risk-bg:${risk.bg}">${risk.label}</span>
          <span class="specialty">${escapeHtml(p.especialidade_sugerida || "sem sugestão")}</span>
          <span class="readout">CONF ${p.confianca.toFixed(2)}</span>
        </div>
        <div class="card-patient"><strong>${escapeHtml(p.paciente_nome)}</strong> · ${escapeHtml(p.comunidade)}
          <span class="origem-tag">· registrado por ${escapeHtml(p.origem)}${p.apoiador_nome ? " (" + escapeHtml(p.apoiador_nome) + ")" : ""}</span>
        </div>
        <p class="card-complaint">${nl2br(escapeHtml(p.queixa_texto))}</p>
        ${vitalsStripHTML(p)}
        ${p.imagem_path ? `<img class="card-thumb" src="${p.imagem_path}" alt="Foto anexada ao caso" />` : ""}
        ${
          p.achado_visual
            ? `<p class="card-reasoning"><span class="tag">VISÃO</span>${escapeHtml(p.achado_visual)}</p>`
            : ""
        }
        <p class="card-reasoning"><span class="tag">IA</span>${escapeHtml(p.justificativa)}</p>
        <div class="card-footer">
          <span class="footer-meta">${escapeHtml(p.modelo)} · ${formatRelative(p.criado_em)}
            · <a href="/ficha/${p.ficha_id}" target="_blank" rel="noopener">ficha completa</a>
          </span>
          <div class="review-actions">
            <button class="btn small btn-nutricao">Sugestão nutricional</button>
            <button class="btn ${isOpen ? "ghost" : "primary"} small btn-revisar">
              ${isOpen ? "Fechar" : "Revisar"}
            </button>
          </div>
        </div>
        ${isOpen ? reviewPanelHTML(p) : ""}
      </article>`;
  }

  function vitalsStripHTML(p) {
    const vitais = [];
    vitais.push(`<span class="vital"><span class="k">SINTOMAS</span>${escapeHtml(p.sintomas || "não informado")}</span>`);
    if (p.temperatura_c != null) {
      vitais.push(`<span class="vital"><span class="k">TEMP</span>${p.temperatura_c}°C</span>`);
    } else if (p.febre_relatada) {
      vitais.push(`<span class="vital"><span class="k">FEBRE RELATADA</span>${escapeHtml(p.febre_relatada)}</span>`);
    }
    if (p.pressao_sistolica != null) {
      vitais.push(`<span class="vital"><span class="k">PAS</span>${p.pressao_sistolica} mmHg</span>`);
    }
    if (p.freq_cardiaca != null) {
      vitais.push(`<span class="vital"><span class="k">FC</span>${p.freq_cardiaca} bpm</span>`);
    }
    return `<div class="vitals-strip">${vitais.join("")}</div>`;
  }

  function reviewPanelHTML(p) {
    return `
      <div class="review-panel">
        <div class="review-label">Classificação corrigida</div>
        <div class="pill-options">
          ${RISK_ORDER.map((r) => {
            const m = RISK_META[r];
            return `<button type="button" class="pill-option" data-risco="${r}"
                      data-active="${r === p.risco}"
                      style="--risk-color:${m.color}; --risk-bg:${m.bg}">${m.label}</button>`;
          }).join("")}
        </div>
        <textarea placeholder="Observação do médico (opcional)"></textarea>
        <div class="review-actions">
          <button class="btn primary small btn-salvar">Salvar revisão</button>
          <button class="btn ghost small btn-cancelar">Cancelar</button>
        </div>
      </div>`;
  }

  // ---------------- alertas coletivos ----------------

  function renderAlertas() {
    const container = el("lista-alertas");
    if (state.alertas.length === 0) {
      container.innerHTML =
        '<div class="state-msg">Nenhum padrão coletivo detectado. ' +
        "A varredura roda sozinha a cada poucos minutos — novos alertas aparecem aqui automaticamente.</div>";
      return;
    }

    container.innerHTML = state.alertas.map(alertaCardHTML).join("");

    for (const a of state.alertas) {
      const card = container.querySelector(`[data-alert="${a.id}"]`);
      if (!card) continue;

      card.querySelector(".btn-ver-casos")?.addEventListener("click", () => toggleCasos(a));
      card.querySelector(".btn-avaliar")?.addEventListener("click", () => avaliarAlerta(a.id, "avaliado"));
      card.querySelector(".btn-descartar")?.addEventListener("click", () => avaliarAlerta(a.id, "descartado"));
    }
  }

  function alertaCardHTML(a) {
    const ficha_ids = a.ficha_ids.split(",").filter(Boolean);
    const casos = state.openCases.get(a.id);
    const casosAbertos = state.openCases.has(a.id);

    return `
      <article class="alert-card ${a.status}" data-alert="${a.id}">
        <div class="alert-top">
          <span class="alert-community">${escapeHtml(a.comunidade)}</span>
          <span class="alert-status">${escapeHtml(a.status)}</span>
        </div>
        <h3 class="alert-title">${escapeHtml(a.titulo)}</h3>
        <p class="alert-desc">${escapeHtml(a.descricao)}</p>
        <div class="alert-footer">
          <span class="readout">${ficha_ids.length} casos · SIMIL ${a.score_similaridade.toFixed(2)} · ${formatRelative(a.criado_em)}</span>
          <div class="alert-actions">
            <button class="btn small btn-ver-casos">${casosAbertos ? "Ocultar casos" : "Ver casos"}</button>
            <button class="btn small btn-avaliar">Marcar avaliado</button>
            <button class="btn ghost small btn-descartar">Descartar</button>
          </div>
        </div>
        ${casosAbertos ? casosListHTML(casos) : ""}
      </article>`;
  }

  function casosListHTML(estado) {
    if (estado === null) {
      return '<div class="cases-list"><span class="readout">Carregando casos...</span></div>';
    }
    const { casos, falhas } = estado;
    if (casos.length === 0) {
      return '<div class="cases-list"><div class="state-msg error">Não foi possível carregar nenhum dos casos deste alerta.</div></div>';
    }
    return (
      '<div class="cases-list">' +
      casos
        .map((f) => {
          const risco = f.classificacao?.risco || "moderado";
          const m = RISK_META[risco] || RISK_META.moderado;
          return `<div class="mini-case">
            <span class="mini-risk" style="color:${m.color}">${m.label}</span>
            <span class="mini-text"><a href="/ficha/${f.id}" target="_blank" rel="noopener"><strong>${escapeHtml(f.paciente_nome || "paciente")}</strong></a> — ${escapeHtml(f.queixa_texto)}</span>
          </div>`;
        })
        .join("") +
      (falhas > 0
        ? `<div class="readout">${falhas} caso(s) não puderam ser carregados (ficha removida ou indisponível).</div>`
        : "") +
      "</div>"
    );
  }

  async function toggleCasos(alerta) {
    if (state.openCases.has(alerta.id)) {
      state.openCases.delete(alerta.id);
      renderAlertas();
      return;
    }
    state.openCases.set(alerta.id, null);
    renderAlertas();

    const ids = alerta.ficha_ids.split(",").filter(Boolean);
    const resultados = await Promise.allSettled(ids.map((id) => fetchJSON(`/fichas/${id}`)));
    const casos = resultados.filter((r) => r.status === "fulfilled").map((r) => r.value);
    const falhas = resultados.length - casos.length;
    state.openCases.set(alerta.id, { casos, falhas });
    renderAlertas();
  }

  async function avaliarAlerta(id, status) {
    try {
      await fetchJSON(`/alertas/${id}/avaliar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      const idx = state.alertas.findIndex((a) => a.id === id);
      if (idx !== -1) state.alertas[idx].status = status;
      state.lastAlertasJSON = JSON.stringify(state.alertas);
      renderAlertas();
      renderTabs();
    } catch (err) {
      alert("Não foi possível atualizar o alerta: " + err.message);
    }
  }

  // ---------------- apoio nutricional ----------------

  function foodChipsHTML(csv, classe) {
    const itens = (csv || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (itens.length === 0) return '<span class="footer-meta">nenhum informado</span>';
    return (
      '<div class="food-chips">' +
      itens.map((i) => `<span class="food-chip${classe ? " " + classe : ""}">${escapeHtml(i)}</span>`).join("") +
      "</div>"
    );
  }

  function renderNutricao() {
    const container = el("lista-nutricao");
    if (state.sugestoesNutricionais.length === 0) {
      container.innerHTML =
        '<div class="state-msg">Nenhuma sugestão nutricional gerada ainda. ' +
        'Peça uma pelo botão "Sugestão nutricional" em um caso na fila de pendência.</div>';
      return;
    }

    container.innerHTML = state.sugestoesNutricionais.map(nutriCardHTML).join("");

    for (const s of state.sugestoesNutricionais) {
      const card = container.querySelector(`[data-nutri="${s.id}"]`);
      if (!card) continue;

      card.querySelector(".btn-validar-nutri")?.addEventListener("click", () => {
        toggleValidarNutri(s.id);
      });
      card.querySelector(".btn-salvar-nutri")?.addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = "Salvando...";
        try {
          await fetchJSON(`/sugestoes-nutricionais/${s.id}/validar`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              obs_profissional: card.querySelector("textarea")?.value || "",
            }),
          });
          state.openValidarNutri.delete(s.id);
          await refresh();
        } catch (err) {
          btn.disabled = false;
          btn.textContent = "Confirmar validação";
          alert("Não foi possível validar a sugestão: " + err.message);
        }
      });
      card.querySelector(".btn-cancelar-nutri")?.addEventListener("click", () => {
        state.openValidarNutri.delete(s.id);
        renderNutricao();
      });
    }
  }

  function toggleValidarNutri(id) {
    if (state.openValidarNutri.has(id)) state.openValidarNutri.delete(id);
    else state.openValidarNutri.add(id);
    renderNutricao();
  }

  function nutriCardHTML(s) {
    const isOpen = state.openValidarNutri.has(s.id);
    return `
      <article class="nutri-card ${s.status}" data-nutri="${s.id}">
        <div class="alert-top">
          <span class="nutri-label">${escapeHtml(s.comunidade)}</span>
          <span class="alert-status">${escapeHtml(s.status)}</span>
        </div>
        <h3 class="alert-title">${escapeHtml(s.paciente_nome)}</h3>
        <p class="card-complaint">${escapeHtml(s.queixa_texto)}</p>
        ${s.alergias ? `<p class="footer-meta">Alergias conhecidas: ${escapeHtml(s.alergias)}</p>` : ""}
        <p class="alert-desc">${escapeHtml(s.recomendacao_geral)}</p>

        <div class="mini-label">Alimentos sugeridos</div>
        ${foodChipsHTML(s.alimentos_sugeridos)}

        <div class="mini-label">Evitar</div>
        ${foodChipsHTML(s.alimentos_evitar, "evitar")}

        <p class="card-reasoning"><span class="tag">IA</span>${escapeHtml(s.justificativa)}</p>

        <div class="alert-footer">
          <span class="readout">CONF ${s.confianca.toFixed(2)} · ${escapeHtml(s.modelo)} · ${formatRelative(s.criado_em)}</span>
          ${
            s.status === "pendente"
              ? `<button class="btn ${isOpen ? "ghost" : "primary"} small btn-validar-nutri">${isOpen ? "Fechar" : "Validar sugestão"}</button>`
              : `<span class="footer-meta">validado</span>`
          }
        </div>
        ${isOpen ? nutriValidarPanelHTML() : ""}
      </article>`;
  }

  function nutriValidarPanelHTML() {
    return `
      <div class="review-panel">
        <div class="review-label">Observação do profissional (opcional)</div>
        <textarea placeholder="Ex: ajustei a sugestão, removi item X, orientei paciente pessoalmente..."></textarea>
        <div class="review-actions">
          <button class="btn primary small btn-salvar-nutri">Confirmar validação</button>
          <button class="btn ghost small btn-cancelar-nutri">Cancelar</button>
        </div>
      </div>`;
  }

  // ---------------- novo caso (formulário de entrada) ----------------
  // A lógica de paciente/mídia/envio é compartilhada com as telas de
  // apoiador e paciente — ver static/triage-form.js.

  function initFormNovoCaso() {
    NucleoTriageForm.init({
      origem: "medico",
      onSuccess: (ficha) => {
        el("resultado-caso").innerHTML = NucleoTriageForm.resultadoCasoHTML(ficha, {
          mostrarBotaoFila: true,
        });
        el("btn-ver-na-fila")?.addEventListener("click", () => switchView("fila"));
        refresh();
      },
    });
  }

  // ---------------- boot ----------------

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  el("busca-fila")?.addEventListener("input", () => renderFila());

  initFormNovoCaso();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
