/* Lógica de formulário de triagem compartilhada entre as 3 telas de entrada:
   painel do médico (app.js), apoiador (apoiador.html) e paciente (paciente.html).

   Contrato de IDs no DOM (cada tela só renderiza os campos que faz sentido
   para o seu contexto — o que não existe é simplesmente ignorado aqui):
     #segmented-paciente, #select-paciente, #bloco-paciente-existente,
     #bloco-paciente-novo, #np-nome, #np-idade, #np-sexo, #np-comunidade,
     #np-alergias, #f-queixa, #f-sintomas, #f-temp (numérico) OU #f-febre
     (select nenhuma/media/alta — usado quando não há termômetro), #f-pas,
     #f-fc, #f-apoiador, #f-imagem, #preview-imagem, #btn-limpar-imagem,
     #form-novo-caso, #btn-enviar-caso, #resultado-caso */

window.NucleoTriageForm = (() => {
  "use strict";

  const RISK_META = {
    critico: { label: "Crítico", color: "var(--critico)", bg: "var(--critico-dim)" },
    alto: { label: "Alto", color: "var(--alto)", bg: "var(--alto-dim)" },
    moderado: { label: "Moderado", color: "var(--moderado)", bg: "var(--moderado-dim)" },
    baixo: { label: "Baixo", color: "var(--baixo)", bg: "var(--baixo-dim)" },
  };

  const el = (id) => document.getElementById(id);

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  async function fetchJSON(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText} — ${body}`);
    }
    return res.json();
  }

  // ---- paciente: seleção existente / novo ----

  let pacientes = [];

  async function fetchPacientes(selecionarId) {
    if (!el("select-paciente")) return;
    try {
      pacientes = await fetchJSON("/pacientes");
      renderSelectPacientes(selecionarId);
    } catch (err) {
      // lista é conveniência; falha aqui não deve travar a tela
    }
  }

  function renderSelectPacientes(selecionarId) {
    const select = el("select-paciente");
    if (!select) return;
    const atual = selecionarId ?? select.value;
    select.innerHTML =
      '<option value="">Selecione o paciente…</option>' +
      pacientes
        .map((p) => `<option value="${p.id}">${escapeHtml(p.nome)} — ${escapeHtml(p.comunidade)}</option>`)
        .join("");
    if (atual) select.value = String(atual);
  }

  function setModoPaciente(modo) {
    document
      .querySelectorAll("#segmented-paciente .segmented-opt")
      .forEach((b) => b.classList.toggle("active", b.dataset.modo === modo));
    if (el("bloco-paciente-existente")) el("bloco-paciente-existente").hidden = modo !== "existente";
    if (el("bloco-paciente-novo")) el("bloco-paciente-novo").hidden = modo !== "novo";
  }

  function modoPacienteAtual() {
    const ativo = document.querySelector("#segmented-paciente .segmented-opt.active");
    return ativo ? ativo.dataset.modo : "existente";
  }

  // ---- mídia: foto anexada como arquivo ----

  const media = { imagemFile: null };

  function resetMediaUI() {
    media.imagemFile = null;
    if (el("f-imagem")) el("f-imagem").value = "";
    if (el("preview-imagem")) {
      el("preview-imagem").hidden = true;
      el("preview-imagem").removeAttribute("src");
    }
    if (el("btn-limpar-imagem")) el("btn-limpar-imagem").hidden = true;
  }

  function initMediaCaptura() {
    const input = el("f-imagem");
    if (!input) return;

    input.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      media.imagemFile = file;
      const preview = el("preview-imagem");
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      el("btn-limpar-imagem").hidden = false;
    });

    el("btn-limpar-imagem")?.addEventListener("click", () => {
      media.imagemFile = null;
      input.value = "";
      el("preview-imagem").hidden = true;
      el("btn-limpar-imagem").hidden = true;
    });
  }

  // ---- resultado (compartilhado entre as 3 telas) ----

  function foodChipsHTML(csv, classe) {
    const itens = (csv || "").split(",").map((s) => s.trim()).filter(Boolean);
    if (itens.length === 0) return "";
    return (
      '<div class="food-chips">' +
      itens.map((i) => `<span class="food-chip${classe ? " " + classe : ""}">${escapeHtml(i)}</span>`).join("") +
      "</div>"
    );
  }

  function sugestaoNutricionalHTML(s) {
    if (!s) return "";
    return `
      <div class="nutri-card pendente" style="margin-top:12px">
        <div class="nutri-label">Sugestão nutricional (ainda não validada por um profissional)</div>
        <p class="alert-desc">${escapeHtml(s.recomendacao_geral)}</p>
        <div class="mini-label">Alimentos sugeridos</div>
        ${foodChipsHTML(s.alimentos_sugeridos)}
        <div class="mini-label">Evitar</div>
        ${foodChipsHTML(s.alimentos_evitar, "evitar")}
      </div>`;
  }

  function apoioImediatoHTML(ficha) {
    if (!ficha.apoio_imediato) return "";
    const risco = ficha.classificacao ? ficha.classificacao.risco : "alto";
    const m = RISK_META[risco] || RISK_META.alto;
    return `
      <div class="result-panel pulse" style="--risk-color:${m.color}; margin-bottom:12px">
        <div class="result-title" style="color:${m.color}">Procure ajuda agora</div>
        <p class="card-complaint">${escapeHtml(ficha.apoio_imediato)}</p>
      </div>`;
  }

  function resultadoCasoHTML(ficha, { mostrarBotaoFila = false, mostrarClassificacao = true } = {}) {
    const c = ficha.classificacao;
    if (!mostrarClassificacao) {
      // Tela do paciente: não expõe risco/confiança cru sem um profissional
      // por perto para contextualizar — só confirma que o caso foi recebido.
      // Mas se for alto/crítico, a mensagem de "procure ajuda agora" aparece
      // primeiro e com destaque — não é hora de só esperar passivamente.
      // A sugestão nutricional (quando a queixa é sobre alimentação/peso) já
      // aparece aqui na hora, mesmo antes da validação do profissional.
      return `
        ${apoioImediatoHTML(ficha)}
        <div class="result-panel" style="--risk-color:var(--cyan)">
          <div class="result-title">Recebido</div>
          <p class="card-complaint">Seu caso foi registrado. Um profissional de saúde vai revisar as informações em breve.</p>
        </div>
        ${sugestaoNutricionalHTML(ficha.sugestao_nutricional)}`;
    }
    if (!c) {
      return '<div class="result-panel">Caso registrado, mas a classificação automática não retornou — será revisado manualmente.</div>';
    }
    const m = RISK_META[c.risco] || RISK_META.moderado;
    return `
      <div class="result-panel" style="--risk-color:${m.color}">
        <div class="result-title">Classificado pela IA</div>
        <div class="card-top" style="margin-bottom:8px">
          <span class="risk-badge" style="--risk-color:${m.color}; --risk-bg:${m.bg}">${m.label}</span>
          <span class="specialty">${escapeHtml(c.especialidade_sugerida || "sem sugestão")}</span>
          <span class="readout">CONF ${c.confianca.toFixed(2)}</span>
        </div>
        ${ficha.imagem_path ? `<img class="card-thumb" src="${ficha.imagem_path}" alt="Foto anexada ao caso" />` : ""}
        ${
          c.achado_visual
            ? `<p class="card-reasoning"><span class="tag">VISÃO</span>${escapeHtml(c.achado_visual)}</p>`
            : ""
        }
        <p class="card-reasoning"><span class="tag">IA</span>${escapeHtml(c.justificativa)}</p>
        <div class="form-actions" style="margin-top:12px">
          ${
            mostrarBotaoFila
              ? '<button type="button" class="btn small" id="btn-ver-na-fila">Ver na fila de pendência</button>'
              : ""
          }
          <span class="form-hint">Este caso foi registrado e aguarda revisão de um profissional de saúde.</span>
        </div>
      </div>
      ${sugestaoNutricionalHTML(ficha.sugestao_nutricional)}`;
  }

  // ---- init ----

  function init(options) {
    const opts = Object.assign(
      { origem: "medico", apoiadorObrigatorio: false, onSuccess: () => {} },
      options
    );

    initMediaCaptura();

    document.querySelectorAll("#segmented-paciente .segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => setModoPaciente(btn.dataset.modo));
    });

    fetchPacientes();

    const form = el("form-novo-caso");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const resultado = el("resultado-caso");
      const btn = el("btn-enviar-caso");
      resultado.innerHTML = "";

      const queixa = el("f-queixa").value.trim();
      if (!queixa) {
        resultado.innerHTML = '<div class="state-msg error">Descreva a queixa antes de enviar.</div>';
        el("f-queixa").focus();
        return;
      }

      const apoiadorInput = el("f-apoiador");
      if (opts.apoiadorObrigatorio && apoiadorInput && !apoiadorInput.value.trim()) {
        resultado.innerHTML = '<div class="state-msg error">Informe seu nome antes de enviar.</div>';
        apoiadorInput.focus();
        return;
      }

      let pacienteId = null;
      try {
        if (modoPacienteAtual() === "existente") {
          pacienteId = el("select-paciente").value;
          if (!pacienteId) {
            resultado.innerHTML =
              '<div class="state-msg error">Selecione um paciente cadastrado, ou troque para "Novo paciente".</div>';
            return;
          }
        } else {
          const nome = el("np-nome").value.trim();
          const idade = el("np-idade").value;
          const sexo = el("np-sexo").value;
          const comunidade = el("np-comunidade").value.trim();
          if (!nome || !idade || !sexo || !comunidade) {
            resultado.innerHTML =
              '<div class="state-msg error">Preencha nome, idade, sexo e comunidade.</div>';
            return;
          }
          btn.disabled = true;
          btn.textContent = "Cadastrando paciente...";
          const paciente = await fetchJSON("/pacientes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              nome,
              idade: Number(idade),
              sexo,
              comunidade,
              alergias: el("np-alergias")?.value.trim() || "",
            }),
          });
          pacienteId = paciente.id;
          pacientes.push(paciente);
        }

        btn.disabled = true;
        btn.textContent = "Classificando com IA...";

        const formData = new FormData();
        formData.append("paciente_id", String(pacienteId));
        formData.append("queixa_texto", queixa);
        formData.append("sintomas", el("f-sintomas")?.value.trim() || "");
        formData.append("origem", opts.origem);
        if (apoiadorInput && apoiadorInput.value.trim()) {
          formData.append("apoiador_nome", apoiadorInput.value.trim());
        }

        const tempInput = el("f-temp");
        const febreInput = el("f-febre");
        if (tempInput && tempInput.value) {
          formData.append("temperatura_c", tempInput.value);
        } else if (febreInput && febreInput.value) {
          formData.append("febre_relatada", febreInput.value);
        }
        if (el("f-pas")?.value) formData.append("pressao_sistolica", el("f-pas").value);
        if (el("f-fc")?.value) formData.append("freq_cardiaca", el("f-fc").value);
        if (media.imagemFile) formData.append("imagem", media.imagemFile);

        const ficha = await fetchJSON("/fichas/upload", { method: "POST", body: formData });

        el("f-queixa").value = "";
        if (el("f-sintomas")) el("f-sintomas").value = "";
        if (tempInput) tempInput.value = "";
        if (febreInput) febreInput.value = "";
        if (el("f-pas")) el("f-pas").value = "";
        if (el("f-fc")) el("f-fc").value = "";
        resetMediaUI();
        renderSelectPacientes(pacienteId);
        setModoPaciente("existente");

        opts.onSuccess(ficha);
      } catch (err) {
        resultado.innerHTML = `<div class="state-msg error">Não foi possível registrar o caso: ${escapeHtml(
          err.message
        )}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = "Enviar para triagem";
      }
    });
  }

  return { init, resultadoCasoHTML, escapeHtml, fetchJSON };
})();
