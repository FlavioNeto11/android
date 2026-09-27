import { useEffect } from 'react';

/**
 * Releitura periódica que respeita a aba.
 *
 * Com a página em segundo plano não há ninguém olhando, e cada tick era uma requisição gasta à toa (na tela de
 * proxy, um toast repetido a cada 3 s por cima). Ao voltar, relê na hora e retoma o intervalo: quem volta quer
 * o dado de agora, não o de antes de sair (auditoria UX 27/09, P3.4).
 */

/** Só `hidden` conta como oculta: sem `document` (node) ou sem a API, a página é tratada como visível. */
export function documentoVisivel(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden';
}

/** `setInterval` que só anda com a aba visível; ao voltar do segundo plano dispara na hora. Devolve o "desligar". */
export function intervaloVisivel(tick: () => void, intervalMs: number): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  const ligar = () => {
    if (timer === null) timer = setInterval(tick, intervalMs);
  };
  const desligar = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
  const aoMudarVisibilidade = () => {
    if (documentoVisivel()) {
      tick();
      ligar();
    } else {
      desligar();
    }
  };
  if (documentoVisivel()) ligar();
  const doc = typeof document === 'undefined' ? null : document;
  doc?.addEventListener('visibilitychange', aoMudarVisibilidade);
  return () => {
    desligar();
    doc?.removeEventListener('visibilitychange', aoMudarVisibilidade);
  };
}

/** Versão em hook. `tick` precisa ser estável (`useCallback`): é dependência do efeito, como qualquer outra. */
export function useIntervaloVisivel(tick: () => void, intervalMs: number, ativo = true): void {
  useEffect(() => {
    if (!ativo) return undefined;
    return intervaloVisivel(tick, intervalMs);
  }, [tick, intervalMs, ativo]);
}
