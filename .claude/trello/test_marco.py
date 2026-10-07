"""28.64: o marco do deploy gerado do CHANGELOG. Trechos FICTÍCIOS no formato do CHANGELOG: nenhum cartão real, nenhuma rede.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_marco.py
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from marco import (  # noqa: E402
    NOTA_GERADO,
    PREFIXO_LEITURA,
    SEPARADOR,
    achar_secao,
    contar_aparelhos,
    corpo_marco,
    decidir_marco,
    itens_de,
    extrair,
    montar_m8,
    montar_m9,
    numero_mais_recente,
    sem_prefixo_leitura,
    titulo_marco,
)
from redacao import redigir  # noqa: E402

# valores inventados; só a forma é a do CHANGELOG real (deploys 55 e 56)
CHANGELOG = """# Registro de mudanças

## 2030-03-04 — 28.99: algo que não é deploy

- texto solto com 31.999 e "Deploy 90" no meio.

## 2030-03-05 — Deploy 91 (corte de teste: tela nova, regra configurável, relatório)

- **Implantado** às 21:15Z: central em `abcdef0123456789`, migrações 201 (índice novo sai) e 202 (`tabela.coluna`), 8 pontas sobre a main `11112222`. Itens: Jev 31.901 (primeira coisa), 31.902 adendo v1.95 (segunda), ADR-901 (decisão), 28.91 e 31.903 (correções), C17 (limite). Aviso: o aparelho caiu às 20:19Z e voltou 21:16Z.
- Prova `real`: deploy `deploy.ps1` (backup `20300305-181424`, ensaio das migrações 201 e 202 ok); `GET /api/health` ok; agente do notebook em `0.1.0+abcdef0`; tag deploy-20300305-2115. Onda de prova com custo US$ 0,289.
- Prova `simulated` (suíte 91 sobre `abcdef0123456789`): `scripts/tests` 790 passed; backend em SQLite inteiro 12528 passed, 13 skipped; frontend 1961 (150 arquivos) e build; catracas 89 (backend) e 7 (scripts); docs-check 0/0; mypy 257 (teto 257); PostgreSQL dirigido 5968 + 4120 passed (464 arquivos).
- `not_run` até a rodada seguinte: onda 2 (3 a 4 alvos); troca de conta no app.

## 2030-03-04 — Deploy 90 (sem migração e escrito "no ar")

- **Implantado** no ar às 9:05Z: central em `99998888777766`, sem migração, 3 pontas sobre a main `33334444`. Itens: 31.801, 31.802.
- Prova `real`: `GET /api/health` ok; backup `20300304-060000`.
- Prova `simulated`: `scripts/tests` 700 passed; frontend 1900.

## 2030-03-03 — Deploy 89 (uma migração)

- **Implantado** às 10:00Z: central em `aaaabbbbcccc00`, migração 150 (coluna nova), 2 pontas. Itens: 31.701.
"""


def _d(n: int):
    secao = achar_secao(CHANGELOG, n)
    assert secao is not None
    return extrair(secao)


def test_secao_mais_recente_e_pedida_e_ausente() -> None:
    assert numero_mais_recente(CHANGELOG) == 91
    assert achar_secao(CHANGELOG)[0].startswith("## 2030-03-05 — Deploy 91")  # type: ignore[index]
    assert achar_secao(CHANGELOG, 89) is not None
    assert achar_secao(CHANGELOG, 77) is None
    assert achar_secao("# nada aqui\n") is None
    # a seção que não é deploy não vira deploy nem vaza para a seguinte
    assert "31.999" not in "".join(achar_secao(CHANGELOG, 91) or [])


def test_campos_do_deploy_91() -> None:
    d = _d(91)
    assert (d.numero, d.data, d.hora) == (91, "2030-03-05", "21:15")
    assert d.commit_longo == "abcdef0123456789" and d.commit == "abcdef01"
    assert d.migracoes == ["201", "202"]  # "8 pontas" não é migração
    assert d.backup == "20300305-181424"
    assert d.tag == "deploy-20300305-2115"
    assert d.agente == "0.1.0+abcdef0"
    assert d.resumo.startswith("corte de teste: tela nova")
    # versão do contrato (v1.95), do agente (0.1.0) e hora (20:19Z) não são item
    assert d.itens == ["31.901", "31.902", "ADR-901", "28.91", "31.903", "C17"]
    assert d.real is not None and d.real.startswith(": deploy `deploy.ps1`")
    assert d.simulated is not None and d.simulated.startswith("(suíte 91")
    assert d.not_run is not None and d.not_run.startswith("até a rodada seguinte")


def test_contagens_da_suite() -> None:
    assert _d(91).contagens == {"scripts": "790", "backend": "12528", "frontend": "1961", "catracas": "89 e 7",
                                "docs-check": "0", "mypy": "257", "postgresql": "5968 + 4120"}
    # o que o texto não traz não aparece (nunca vira zero)
    assert _d(90).contagens == {"scripts": "700", "frontend": "1900"}
    assert _d(89).contagens == {}


def test_sem_migracao_uma_migracao_e_hora_com_no_ar() -> None:
    d90 = _d(90)
    assert d90.migracoes == [] and d90.hora == "09:05"
    assert "sem migração" in titulo_marco(d90)
    d89 = _d(89)
    assert d89.migracoes == ["150"] and titulo_marco(d89).endswith("· migração 150")
    assert d89.real is None and d89.not_run is None and d89.backup is None and d89.tag is None


def test_titulo_nao_repete_a_migracao_que_o_resumo_do_cabecalho_ja_diz() -> None:
    d = _d(90)
    d.resumo = "correções do corte 56; sem migração"
    t = titulo_marco(d)
    assert t.count("sem migração") == 1 and t.endswith("sem migração")


def test_titulo_do_marco_no_formato_do_molde() -> None:
    t = titulo_marco(_d(91))
    assert t.startswith("📅 Deploy 91 · 05/03 21:15Z (abcdef01) · corte de teste: tela nova, regra configurável, relatório")
    assert t.endswith(" · migrações 201 e 202")


def test_corpo_tem_as_secoes_e_nao_inventa() -> None:
    c = corpo_marco(_d(91))
    for trecho in ("**Para quem não é técnico:**", "**Por que importa:**", "**Técnico:**", "**Prova `real`:**",
                   "**Prova `simulated`** (suíte 91", "**`not_run`:** até a rodada seguinte", "**Fonte:** `CHANGELOG.md`"):
        assert trecho in c, trecho
    assert c.count(NOTA_GERADO) == 2
    assert "no ar em 05/03/2030 às 21:15Z (18:15 Brasília)" in c
    assert "backup `20300305-181424`" in c and "tag `deploy-20300305-2115`" in c
    assert "agente do notebook `0.1.0+abcdef0`" in c
    assert "4 itens do plano (31.901, 31.902, 28.91, 31.903)" in c
    # sem a linha, o corpo diz que não consta em vez de preencher
    c89 = corpo_marco(_d(89))
    assert "**Prova `real`:** não consta no registro do CHANGELOG" in c89
    assert "sem migração" not in c89 and "migração 150" in c89


def test_decidir_criar_ou_atualizar_sem_duplicar() -> None:
    cartoes = [{"id": "a1", "name": "📅 Deploy 90 · 04/03 09:05Z (99998888) · x · sem migração"},
               {"id": "a2", "name": "📅 Deploy 910 · não é o 91"},
               {"id": "a3", "name": "Outra coisa 📅 Deploy 91 no meio"}]
    assert decidir_marco(91, cartoes) == ("criar", None)  # "Deploy 910" e "no meio" não contam
    assert decidir_marco(90, cartoes) == ("atualizar", "a1")
    cartoes.append({"id": "a4", "name": "📅 Deploy 91 · 05/03 21:15Z (abcdef01) · título antigo · migrações 201"})
    assert decidir_marco(91, cartoes) == ("atualizar", "a4")  # acha pelo prefixo, o título pode ter mudado
    assert decidir_marco(91, []) == ("criar", None)


def test_m9_das_contagens_do_changelog() -> None:
    nome, topo = montar_m9(_d(91), "05/03 22:00Z")  # type: ignore[misc]
    assert nome == ("📏 M9 · Testes automatizados — meta: toda suíte verde antes de integrar · "
                    "atual: 12 528 backend + 790 scripts + 1 961 frontend (suíte 91)")
    assert topo.startswith(f"{PREFIXO_LEITURA}05/03 22:00Z (prova simulada, CHANGELOG, deploy 91 sobre abcdef01):**")
    assert "PostgreSQL dirigido 5 968 + 4 120" in topo and "catracas 89 e 7" in topo and topo.endswith(SEPARADOR)
    assert montar_m9(_d(89), "x") is None  # sem contagem no registro, não há o que ler


def test_m8_conta_por_estado_e_diz_que_e_pontual() -> None:
    inst = [{"state": "online"}] * 7 + [{"state": "stopped"}] * 6 + [{"state": "error"}, {}]
    estados = contar_aparelhos(inst)  # type: ignore[arg-type]
    assert estados == Counter({"online": 7, "stopped": 6, "error": 1, "sem estado": 1})
    nome, topo = montar_m8(_d(91), estados, "05/03 22:00Z")
    assert nome.endswith("atual: 7 online de 15 (8 fora de online)")
    assert "15 aparelhos cadastrados, 7 online e 8 em outro estado (online 7, stopped 6, error 1, sem estado 1)" in topo
    assert "leitura é pontual" in topo and "(real, GET /api/instances" in topo


def test_prefixo_antigo_sai_antes_do_novo() -> None:
    antigo = f"{PREFIXO_LEITURA}01/01 00:00Z (x):** velho{SEPARADOR}descrição original\n\n---\n\noutra parte"
    assert sem_prefixo_leitura(antigo) == "descrição original\n\n---\n\noutra parte"
    assert sem_prefixo_leitura("sem prefixo\n\n---\n\nresto") == "sem prefixo\n\n---\n\nresto"
    assert sem_prefixo_leitura(f"{PREFIXO_LEITURA}sem separador") == f"{PREFIXO_LEITURA}sem separador"
    # duas leituras seguidas não empilham
    nova = "**Leitura de 02/01 00:00Z (y):** novo" + SEPARADOR
    assert (nova + sem_prefixo_leitura(antigo)).count(PREFIXO_LEITURA) == 1


def test_redacao_tira_handle_ip_e_credencial_do_cartao() -> None:
    d = _d(91)
    d.real = ": ok em 192.168.0.10 com @fulano_de_tal.teste e senha=abc12345"
    texto = redigir(titulo_marco(d) + "\n" + corpo_marco(d))
    assert "192.168.0.10" not in texto and "[ip]" in texto
    assert "@fulano_de_tal.teste" not in texto and "@[conta]" in texto
    assert "abc12345" not in texto and "[segredo]" in texto
    assert "abcdef01" in texto  # o hash de commit é referência de código e fica


def test_hora_de_agora_no_formato_do_molde() -> None:
    from marco import agora_rotulo
    assert agora_rotulo(datetime(2030, 3, 5, 22, 7, tzinfo=timezone.utc)) == "05/03 22:07Z"


def test_itens_que_ficaram_fora_do_deploy_nao_contam() -> None:
    texto = "Itens: 28.63, 28.64 e o teste do AnexosTab (28.62, 28.60 e 28.58 ficaram fora por conflito e vão no 59); Jev 15.15."
    assert itens_de(texto) == ["28.63", "28.64", "15.15"]
