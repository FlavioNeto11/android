"""30.31 (fatia 2): a conferência do efeito pelo próprio app de QA, ao fim da execução de validação.

O QA Messenger é do projeto e expõe um `ContentProvider` com as mensagens enviadas (`scripts/eval_run.py` já o usava
como oráculo da bateria). Uma linha por mensagem, no formato do `adb shell content query`:

    Row: 19 _id=20, account=qa-user-10, contact=QA-001, body=Rodizio android-10 r-…-7dc718, status=Entregue ✓✓, …

A mensagem só se liga à execução quando o comando traz `{run_id}` (o texto enviado leva o id da execução); sem ele, a
conferência não se aplica e nada muda. Só leitura: nenhuma escrita no aparelho nem no app.
"""
from __future__ import annotations

PACOTE_DO_QA = "com.pocqa.messenger"
URI_DAS_MENSAGENS = "content://com.pocqa.messenger.provider/messages"
#: O marcador do comando que põe o id da execução no texto enviado (o mesmo `{run_id}` que o planejador substitui).
MARCA_DA_EXECUCAO = "{run_id}"


def conferencia_se_aplica(comando: str | None) -> bool:
    """Só o comando que manda o id da execução no texto permite contar as mensagens DESTA execução."""
    return MARCA_DA_EXECUCAO in (comando or "")


def mensagens_da_execucao(saida: str, run_id: str) -> int:
    """Quantas linhas do provedor são desta execução (o id no corpo). Saída vazia ou sem linhas = 0."""
    if not run_id:
        return 0
    return sum(1 for linha in saida.splitlines() if linha.lstrip().startswith("Row:") and run_id in linha)
