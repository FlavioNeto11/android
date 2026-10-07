import { lerEstagio, rotuloDoEstagio } from './modelo';

/** O estágio da soma em palavras: o da operação, os dois baldes do servidor, ou o texto cru de um estágio que o painel não conhece. */
export function rotuloDoEstagioDoCusto(id: string | null): string {
  if (id === null || id === 'sem_estagio') return 'Sem estágio';
  if (id === 'sem_passo') return 'Planejamento e chamadas sem etapa';
  const e = lerEstagio(id);
  return e ? rotuloDoEstagio(e) : id;
}
