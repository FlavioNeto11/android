"""31.70: o resto da varredura do 31.67. O erro sobre a saída do modelo sai sem o texto dela.

O segredo vai num VALOR que a mensagem antiga mostrava (o `input_value=` da `ValidationError`, o `.doc` do
`JSONDecodeError`, o rótulo do enum). Cada teste confere a mensagem, `__cause__` e `__context__`: com `from exc` (ou
levantado dentro do `except`, mesmo com `from None`) a exceção original fica encadeada e vai junto a qualquer log com
traceback.

Nível de prova: `simulated` (funções puras, sem provedor). Nada real.
"""
from __future__ import annotations

import pytest

SEGREDO = "texto-do-modelo-que-nao-pode-ficar-na-causa-31-70"


def _sem_o_texto(exc: BaseException) -> None:
    assert SEGREDO not in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None


@pytest.mark.parametrize("bruto", [
    '{"command": ["' + SEGREDO + '"]}',          # esquema: o pydantic mostraria input_value=
    '{"command": "' + SEGREDO + '" ',             # JSON cortado: o .doc é o texto inteiro
])
def test_refinamento_invalido_sem_o_texto(bruto: str) -> None:
    from app.modules.execution.domain.command_refinement import RefinamentoInvalido, refinement_from_json
    with pytest.raises(RefinamentoInvalido) as erro:
        refinement_from_json(bruto)
    _sem_o_texto(erro.value)


@pytest.mark.parametrize("bruto", [
    '{"alerta_conduta": ["' + SEGREDO + '"]}',
    '{"alerta_conduta": ["' + SEGREDO + '" ',
])
def test_orquestracao_invalida_sem_o_texto(bruto: str) -> None:
    from app.modules.execution.domain.orquestracao import OrquestracaoInvalida, orquestracao_from_json
    with pytest.raises(OrquestracaoInvalida) as erro:
        orquestracao_from_json(bruto)
    _sem_o_texto(erro.value)


def test_parecer_do_hub_ilegivel_sem_o_texto() -> None:
    from app.planning.curador import ParecerIlegivel, parecer_from_json
    with pytest.raises(ParecerIlegivel) as erro:
        parecer_from_json('{"conclusao": "' + SEGREDO + '" ')
    _sem_o_texto(erro.value)


def test_parecer_do_aprendizado_ilegivel_sem_o_texto() -> None:
    from app.modules.learning.domain import curador
    with pytest.raises(curador._Invalida) as erro:
        curador._parecer('{"conclusao": "' + SEGREDO + '" ', None, None)  # type: ignore[arg-type]
    _sem_o_texto(erro.value)


def test_rotulo_fora_do_enum_sem_o_texto() -> None:
    from app.modules.learning.domain import curador
    motivo = next(iter(curador.MotivoDeInvalidade))
    with pytest.raises(curador._Invalida) as erro:
        curador._rotulo(curador.MotivoDeInvalidade, SEGREDO, motivo)
    _sem_o_texto(erro.value)


def test_documento_de_habilidade_ilegivel_sem_o_texto() -> None:
    from app.modules.skills.domain.document import NotJson, parse_json_object
    with pytest.raises(NotJson) as erro:
        parse_json_object('{"passo": "' + SEGREDO + '" ')
    _sem_o_texto(erro.value)

def test_motivo_sem_valor_nao_ecoa_validador_nem_chave_do_modelo() -> None:
    """H1 da leitura do #373: o `msg` de um validador que ecoa o valor e a chave de `dict` ou de `extra_forbidden` (texto
    do modelo) não entram no motivo; ficam o `type` e o nome de campo do esquema."""
    from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

    from app.modules.execution.domain.command_refinement import motivo_sem_valor

    class Item(BaseModel):
        model_config = ConfigDict(extra="forbid")
        nome: str

        @field_validator("nome")
        @classmethod
        def _ecoa(cls, v: str) -> str:
            raise ValueError(f"nome recusado: {v}")

    class Saida(BaseModel):
        itens: list[Item]
        contagem: dict[str, int]

    bruto = {"itens": [{"nome": SEGREDO, SEGREDO + "-chave": 1}], "contagem": {SEGREDO + "-dict": "x"}}
    with pytest.raises(ValidationError) as erro:
        Saida.model_validate(bruto)
    assert SEGREDO in str(erro.value)                      # o pydantic sozinho ecoa: o teste discrimina
    motivo = motivo_sem_valor(erro.value, Saida)
    assert SEGREDO not in motivo
    assert "itens.0.nome: value_error" in motivo and "itens.0.?: extra_forbidden" in motivo
    assert "contagem.?: int_parsing" in motivo


# ---------------------------------------------------------------- sobras da leitura do 31.70: a função irmã do 31.63
def _esquema_que_ecoa() -> type:
    from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

    class Item(BaseModel):
        model_config = ConfigDict(extra="forbid")
        nome: str
        rotulo: str = Field(alias="label")
        nota: int = Field(0, validation_alias=AliasChoices("nota", "score"))

        @field_validator("nome")
        @classmethod
        def _ecoa(cls, v: str) -> str:
            raise ValueError(f"nome recusado: {v}")

    class Saida(BaseModel):
        itens: list[Item]
        contagem: dict[str, int]

    return Saida


def _bruto() -> dict[str, object]:
    return {"itens": [{"nome": SEGREDO, "label": 7, "score": "x", SEGREDO + "-chave": 1}],
            "contagem": {SEGREDO + "-dict": "x"}}


def test_a_irma_do_31_63_tambem_nao_ecoa_chave_do_modelo() -> None:
    """Antes, `erro_de_validacao_sem_entrada` deixava a chave de `dict` e a do `extra_forbidden` no `loc`."""
    from pydantic import ValidationError

    from app.planning.provider import erro_de_validacao_sem_entrada
    saida = _esquema_que_ecoa()
    with pytest.raises(ValidationError) as erro:
        saida.model_validate(_bruto())
    assert SEGREDO in str(erro.value)                      # o pydantic sozinho ecoa: o teste discrimina
    motivo = erro_de_validacao_sem_entrada(erro.value, saida)
    assert SEGREDO not in motivo
    assert "itens.0.nome: value_error" in motivo and "itens.0.?: extra_forbidden" in motivo
    assert "contagem.?: int_parsing" in motivo


def test_alias_e_validation_alias_contam_como_nome_de_campo() -> None:
    from pydantic import ValidationError

    from app.modules.execution.domain.command_refinement import motivo_sem_valor
    from app.planning.provider import erro_de_validacao_sem_entrada
    from app.shared.validacao import nomes_de_campo
    saida = _esquema_que_ecoa()
    assert {"label", "rotulo", "nota", "score"} <= nomes_de_campo(saida)
    with pytest.raises(ValidationError) as erro:
        saida.model_validate(_bruto())
    for motivo in (erro_de_validacao_sem_entrada(erro.value, saida), motivo_sem_valor(erro.value, saida)):
        assert "itens.0.label: string_type" in motivo and "itens.0.score: int_parsing" in motivo


def test_validar_saida_levanta_sem_o_texto_e_sem_a_causa() -> None:
    from app.planning.provider import AIError, validar_saida
    with pytest.raises(AIError) as erro:
        validar_saida(_esquema_que_ecoa(), _bruto(), "Saída inválida")
    _sem_o_texto(erro.value)
