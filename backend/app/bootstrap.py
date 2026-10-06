"""A montagem do `AppState` (15.15 K, F5c, recorte A): os blocos de `AppState.__init__` que só dependem de `cfg`, do banco e de peças já
montadas viram funções `montar_*`, que devolvem os objetos. O `__init__` continua dono dos atributos (o resto do código e o mypy leem
`state.<atributo>` como sempre) e chama estas funções na MESMA ordem em que construía antes: nenhuma construção mudou de lugar na sequência,
nem argumento, nem regra.

Só entra aqui o que não lê `self`. O que precisa do estado (a trava de líder `self._lider`, o relógio do banco, os atributos que as rotas leem)
entra como argumento e não como `AppState`; este módulo não importa `app.state` (ciclo). O corte inteiro (declarar os atributos na classe e
mover o `__init__`) fica para o recorte B.
"""
from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .db import Database
from .modules.avisos.infrastructure.servico import ServicoDeAvisos
from .modules.avisos.infrastructure.anexos import ArmazemDeAnexos
from .modules.avisos.infrastructure.canais_frota import CanaisDaFrota
from .modules.decisoes.infrastructure.adaptador_sql import AdaptadorDeDecisoes
from .modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from .modules.decisoes.infrastructure.registro_sql import RegistroSql as RegistroDeDecisoes
from .modules.decisoes.infrastructure.resumo_sql import ResumoDasDecisoes
from .modules.decisoes.infrastructure.servico import ServicoDeDecisoes
from .modules.learning.infrastructure.segredo import TriagemDeCredencial
from .storage import DISK, DiskStorage, Storage, build_storage
from .taskqueue.travas import Lideranca


def montar_armazenamento(cfg: Config) -> tuple[Storage, Storage]:
    """O storage de evidências (item 5.7: disco local por omissão, S3-compatível por bandeira; a chave gravada em `evidence.path` é chave de
    storage e é a mesma nas duas pontas) e o dos avatares de perfil, sob `avatars/<id>.jpg`. Em disco a raiz dos avatares é `data/`, então o
    arquivo continua onde sempre esteve; fora do disco, eles vão para o mesmo storage (no disco de uma réplica responderiam 404 na outra)."""
    storage = build_storage(
        cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
        endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
        access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
        secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
    avatares = DiskStorage(cfg.data_dir) if storage.name == DISK else storage
    return storage, avatares


def montar_lideranca_e_canais(cfg: Config, db: Database) -> tuple[Lideranca, CanaisDaFrota, ArmazemDeAnexos]:
    """A trava de líder dos laços de fundo (28.1: com dois backends com scheduler no mesmo banco, só um roda saldos, curadoria e retenção), os
    canais que este backend liga (28.37: a saúde acusa quando o líder da trava `avisos` não liga um deles) e os anexos dos canais (28.24: o
    arquivo em `data/anexos/`, fora do Git, pelo sha256; a faxina do 28.16 os apaga)."""
    lideranca = Lideranca(db, dono=cfg.owner_id)
    canais_da_frota = CanaisDaFrota(db, cfg, dono=cfg.owner_id, roda=cfg.roda_scheduler)
    anexos_canal = ArmazemDeAnexos(db, cfg.data_dir / "anexos")
    return lideranca, canais_da_frota, anexos_canal


def montar_decisoes(cfg: Config, db: Database, avisos: ServicoDeAvisos,
                    lider: Callable[[str], int | None]) -> tuple[RegistroDeDecisoes, ServicoDeDecisoes]:
    """O que a plataforma decide sozinha (28.25): o registro único, o adaptador que recolhe os produtores e o resumo agrupado (no máximo uma
    mensagem por janela) pelo mesmo caminho dos avisos. O desfazer entra pelas rotas."""
    registro = RegistroDeDecisoes(db)
    estado = EstadoDasDecisoes(db)
    servico = ServicoDeDecisoes(
        cfg, AdaptadorDeDecisoes(db, registro, estado, redigir=TriagemDeCredencial().redigir),
        ResumoDasDecisoes(db, estado, enfileirar=avisos.enfileirar_aviso,
                          pode_avisar=lambda: avisos.ligado and avisos.canal() is not None,
                          redigir=TriagemDeCredencial().redigir),
        lider=lider)
    return registro, servico
