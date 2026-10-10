"""Rascunho de `telas.yaml` a partir de hierarquias que a PESSOA percorreu (item 12.3). Só leitura, sem IA, sem aparelho (US$ 0).

Entrada: arquivos XML de hierarquia (o `page_source` do Appium ou `adb exec-out uiautomator dump`), um por tela, que a pessoa
guardou ao percorrer o app no controle manual (`GET /api/instances/{id}/hierarchy`, ver docs/dominios/ ... controle manual).
Saída: um esboço de `telas.yaml` com uma regra por arquivo (nome = nome do arquivo) e sinais candidatos tirados dos textos
estáveis da tela, para um humano EDITAR. O esboço:

- diz `RASCUNHO` na razão de cada regra e nunca escolhe `tipo` por você: login (campo de senha), `autenticada` e o resto são
  palpites marcados para conferir; quem classifica é a pessoa;
- é conferido pelo carregador (`conhecimento_de_telas.de_dados`) e o resultado vai para o stderr: se o esboço não carrega, o que
  falta aparece lá (não existe `estado_conhecido` sem você dizer quais são as telas de casa);
- deixa de fora o que identifica a pessoa: o texto de campo editável e de senha, o texto de um elemento cujo `resource-id` diz
  nome, conta, usuário, perfil, avatar ou contato, e tudo que casar com `--ignorar` (o nome de exibição, o @ ou o e-mail da
  conta que a pessoa percorreu). O nome de quem percorreu o app é dado pessoal, muda a cada conta e nunca serve de sinal;
- NUNCA grava em `backend/app/conhecimento/apps/`: a saída vai para o stdout ou para `--saida`, e um destino dentro dessa
  pasta é recusado. Promover o esboço para o app é um commit seu, depois da decisão do dono sobre qual app vem primeiro.

Exemplo (a partir da raiz do repositório):
    backend/.venv/Scripts/python.exe scripts/rascunho-de-telas.py --app com.exemplo.email --ignorar "Ana Correio" entrada.xml caixa.xml --saida rascunho.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "backend"))

from app.automation import conhecimento_de_telas as telas  # noqa: E402
from app.automation.hierarchy import UiElement, parse_hierarchy  # noqa: E402
from app.planning.capabilities import CONHECIMENTO_DE_APPS  # noqa: E402

#: Texto que muda de uma abertura para a outra (hora, contagem, e-mail, @) não serve de sinal.
_INSTAVEL = re.compile(r"\d|@|https?://")
#: O `resource-id` de um elemento que mostra a IDENTIDADE da pessoa (o nome na barra, o e-mail da conta, o avatar): o texto dele
#: nunca é sinal, mesmo que pareça estável.
_ID_DE_IDENTIDADE = re.compile(r"name|nome|account|conta|user|usuario|profile|perfil|avatar|display|owner|sender|contact|contato|"
                               r"e?mail_?address|handle", re.IGNORECASE)
MINIMO_DO_IGNORAR = 3
SINAIS_POR_TELA = 2
IDS_POR_TELA = 3


def _normal(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower().strip()


def _e_da_pessoa(e: UiElement, ignorar: tuple[str, ...]) -> bool:
    """O texto do elemento é a identidade de quem percorreu o app? Pelo `resource-id` de identidade ou por casar (um contém o
    outro, sem acento nem caixa) com um dos `--ignorar`. 'Ana' dentro de 'Ana Correio' e 'Ana Correio' dentro de 'Olá, Ana Correio'
    casam; o `--ignorar` curto demais (menos de 3 letras) é recusado pelo `main`, porque casaria quase tudo."""
    if e.resource_id and _ID_DE_IDENTIDADE.search(e.resource_id.rsplit("/", 1)[-1]):
        return True
    texto = _normal(e.text or "")
    return bool(texto) and any(i in texto or texto in i for i in ignorar)


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_") or "tela"


def _regex_do_texto(texto: str) -> str:
    """O texto inteiro, sem acento nem caixa (o conhecimento de telas normaliza a tela antes de comparar)."""
    return r"^\s*" + re.escape(_normal(texto)) + r"\s*$"


def _ids(elementos: list[UiElement]) -> list[str]:
    vistos: list[str] = []
    for e in elementos:
        sufixo = e.resource_id.rsplit("/", 1)[-1] if e.resource_id else ""
        if sufixo and not sufixo.startswith("android:") and sufixo not in vistos and not _INSTAVEL.search(sufixo):
            vistos.append(sufixo)
    return vistos[:IDS_POR_TELA]


def _textos_estaveis(elementos: list[UiElement], ignorar: tuple[str, ...] = ()) -> list[str]:
    achados: list[str] = []
    for e in elementos:
        t = (e.text or "").strip()
        if (3 <= len(t) <= 40 and not _INSTAVEL.search(t) and not e.editable and not e.password
                and not _e_da_pessoa(e, ignorar) and t not in achados):
            achados.append(t)
    return achados[:SINAIS_POR_TELA]


def rascunhar(app: str, arquivos: list[Path], ignorar: tuple[str, ...] = ()) -> dict[str, Any]:
    ignorar = tuple(n for n in (_normal(i) for i in ignorar) if len(n) >= MINIMO_DO_IGNORAR)
    sinais: dict[str, str] = {}
    regras: list[dict[str, Any]] = []
    nomes: set[str] = set()
    for arquivo in arquivos:
        arvore = parse_hierarchy(arquivo.read_text(encoding="utf-8"))
        nome = _slug(arquivo.stem)
        if nome in nomes:
            raise SystemExit(f"{arquivo}: dois arquivos dão a mesma tela {nome!r}; renomeie um")
        nomes.add(nome)
        regra: dict[str, Any] = {"tela": nome, "tipo": "desconhecida_a_classificar", "razao": f"RASCUNHO de {arquivo.name}: revise"}
        if any(e.password for e in arvore.elements):
            regra.update(tipo="login", formulario_de_senha=True)
        ids = _ids(arvore.elements)
        if ids:
            regra["ids"] = ids
        for i, texto in enumerate(_textos_estaveis(arvore.elements, ignorar)):
            chave = f"{nome}_{i + 1}"
            sinais[chave] = _regex_do_texto(texto)
            regra.setdefault("sinal", chave)
        regras.append(regra)
    return {"app": app, "versao": 1, "idioma_padrao": "pt", "sinais": {"pt": sinais}, "extracoes": {}, "telas": regras,
            "estado_conhecido": {"telas": [], "voltar_max": 2, "reabrir": True}}


def _conferir(dados: dict[str, Any]) -> list[str]:
    """O que o carregador acha do esboço. Os dois problemas de partida são esperados e dizem o que falta decidir."""
    avisos: list[str] = []
    tipos_invalidos = [r["tela"] for r in dados["telas"] if r["tipo"] not in telas.TIPOS]
    if tipos_invalidos:
        avisos.append(f"defina o `tipo` de: {', '.join(tipos_invalidos)} (vocabulário: {', '.join(sorted(telas.TIPOS))})")
    if not dados["estado_conhecido"]["telas"]:
        avisos.append("preencha `estado_conhecido.telas` com as telas de casa (as de dentro do app, já logado)")
    if not tipos_invalidos and dados["estado_conhecido"]["telas"]:
        try:
            telas.de_dados(dados)
        except telas.ConhecimentoInvalido as exc:
            avisos.append(f"o carregador recusa o esboço: {exc}")
    return avisos


def _destino_permitido(destino: Path) -> bool:
    """Recusa gravar dentro de `backend/app/conhecimento/apps/`: o esboço é de revisão, nunca vai direto ao app."""
    try:
        destino.resolve().relative_to(CONHECIMENTO_DE_APPS.resolve())
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--app", required=True, help="o pacote Android (com.exemplo.app)")
    ap.add_argument("--saida", type=Path, help="grava aqui em vez de imprimir (nunca dentro de app/conhecimento/apps)")
    ap.add_argument("--ignorar", action="append", default=[], metavar="TEXTO",
                    help="o nome de exibição, o @ ou o e-mail da conta que percorreu o app; o texto que casar nunca vira sinal "
                         "(repita a opção para mais de um; pelo menos 3 letras)")
    ap.add_argument("xml", nargs="+", type=Path, help="hierarquias XML, uma por tela")
    args = ap.parse_args(argv)
    if args.saida is not None and not _destino_permitido(args.saida):
        print(f"recusado: {args.saida} está dentro de app/conhecimento/apps; grave fora e revise antes de promover", file=sys.stderr)
        return 2
    if args.saida is not None and args.saida.resolve() in {p.resolve() for p in args.xml}:
        print(f"recusado: {args.saida} é também uma das entradas; a captura da pessoa não é sobrescrita", file=sys.stderr)
        return 2
    faltando = [str(p) for p in args.xml if not p.is_file()]
    if faltando:
        print(f"arquivo não encontrado: {', '.join(faltando)}", file=sys.stderr)
        return 2
    curtos = [i for i in args.ignorar if len(_normal(i)) < MINIMO_DO_IGNORAR]
    if curtos:
        print(f"recusado: --ignorar precisa de pelo menos {MINIMO_DO_IGNORAR} letras (casaria quase tudo): {curtos}", file=sys.stderr)
        return 2
    dados = rascunhar(args.app, list(args.xml), tuple(args.ignorar))
    texto = ("# RASCUNHO gerado por scripts/rascunho-de-telas.py: revise tipos, sinais e estado_conhecido antes de promover.\n"
             "# ATENÇÃO: o texto das telas pode conter o NOME da conta ou da pessoa (ex.: a tela de casa). Troque por um rótulo fixo\n"
             "# do app ou apague o sinal ANTES de commitar: nome próprio é dado pessoal e muda a cada conta.\n"
             + yaml.safe_dump(dados, allow_unicode=True, sort_keys=False))
    if args.saida is not None:
        args.saida.write_text(texto, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(texto)
    for aviso in _conferir(dados):
        print(f"[rascunho] {aviso}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
