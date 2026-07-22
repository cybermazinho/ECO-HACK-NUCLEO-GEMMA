"""Popula o NÚCLEO com pacientes e fichas de triagem sintéticas via API HTTP.

Inclui um cluster deliberado em "Vila Rio Verde" (vários casos de sintomas
gastrointestinais parecidos em poucos dias) para testar a detecção de
possível problema coletivo (ex: contaminação de água).

Uso:
    python data/seed_patients.py [--base-url http://localhost:8000]
"""

import argparse
import sys

import requests

PACIENTES = [
    # --- Cluster de possível contaminação de água em Vila Rio Verde ---
    {"nome": "Maria Souza", "idade": 34, "sexo": "F", "comunidade": "Vila Rio Verde", "alergias": ""},
    {"nome": "João Pereira", "idade": 41, "sexo": "M", "comunidade": "Vila Rio Verde", "alergias": ""},
    {"nome": "Ana Lima", "idade": 8, "sexo": "F", "comunidade": "Vila Rio Verde", "alergias": ""},
    {"nome": "Carlos Nunes", "idade": 55, "sexo": "M", "comunidade": "Vila Rio Verde", "alergias": "dipirona"},
    {"nome": "Beatriz Alves", "idade": 29, "sexo": "F", "comunidade": "Vila Rio Verde", "alergias": ""},
    # --- Casos variados em outras comunidades ---
    {"nome": "Pedro Santos", "idade": 67, "sexo": "M", "comunidade": "Assentamento Boa Esperança", "alergias": ""},
    {"nome": "Lucia Ferreira", "idade": 2, "sexo": "F", "comunidade": "Assentamento Boa Esperança", "alergias": "penicilina"},
    {"nome": "Rafael Costa", "idade": 22, "sexo": "M", "comunidade": "Comunidade Ribeirinha Santa Fé", "alergias": ""},
    {"nome": "Fernanda Dias", "idade": 45, "sexo": "F", "comunidade": "Comunidade Ribeirinha Santa Fé", "alergias": ""},
    {"nome": "Antonio Rocha", "idade": 78, "sexo": "M", "comunidade": "Assentamento Boa Esperança", "alergias": ""},
    {"nome": "Juliana Martins", "idade": 31, "sexo": "F", "comunidade": "Vila Rio Verde", "alergias": ""},
]

# índice do paciente (na lista acima) -> ficha
FICHAS = [
    # Cluster gastrointestinal em Vila Rio Verde — últimos dias, sintomas parecidos
    dict(idx=0, queixa_texto="Diarreia forte e vômito desde ontem à noite, dor de barriga.",
         sintomas="diarreia, vomito, dor abdominal, mal estar", temperatura_c=37.8,
         pressao_sistolica=110, freq_cardiaca=95),
    dict(idx=1, queixa_texto="Diarreia e vômito há 2 dias, muita fraqueza.",
         sintomas="diarreia, vomito, fraqueza, dor abdominal", temperatura_c=38.0,
         pressao_sistolica=105, freq_cardiaca=98),
    dict(idx=2, queixa_texto="Minha filha está com diarreia e vomitando, não quer comer.",
         sintomas="diarreia, vomito, inapetencia, dor abdominal", temperatura_c=38.2,
         pressao_sistolica=None, freq_cardiaca=110),
    dict(idx=3, queixa_texto="Dor de barriga forte, diarreia e vômito desde a madrugada.",
         sintomas="diarreia, vomito, dor abdominal, calafrio", temperatura_c=37.9,
         pressao_sistolica=115, freq_cardiaca=92),
    dict(idx=4, queixa_texto="Estou com diarreia, vômito e dor de barriga há 1 dia.",
         sintomas="diarreia, vomito, dor abdominal", temperatura_c=37.6,
         pressao_sistolica=108, freq_cardiaca=90),
    dict(idx=10, queixa_texto="Diarreia leve e enjoo desde hoje de manhã.",
         sintomas="diarreia, enjoo, dor abdominal leve", temperatura_c=37.3,
         pressao_sistolica=112, freq_cardiaca=88),

    # Casos variados — para testar a classificação de risco isoladamente
    dict(idx=5, queixa_texto="Dor forte no peito e falta de ar ao subir a rampa de casa.",
         sintomas="dor no peito, falta de ar, sudorese", temperatura_c=36.7,
         pressao_sistolica=168, freq_cardiaca=104),
    dict(idx=6, queixa_texto="Bebê com febre alta e muito choro há 6 horas.",
         sintomas="febre alta, irritabilidade, choro constante", temperatura_c=39.4,
         pressao_sistolica=None, freq_cardiaca=140),
    dict(idx=7, queixa_texto="Torci o tornozelo jogando bola, está inchado mas consigo andar.",
         sintomas="dor no tornozelo, inchaco leve", temperatura_c=36.5,
         pressao_sistolica=118, freq_cardiaca=76),
    dict(idx=8, queixa_texto="Venho para acompanhamento de pressão alta, sem sintomas novos.",
         sintomas="nenhum sintoma novo, hipertensa em acompanhamento", temperatura_c=36.6,
         pressao_sistolica=145, freq_cardiaca=80),
    dict(idx=9, queixa_texto="Tosse seca e cansaço há uma semana, sem febre.",
         sintomas="tosse seca, cansaco leve", temperatura_c=36.8,
         pressao_sistolica=130, freq_cardiaca=82),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    session = requests.Session()
    ids = []

    print(f"Criando {len(PACIENTES)} pacientes...")
    for p in PACIENTES:
        r = session.post(f"{args.base_url}/pacientes", json=p, timeout=30)
        r.raise_for_status()
        ids.append(r.json()["id"])

    print(f"Criando {len(FICHAS)} fichas (isso chama o Gemma, pode levar alguns minutos)...")
    for i, f in enumerate(FICHAS, 1):
        idx = f.pop("idx")
        payload = {"paciente_id": ids[idx], **f}
        r = session.post(f"{args.base_url}/fichas", json=payload, timeout=120)
        r.raise_for_status()
        ficha = r.json()
        classif = ficha.get("classificacao") or {}
        print(
            f"  [{i}/{len(FICHAS)}] ficha={ficha['id']} "
            f"risco={classif.get('risco')} conf={classif.get('confianca')}"
        )

    print("\nPronto. Endpoints úteis:")
    print(f"  GET {args.base_url}/pendencias")
    print(f"  POST {args.base_url}/admin/rodar-similaridade")
    print(f"  GET {args.base_url}/alertas")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print("Não consegui conectar na API. Suba o servidor primeiro:")
        print("  ./venv/bin/uvicorn app.main:app --reload")
        sys.exit(1)
