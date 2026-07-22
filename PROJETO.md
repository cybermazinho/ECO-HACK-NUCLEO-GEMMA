# NÚCLEO — IA Clínica onde a internet não chega

Protótipo funcional de uma "maleta" de IA clínica para operação 100% offline em
comunidades remotas: triagem assistida, análise de imagem, apoio nutricional,
detecção de problemas coletivos e prontuário — tudo rodando local, sem depender
de internet, com toda saída de IA sujeita a revisão humana.

Conceito original: `NUCLEO_IA_Clinica_Visao_Geral.pdf` (deck de apresentação).
Este documento descreve o que foi efetivamente implementado.

## Stack

| Camada | Tecnologia |
|---|---|
| Modelo de IA | **Gemma 4 E4B** (`gemma4:e4b`), multimodal texto+imagem, via **Ollama** local |
| Backend | **Python 3.12** + **FastAPI** 0.115 + **Uvicorn** |
| Banco de dados | **SQLite** via **SQLAlchemy** 2.0 (arquivo único `nucleo.db`, zero infra) |
| Validação/schemas | **Pydantic** 2.10 |
| Job agendado | **APScheduler** (roda o cruzamento de dados a cada 10 min, in-process) |
| Detecção de similaridade | **scikit-learn** (TF-IDF + cosseno) combinado com Jaccard sobre sintomas — sem GPU, sem embeddings |
| Upload de arquivos | `python-multipart` (fotos anexadas às fichas) |
| Frontend | **HTML/CSS/JS vanilla**, sem framework, sem build step, sem fontes externas (tudo precisa funcionar offline) |
| Cliente Gemma | `requests` puro contra a API REST do Ollama (`/api/generate`) |

Por que essa stack: o hardware alvo real da maleta (PDF) é um mini-PC
**Ryzen 7 7730U sem GPU dedicada** — por isso tudo foi escolhido para rodar bem
em CPU (SQLite em vez de Postgres nesta fase, TF-IDF em vez de embeddings
vetoriais, sem build de frontend, sem dependência de CDN).

## O que foi construído

### 1. Triagem clínica com IA (`/`, `/apoiador`, `/paciente`)
Três telas de entrada, mesma engine (`triage-form.js` compartilhado):
- **Painel do médico** (`/`) — cadastro completo + fila + alertas + nutrição
- **Apoiador de campo** (`/apoiador`) — agente de saúde registra o caso, com campo obrigatório de identificação (`apoiador_nome`)
- **Autoatendimento do paciente** (`/paciente`) — sem campo de pressão (exige aparelho), febre como menu (nenhuma/média/alta) em vez de termômetro, e o resultado não expõe risco cru — só confirma recebimento

O Gemma classifica cada ficha em `baixo/moderado/alto/crítico`, com
especialidade sugerida, justificativa e confiança. Se a foto de uma lesão é
anexada, o Gemma analisa a imagem também (campo `achado_visual` separado, para
o médico conferir se a IA "viu" o que diz ter visto).

Casos **alto/crítico** vindos do autoatendimento do paciente disparam uma
mensagem imediata de "procure ajuda agora" — sem sugerir remédio ou conduta,
só orientando buscar presencial sem esperar.

### 2. Fila de pendência (revisão humana obrigatória)
Todo caso cai pendente até o médico revisar/corrigir. A fila mostra:
- Classificação da IA + confiança + modelo (rastreabilidade)
- Os **dados brutos informados** (sintomas, vitais, quem registrou) — não só a conclusão da IA
- Busca por paciente/comunidade/sintoma/queixa
- Link direto pra "ficha completa" de cada caso

### 3. Apoio nutricional
Sob demanda (botão na fila) ou automático (autoatendimento, se a queixa for
sobre alimentação/peso — detectado por palavras-chave). Sugestão respeita
alergias cadastradas e prioriza alimentos acessíveis em comunidades rurais.
Fica pendente até validação do profissional.

### 4. Detecção de problema coletivo
Job que roda sozinho a cada 10 min: agrupa fichas recentes da mesma comunidade
por similaridade de sintomas (TF-IDF + Jaccard, sem chamar IA para detectar —
só para descrever o cluster já encontrado). Mostra quais pacientes (nome +
queixa) formam o grupo, com link pra ficha de cada um.

### 5. Ficha completa (`/ficha/{id}`)
Visão consolidada: paciente, dados brutos, classificação (+ correção do
médico), sugestões nutricionais vinculadas — o "prontuário" do PDF.

## Limites conhecidos / decisões conscientes

- **Áudio não é multimodal de verdade**: o Ollama ainda não expõe entrada de
  áudio na API (mesmo o Gemma 4 sendo capaz disso) — por isso removemos a
  gravação de voz em vez de fingir que é nativa.
- **HTTPS**: gravação de mídia ao vivo via navegador exige contexto seguro
  (localhost ou HTTPS) — em `http://<ip>:8010` na rede local isso não
  funcionaria; hoje contornado usando inputs de arquivo simples (`<input
  type=file>`), que não têm essa exigência.
- **Prompt do Gemma é sensível a tamanho**: descobrimos empiricamente que
  prompts longos fazem o modelo (pequeno, E4B) ignorar a imagem anexada —
  o prompt de classificação com foto é deliberadamente compacto.
- **SQLite é suficiente para o protótipo**, mas não tem migração de schema
  (mudanças de coluna exigem recriar o banco) — ok para fase de validação,
  precisa de Alembic ou similar antes de produção real.

## Benchmark: GPU vs. CPU-only

Testado em 2026-07-22, mesma máquina, mesmos prompts exatos nos dois lados —
uma instância Ollama normal (GPU) e uma segunda instância temporária forçada a
`OLLAMA_LLM_LIBRARY=cpu` + `CUDA_VISIBLE_DEVICES=""` (confirmado via `ollama
ps`: `100% GPU` vs `100% CPU`), ambas servindo `gemma4:e4b`.

| Cenário | GPU (RTX 5070) | CPU-only (16 threads) | Fator |
|---|---|---|---|
| Texto livre longo (~300 palavras) | **12.4s** | **112.4s** | ~9x |
| Classificação de triagem (texto, JSON curto) | **1.28s** | **11.1s** | ~8.6x |
| Classificação de triagem + foto anexada | **2.14s** | **~15-17s** | ~7-8x |

Máquina de teste: CPU **AMD Ryzen 7 7700X** (8C/16T, **desktop**), GPU NVIDIA
RTX 5070 (12GB). O primeiro teste em CPU incluiu o carregamento inicial do
modelo (9.5GB) pra RAM; os dois seguintes já estavam com o modelo quente
(`OLLAMA_KEEP_ALIVE` default 5 min) — por isso são a comparação mais justa.

**Ressalva importante para o hardware alvo real:** este teste usou um Ryzen 7
**7700X** (desktop, alto clock, boa dissipação térmica). A maleta usa um Ryzen
7 **7730U** (mobile, 15-28W) — mesma família Zen 3, mas clocks bem mais baixos.
Espere números **piores** que os 11-17s acima no hardware final; os 11-17s são
um piso otimista, não uma garantia.

Também vale registrar: em uma das rodadas com foto no teste de CPU, o modelo
caiu na alucinação conhecida ("nenhuma imagem foi enviada", ~1 em 3 chamadas —
ver seção de limites) — confirma que esse comportamento é do modelo, não do
hardware, e o retry automático já implementado continua necessário nos dois
cenários.

## Como rodar

```bash
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Requer Ollama rodando localmente com `gemma4:e4b` já baixado
(`ollama pull gemma4:e4b`).
