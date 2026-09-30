/**
 * Mapa único de tradução: identificador técnico → português claro (revisão de UX, tarefa 04).
 *
 * Cartões, trilhas de comando e logs mostravam `app.distribute`, `device.network` e
 * `seletor id=com.pocqa.messenger:id/account_label|text=qa-user-10: 1 elemento(s)` como vieram do backend. Quem
 * precisa do identificador original (suporte, busca em log) o encontra no `title` do elemento: o texto visível é
 * sempre o traduzido. Módulo puro (sem React e sem DOM), testável em node.
 */

/** Verbos de comando que NÃO são ações do aparelho (as ações de ciclo de vida têm rótulo em `ACTION_META`). */
const COMANDOS: Readonly<Record<string, string>> = {
  'app.install': 'Instalação de app',
  'app.verify': 'Verificação de app',
  'app.canary': 'Teste de versão do app',
  'app.rollback': 'Volta de versão do app',
  'app.distribute': 'Distribuição de app',
  'store.sync': 'Sincronização da loja',
  'session.connect': 'Entrada na conta',
  'session.verify': 'Verificação da sessão',
  'session.logout': 'Saída da conta',
  'session.close': 'Encerramento da sessão',
  'session.needs_person': 'Sessão aguardando uma pessoa',
  'device.proxy': 'Proxy do aparelho',
  'device.network': 'Rede do aparelho',
  'device.state': 'Estado do aparelho',
  'device.locked_account': 'Conta travada no aparelho',
  'emulator.process_alive': 'Emulador em funcionamento',
  'emulator.save_snapshot': 'Salvamento do estado do emulador',
  'emulator.stop_process': 'Desligamento do emulador',
  'instance.remediation': 'Reparo automático do aparelho',
  // Verbos de ciclo de vida: o mesmo vocabulário do backend, para quando chegarem fora de `ACTION_META`.
  create: 'Criação do AVD',
  start: 'Início do aparelho',
  stop: 'Parada do aparelho',
  restart: 'Reinício do aparelho',
  reset: 'Limpeza dos dados',
  install_apk: 'Instalação de app',
  open_app: 'Abertura de app',
  hibernate: 'Hibernação',
  wake: 'Despertar',
  home: 'Tecla Início',
  back: 'Tecla Voltar',
  recents: 'Tecla Recentes',
};

const GRUPOS: Readonly<Record<string, string>> = {
  app: 'app',
  session: 'sessão',
  device: 'aparelho',
  store: 'loja',
  emulator: 'emulador',
  instance: 'aparelho',
  account: 'conta',
};

function capitalizar(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/**
 * Verbo desconhecido: nunca devolve o identificador cru. `app.foo_bar` → "Foo bar (app)". Serve de rede de
 * segurança para verbos novos do backend, que ganham rótulo próprio em `COMANDOS` quando a tela precisar.
 */
function humanizar(identificador: string): string {
  const partes = identificador.split('.');
  const nome = (partes[partes.length - 1] ?? identificador).replace(/[_-]+/g, ' ').trim();
  const grupo = partes.length > 1 ? GRUPOS[partes[0] ?? ''] : undefined;
  const base = capitalizar(nome || 'comando');
  return grupo ? `${base} (${grupo})` : base;
}

/** O rótulo em português de um verbo de comando; `undefined` quando o mapa não o conhece. */
export function rotuloConhecidoDoComando(verbo: string): string | undefined {
  return COMANDOS[verbo];
}

/** O rótulo em português de qualquer verbo de comando (conhecido ou não). */
export function rotuloDoComando(verbo: string): string {
  return COMANDOS[verbo] ?? humanizar(verbo);
}

// ---- evidência observada --------------------------------------------------------------------------------

const NIVEL_OBSERVADO: Readonly<Record<string, string>> = {
  none: 'sem confirmação',
  appeared: 'apareceu na conversa',
  sent: 'enviada',
  delivered: 'entregue',
  read: 'lida',
};

/**
 * A evidência que o backend guardou ao ler a tela, em português. Dois formatos técnicos:
 * - `seletor id=com.app:id/account_label|text=qa-user-10: 1 elemento(s)` → `Confirmado na tela: “qa-user-10”`;
 * - `… [nível observado: delivered]` → `… [nível observado: entregue]`.
 * O texto original fica no `title` de quem chama.
 */
export function evidenciaLegivel(evidencia: string): string {
  const seletor = /^seletor\s+id=[^|]*\|text=(.*?):\s*\d+\s*elemento\(s\)\s*$/s.exec(evidencia.trim());
  if (seletor) return `Confirmado na tela: “${seletor[1]}”`;
  if (/^seletor\s+/i.test(evidencia)) return 'Confirmado na tela pelo identificador do elemento';
  return evidencia.replace(/\[nível observado:\s*([a-z_]+)\]/gi, (_t, nivel: string) =>
    `[nível observado: ${NIVEL_OBSERVADO[nivel.toLowerCase()] ?? nivel}]`);
}
