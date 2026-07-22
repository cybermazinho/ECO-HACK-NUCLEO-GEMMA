import json
import re

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e4b"

RISCOS_VALIDOS = {"baixo", "moderado", "alto", "critico"}


_PADRAO_RECUSA_IMAGEM = re.compile(
    r"n[ãa]o (foi|há|houve|tem)|nenhuma imagem|sem (a )?imagem|imposs[íi]vel avaliar",
    re.IGNORECASE,
)


def _parece_recusa_imagem(achado_visual: str) -> bool:
    return bool(_PADRAO_RECUSA_IMAGEM.search(achado_visual or ""))


def _extract_json(texto: str) -> dict:
    """Extrai o primeiro objeto JSON válido da resposta do modelo."""
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError(f"Nenhum JSON encontrado na resposta do modelo: {texto!r}")
    return json.loads(match.group(0))


def _chamar_gemma(
    prompt: str, imagens_base64: list[str] | None = None, timeout: int = 120
) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if imagens_base64:
        payload["images"] = imagens_base64

    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["response"]


def classificar_ficha(
    queixa_texto: str,
    sintomas: str,
    idade: int,
    sexo: str,
    temperatura_c: float | None,
    pressao_sistolica: int | None,
    freq_cardiaca: int | None,
    imagem_base64: str | None = None,
    febre_relatada: str | None = None,
) -> dict:
    """Pede ao Gemma para classificar o risco de uma ficha de triagem.

    Se `imagem_base64` for informado (ex: foto de lesão de pele, ferimento),
    ela é enviada junto ao Gemma — o gemma4:e4b é multimodal (texto+imagem) e
    o achado visual entra na mesma classificação de risco.

    `febre_relatada` (nenhuma/media/alta) é usado quando não há termômetro
    disponível (ex: autoatendimento do paciente) — não inventamos um número
    de temperatura, só descrevemos a sensação relatada.

    Retorna dict com: risco, especialidade_sugerida, justificativa, confianca.
    """
    vitais = []
    if temperatura_c is not None:
        vitais.append(f"temperatura {temperatura_c}°C")
    elif febre_relatada and febre_relatada != "nenhuma":
        vitais.append(f"febre relatada pelo paciente (sem termômetro): {febre_relatada}")
    if pressao_sistolica is not None:
        vitais.append(f"pressão sistólica {pressao_sistolica} mmHg")
    if freq_cardiaca is not None:
        vitais.append(f"frequência cardíaca {freq_cardiaca} bpm")
    vitais_txt = ", ".join(vitais) if vitais else "não informados"

    if imagem_base64:
        # Testamos empiricamente: com o prompt "verboso" (várias linhas de
        # instrução entre o pedido de observar a imagem e o JSON de saída),
        # o gemma4:e4b ignorava a foto quase sempre — mesmo repetindo a
        # chamada. Compactar (achado_visual como primeiro campo, logo após a
        # instrução) resolveu isso. Mas aí surgiu o problema oposto: o modelo
        # às vezes fixava só no achado visual e ignorava febre/diarreia/sinais
        # vitais relatados em texto. A linha "considerando IGUALMENTE" abaixo
        # foi o que corrigiu — sem ela, a lesão de pele dominava a
        # justificativa mesmo com sintomas sistêmicos relatados.
        prompt = f"""Observe atentamente a imagem clínica anexada antes de tudo — é uma foto do paciente. Descreva objetivamente o que vê nela no campo achado_visual.

Dados do caso — Paciente: {idade} anos, {sexo}. Sintomas relatados: {sintomas or "não especificados"}. Queixa: "{queixa_texto}". Sinais vitais: {vitais_txt}.

Classifique o risco considerando IGUALMENTE o achado visual E os sintomas/sinais vitais relatados acima — o quadro completo, não só um dos dois.

Você é um assistente de triagem clínica (NÃO diagnostique). Responda APENAS com JSON:
{{
  "achado_visual": "descrição literal do que vê na imagem",
  "risco": "baixo" | "moderado" | "alto" | "critico",
  "especialidade_sugerida": "string curta, ex: clinica geral, pediatria, infectologia",
  "justificativa": "1-2 frases explicando o motivo, mencionando tanto o achado visual quanto os sintomas/sinais relatados",
  "confianca": número entre 0 e 1
}}"""
    else:
        prompt = f"""Você é um assistente de triagem clínica. NÃO faça diagnóstico definitivo.
Analise o caso abaixo e classifique o risco para priorizar a fila de atendimento médico.

Paciente: {idade} anos, sexo {sexo}.
Sinais vitais: {vitais_txt}.
Sintomas relatados: {sintomas or "não especificados"}.
Queixa (texto livre do paciente): "{queixa_texto}"

Responda APENAS com um JSON no formato exato:
{{
  "risco": "baixo" | "moderado" | "alto" | "critico",
  "especialidade_sugerida": "string curta, ex: clinica geral, pediatria, infectologia",
  "justificativa": "1-2 frases explicando o motivo da classificação",
  "confianca": número entre 0 e 1
}}"""

    imagens = [imagem_base64] if imagem_base64 else None
    bruto = _chamar_gemma(prompt, imagens_base64=imagens)
    dado = _extract_json(bruto)

    if imagem_base64 and _parece_recusa_imagem(dado.get("achado_visual", "")):
        # gemma4:e4b às vezes "alucina" que nenhuma imagem foi enviada mesmo
        # recebendo uma (falha estocástica de atenção multimodal, não um erro
        # de transporte — testamos e ~1 em cada 3 chamadas falha assim, então
        # tentar de novo resolve na grande maioria dos casos).
        for _ in range(2):
            bruto = _chamar_gemma(prompt, imagens_base64=imagens)
            dado = _extract_json(bruto)
            if not _parece_recusa_imagem(dado.get("achado_visual", "")):
                break

    risco = str(dado.get("risco", "")).strip().lower()
    if risco not in RISCOS_VALIDOS:
        risco = "moderado"

    confianca = dado.get("confianca", 0.5)
    try:
        confianca = float(confianca)
    except (TypeError, ValueError):
        confianca = 0.5
    confianca = max(0.0, min(1.0, confianca))

    return {
        "risco": risco,
        "especialidade_sugerida": str(dado.get("especialidade_sugerida", ""))[:200],
        "justificativa": str(dado.get("justificativa", ""))[:1000],
        "confianca": confianca,
        "modelo": MODEL,
        "achado_visual": (str(dado["achado_visual"])[:1000] if dado.get("achado_visual") else None),
    }


def gerar_apoio_imediato(risco: str, especialidade_sugerida: str) -> str:
    """Mensagem curta e acolhedora para o paciente quando o caso é alto/crítico —
    prioriza dizer para buscar ajuda presencial AGORA (não esperar a fila) e dar
    orientação de segurança genérica. NUNCA sugere medicamento, dose ou conduta
    clínica específica — isso é decisão exclusiva do profissional presencial.
    """
    prompt = f"""Você é um assistente de acolhimento em uma unidade de saúde remota. Um sistema de triagem
classificou o caso de um paciente como risco "{risco}" (especialidade sugerida: {especialidade_sugerida or "avaliação médica"}).

Escreva uma mensagem curta (2-3 frases), calorosa e direta, para o próprio paciente, que:
- Deixe claro que o caso é prioritário e que ele deve buscar um profissional presencial AGORA, sem esperar sentado na fila normal.
- Dê UMA orientação de segurança bem genérica enquanto aguarda (ex: não ficar sozinho, pedir para alguém avisar a equipe se algo piorar).
- NUNCA sugira remédio, dose, ou qualquer conduta médica específica — isso é só do profissional presencial.
- Tom acolhedor, sem termos técnicos assustadores.

Responda APENAS com um JSON no formato exato:
{{
  "mensagem": "o texto da mensagem, 2-3 frases"
}}"""

    bruto = _chamar_gemma(prompt)
    dado = _extract_json(bruto)
    return str(dado.get("mensagem", ""))[:600]


def sugerir_nutricao(
    idade: int,
    sexo: str,
    comunidade: str,
    alergias: str,
    queixa_texto: str,
    sintomas: str,
) -> dict:
    """Pede ao Gemma uma orientação alimentar contextualizada — por condição
    relatada, alergias do paciente e realidade de comunidades remotas (evitar
    itens caros/difíceis de achar fora de grandes cidades).

    Sempre precisa de validação humana antes de chegar ao paciente (não
    substitui nutricionista).
    """
    prompt = f"""Você é um assistente de apoio nutricional para uma equipe de saúde numa comunidade remota, sem nutricionista disponível no local. Suas sugestões serão revisadas por um profissional de saúde antes de chegar ao paciente — você não substitui essa validação.

Paciente: {idade} anos, {sexo}, comunidade "{comunidade}".
Alergias conhecidas: {alergias or "nenhuma relatada"}.
Sintomas relatados: {sintomas or "não especificados"}.
Queixa: "{queixa_texto}"

Sugira uma orientação alimentar considerando a queixa/sintomas, respeitando SEMPRE as alergias informadas (nunca sugira um alimento ao qual o paciente é alérgico), e priorizando alimentos comuns e acessíveis em comunidades rurais/remotas do Brasil (evite itens caros ou difíceis de encontrar fora de grandes cidades).

Responda APENAS com um JSON no formato exato:
{{
  "recomendacao_geral": "2-3 frases de orientação alimentar geral",
  "alimentos_sugeridos": ["alimento 1", "alimento 2", "alimento 3"],
  "alimentos_evitar": ["alimento 1", "alimento 2"],
  "justificativa": "1-2 frases explicando a lógica da sugestão, incluindo como respeitou as alergias informadas",
  "confianca": número entre 0 e 1
}}"""

    bruto = _chamar_gemma(prompt)
    dado = _extract_json(bruto)

    confianca = dado.get("confianca", 0.5)
    try:
        confianca = float(confianca)
    except (TypeError, ValueError):
        confianca = 0.5
    confianca = max(0.0, min(1.0, confianca))

    def _lista_para_texto(chave: str) -> str:
        valor = dado.get(chave, [])
        if isinstance(valor, list):
            return ", ".join(str(v).strip() for v in valor if str(v).strip())
        return str(valor or "")

    return {
        "recomendacao_geral": str(dado.get("recomendacao_geral", ""))[:1000],
        "alimentos_sugeridos": _lista_para_texto("alimentos_sugeridos")[:500],
        "alimentos_evitar": _lista_para_texto("alimentos_evitar")[:500],
        "justificativa": str(dado.get("justificativa", ""))[:1000],
        "confianca": confianca,
        "modelo": MODEL,
    }


def descrever_cluster(comunidade: str, casos: list[str]) -> dict:
    """Pede ao Gemma para resumir por que um grupo de fichas parece ser o mesmo problema.

    `casos` é uma lista de textos (queixa + sintomas) das fichas do cluster.
    Retorna dict com: titulo, descricao.
    """
    lista_casos = "\n".join(f"- {c}" for c in casos)

    prompt = f"""Você é um assistente de vigilância epidemiológica local. Um algoritmo estatístico
já identificou que os casos abaixo, todos da comunidade "{comunidade}", têm sintomas
muito parecidos entre si e podem indicar um problema de saúde coletivo (ex: contaminação
de água, surto infeccioso, exposição ambiental comum).

Casos agrupados:
{lista_casos}

Responda APENAS com um JSON no formato exato:
{{
  "titulo": "título curto do possível problema coletivo (max 10 palavras)",
  "descricao": "2-4 frases explicando o padrão observado e uma possível causa comum a investigar. Deixe claro que é uma hipótese para o médico avaliar, não uma conclusão."
}}"""

    bruto = _chamar_gemma(prompt)
    dado = _extract_json(bruto)
    return {
        "titulo": str(dado.get("titulo", "Possível problema coletivo"))[:200],
        "descricao": str(dado.get("descricao", ""))[:1500],
    }
