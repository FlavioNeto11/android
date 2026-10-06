/**
 * 31.88 F2 (adendo v1.71): "Vale para" na revisão do salvar. Três escolhas, e o corpo do save e da prévia leva
 * `scope_on_proof` (e as listas só em "escolher"): "quem_ensinou" junto de lista é 400 `scope_ambiguous`, e sem persona
 * no treino é 409 `no_teacher_persona`, por isso a opção sai desabilitada com o motivo antes de a API recusar.
 */
import type { ReactNode } from 'react';
import type { InstagramProfile, PolicyGroup, TrainingScope } from '../../api/types';
import styles from './Training.module.css';

export type ModoDoEscopo = 'todos' | 'quem_ensinou' | 'escolher';

/** O pedaço do corpo do save e da prévia que diz a quem o fluxo vale. */
export function corpoDoEscopo(modo: ModoDoEscopo, perfis: Iterable<string>, grupos: Iterable<string>):
  { scope_on_proof: 'todos' | 'quem_ensinou'; profile_ids?: string[]; group_ids?: string[] } {
  if (modo === 'quem_ensinou') return { scope_on_proof: 'quem_ensinou' };
  if (modo === 'todos') return { scope_on_proof: 'todos', profile_ids: [], group_ids: [] };
  return { scope_on_proof: 'todos', profile_ids: [...perfis].sort(), group_ids: [...grupos].sort() };
}

/** O que o servidor gravou (ou gravaria), em palavras. O nome do perfil só aparece se a lista dele carregou. */
export function textoDoEscopo(scope: TrainingScope, perfis: readonly InstagramProfile[], grupos: readonly PolicyGroup[]): string {
  if (scope.on_proof === 'quem_ensinou') return 'só a persona que ensinou, também depois da prova';
  const nomes = [
    ...scope.group_ids.map((id) => `grupo ${grupos.find((g) => g.id === id)?.name ?? id}`),
    ...scope.profile_ids.map((id) => { const p = perfis.find((x) => x.id === id); return p ? `@${p.username}` : 'um perfil'; }),
  ];
  return nomes.length ? nomes.join(', ') : 'todos os perfis, depois de provado';
}

/** A frase do que vale antes da prova: o fluxo ensinado só serve a persona que ensinou até passar por ela. */
export const ATE_A_PROVA = 'Até a prova passar, só a persona que ensinou usa o fluxo; a escolha vale depois.';

export function SeletorValePara({ modo, onModo, temPersona, grupoDoNome, children }: {
  modo: ModoDoEscopo;
  onModo: (m: ModoDoEscopo) => void;
  /** O treino tinha persona: sem ela não existe "quem ensinou". */
  temPersona: boolean;
  grupoDoNome: string;
  children?: ReactNode;
}) {
  const opcao = (valor: ModoDoEscopo, rotulo: string, desabilitada = false) => (
    <label key={valor} className={desabilitada ? styles.muted : undefined}>
      <input type="radio" name={grupoDoNome} value={valor} aria-label={rotulo} checked={modo === valor} disabled={desabilitada}
             onChange={() => onModo(valor)} /> {rotulo}
    </label>
  );
  return (
    <>
      <div className={styles.scopeGrid} role="radiogroup" aria-label="Vale para">
        {opcao('todos', 'Todos, depois de provado')}
        {opcao('quem_ensinou', 'Só quem ensinou', !temPersona)}
        {opcao('escolher', 'Escolher perfis e grupos')}
      </div>
      {!temPersona ? <p className={styles.muted}>Este treino não teve persona: não há “quem ensinou”.</p> : null}
      {children}
    </>
  );
}
