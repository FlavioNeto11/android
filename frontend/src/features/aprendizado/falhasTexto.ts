/**
 * O texto de "O que mais falha" para quem OPERA, não para quem desenvolve: o erro cru do provedor, o id da tentativa e o
 * estado do backlog chegam do backend como estão gravados, e aqui viram frase. O original continua no bloco "Para quem
 * desenvolve" (recolhido), que é onde o id, o "onde alterar" e o "Copiar para sessão" fazem sentido.
 */

/** Os estados do backlog (`domain/vocabulario.py::EstadoDoBacklog`), em português. Estado novo aparece como veio. */
const BACKLOG: Record<string, string> = {
  open: 'aberto', triaged: 'triado', planned: 'planejado', fixed_pending_proof: 'corrigido, falta prova',
  fixed: 'corrigido', reopened: 'reaberto', wontfix: 'não será corrigido',
};

export function rotuloDoBacklog(estado: string): string {
  return BACKLOG[estado] ?? estado;
}

/**
 * A tentativa como a pessoa a lê: `r-…:android-05:v1:open_app:a1` → "android-05 · etapa open_app · tentativa 1" (a
 * palavra diz que o código é a chave da etapa no plano, não um nome; validação do deploy 4). Fora desse formato devolve
 * `null` (quem chama mostra só a execução).
 */
export function descreverTentativa(attemptId: string | null | undefined): string | null {
  if (!attemptId) return null;
  const partes = attemptId.split(':');
  if (partes.length < 5) return null;
  const [, aparelho, , etapa, tentativa] = partes;
  const n = /^a(\d+)$/.exec(tentativa ?? '');
  if (!aparelho || !etapa || !n) return null;
  return `${aparelho} · etapa ${etapa} · tentativa ${n[1]}`;
}

/** Os erros do provedor de IA que já apareceram na tela do dono, do mais específico ao mais geral. */
const ERROS_DO_PROVEDOR: readonly [RegExp, string][] = [
  [/credit balance is too low|insufficient_quota|exceeded your current quota/i,
    'Saldo da conta de IA esgotado: o provedor recusou a chamada.'],
  [/rate.?limit|Error code: 429/i, 'Limite de chamadas do provedor de IA atingido; a tentativa parou.'],
  [/overloaded|Error code: 529|Error code: 503/i, 'Provedor de IA sobrecarregado ou fora do ar naquele momento.'],
  [/invalid.?api.?key|authentication_error|Error code: 401/i, 'A chave do provedor de IA foi recusada.'],
  [/timed? ?out|timeout/i, 'O provedor de IA não respondeu a tempo.'],
];

/**
 * O erro de um exemplo em português. O texto da própria execução (já em português) passa como está; o erro cru do
 * provedor ("Error code: 400 - {'type': 'error', …}") vira uma frase, e o que não se reconhece vira uma frase genérica
 * em vez do JSON.
 */
export function traduzirErro(erro: string | null | undefined): string | null {
  if (!erro) return null;
  for (const [padrao, frase] of ERROS_DO_PROVEDOR) if (padrao.test(erro)) return frase;
  const codigo = /Error code: (\d{3})/.exec(erro);
  if (codigo) return `O provedor de IA recusou a chamada (código ${codigo[1]}).`;
  if (/[{[]'type'|"type":/.test(erro)) return 'O provedor de IA devolveu um erro técnico.';
  return erro;
}
