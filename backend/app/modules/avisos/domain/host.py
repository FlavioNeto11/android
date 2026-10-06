"""O que a Central confere no PRÓPRIO host e avisa pela rotina (itens 28.60 e 28.58). Domínio puro: recebe valores
simples, devolve `Aviso`; quem lê o arquivo, o disco e o relógio é o `VigiaDoHost`.

Dois assuntos, dois tipos, os dois no nível 3 (`mensagem.NIVEL_POR_TIPO`): a janela da rotina os junta numa mensagem só e
mostra UMA LINHA POR AVISO, que é o título. Por isso o título leva o resultado e os números; o corpo (molde do 28.31:
resultado, crítico, "Espera você" ou "Nada a fazer") só sai quando o aviso vai sozinho.

- `host.restore_ensaio` (28.60): o veredito do `scripts/restore-ensaio.ps1` (`data/restore-ensaio/ultimo.json`) deu
  `falhou` ou `pulado`, o arquivo não pôde ser lido, ou o último veredito ficou velho (a tarefa semanal parou);
- `host.disco_baixo` (28.58): o livre do disco do central caiu abaixo do piso, e de novo a cada degrau abaixo dele.

Nenhum caminho de arquivo, usuário, nome de máquina, valor de tabela ou segredo entra na mensagem. O `motivo` do ensaio é
texto do script: só passa por uma lista branca de caracteres (sem barra, sem `@`, sem `?`), e o `pulado` usa frases fixas.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from .mensagem import Aviso, chave_do_fato, nivel_do_tipo, titulo_do_aviso

TIPO_DO_ENSAIO = "host.restore_ensaio"
TIPO_DO_DISCO = "host.disco_baixo"

# ---------------------------------------------------------------------- o ensaio de restauração (28.60)
RESULTADOS_DO_ENSAIO = frozenset({"ok", "falhou", "pulado"})
#: `AAAAMMDD-HHmmss`, o nome da pasta da cópia. Só a data sai ("cópia de 05/10").
_COPIA = re.compile(r"(\d{4})(\d{2})(\d{2})-\d{6}")
#: O que o `motivo` do script pode ter para sair: letras, dígitos, espaço e pontuação de frase. Barra (de caminho),
#: `@`, `?`, `=` e aspas ficam de fora, e o texto inteiro é recusado se qualquer um aparecer.
_MOTIVO_SEGURO = re.compile(r"[0-9A-Za-zÀ-ÿ ().,;:%+_-]{1,160}")
GESTO_DO_ENSAIO = "Espera você: peça à orquestradora para repetir o ensaio e olhar o veredito. Nada é apagado."


@dataclass(frozen=True, slots=True)
class Veredito:
    """O `ultimo.json` do ensaio, só com o que o aviso usa."""

    resultado: str            # `ok`, `falhou` ou `pulado`
    ts_utc: str               # o carimbo como o script gravou (a chave do aviso)
    ts: datetime              # o mesmo carimbo, com fuso
    copia: str                # nome da pasta da cópia (vazio no `pulado` sem cópia)
    motivo: str               # texto do script (não sai sem passar pela lista branca)


def _utc(texto: object) -> datetime | None:
    if not isinstance(texto, str):
        return None
    try:
        quando = datetime.fromisoformat(texto.strip())
    except ValueError:
        return None
    return quando.astimezone(timezone.utc) if quando.tzinfo is not None else None


def veredito_de(dados: object) -> Veredito | None:
    """O veredito, ou `None` quando o JSON não é um objeto, o `resultado` não é do contrato ou falta o `ts_utc` (sem ele
    não há chave nem idade: o arquivo conta como ilegível)."""
    if not isinstance(dados, Mapping):
        return None
    resultado, ts_utc = dados.get("resultado"), dados.get("ts_utc")
    ts = _utc(ts_utc)
    if resultado not in RESULTADOS_DO_ENSAIO or ts is None or not isinstance(ts_utc, str):
        return None
    copia, motivo = dados.get("copia"), dados.get("motivo")
    return Veredito(str(resultado), ts_utc.strip(), ts, copia if isinstance(copia, str) else "",
                    motivo if isinstance(motivo, str) else "")


def _da_copia(copia: str) -> str:
    achou = _COPIA.fullmatch(copia.strip())
    return f"cópia de {achou.group(3)}/{achou.group(2)}" if achou else "cópia mais nova"


def _motivo_que_sai(motivo: str) -> str:
    texto = " ".join(motivo.split())
    return texto if _MOTIVO_SEGURO.fullmatch(texto) else ""


def _dia(agora: datetime) -> str:
    return f"{agora.astimezone(timezone.utc):%Y-%m-%d}"


def aviso_do_ensaio(v: Veredito) -> Aviso | None:
    """`falhou` ou `pulado` de UM veredito. A chave é a do veredito (`restore-ensaio:<resultado>:<ts_utc>`): o mesmo
    `ultimo.json` lido a cada tick vira uma mensagem só. `ok` não avisa."""
    chave = chave_do_fato("restore-ensaio", v.resultado, v.ts_utc)
    if v.resultado == "falhou":
        motivo = _motivo_que_sai(v.motivo)
        linhas = [f"O ensaio semanal não conseguiu restaurar a {_da_copia(v.copia)}"
                  + (f": {motivo}." if motivo else "."),
                  "Crítico: sem o ensaio não há prova de que o backup mais novo volta a abrir. O banco vivo não foi tocado.",
                  GESTO_DO_ENSAIO]
        return Aviso(chave=chave, tipo=TIPO_DO_ENSAIO,
                     titulo=titulo_do_aviso(f"🧪 Ensaio de restauração falhou ({_da_copia(v.copia)})"),
                     corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DO_ENSAIO))
    if v.resultado == "pulado":
        # Frase fixa: o motivo do script cita a pasta das cópias.
        postgres = "postgresql" in v.motivo.lower()
        causa = ("a cópia mais nova é de PostgreSQL, que o ensaio ainda não cobre" if postgres
                 else "não havia cópia com manifesto para ensaiar")
        gesto = ("Nada a fazer agora: o ensaio só cobre cópia SQLite." if postgres
                 else "Espera você: peça à orquestradora para conferir se o backup diário (farm-backup) está gerando cópias.")
        linhas = [f"O ensaio semanal foi pulado: {causa}.",
                  "Crítico: o backup desta semana ficou sem prova de restauração.", gesto]
        return Aviso(chave=chave, tipo=TIPO_DO_ENSAIO,
                     titulo=titulo_do_aviso("🧪 Ensaio de restauração pulado: " + ("cópia de PostgreSQL" if postgres
                                                                                  else "sem cópia para ensaiar")),
                     corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DO_ENSAIO))
    return None


def aviso_do_ensaio_velho(ultimo: datetime, agora: datetime) -> Aviso:
    """O último veredito ficou mais velho que o limite (`avisos.restore_ensaio.idade_max_h`): a tarefa semanal deixou de
    rodar. Uma mensagem por dia UTC enquanto durar, mesmo que o veredito antigo fosse `ok`."""
    dias = max(1, int((agora - ultimo).total_seconds() // 86400))
    linhas = [f"O último veredito do ensaio é de {ultimo.astimezone(timezone.utc):%d/%m} ({dias} dias): a tarefa semanal "
              "deixou de rodar.",
              "Crítico: backup sem ensaio é suposição, e a última prova de restauração ficou velha.",
              "Espera você: peça à orquestradora para conferir a tarefa agendada e rodar o ensaio."]
    return Aviso(chave=chave_do_fato("restore-ensaio", "velho", _dia(agora)), tipo=TIPO_DO_ENSAIO,
                 titulo=titulo_do_aviso(f"🧪 Ensaio de restauração sem rodar há {dias} dias"),
                 corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DO_ENSAIO))


def aviso_do_ensaio_ilegivel(agora: datetime) -> Aviso:
    """O arquivo do veredito existe e não pôde ser lido (JSON quebrado, sem o resultado ou sem o carimbo): um pipeline
    quebrado não pode parecer saudável. Uma mensagem por dia UTC."""
    linhas = ["O arquivo do último veredito existe, mas não pôde ser lido (JSON inválido ou sem o resultado).",
              "Crítico: não sei dizer se o último ensaio de restauração passou.",
              GESTO_DO_ENSAIO.replace("repetir o ensaio e olhar o veredito", "olhar o ensaio")]
    return Aviso(chave=chave_do_fato("restore-ensaio", "ilegivel", _dia(agora)), tipo=TIPO_DO_ENSAIO,
                 titulo=titulo_do_aviso("🧪 Ensaio de restauração: veredito ilegível"),
                 corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DO_ENSAIO))


# ---------------------------------------------------------------------- disco baixo no central (28.58)
#: Em GB: o aviso vira crítico abaixo disto (a criação de AVD e o backup precisam de folga).
CRITICO_PADRAO_GB = 60.0
NAO_MEDIDO = "não medido"


def degrau_do_disco(livre_gb: float, piso_gb: float, degrau_gb: float) -> float | None:
    """O degrau (em GB) em que o livre está: `None` com o livre no piso ou acima; senão o piso e, a cada `degrau_gb` a menos,
    o próximo (100, 80, 60...; o último é o maior degrau positivo). Com o livre exatamente num degrau, vale o de cima:
    80 GB livres ainda são "abaixo de 100"."""
    if livre_gb >= piso_gb or piso_gb <= 0 or degrau_gb <= 0:
        return None
    k = min(math.ceil((piso_gb - livre_gb) / degrau_gb) - 1, math.ceil(piso_gb / degrau_gb) - 1)
    return piso_gb - k * degrau_gb


def _gb(valor: float | None) -> str:
    return NAO_MEDIDO if valor is None else f"{valor:.0f} GB"


def aviso_do_disco(livre_gb: float, total_gb: float, degrau: float, ocupacao: Mapping[str, float | None], *,
                   critico_gb: float, proximo: float | None, episodio: int, agora: datetime) -> Aviso:
    """O aviso do degrau. `ocupacao` é nome → GB (ou `None`: não medido); a Central NUNCA apaga o que está nela. A chave é
    `disco-baixo:<degrau>:<dia UTC>:<episódio>`: o `episódio` sobe cada vez que o disco volta ao piso e cai de novo, então
    a recaída do mesmo dia avisa de novo, e a subida do processo (episódio 0) não repete o que já saiu naquele dia."""
    critico = livre_gb < critico_gb
    onde = ", ".join(f"{nome} {_gb(gb)}" for nome, gb in ocupacao.items())
    fim = (f"o próximo aviso sai abaixo de {proximo:.0f} GB" if proximo is not None and proximo > 0
           else "este é o último degrau")
    linhas = [f"Livre: {livre_gb:.0f} GB de {total_gb:.0f} GB. Ocupam: {onde}.",
              ("Crítico: backups e criação de aparelho podem falhar." if critico else "Crítico: nada."),
              ("Espera você: libere espaço (backups antigos, AVDs sem uso, capturas velhas); a Central não apaga nada."
               if critico else f"Nada a fazer agora; {fim}.")]
    rotulo = f"{degrau:.0f}"
    return Aviso(chave=chave_do_fato("disco-baixo", rotulo, _dia(agora), str(episodio)), tipo=TIPO_DO_DISCO,
                 titulo=titulo_do_aviso(f"💾 Disco do central com {livre_gb:.0f} GB livres (abaixo de {rotulo} GB)"),
                 corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DO_DISCO))
