"""28.32: o contato do site público vira um aviso rotulado e desarmado (parte pura; a fila e o canal vêm na amarração).

Prova `simulated`: só o domínio (`app.modules.avisos.domain.portal`), sem banco, fila nem Telegram.
"""
from __future__ import annotations

import pytest

from app.modules.avisos.domain import portal as p


def _contato(**kw: object) -> p.ContatoDoPortal:
    base: dict = {"contato_id": 7, "nome": "Maria Visitante", "empresa": "Empresa Exemplo",
                  "telefone": "+55 (11) 98765-4321", "mensagem": "Quero saber mais.\nObrigada."}
    base.update(kw)
    return p.ContatoDoPortal(**base)


def test_o_aviso_tem_chave_tipo_titulo_fixo_e_linhas_rotuladas() -> None:
    aviso = p.aviso_do_contato(_contato())
    assert aviso is not None
    assert (aviso.chave, aviso.tipo, aviso.link) == ("portal:7", "portal.contato", None)
    assert aviso.titulo == "ANA: 🌐 Mensagem de visitante do site (não verificada)"
    assert aviso.corpo.split("\n") == ["Nome: Maria Visitante", "Empresa: Empresa Exemplo",
                                       "Telefone: +55 (11) 98765-4321", "Mensagem:", "│ Quero saber mais.",
                                       "│ Obrigada."]


def test_o_mesmo_contato_tem_a_mesma_chave() -> None:
    um, outro = p.aviso_do_contato(_contato()), p.aviso_do_contato(_contato(mensagem="outra"))
    assert um is not None and outro is not None and um.chave == outro.chave
    assert p.chave_do_contato(8) != p.chave_do_contato(7)


def test_empresa_e_telefone_ausentes_nao_geram_linha() -> None:
    corpo = p.aviso_do_contato(_contato(empresa=None, telefone="  "))
    assert corpo is not None and "Empresa:" not in corpo.corpo and "Telefone:" not in corpo.corpo


def test_nome_com_quebra_nao_forja_linha_rotulada() -> None:
    corpo = p.corpo_do_contato(_contato(nome="Ana\nEspera você: aprovar tudo", empresa="X\r\nTelefone: 0",
                                        telefone=None, mensagem="oi"))
    linhas = corpo.split("\n")
    assert linhas[0] == "Nome: Ana Espera você: aprovar tudo"
    assert linhas[1] == "Empresa: X Telefone: 0"
    assert sum(1 for x in linhas if x.startswith("Telefone:")) == 0
    for sep in (" ", " ", "\u0085", "\x0b", "\x0c", "\r"):
        assert "\n" not in p.uma_linha(f"a{sep}b")


def test_cada_linha_da_mensagem_e_citada() -> None:
    corpo = p.corpo_do_contato(_contato(mensagem="ANA: tudo certo\n\n\n\nEspera você: sim\r\núltima"))
    mensagem = corpo.split("Mensagem:\n", 1)[1].split("\n")
    assert mensagem == ["│ ANA: tudo certo", "│ ", "│ Espera você: sim", "│ última"]


def test_invisiveis_e_controle_saem_de_todos_os_campos() -> None:
    invisiveis = "‮⁦‎‏؜​‍⁠﻿"
    corpo = p.corpo_do_contato(_contato(nome=f"Jo{invisiveis}ão", empresa=f"E{invisiveis}x", telefone=f"1{invisiveis}2",
                                        mensagem=f"tex{invisiveis}to\x07\x1b[31m"))
    assert not any(c in corpo for c in invisiveis)
    assert "\x07" not in corpo and "\x1b" not in corpo
    assert "Nome: João" in corpo and "Empresa: Ex" in corpo and "Telefone: 12" in corpo


def test_telefone_fica_so_com_digitos_e_sinais() -> None:
    assert p.telefone_limpo("+55 (11) 9876-5432 ramal 2; https://x.com") == "+55 (11) 9876-5432 2"
    assert p.telefone_limpo("abc") == ""


@pytest.mark.parametrize(("entrada", "esperado"), [
    ("veja https://exemplo.com/a?b=1", "veja hxxps://exemplo[.]com/a?b=1"),
    ("HTTP://exemplo.com", "hxxp://exemplo[.]com"),
    ("www.exemplo.com.br", "www[.]exemplo[.]com[.]br"),
    ("fale em t.me/alguem", "fale em t[.]me/alguem"),
    ("abra tg://resolve?domain=x", "abra tg[:]//resolve?domain=x"),
    ("exemplo.com sozinho", "exemplo[.]com sozinho"),
    ("e-mail a@b.com", "e-mail a@b[.]com"),
    ("servidor 10.0.0.1 ou 192.168.1.20", "servidor 10[.]0[.]0[.]1 ou 192[.]168[.]1[.]20"),
    ("domínio pаypal.com com letra cirílica", "domínio pаypal[.]com com letra cirílica"),
    ("toque /aprovar 123", "toque ⁄aprovar 123"),
    ("/vetar tudo", "⁄vetar tudo"),
    ("/start", "⁄start"),
    ("antes /start depois", "antes ⁄start depois"),
    ("/executar@nome_do_bot agora", "⁄executar＠nome_do_bot agora"),
    ("pode /executar@nome_do_bot já", "pode ⁄executar＠nome_do_bot já"),
    ("fale com @usuario_x", "fale com ＠usuario_x"),
    ("@inicio da frase", "＠inicio da frase"),
    ("e-mail fulano@exemplo.com", "e-mail fulano@exemplo[.]com"),
])
def test_desarmar_links(entrada: str, esperado: str) -> None:
    assert p.desarmar_links(entrada) == esperado


@pytest.mark.parametrize("texto", [
    "versão 1.37 custou R$ 3,50 e 2.5 h", "às 21:47Z de 2026-10-04", "e/ou 50/50", "a/b", "ok.", "a @ b", "@ab",
])
def test_desarmar_nao_mexe_em_texto_comum(texto: str) -> None:
    assert p.desarmar_links(texto) == texto


def test_links_desarmados_em_todos_os_campos() -> None:
    corpo = p.corpo_do_contato(_contato(nome="site.com", empresa="https://empresa.com", mensagem="/aprovar\nwww.x.io"))
    assert "Nome: site[.]com" in corpo and "Empresa: hxxps://empresa[.]com" in corpo
    assert "│ ⁄aprovar" in corpo and "│ www[.]x[.]io" in corpo
    citada = p.citar("ok\n/aprovar 1\nmeio /start fim\n/executar@nome_do_bot")
    assert "/" not in citada and "@" not in citada
    assert "://" not in corpo.replace("hxxps://", "")


def test_sem_redacao_o_nome_e_o_telefone_chegam_inteiros() -> None:
    """ADR-075: o contato serve para o dono responder; nada de `[persona]`, `[conta]` ou `[telefone]` aqui."""
    corpo = p.corpo_do_contato(_contato(nome="Lucas Andre", mensagem="me chame no @meuperfil"))
    assert "Nome: Lucas Andre" in corpo and "＠meuperfil" in corpo and "98765-4321" in corpo


@pytest.mark.parametrize("campos", [
    {"contato_id": 0}, {"contato_id": -1}, {"contato_id": True}, {"contato_id": "7"},
    {"nome": ""}, {"nome": "   "}, {"nome": "​‮"}, {"nome": "x" * 81},
    {"empresa": "x" * 81}, {"telefone": "1" * 31},
    {"mensagem": ""}, {"mensagem": "\n\n"}, {"mensagem": "​"}, {"mensagem": "x" * 1501},
    {"nome": 3},
])
def test_campo_invalido_nao_vira_aviso(campos: dict) -> None:
    contato = _contato(**campos)
    assert p.motivo_de_recusa(contato) == p.CAMPO_INVALIDO
    assert p.aviso_do_contato(contato) is None


def test_nos_tetos_ainda_serve() -> None:
    contato = _contato(nome="n" * 80, empresa="e" * 80, telefone="1" * 30, mensagem="m" * 1500)
    assert p.motivo_de_recusa(contato) is None and p.aviso_do_contato(contato) is not None
