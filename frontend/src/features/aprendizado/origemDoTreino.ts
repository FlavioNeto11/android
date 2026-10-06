/**
 * 31.136 (adendo v1.88, item 31.135 do backend): o fluxo demonstrado no treino diz de que sessão veio, em que aparelho, quem
 * ensinou e quando, e o Livro leva à sessão salva. O campo é OPCIONAL: backend anterior (só `session_id` quando veio de uma
 * falha, v1.81) ou sem o adendo segue como antes, sem aparelho, pessoa nem data, e então não há link. A leitura é tolerante
 * e fica isolada aqui, para o nome dos campos do adendo mudar num lugar só. Os nomes são os do v1.88: `session_id`,
 * `instance_id` (aparelho), `operator` (quem ensinou) e `ensinado_em` (data ISO do salvar).
 */
import { PARAM_TREINO } from '../../store/ui';
import { hashDe } from '../../lib/rotas';
import type { ConteudoDoFluxo } from './model';

export interface OrigemDoTreino {
  sessao: string;
  /** O aparelho onde o treino foi feito; `null` sem o campo. */
  aparelho: string | null;
  /** Quem ensinou, como o backend o escreve (a pessoa do painel ou a persona); `null` sem o campo. */
  pessoa: string | null;
  /** Quando a sessão foi gravada (ISO); `null` sem o campo. */
  quando: string | null;
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

/** `null` quando o fluxo não veio de uma sessão de treino (ou o backend ainda não diz qual). */
export function lerOrigemDoTreino(origem: ConteudoDoFluxo['origem'] | null | undefined): OrigemDoTreino | null {
  if (!origem || origem.tipo !== 'treino') return null;
  const sessao = texto(origem.session_id);
  if (!sessao) return null;
  return { sessao, aparelho: texto(origem.instance_id), pessoa: texto(origem.operator), quando: texto(origem.ensinado_em) };
}

/** O endereço que abre o Foco do aparelho com a sessão salva aberta em leitura (`TrainingBar` consome o `treino`); sem aparelho, não há. */
export function linkDaSessaoDeTreino(o: OrigemDoTreino): string | null {
  return o.aparelho ? hashDe('aprendizado', { query: { aba: 'aprendido', foco: o.aparelho, [PARAM_TREINO]: o.sessao } }) : null;
}
