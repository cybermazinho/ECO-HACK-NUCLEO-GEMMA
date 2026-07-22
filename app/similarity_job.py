"""Job periódico: procura, dentro de cada comunidade, fichas recentes com sintomas
muito parecidos entre si — possível sinal de problema coletivo (água, surto, etc).

A similaridade combina duas métricas leves (sem GPU, sem embeddings):
  - TF-IDF + cosseno sobre o texto livre da queixa;
  - Jaccard sobre os tokens do campo estruturado `sintomas`.
O maior dos dois valores é usado, porque pacientes descrevem a mesma coisa com
palavras bem diferentes na queixa livre ("não quer comer" vs "vômito"), mas o
campo de sintomas tende a compartilhar termos mesmo com frases diferentes.

O Gemma só é chamado para *descrever* clusters já detectados estatisticamente.
"""

import logging
import re
from datetime import timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import gemma_client
from .db import AlertaSimilaridade, Ficha, Patient, get_session, utcnow

logger = logging.getLogger("nucleo.similaridade")

JANELA_DIAS = 30
MIN_CLUSTER = 3
LIMIAR_SIMILARIDADE = 0.35

STOPWORDS_PT = {
    "de", "a", "o", "que", "e", "do", "da", "em", "um", "para", "com", "não",
    "uma", "os", "no", "se", "na", "por", "mais", "as", "dos", "como", "mas",
    "ao", "ele", "das", "seu", "sua", "ou", "quando", "muito", "há", "nos",
    "já", "está", "eu", "também", "só", "pelo", "pela", "até", "isso", "ela",
    "entre", "depois", "sem", "mesmo", "aos", "seus", "quem", "nas", "me",
    "esse", "eles", "você", "essa", "num", "nem", "suas", "meu", "às", "minha",
    "tem", "sinto", "sentindo", "dias", "desde",
    # modificadores de intensidade/tempo: descrevem gravidade, não o problema
    # em si — sem isso, duas queixas sem nenhuma relação (ex: "dor de cabeça
    # leve" e "diarreia leve") colam por só compartilharem "leve"/"forte".
    "leve", "leves", "forte", "fortes", "grave", "graves", "moderada",
    "moderado", "moderados", "moderadas", "intensa", "intenso", "intensos",
    "intensas", "hoje", "ontem", "manha", "manhã", "noite", "madrugada",
    "agora", "pouco",
}


def _tokens_sintomas(sintomas: str) -> set[str]:
    palavras = re.split(r"[,\s]+", sintomas.lower())
    return {p for p in palavras if p and p not in STOPWORDS_PT}


def _matriz_jaccard(conjuntos: list[set[str]]) -> np.ndarray:
    n = len(conjuntos)
    matriz = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = conjuntos[i], conjuntos[j]
            if not a or not b:
                continue
            score = len(a & b) / len(a | b)
            matriz[i, j] = matriz[j, i] = score
    return matriz


def _ja_alertados(session) -> set[int]:
    """IDs de ficha que já entraram em algum alerta (novo ou avaliado)."""
    ids: set[int] = set()
    for (ficha_ids_str,) in session.query(AlertaSimilaridade.ficha_ids).all():
        ids.update(int(x) for x in ficha_ids_str.split(",") if x)
    return ids


def rodar_analise_similaridade() -> list[AlertaSimilaridade]:
    session = get_session()
    novos_alertas: list[AlertaSimilaridade] = []
    try:
        desde = utcnow() - timedelta(days=JANELA_DIAS)
        ja_cobertas = _ja_alertados(session)

        linhas = (
            session.query(Ficha, Patient)
            .join(Patient, Ficha.paciente_id == Patient.id)
            .filter(Ficha.criado_em >= desde)
            .all()
        )

        por_comunidade: dict[str, list[tuple[Ficha, Patient]]] = {}
        for ficha, paciente in linhas:
            if ficha.id in ja_cobertas:
                continue
            por_comunidade.setdefault(paciente.comunidade, []).append((ficha, paciente))

        for comunidade, itens in por_comunidade.items():
            if len(itens) < MIN_CLUSTER:
                continue

            textos = [f"{f.queixa_texto} {f.sintomas or ''}" for f, _ in itens]
            try:
                vetor = TfidfVectorizer(stop_words=list(STOPWORDS_PT)).fit_transform(textos)
            except ValueError:
                continue  # vocabulário vazio (textos muito curtos/genéricos)

            sim_texto = cosine_similarity(vetor)
            sim_sintomas = _matriz_jaccard(
                [_tokens_sintomas(f.sintomas or "") for f, _ in itens]
            )
            sim = np.maximum(sim_texto, sim_sintomas)
            n = len(itens)
            adjacencia = [
                {j for j in range(n) if j != i and sim[i, j] >= LIMIAR_SIMILARIDADE}
                for i in range(n)
            ]

            visitados: set[int] = set()
            for i in range(n):
                if i in visitados:
                    continue

                # BFS para pegar todo o componente conexo (similaridade transitiva:
                # A~B e B~C agrupam A,B,C mesmo que A e C não sejam diretamente parecidos).
                grupo = []
                fila = [i]
                vistos_local = {i}
                while fila:
                    atual = fila.pop()
                    grupo.append(atual)
                    for vizinho in adjacencia[atual]:
                        if vizinho not in vistos_local:
                            vistos_local.add(vizinho)
                            fila.append(vizinho)

                if len(grupo) < MIN_CLUSTER:
                    visitados.update(grupo)
                    continue

                visitados.update(grupo)
                fichas_grupo = [itens[k][0] for k in grupo]
                pares = [
                    sim[a, b]
                    for idx_a, a in enumerate(grupo)
                    for b in grupo[idx_a + 1 :]
                ]
                score_medio = float(sum(pares) / len(pares)) if pares else 0.0

                casos_txt = [
                    f"{f.queixa_texto} (sintomas: {f.sintomas or 'n/d'})" for f in fichas_grupo
                ]
                try:
                    resumo = gemma_client.descrever_cluster(comunidade, casos_txt)
                except Exception:
                    logger.exception("Falha ao pedir descrição do cluster ao Gemma")
                    resumo = {
                        "titulo": f"Possível problema coletivo em {comunidade}",
                        "descricao": (
                            f"{len(fichas_grupo)} casos com sintomas semelhantes detectados "
                            f"nos últimos {JANELA_DIAS} dias (descrição automática indisponível)."
                        ),
                    }

                alerta = AlertaSimilaridade(
                    comunidade=comunidade,
                    ficha_ids=",".join(str(f.id) for f in fichas_grupo),
                    titulo=resumo["titulo"],
                    descricao=resumo["descricao"],
                    score_similaridade=round(score_medio, 3),
                )
                session.add(alerta)
                novos_alertas.append(alerta)

        session.commit()
        for a in novos_alertas:
            session.refresh(a)
        return novos_alertas
    finally:
        session.close()
