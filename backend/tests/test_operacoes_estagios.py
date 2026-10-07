"""31.154: os estágios de um alvo da operação, derivados do que a execução grava, no vocabulário e na ordem do dono.

`simulated`: fatos montados à mão, sem banco nem aparelho. Prova a regra pura: parada na criação, caminho até a ação
preparada (barreira) e até o resultado verificado, lacuna sem inferência, falha de um alvo sem efeito nos outros (30).
"""
from __future__ import annotations

import pytest

from app.integrations.app_declarado.pacote import PacoteInvalido, definicao_de_dados
from app.modules.applications.infrastructure.registry import definition_of
from app.modules.operacoes.domain.estagios import ESTAGIOS, EtapaLida, FatosDoAlvo, derivar

IG = {"OPEN_PROFILE": "target_localizado", "OPEN_POST": "post_localizado",
      "OPEN_COMMENTS": "interface_de_comentario_alcancada"}
T0 = "2026-10-07T10:00:00.000Z"


def _etapa(cap: str | None, status: str, *, efeito: bool = False, texto: bool = False, verificada: bool = False,
           pedido: str | None = None, h: str = "01") -> EtapaLida:
    return EtapaLida(capability=cap, status=status, side_effect=efeito, terminou_em=f"2026-10-07T10:{h}:30.000Z"
                     if status == "succeeded" else None, comecou_em=f"2026-10-07T10:{h}:00.000Z"
                     if status != "pending" else None, tem_texto=texto, verificada=verificada,
                     pedido_de_aprovacao=pedido)


def _fatos(etapas: list[EtapaLida], *, status: str = "running", acao_final: str = "preparar", marcas: dict[str, str]
           | None = None, motivo: str | None = None) -> FatosDoAlvo:
    return FatosDoAlvo(parada_na_criacao=None, motivo_na_criacao=None, objetivo_status=status,
                       objetivo_comecou_em="2026-10-07T10:00:30.000Z", objetivo_motivo=motivo, run_status="running",
                       etapas=etapas, marcas=marcas or {}, abertura="instagram_aberto", estagio_por_capability=IG,
                       acao_final=acao_final, criado_em=T0)


def _nomes(leitura: object) -> list[str]:
    return [e for e, _ in leitura.estagios]  # type: ignore[attr-defined]


def test_o_alvo_sem_conta_para_em_conta_com_o_motivo_e_nunca_ganha_estagio_de_app() -> None:
    lt = derivar(FatosDoAlvo(parada_na_criacao="conta", motivo_na_criacao="sem conta", objetivo_status=None,
                             objetivo_comecou_em=None, objetivo_motivo=None, run_status=None, criado_em=T0))
    assert (lt.estagio, lt.estado, lt.motivo, lt.parou_em) == ("persona", "bloqueado", "sem conta", "conta")
    assert _nomes(lt) == ["persona"]
    lt = derivar(FatosDoAlvo(parada_na_criacao="sessao", motivo_na_criacao="sem sessão", objetivo_status=None,
                             objetivo_comecou_em=None, objetivo_motivo=None, run_status=None, criado_em=T0))
    assert _nomes(lt) == ["persona", "conta"] and lt.parou_em == "sessao"


def test_o_caminho_do_instagram_ate_a_acao_preparada_na_ordem_do_dono() -> None:
    etapas = [_etapa("OPEN_PROFILE", "succeeded", h="01"), _etapa("OPEN_POST", "succeeded", h="02"),
              _etapa("OPEN_COMMENTS", "succeeded", h="03"),
              _etapa("CREATE_COMMENT", "pending", efeito=True, texto=True, pedido="pending", h="04")]
    lt = derivar(_fatos(etapas, status="waiting_user", marcas={"conteudo_lido": T0, "conhecimento_recuperado": T0}))
    assert _nomes(lt) == ["persona", "conta", "sessao", "aparelho", "instagram_aberto", "target_localizado",
                          "post_localizado", "conteudo_lido", "conhecimento_recuperado", "resposta_gerada",
                          "interface_de_comentario_alcancada", "acao_preparada"]
    assert (lt.estagio, lt.estado) == ("acao_preparada", "concluido")   # `preparar`: a barreira é o fim do alvo


def test_executar_vai_ate_o_resultado_verificado_e_sem_verificacao_nao_conclui() -> None:
    base = [_etapa("OPEN_PROFILE", "succeeded"), _etapa("OPEN_POST", "succeeded"), _etapa("OPEN_COMMENTS", "succeeded")]
    feito = derivar(_fatos([*base, _etapa("CREATE_COMMENT", "succeeded", efeito=True, texto=True, verificada=True,
                                          pedido="approved")], status="succeeded", acao_final="executar"))
    assert (feito.estagio, feito.estado) == ("resultado_verificado", "concluido")
    assert "acao_executada" in _nomes(feito)
    sem_prova = derivar(_fatos([*base, _etapa("CREATE_COMMENT", "succeeded", efeito=True, texto=True,
                                              pedido="approved")], status="uncertain", acao_final="executar",
                               motivo="pós-condição não comprovada"))
    assert sem_prova.estado == "bloqueado" and "resultado_verificado" not in _nomes(sem_prova)


def test_a_acao_que_falhou_e_bloqueada_no_lugar_da_executada() -> None:
    lt = derivar(_fatos([_etapa("OPEN_PROFILE", "succeeded"), _etapa("OPEN_POST", "succeeded"),
                         _etapa("OPEN_COMMENTS", "succeeded"),
                         _etapa("CREATE_COMMENT", "failed", efeito=True, texto=True, pedido="approved")],
                        status="failed", acao_final="executar", motivo="o botão Post não apareceu"))
    assert lt.estagio == "acao_bloqueada" and "acao_executada" not in _nomes(lt)
    assert (lt.estado, lt.motivo, lt.parou_em) == ("bloqueado", "o botão Post não apareceu", "acao_bloqueada")


def test_lacuna_nao_e_inferida_por_estagio_posterior() -> None:
    lt = derivar(_fatos([_etapa("OPEN_PROFILE", "succeeded"), _etapa("OPEN_POST", "pending"),
                         _etapa("OPEN_COMMENTS", "succeeded")]))
    assert "post_localizado" not in _nomes(lt) and "interface_de_comentario_alcancada" in _nomes(lt)
    assert "conhecimento_recuperado" not in _nomes(lt)       # ninguém marcou


def test_parada_no_meio_diz_onde_parou() -> None:
    lt = derivar(_fatos([_etapa("OPEN_PROFILE", "succeeded"), _etapa("OPEN_POST", "failed")], status="failed",
                        motivo="post não encontrado"))
    assert (lt.estagio, lt.estado, lt.parou_em, lt.motivo) == ("target_localizado", "bloqueado", "post_localizado",
                                                                "post não encontrado")


def test_trinta_alvos_um_falha_e_os_outros_seguem_cada_um_no_seu_estagio() -> None:
    leituras = []
    for i in range(30):
        if i == 7:
            etapas = [_etapa("OPEN_PROFILE", "failed")]
            leituras.append(derivar(_fatos(etapas, status="failed", motivo="perfil não encontrado")))
            continue
        etapas = [_etapa("OPEN_PROFILE", "succeeded"), _etapa("OPEN_POST", "succeeded"),
                  _etapa("OPEN_COMMENTS", "succeeded"),
                  _etapa("CREATE_COMMENT", "pending", efeito=True, texto=True, pedido="pending")]
        leituras.append(derivar(_fatos(etapas, status="waiting_user")))
    estados = [lt.estado for lt in leituras]
    assert estados.count("concluido") == 29 and estados[7] == "bloqueado"
    assert leituras[7].parou_em == "target_localizado"
    assert all(lt.estagio == "acao_preparada" for i, lt in enumerate(leituras) if i != 7)


def test_o_vocabulario_e_a_ordem_do_dono() -> None:
    assert ESTAGIOS[:4] == ("persona", "conta", "sessao", "aparelho")
    assert ESTAGIOS[-3:] == ("acao_executada", "acao_bloqueada", "resultado_verificado")


def test_o_app_declara_os_seus_estagios_e_o_instagram_traz_o_caminho_do_comentario() -> None:
    ig = definition_of("com.instagram.android")
    assert ig.operation_opening == "instagram_aberto"
    assert dict(ig.operation_stages) == IG
    base = {"app": "com.exemplo.app", "nome": "Exemplo"}
    assert definicao_de_dados(base).operation_opening == "app_aberto"
    with pytest.raises(PacoteInvalido):
        definicao_de_dados({**base, "operacao": {"estagios": {"ABRIR": "resultado_verificado"}}})
    with pytest.raises(PacoteInvalido):
        definicao_de_dados({**base, "operacao": {"abertura": "Instagram"}})


def test_acao_recusada_pela_porta_e_acao_bloqueada_e_nao_preparada() -> None:
    """Achado do percurso da Portal (op-20261006193306-7e8b5f): a regra da frota recusou `comment_1` antes de ela rodar
    (`blocked_kind='policy'`, objetivo em `waiting_user`, etapa `ready`, sem texto). O alvo mostrava "parou em Ação
    preparada"; recusada não é preparada."""
    from dataclasses import replace

    etapas = [_etapa("OPEN_PROFILE", "succeeded", h="01"), _etapa("OPEN_POST", "succeeded", h="02"),
              _etapa("OPEN_COMMENTS", "succeeded", h="03"), _etapa("CREATE_COMMENT", "ready", efeito=True)]
    fatos = replace(_fatos(etapas, status="waiting_user", motivo="1 outra(s) conta(s) da frota já mexeram com o perfil alvo"),
                    objetivo_bloqueio="policy")
    lt = derivar(fatos)
    assert (lt.estagio, lt.estado, lt.parou_em) == ("acao_bloqueada", "bloqueado", "acao_bloqueada")
    assert "acao_preparada" not in _nomes(lt) and lt.motivo is not None and "frota" in lt.motivo
    # o pedido de aprovação (a espera do liberar) NÃO é recusa: segue na ação preparada
    preparada = [*etapas[:3], _etapa("CREATE_COMMENT", "ready", efeito=True, texto=True, pedido="pending")]
    lt2 = derivar(replace(_fatos(preparada, status="waiting_user"), objetivo_bloqueio="approval"))
    assert (lt2.estagio, lt2.estado) == ("acao_preparada", "concluido")


def test_a_hora_do_rascunho_que_espera_o_liberar_e_a_do_pedido() -> None:
    """A etapa com efeito que espera o liberar não começou: a hora de `resposta_gerada`/`acao_preparada` é a do pedido de
    aprovação, não a do início do objetivo (que caía antes de `post_localizado`)."""
    from dataclasses import replace

    efeito = replace(_etapa("CREATE_COMMENT", "ready", efeito=True, texto=True, pedido="pending"),
                     pedido_em="2026-10-07T10:05:00.000Z", comecou_em=None)   # `ready`: não começou
    lt = derivar(_fatos([_etapa("OPEN_PROFILE", "succeeded", h="01"), efeito], status="waiting_user"))
    assert dict(lt.estagios)["acao_preparada"] == dict(lt.estagios)["resposta_gerada"] == "2026-10-07T10:05:00.000Z"


def test_depois_do_liberar_cada_estagio_guarda_a_sua_hora() -> None:
    """Achado (c) do percurso da Portal no 57: na operação liberada, resposta gerada, ação preparada, ação executada e
    resultado verificado saíam todos com a hora do fim da etapa (a da liberação). O rascunho fica com a hora do pedido;
    o efeito e a verificação, com a do fim."""
    from dataclasses import replace

    efeito = replace(_etapa("CREATE_COMMENT", "succeeded", efeito=True, texto=True, verificada=True, pedido="approved",
                            h="08"), pedido_em="2026-10-07T10:05:00.000Z")
    lt = derivar(_fatos([_etapa("OPEN_COMMENTS", "succeeded", h="03"), efeito], status="succeeded",
                        acao_final="executar"))
    horas = dict(lt.estagios)
    assert horas["resposta_gerada"] == horas["acao_preparada"] == "2026-10-07T10:05:00.000Z"
    assert horas["acao_executada"] == horas["resultado_verificado"] == "2026-10-07T10:08:30.000Z"
    # sem pedido de aprovação, o rascunho é da etapa que rodou: a hora em que ela começou
    direto = derivar(_fatos([replace(efeito, pedido_de_aprovacao=None, pedido_em=None)], status="succeeded",
                            acao_final="executar"))
    assert dict(direto.estagios)["resposta_gerada"] == "2026-10-07T10:08:00.000Z"


def test_a_latencia_de_cada_estagio_e_desde_o_evento_anterior_no_tempo_e_o_liberar_sai_a_parte() -> None:
    """Latência por estágio (métrica de primeira classe do dono): a etapa é o tempo desde o evento ANTERIOR no tempo, não
    na ordem do dono (a interface abre antes do rascunho), e a espera pelo liberar não entra na ação executada."""
    from dataclasses import replace

    from app.modules.operacoes.domain.latencia import do_alvo, por_estagio

    efeito = replace(_etapa("CREATE_COMMENT", "succeeded", efeito=True, texto=True, verificada=True, pedido="approved",
                            h="08"), pedido_em="2026-10-07T10:05:00.000Z")
    lt = derivar(_fatos([_etapa("OPEN_COMMENTS", "succeeded", h="03"), efeito], status="succeeded",
                        acao_final="executar"))
    assert lt.liberado_em == "2026-10-07T10:08:00.000Z"      # a etapa com efeito começou no liberar
    lat = do_alvo(lt.estagios, T0, lt.liberado_em, "instagram_aberto")
    etapa = dict(zip([e for e, _ in lt.estagios], lat.etapas_ms, strict=True))
    assert etapa["persona"] == etapa["conta"] == 0
    assert etapa["aparelho"] == 30_000                       # 10:00:30 desde a criação (10:00:00)
    assert etapa["instagram_aberto"] == 150_000              # 10:03:00 (a 1ª etapa começou) desde 10:00:30
    assert etapa["interface_de_comentario_alcancada"] == 30_000    # 10:03:30 desde 10:03:00
    assert etapa["resposta_gerada"] == 90_000                # 10:05:00 desde 10:03:30, não negativo pela ordem do dono
    assert etapa["acao_preparada"] == 0                      # a mesma hora do rascunho
    assert etapa["acao_executada"] == 30_000                 # 10:08:30 desde o liberar (10:08:00), sem a espera
    assert etapa["resultado_verificado"] == 0
    assert (lat.duracao_ms, lat.espera_do_liberar_ms) == (510_000, 180_000)
    # sem liberar: a ação executada conta desde a preparada, e não há espera
    sem = do_alvo(lt.estagios, T0, None, "instagram_aberto")
    assert dict(zip([e for e, _ in lt.estagios], sem.etapas_ms, strict=True))["acao_executada"] == 210_000
    assert sem.espera_do_liberar_ms is None
    # hora ilegível não entra e não derruba as outras
    torta = do_alvo((("persona", T0), ("aparelho", "ontem"), ("instagram_aberto", "2026-10-07T10:01:00.000Z")), T0)
    assert torta.etapas_ms == (0, None, 60_000) and torta.duracao_ms == 60_000
    # entre os alvos: n, p50, p95 e máximo por estágio, só com etapa medida
    alvos = [{"estagios": [{"estagio": "aparelho", "etapa_ms": ms}, {"estagio": "conta", "etapa_ms": None}]}
             for ms in (1000, 2000, 3000, 10_000)]
    assert por_estagio(alvos) == {"aparelho": {"n": 4, "p50_ms": 3000, "p95_ms": 10_000, "max_ms": 10_000}}


def test_a_fila_do_aparelho_conta_quem_roda_a_prioridade_e_a_idade_e_a_previsao_so_com_amostra() -> None:
    """31.206 (adendo v1.114): a posição segue a ordem do despacho (o que roda, a maior prioridade, a execução mais
    antiga) e conta trabalho de qualquer operação; a previsão é a_frente x a mediana de trabalho dos terminados, e sem
    nenhum terminado é None, nunca um número inventado."""
    from datetime import datetime, timezone

    from app.modules.operacoes.domain.fila import Trabalho, a_frente, base_ms, previsao

    t = [Trabalho("r-roda", True, 0, "2026-10-07T10:09:00Z"), Trabalho("r-alta", False, 5, "2026-10-07T10:08:00Z"),
         Trabalho("r-velha", False, 0, "2026-10-07T09:00:00Z"), Trabalho("r-nova", False, 0, "2026-10-07T11:00:00Z"),
         Trabalho("r-eu", False, 0, "2026-10-07T10:00:00Z")]
    assert a_frente("r-eu", 0, "2026-10-07T10:00:00Z", t) == 3          # roda, alta prioridade e a mais velha
    assert a_frente("r-eu", 9, "2026-10-07T10:00:00Z", t) == 1          # prioridade maior: só o que já roda
    terminado = {"estado": "concluido", "estagios": [{"estagio": "persona", "em": "2026-10-07T09:00:00.000Z"},
                                                     {"estagio": "aparelho", "em": "2026-10-07T10:00:00.000Z"},
                                                     {"estagio": "acao_preparada", "em": "2026-10-07T10:01:30.000Z"}]}
    assert base_ms([terminado, {"estado": "pendente", "estagios": []}]) == 90_000   # do aparelho ao último estágio
    assert base_ms([{"estado": "pendente", "estagios": []}]) is None
    agora = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    assert previsao(agora, 2, 90_000) == "2026-10-07T12:03:00.000Z"
    assert previsao(agora, 2, None) is None


def test_o_alvo_que_espera_a_porta_de_sessao_diz_por_que() -> None:
    """31.173: o objetivo `pending` antes do aparelho (a porta de sessão relendo a tela) era `pendente` sem motivo; agora
    leva o motivo da espera. Sem motivo gravado, continua `pendente` sem motivo."""
    import dataclasses

    espera = "a verificação desta sessão passou da validade; o aparelho vai ser relido antes da tarefa"
    fatos = FatosDoAlvo(parada_na_criacao=None, motivo_na_criacao=None, objetivo_status="pending",
                        objetivo_comecou_em=None, objetivo_motivo=espera, run_status="running", etapas=[], marcas={},
                        abertura="instagram_aberto", estagio_por_capability=IG, acao_final="preparar", criado_em=T0)
    lida = derivar(fatos)
    assert (lida.estado, lida.motivo, lida.estagio) == ("pendente", espera, "sessao")
    sem = derivar(dataclasses.replace(fatos, objetivo_motivo=None))
    assert (sem.estado, sem.motivo) == ("pendente", None)
