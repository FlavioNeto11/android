"""28.32: o contato do site público vira um aviso rotulado e desarmado (parte pura; a fila e o canal vêm na amarração).

Prova `simulated`: só o domínio (`app.modules.avisos.domain.portal`), sem banco, fila nem Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone

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
    invisiveis = "\u202e\u2066\u200e\u200f\u061c\u200b\u200d\u2060\ufeff"
    corpo = p.corpo_do_contato(_contato(nome=f"Jo{invisiveis}ão", empresa=f"E{invisiveis}x", telefone=f"1{invisiveis}2",
                                        mensagem=f"tex{invisiveis}to\x07\x1b[31m"))
    assert not any(c in corpo for c in invisiveis)
    assert "\x07" not in corpo and "\x1b" not in corpo
    assert "Nome: João" in corpo and "Empresa: Ex" in corpo and "Telefone: 12" in corpo


@pytest.mark.parametrize("branco", ["\u2003", "\u3000", "\u00a0", "\u2800", "\u3164", "\u115f", "\u1160", "\uffa0"])
def test_branco_unicode_nao_empurra_o_texto_para_o_comeco_da_linha(branco: str) -> None:
    """Revisão do #331 (A1): 200 brancos que não são o espaço ASCII levavam o "ANA:" do visitante à coluna 0 da linha
    seguinte da tela, sem o `│ `, no nome e na mensagem."""
    forja = "oi" + branco * 200 + "ANA: \u26a0\ufe0f Precisa de você"
    corpo = p.corpo_do_contato(_contato(nome=forja, mensagem=forja, empresa=None, telefone=None))
    assert corpo.split("\n") == ["Nome: oi ANA: \u26a0\ufe0f Precisa de você", "Mensagem:",
                                 "│ oi ANA: \u26a0\ufe0f Precisa de você"]


def test_toda_a_categoria_de_formato_sai() -> None:
    assert p.uma_linha("a\u00adb\u2061c\U000e0041d\u2064e") == "abcde"
    assert p.citar("x\u00ady") == "│ xy"


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
    ("domínio p\u0430ypal.com com letra cirílica", "domínio p\u0430ypal[.]com com letra cirílica"),
    ("toque /aprovar 123", "toque ⁄aprovar 123"),
    ("/vetar tudo", "⁄vetar tudo"),
    ("/start", "⁄start"),
    ("antes /start depois", "antes ⁄start depois"),
    ("/executar@nome_do_bot agora", "⁄executar＠nome_do_bot agora"),
    ("pode /executar@nome_do_bot já", "pode ⁄executar＠nome_do_bot já"),
    ("fale com @usuario_x", "fale com ＠usuario_x"),
    ("@inicio da frase", "＠inicio da frase"),
    ("e-mail fulano@exemplo.com", "e-mail fulano\uff20exemplo[.]com"),
    # revisão do #331 (A2): pontuação antes do comando, ponto ideográfico e de largura cheia, letra colada antes do
    # esquema, `tg:` sem barras, outro esquema e o `[.]` digitado pelo visitante depois da menção
    ("ok,/status", "ok,\u2044status"),
    ("(/pendencias)", "(\u2044pendencias)"),
    ("exemplo\u3002com", "exemplo[.]com"),
    ("exemplo\uff0ecom", "exemplo[.]com"),
    ("exemplo\uff61com", "exemplo[.]com"),
    ("\u0430https://x.com", "\u0430hxxps://x[.]com"),
    ("tg:resolve", "tg[:]resolve"),
    ("ftp://x.com", "ftp[:]//x[.]com"),
    ("@usuario[.]x", "\uff20usuario[.]x"),
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
    {"nome": ""}, {"nome": "   "}, {"nome": "\u200b\u202e"}, {"nome": "x" * 81},
    {"empresa": "x" * 81}, {"telefone": "1" * 31},
    {"mensagem": ""}, {"mensagem": "\n\n"}, {"mensagem": "\u200b"}, {"mensagem": "x" * 1501},
    {"nome": 3},
])
def test_campo_invalido_nao_vira_aviso(campos: dict) -> None:
    contato = _contato(**campos)
    assert p.motivo_de_recusa(contato) == p.CAMPO_INVALIDO
    assert p.aviso_do_contato(contato) is None


def test_nos_tetos_ainda_serve() -> None:
    contato = _contato(nome="n" * 80, empresa="e" * 80, telefone="1" * 30, mensagem="m" * 1500)
    assert p.motivo_de_recusa(contato) is None and p.aviso_do_contato(contato) is not None


def test_ordinais_do_portugues_ficam() -> None:
    """Revisão do #331: o NFKC trocava `º` e `ª` por `o` e `a`. O resto da compatibilidade segue."""
    texto = "n\u00ba 12, 1\u00aa via, 2\u00ba andar"
    assert p.uma_linha(texto) == texto
    assert p.citar(texto) == "│ " + texto
    assert p.desarmar_links(texto) == texto
    assert p.uma_linha("10 m\u00b2, marca\u2122") == "10 m2, marcaTM"


def test_marcas_combinantes_empilhadas_saem() -> None:
    """Revisão do #331: 30 marcas (Mn) por cima de uma letra eram desenhadas sobre o título e o `│`. Ficam 2."""
    zalgo = "a" + "\u0301\u0300\u0302" * 10 + "b"
    linha = p.uma_linha(zalgo)
    assert linha == "\u00e1\u0300\u0302b"           # o NFKC compõe o 1º acento com a letra; ficam 2 marcas soltas
    citada = p.citar(zalgo + "\n" + "\u0336" * 30 + "x\nfim")
    # revisão do #335 (N1): a marca no começo da linha não tem base e sairia por cima do `│ `
    assert citada.split("\n") == ["│ \u00e1\u0300\u0302b", "│ x", "│ fim"]
    assert p.uma_linha("\u20d2" * 5 + "Ana") == "Ana" and p.citar("\u0338ok") == "│ ok"
    assert p.uma_linha("e\u0301 voc\u00ea") == "\u00e9 voc\u00ea"          # o acento composto não é marca solta
    assert p.uma_linha("x" + "\u20dd" * 5) == "x\u20dd\u20dd"                  # a marca envolvente (Me) também


AGORA = datetime(2026, 10, 4, 22, 41, 7, tzinfo=timezone.utc)


def test_resumo_chave_por_hora_e_so_contagens() -> None:
    aviso = p.aviso_do_resumo(3, 0, 1, AGORA)
    assert aviso is not None
    # abaixo do limiar: a rotina (nível 3), com as contagens no título
    assert (aviso.chave, aviso.tipo, aviso.link, aviso.nivel) == ("portal-resumo:2026-10-04T22Z", "portal.resumo_rotina",
                                                                  None, 3)
    assert aviso.titulo == "ANA: 🌐 Contatos do site acima do limite: 3 guardados, 0 descartados"
    assert aviso.corpo.split("\n") == ["3 contatos guardados sem aviso e 0 descartados na última hora.",
                                       "Crítico: nada.", "Nada a fazer: os guardados ficam na Central, sem aviso."]
    outra = p.aviso_do_resumo(9, 1, 2, AGORA.replace(minute=59))
    assert outra is not None and outra.chave == aviso.chave
    # acima do limiar: nível 2, sai na hora, título fixo
    assert (outra.tipo, outra.nivel, outra.titulo) == ("portal.resumo", 2, "ANA: 🌐 Contatos do site acima do limite")


@pytest.mark.parametrize(("retidos", "descartados", "espera"), [
    (19, 0, False), (20, 0, True), (0, 1, True), (1, 0, False), (0, 500, True)])
def test_resumo_acima_do_limiar_aponta_o_abuso_e_nunca_espera_o_dono(retidos: int, descartados: int,
                                                                     espera: bool) -> None:
    """Orquestradora, 04/10 22:37Z: sem gesto possível no aviso, nunca "Espera você"; o formulário se protege."""
    linhas = p.corpo_do_resumo(retidos, descartados, 1).split("\n")
    assert len(linhas) == 3 and not any("Espera você" in x for x in linhas)
    if espera:
        assert linhas[1:] == ["Crítico: possível abuso do formulário de contato do site.",
                              "Nada a fazer agora: o formulário se protege sozinho. Se quiser desligar o contato do "
                              "site, diga no chat da orquestradora."]
    else:
        assert linhas[1:] == ["Crítico: nada.", "Nada a fazer: os guardados ficam na Central, sem aviso."]


def test_resumo_singular_e_janela() -> None:
    assert p.corpo_do_resumo(1, 1, 3).split("\n")[0] == "1 contato guardado sem aviso e 1 descartado nas últimas 3 h."


@pytest.mark.parametrize(("retidos", "descartados", "janela_h"), [
    (0, 0, 1), (-1, 2, 1), (1, -1, 1), (True, 0, 1), (1, 0, 0), (1, 0, 25), (1.5, 0, 1), ("3", 0, 1),
    (p.CONTAGEM_MAX + 1, 0, 1), (1, 0, True)])
def test_resumo_invalido(retidos: object, descartados: object, janela_h: object) -> None:
    assert not p.resumo_valido(retidos, descartados, janela_h)
    assert p.aviso_do_resumo(retidos, descartados, janela_h, AGORA) is None  # type: ignore[arg-type]
