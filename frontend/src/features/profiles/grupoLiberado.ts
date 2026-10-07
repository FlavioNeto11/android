import type { Settings } from '../../api/types';

/**
 * O grupo dispensado da aprovação (ADR-082, 31.253/31.265): o que `grupo_sem_aprovacao` aponta. Quem mexe no grupo de uma persona
 * (na ficha ou em lote) precisa ver, ANTES de confirmar, que pôr alguém nele é tirar a revisão humana das portas. O painel só
 * afirma o que o central diz: sem `settings` ou sem a chave, nenhum grupo é tratado como dispensado.
 */
export function idDoGrupoLiberado(settings: Pick<Settings, 'grupo_sem_aprovacao'> | null | undefined): string {
  return (settings?.grupo_sem_aprovacao ?? '').trim();
}

export function ehGrupoLiberado(grupoId: string | null | undefined, settings: Pick<Settings, 'grupo_sem_aprovacao'> | null | undefined): boolean {
  const liberado = idDoGrupoLiberado(settings);
  return liberado !== '' && grupoId === liberado;
}

/** O efeito de entrar no grupo dispensado, em uma frase que a confirmação mostra. `executaLigado` = `operacao_grupo_liberado_executa`. */
export function efeitoDeEntrarNoGrupoLiberado(settings: Pick<Settings, 'operacao_grupo_liberado_executa'> | null | undefined): string {
  if (settings?.operacao_grupo_liberado_executa === false) {
    return 'Este é o grupo sem aprovação nas portas, mas a chave “Operação que executa age sem aprovação para o grupo dispensado” está desligada '
      + '(Configurações › Limites): hoje a persona continua esperando o Liberar numa operação que executa.';
  }
  return 'Este é o grupo sem aprovação nas portas: numa operação com ação final “Executar”, a persona nasce com permissão de agir e comenta na conta '
    + 'real sem ninguém ler o texto antes e sem passar pelo Liberar. Recusas, frota, espaçamento entre contas, tetos, conduta e proteção de conta continuam valendo.';
}

/** O que muda ao sair do grupo dispensado: volta a esperar o Liberar. */
export const EFEITO_DE_SAIR_DO_GRUPO_LIBERADO = 'Sai do grupo sem aprovação nas portas: numa operação que executa, a persona volta a esperar o Liberar.';
