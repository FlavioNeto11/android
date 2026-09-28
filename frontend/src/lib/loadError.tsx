import { RefreshCw, ServerCrash } from 'lucide-react';
import { hintForError, toApiError } from '../api/client';
import { Banner } from '../components/Banner';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';

/**
 * Erro de CARGA guardado no estado da tela, não só num toast passageiro.
 *
 * Falha de carga nunca pode virar lista vazia: "Nenhum perfil cadastrado" com a API caída é mentira, e quem lê não
 * tem como saber (auditoria UX 27/09, P1.3). O mesmo vale para "Carregando…" para sempre depois de um erro
 * (P2.11). Aqui fica o formato único do erro e as duas apresentações que as telas usam: o estado inteiro com
 * "Tentar de novo" (quando não há nada carregado) e a faixa "mostrando a última leitura" (quando há).
 */
export interface LoadError {
  message: string;
  hint: string;
}

export function toLoadError(e: unknown): LoadError {
  const err = toApiError(e);
  return { message: err.message, hint: hintForError(err) };
}

/** Estado de erro no lugar da lista: o que aconteceu, o próximo passo e a saída. `what` = "os perfis". */
export function LoadErrorState({ what, error, onRetry, compact }: {
  what: string;
  error: LoadError;
  onRetry: () => void;
  compact?: boolean;
}) {
  return (
    <EmptyState
      icon={ServerCrash}
      tone="danger"
      compact={compact}
      title={`Não foi possível carregar ${what}`}
      hint={error.hint}
      actions={<Button variant="outline" icon={RefreshCw} onClick={onRetry}>Tentar de novo</Button>}
    >
      {error.message}
    </EmptyState>
  );
}

/** A releitura falhou, mas há dado na tela: avisa que ele pode estar velho, em vez de engolir a falha. */
export function LoadErrorBanner({ error, onRetry }: { error: LoadError; onRetry?: () => void }) {
  return (
    <Banner
      tone="warning"
      icon={ServerCrash}
      compact
      role="status"
      title="Mostrando a última leitura"
      actions={onRetry ? <Button size="sm" variant="ghost" icon={RefreshCw} onClick={onRetry}>Tentar de novo</Button> : undefined}
    >
      {error.message} {error.hint}
    </Banner>
  );
}
