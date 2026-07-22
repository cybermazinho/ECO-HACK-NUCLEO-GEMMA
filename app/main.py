import base64
import logging
import re
import uuid
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case
from sqlalchemy.orm import Session

from . import gemma_client, schemas
from .db import (
    AlertaSimilaridade,
    Classificacao,
    Ficha,
    Patient,
    SugestaoNutricional,
    get_session,
    init_db,
    utcnow,
)
from .similarity_job import rodar_analise_similaridade

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nucleo.api")

JOB_INTERVAL_MINUTOS = 10

app = FastAPI(title="NÚCLEO — IA Clínica", version="0.1.0")
scheduler = BackgroundScheduler()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
(UPLOADS_DIR / "imagens").mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/", include_in_schema=False)
def painel():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/apoiador", include_in_schema=False)
def tela_apoiador():
    return FileResponse(STATIC_DIR / "apoiador.html")


@app.get("/paciente", include_in_schema=False)
def tela_paciente():
    return FileResponse(STATIC_DIR / "paciente.html")


@app.get("/ficha/{ficha_id}", include_in_schema=False)
def tela_ficha(ficha_id: int):
    return FileResponse(STATIC_DIR / "ficha.html")


def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.add_job(
        rodar_analise_similaridade,
        "interval",
        minutes=JOB_INTERVAL_MINUTOS,
        id="similaridade",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Job de similaridade agendado a cada %s minutos.", JOB_INTERVAL_MINUTOS
    )


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown(wait=False)


_PADRAO_NUTRICAO = re.compile(
    r"peso|emagrec|engord|aliment|dieta|nutri|comer|apetite|\bfome\b|desnutri|"
    r"obesidade|\bcomida\b|vitamina|anemia",
    re.IGNORECASE,
)


def _parece_relacionado_a_nutricao(queixa_texto: str, sintomas: str) -> bool:
    return bool(_PADRAO_NUTRICAO.search(f"{queixa_texto} {sintomas}"))


# --- Pacientes -------------------------------------------------------------

@app.post("/pacientes", response_model=schemas.PacienteOut)
def criar_paciente(dado: schemas.PacienteIn, db: Session = Depends(get_db)):
    paciente = Patient(**dado.model_dump())
    db.add(paciente)
    db.commit()
    db.refresh(paciente)
    return paciente


@app.get("/pacientes", response_model=list[schemas.PacienteOut])
def listar_pacientes(db: Session = Depends(get_db)):
    return db.query(Patient).order_by(Patient.id).all()


# --- Fichas / Triagem -------------------------------------------------------

def _classificar_e_salvar(
    db: Session, ficha: Ficha, paciente: Patient, imagem_base64: str | None = None
) -> Classificacao:
    """Chama o Gemma, cria a Classificacao e commita. Nunca deixa uma ficha sem
    classificação: se o Gemma falhar, cai em pendente/baixa-confiança para
    revisão manual em vez de quebrar o cadastro da ficha."""
    try:
        resultado = gemma_client.classificar_ficha(
            queixa_texto=ficha.queixa_texto,
            sintomas=ficha.sintomas,
            idade=paciente.idade,
            sexo=paciente.sexo,
            temperatura_c=ficha.temperatura_c,
            pressao_sistolica=ficha.pressao_sistolica,
            freq_cardiaca=ficha.freq_cardiaca,
            imagem_base64=imagem_base64,
            febre_relatada=ficha.febre_relatada,
        )
        classificacao = Classificacao(ficha_id=ficha.id, **resultado)
    except Exception:
        logger.exception("Falha ao classificar ficha %s via Gemma", ficha.id)
        classificacao = Classificacao(
            ficha_id=ficha.id,
            risco="moderado",
            especialidade_sugerida="",
            justificativa="Classificação automática indisponível — revisar manualmente.",
            confianca=0.0,
            modelo="indisponivel",
        )
    db.add(classificacao)
    db.commit()
    db.refresh(classificacao)
    return classificacao


def _ficha_out(ficha: Ficha, paciente: Patient) -> schemas.FichaOut:
    ficha_out = schemas.FichaOut.model_validate(ficha)
    ficha_out.paciente_nome = paciente.nome
    ficha_out.comunidade = paciente.comunidade
    return ficha_out


@app.post("/fichas", response_model=schemas.FichaOut)
def criar_ficha(dado: schemas.FichaIn, db: Session = Depends(get_db)):
    paciente = db.get(Patient, dado.paciente_id)
    if paciente is None:
        raise HTTPException(404, "Paciente não encontrado")

    ficha = Ficha(**dado.model_dump())
    db.add(ficha)
    db.commit()
    db.refresh(ficha)

    _classificar_e_salvar(db, ficha, paciente)
    db.refresh(ficha)
    return _ficha_out(ficha, paciente)


def _salvar_upload(arquivo: UploadFile, conteudo: bytes, subpasta: str) -> str:
    ext = Path(arquivo.filename or "").suffix or ""
    nome_arquivo = f"{uuid.uuid4().hex}{ext}"
    destino = UPLOADS_DIR / subpasta / nome_arquivo
    destino.write_bytes(conteudo)
    return f"/uploads/{subpasta}/{nome_arquivo}", destino


@app.post("/fichas/upload", response_model=schemas.FichaOut)
async def criar_ficha_com_midia(
    paciente_id: int = Form(...),
    queixa_texto: str = Form(...),
    sintomas: str = Form(""),
    temperatura_c: float | None = Form(None),
    febre_relatada: str | None = Form(None),
    pressao_sistolica: int | None = Form(None),
    freq_cardiaca: int | None = Form(None),
    origem: str = Form("medico"),
    apoiador_nome: str | None = Form(None),
    imagem: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Mesmo fluxo de /fichas, mas aceita foto junto (multipart) — a foto vai
    direto para o Gemma (gemma4:e4b é multimodal de verdade para imagem).

    `origem` identifica de qual tela veio (medico/apoiador/paciente) — fica
    visível na fila e na ficha completa, para o médico saber quem informou os
    dados. `febre_relatada` é usado no lugar de `temperatura_c` quando não há
    termômetro (ex: autoatendimento do paciente)."""
    paciente = db.get(Patient, paciente_id)
    if paciente is None:
        raise HTTPException(404, "Paciente não encontrado")

    imagem_path = None
    imagem_base64 = None
    if imagem is not None and imagem.filename:
        conteudo = await imagem.read()
        imagem_path, _ = _salvar_upload(imagem, conteudo, "imagens")
        imagem_base64 = base64.b64encode(conteudo).decode()

    ficha = Ficha(
        paciente_id=paciente_id,
        queixa_texto=queixa_texto.strip(),
        sintomas=sintomas,
        temperatura_c=temperatura_c,
        febre_relatada=febre_relatada,
        pressao_sistolica=pressao_sistolica,
        freq_cardiaca=freq_cardiaca,
        imagem_path=imagem_path,
        origem=origem,
        apoiador_nome=apoiador_nome,
    )
    db.add(ficha)
    db.commit()
    db.refresh(ficha)

    classificacao = _classificar_e_salvar(db, ficha, paciente, imagem_base64=imagem_base64)
    db.refresh(ficha)

    ficha_out = _ficha_out(ficha, paciente)

    # No autoatendimento do paciente, se a queixa parece ser sobre
    # alimentação/peso, já gera a sugestão nutricional na hora — o paciente
    # não deveria precisar esperar um profissional clicar em um botão depois
    # para receber uma orientação básica. A sugestão continua "pendente" e
    # entra na fila de validação do profissional normalmente.
    if origem == "paciente" and _parece_relacionado_a_nutricao(ficha.queixa_texto, ficha.sintomas):
        try:
            resultado_nutri = gemma_client.sugerir_nutricao(
                idade=paciente.idade,
                sexo=paciente.sexo,
                comunidade=paciente.comunidade,
                alergias=paciente.alergias,
                queixa_texto=ficha.queixa_texto,
                sintomas=ficha.sintomas,
            )
            sugestao = SugestaoNutricional(ficha_id=ficha.id, **resultado_nutri)
            db.add(sugestao)
            db.commit()
            db.refresh(sugestao)
            ficha_out.sugestao_nutricional = _sugestao_nutricional_out(sugestao, ficha, paciente)
        except Exception:
            logger.exception(
                "Falha ao gerar sugestão nutricional automática para ficha %s", ficha.id
            )

    # Se o caso do paciente saiu alto/crítico, ele não deveria só ficar
    # esperando em silêncio — mostra uma mensagem curta pedindo para buscar
    # ajuda presencial agora, sem entrar em conduta clínica específica.
    if origem == "paciente" and classificacao.risco in ("alto", "critico"):
        try:
            ficha_out.apoio_imediato = gemma_client.gerar_apoio_imediato(
                risco=classificacao.risco,
                especialidade_sugerida=classificacao.especialidade_sugerida,
            )
        except Exception:
            logger.exception("Falha ao gerar apoio imediato para ficha %s", ficha.id)

    return ficha_out


@app.get("/fichas/{ficha_id}", response_model=schemas.FichaOut)
def obter_ficha(ficha_id: int, db: Session = Depends(get_db)):
    ficha = db.get(Ficha, ficha_id)
    if ficha is None:
        raise HTTPException(404, "Ficha não encontrada")
    paciente = db.get(Patient, ficha.paciente_id)
    return _ficha_out(ficha, paciente)


# --- Fila de pendências para o médico ---------------------------------------

_ORDEM_RISCO = case(
    (Classificacao.risco == "critico", 0),
    (Classificacao.risco == "alto", 1),
    (Classificacao.risco == "moderado", 2),
    (Classificacao.risco == "baixo", 3),
    else_=4,
)


@app.get("/pendencias", response_model=list[schemas.PendenciaOut])
def listar_pendencias(db: Session = Depends(get_db)):
    linhas = (
        db.query(Classificacao, Ficha, Patient)
        .join(Ficha, Classificacao.ficha_id == Ficha.id)
        .join(Patient, Ficha.paciente_id == Patient.id)
        .filter(Classificacao.status == "pendente")
        .order_by(_ORDEM_RISCO, Classificacao.criado_em.asc())
        .all()
    )
    return [
        schemas.PendenciaOut(
            classificacao_id=c.id,
            ficha_id=f.id,
            paciente_nome=p.nome,
            comunidade=p.comunidade,
            risco=c.risco,
            especialidade_sugerida=c.especialidade_sugerida,
            justificativa=c.justificativa,
            achado_visual=c.achado_visual,
            confianca=c.confianca,
            modelo=c.modelo,
            queixa_texto=f.queixa_texto,
            sintomas=f.sintomas,
            temperatura_c=f.temperatura_c,
            febre_relatada=f.febre_relatada,
            pressao_sistolica=f.pressao_sistolica,
            freq_cardiaca=f.freq_cardiaca,
            imagem_path=f.imagem_path,
            origem=f.origem,
            apoiador_nome=f.apoiador_nome,
            criado_em=c.criado_em,
        )
        for c, f, p in linhas
    ]


@app.post("/pendencias/{classificacao_id}/revisar", response_model=schemas.ClassificacaoOut)
def revisar_pendencia(
    classificacao_id: int, dado: schemas.RevisaoIn, db: Session = Depends(get_db)
):
    classificacao = db.get(Classificacao, classificacao_id)
    if classificacao is None:
        raise HTTPException(404, "Classificação não encontrada")

    classificacao.status = "revisado"
    classificacao.risco_corrigido = dado.risco_corrigido
    classificacao.obs_medico = dado.obs_medico
    classificacao.revisado_em = utcnow()
    db.commit()
    db.refresh(classificacao)
    return classificacao


# --- Alertas de possível problema coletivo ----------------------------------

@app.get("/alertas", response_model=list[schemas.AlertaOut])
def listar_alertas(status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AlertaSimilaridade)
    if status:
        query = query.filter(AlertaSimilaridade.status == status)
    return query.order_by(AlertaSimilaridade.criado_em.desc()).all()


@app.post("/alertas/{alerta_id}/avaliar", response_model=schemas.AlertaOut)
def avaliar_alerta(
    alerta_id: int, dado: schemas.AvaliarAlertaIn, db: Session = Depends(get_db)
):
    alerta = db.get(AlertaSimilaridade, alerta_id)
    if alerta is None:
        raise HTTPException(404, "Alerta não encontrado")
    alerta.status = dado.status
    db.commit()
    db.refresh(alerta)
    return alerta


@app.post("/admin/rodar-similaridade", response_model=list[schemas.AlertaOut])
def rodar_similaridade_manual():
    """Dispara o job de cruzamento de dados agora (fora do agendamento), útil para demo."""
    return rodar_analise_similaridade()


# --- Apoio nutricional -------------------------------------------------------

def _sugestao_nutricional_out(s: SugestaoNutricional, ficha: Ficha, paciente: Patient) -> schemas.SugestaoNutricionalOut:
    return schemas.SugestaoNutricionalOut(
        id=s.id,
        ficha_id=ficha.id,
        paciente_nome=paciente.nome,
        comunidade=paciente.comunidade,
        alergias=paciente.alergias,
        queixa_texto=ficha.queixa_texto,
        recomendacao_geral=s.recomendacao_geral,
        alimentos_sugeridos=s.alimentos_sugeridos,
        alimentos_evitar=s.alimentos_evitar,
        justificativa=s.justificativa,
        confianca=s.confianca,
        modelo=s.modelo,
        status=s.status,
        obs_profissional=s.obs_profissional,
        criado_em=s.criado_em,
    )


@app.post("/fichas/{ficha_id}/sugestao-nutricional", response_model=schemas.SugestaoNutricionalOut)
def gerar_sugestao_nutricional(ficha_id: int, db: Session = Depends(get_db)):
    """Pede ao Gemma uma orientação alimentar para essa ficha (sob demanda —
    não é gerado automaticamente para toda ficha). Se já existir uma sugestão
    pendente para a ficha, retorna ela em vez de gerar outra."""
    ficha = db.get(Ficha, ficha_id)
    if ficha is None:
        raise HTTPException(404, "Ficha não encontrada")
    paciente = db.get(Patient, ficha.paciente_id)

    existente = (
        db.query(SugestaoNutricional)
        .filter(SugestaoNutricional.ficha_id == ficha_id, SugestaoNutricional.status == "pendente")
        .order_by(SugestaoNutricional.criado_em.desc())
        .first()
    )
    if existente is not None:
        return _sugestao_nutricional_out(existente, ficha, paciente)

    try:
        resultado = gemma_client.sugerir_nutricao(
            idade=paciente.idade,
            sexo=paciente.sexo,
            comunidade=paciente.comunidade,
            alergias=paciente.alergias,
            queixa_texto=ficha.queixa_texto,
            sintomas=ficha.sintomas,
        )
        sugestao = SugestaoNutricional(ficha_id=ficha.id, **resultado)
    except Exception:
        logger.exception("Falha ao gerar sugestão nutricional para ficha %s", ficha.id)
        raise HTTPException(502, "Não foi possível gerar a sugestão nutricional agora")

    db.add(sugestao)
    db.commit()
    db.refresh(sugestao)
    return _sugestao_nutricional_out(sugestao, ficha, paciente)


@app.get("/sugestoes-nutricionais", response_model=list[schemas.SugestaoNutricionalOut])
def listar_sugestoes_nutricionais(status: str | None = None, db: Session = Depends(get_db)):
    query = (
        db.query(SugestaoNutricional, Ficha, Patient)
        .join(Ficha, SugestaoNutricional.ficha_id == Ficha.id)
        .join(Patient, Ficha.paciente_id == Patient.id)
    )
    if status:
        query = query.filter(SugestaoNutricional.status == status)
    linhas = query.order_by(SugestaoNutricional.criado_em.desc()).all()
    return [_sugestao_nutricional_out(s, f, p) for s, f, p in linhas]


@app.post("/sugestoes-nutricionais/{sugestao_id}/validar", response_model=schemas.SugestaoNutricionalOut)
def validar_sugestao_nutricional(
    sugestao_id: int, dado: schemas.ValidarSugestaoIn, db: Session = Depends(get_db)
):
    sugestao = db.get(SugestaoNutricional, sugestao_id)
    if sugestao is None:
        raise HTTPException(404, "Sugestão não encontrada")

    sugestao.status = "validado"
    sugestao.obs_profissional = dado.obs_profissional
    sugestao.validado_em = utcnow()
    db.commit()
    db.refresh(sugestao)

    ficha = db.get(Ficha, sugestao.ficha_id)
    paciente = db.get(Patient, ficha.paciente_id)
    return _sugestao_nutricional_out(sugestao, ficha, paciente)


# --- Ficha completa (visão consolidada / prontuário) ------------------------

@app.get("/fichas/{ficha_id}/completa", response_model=schemas.FichaCompletaOut)
def obter_ficha_completa(ficha_id: int, db: Session = Depends(get_db)):
    ficha = db.get(Ficha, ficha_id)
    if ficha is None:
        raise HTTPException(404, "Ficha não encontrada")
    paciente = db.get(Patient, ficha.paciente_id)

    sugestoes = (
        db.query(SugestaoNutricional)
        .filter(SugestaoNutricional.ficha_id == ficha_id)
        .order_by(SugestaoNutricional.criado_em.desc())
        .all()
    )

    return schemas.FichaCompletaOut(
        ficha_id=ficha.id,
        paciente_nome=paciente.nome,
        idade=paciente.idade,
        sexo=paciente.sexo,
        comunidade=paciente.comunidade,
        alergias=paciente.alergias,
        origem=ficha.origem,
        apoiador_nome=ficha.apoiador_nome,
        queixa_texto=ficha.queixa_texto,
        sintomas=ficha.sintomas,
        temperatura_c=ficha.temperatura_c,
        febre_relatada=ficha.febre_relatada,
        pressao_sistolica=ficha.pressao_sistolica,
        freq_cardiaca=ficha.freq_cardiaca,
        imagem_path=ficha.imagem_path,
        criado_em=ficha.criado_em,
        classificacao=ficha.classificacao,
        sugestoes_nutricionais=[
            _sugestao_nutricional_out(s, ficha, paciente) for s in sugestoes
        ],
    )
