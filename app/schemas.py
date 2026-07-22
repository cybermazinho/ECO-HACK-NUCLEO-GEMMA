from datetime import datetime

from pydantic import BaseModel, Field


class PacienteIn(BaseModel):
    nome: str
    idade: int = Field(ge=0, le=130)
    sexo: str
    comunidade: str
    alergias: str = ""


class PacienteOut(PacienteIn):
    id: int
    criado_em: datetime

    class Config:
        from_attributes = True


class FichaIn(BaseModel):
    paciente_id: int
    queixa_texto: str
    sintomas: str = ""
    temperatura_c: float | None = None
    febre_relatada: str | None = None
    pressao_sistolica: int | None = None
    freq_cardiaca: int | None = None
    origem: str = "medico"
    apoiador_nome: str | None = None


class ClassificacaoOut(BaseModel):
    id: int
    risco: str
    especialidade_sugerida: str
    justificativa: str
    achado_visual: str | None = None
    confianca: float
    modelo: str
    status: str
    risco_corrigido: str | None
    obs_medico: str | None
    criado_em: datetime

    class Config:
        from_attributes = True


class SugestaoNutricionalOut(BaseModel):
    id: int
    ficha_id: int
    paciente_nome: str
    comunidade: str
    alergias: str
    queixa_texto: str
    recomendacao_geral: str
    alimentos_sugeridos: str
    alimentos_evitar: str
    justificativa: str
    confianca: float
    modelo: str
    status: str
    obs_profissional: str | None
    criado_em: datetime


class FichaOut(BaseModel):
    id: int
    paciente_id: int
    paciente_nome: str = ""
    comunidade: str = ""
    queixa_texto: str
    sintomas: str
    temperatura_c: float | None
    febre_relatada: str | None = None
    pressao_sistolica: int | None
    freq_cardiaca: int | None
    imagem_path: str | None = None
    origem: str = "medico"
    apoiador_nome: str | None = None
    criado_em: datetime
    classificacao: ClassificacaoOut | None = None
    sugestao_nutricional: SugestaoNutricionalOut | None = None
    apoio_imediato: str | None = None

    class Config:
        from_attributes = True


class PendenciaOut(BaseModel):
    classificacao_id: int
    ficha_id: int
    paciente_nome: str
    comunidade: str
    risco: str
    especialidade_sugerida: str
    justificativa: str
    achado_visual: str | None
    confianca: float
    modelo: str
    queixa_texto: str
    sintomas: str
    temperatura_c: float | None
    febre_relatada: str | None
    pressao_sistolica: int | None
    freq_cardiaca: int | None
    imagem_path: str | None
    origem: str
    apoiador_nome: str | None
    criado_em: datetime


class RevisaoIn(BaseModel):
    risco_corrigido: str
    obs_medico: str = ""


class AlertaOut(BaseModel):
    id: int
    comunidade: str
    ficha_ids: str
    titulo: str
    descricao: str
    score_similaridade: float
    status: str
    criado_em: datetime

    class Config:
        from_attributes = True


class AvaliarAlertaIn(BaseModel):
    status: str  # avaliado | descartado


class ValidarSugestaoIn(BaseModel):
    obs_profissional: str = ""


class FichaCompletaOut(BaseModel):
    """Visão consolidada de uma ficha — paciente, dados brutos informados,
    classificação da IA (+ revisão do médico) e sugestões nutricionais."""

    ficha_id: int
    paciente_nome: str
    idade: int
    sexo: str
    comunidade: str
    alergias: str
    origem: str
    apoiador_nome: str | None
    queixa_texto: str
    sintomas: str
    temperatura_c: float | None
    febre_relatada: str | None
    pressao_sistolica: int | None
    freq_cardiaca: int | None
    imagem_path: str | None
    criado_em: datetime
    classificacao: ClassificacaoOut | None
    sugestoes_nutricionais: list[SugestaoNutricionalOut]
