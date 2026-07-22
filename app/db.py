from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = "sqlite:///./nucleo.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    idade = Column(Integer, nullable=False)
    sexo = Column(String, nullable=False)
    comunidade = Column(String, nullable=False, index=True)
    alergias = Column(String, default="")
    criado_em = Column(DateTime, default=utcnow)

    fichas = relationship("Ficha", back_populates="paciente")


class Ficha(Base):
    """Registro de triagem (uma consulta/queixa)."""

    __tablename__ = "fichas"

    id = Column(Integer, primary_key=True)
    paciente_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    queixa_texto = Column(Text, nullable=False)
    sintomas = Column(Text, default="")  # lista separada por vírgula
    temperatura_c = Column(Float, nullable=True)
    febre_relatada = Column(String, nullable=True)  # nenhuma|media|alta — quando não há termômetro
    pressao_sistolica = Column(Integer, nullable=True)
    freq_cardiaca = Column(Integer, nullable=True)
    imagem_path = Column(String, nullable=True)
    origem = Column(String, default="medico")  # medico | apoiador | paciente
    apoiador_nome = Column(String, nullable=True)
    criado_em = Column(DateTime, default=utcnow, index=True)

    paciente = relationship("Patient", back_populates="fichas")
    classificacao = relationship(
        "Classificacao", back_populates="ficha", uselist=False
    )


class Classificacao(Base):
    """Saída da IA (Gemma) para uma ficha — vira item de pendência para o médico."""

    __tablename__ = "classificacoes"

    id = Column(Integer, primary_key=True)
    ficha_id = Column(Integer, ForeignKey("fichas.id"), nullable=False, unique=True)

    risco = Column(String, nullable=False)  # baixo | moderado | alto | critico
    especialidade_sugerida = Column(String, default="")
    justificativa = Column(Text, default="")
    achado_visual = Column(Text, nullable=True)
    confianca = Column(Float, default=0.0)
    modelo = Column(String, default="")

    status = Column(String, default="pendente")  # pendente | revisado
    risco_corrigido = Column(String, nullable=True)
    obs_medico = Column(Text, nullable=True)
    revisado_em = Column(DateTime, nullable=True)

    criado_em = Column(DateTime, default=utcnow)

    ficha = relationship("Ficha", back_populates="classificacao")


class SugestaoNutricional(Base):
    """Orientação alimentar gerada pelo Gemma para uma ficha — pedida sob
    demanda pelo profissional (não é gerada para toda ficha), e também exige
    validação humana antes de chegar ao paciente."""

    __tablename__ = "sugestoes_nutricionais"

    id = Column(Integer, primary_key=True)
    ficha_id = Column(Integer, ForeignKey("fichas.id"), nullable=False, index=True)

    recomendacao_geral = Column(Text, default="")
    alimentos_sugeridos = Column(Text, default="")  # separados por vírgula
    alimentos_evitar = Column(Text, default="")  # separados por vírgula
    justificativa = Column(Text, default="")
    confianca = Column(Float, default=0.0)
    modelo = Column(String, default="")

    status = Column(String, default="pendente")  # pendente | validado
    obs_profissional = Column(Text, nullable=True)
    validado_em = Column(DateTime, nullable=True)

    criado_em = Column(DateTime, default=utcnow, index=True)

    ficha = relationship("Ficha")


class AlertaSimilaridade(Base):
    """Alerta gerado pelo job periódico ao detectar possível problema coletivo."""

    __tablename__ = "alertas_similaridade"

    id = Column(Integer, primary_key=True)
    comunidade = Column(String, nullable=False, index=True)
    ficha_ids = Column(String, nullable=False)  # ids separados por vírgula
    titulo = Column(String, nullable=False)
    descricao = Column(Text, default="")
    score_similaridade = Column(Float, default=0.0)
    status = Column(String, default="novo")  # novo | avaliado | descartado
    criado_em = Column(DateTime, default=utcnow, index=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
