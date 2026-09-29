/**
 * Para quem e onde uma execução acontece (adendo v0.29, ADR-044), do lado do painel. Aqui fica só o que é regra: o
 * eco da prévia em `targets`, a frase de cada recusa e o que a resposta a uma pergunta faz com a seleção. Puro, para
 * ser testado sem tela.
 */
import { hintForError, type ApiError } from '../../api/client';
import type { AppConfig, ResolvedTarget, RunTarget, TargetOrigin, TargetQuestion } from '../../api/types';
import { appLabel } from '../runs/model';

/** A origem de cada alvo, como a prévia mostra (o pedido do dono: interface / texto / vínculo / balanceamento). */
export const ORIGEM: Record<TargetOrigin, { rotulo: string; dica: string }> = {
  ui: { rotulo: 'interface', dica: 'Escolhido na tela: a seleção de personas ou de aparelhos.' },
  texto: { rotulo: 'texto', dica: 'Tirado do texto do comando. Só executa depois de confirmado aqui.' },
  vinculo: { rotulo: 'vínculo', dica: 'Aparelho da persona: onde a sessão já está pronta, ou o principal.' },
  balanceamento: { rotulo: 'balanceamento', dica: 'Desempate pela carga dos servidores entre aparelhos igualmente bons.' },
};

/**
 * Os apps que este alvo exige, pelo NOME do catálogo (contrato C5, `ResolvedTarget.app_ids`) — item 24.6: a prévia
 * não mostra mais um app só por alvo, e sim o CONJUNTO que o comando atravessa. `app_id` é o de trás para quem
 * ainda não populou `app_ids` (alvo que veio do `detail` de uma recusa, sem este campo). Vazio = alvo sem app.
 */
export function appsDoAlvo(t: Pick<ResolvedTarget, 'app_id' | 'app_ids'>, apps: readonly Pick<AppConfig, 'id' | 'name'>[]): string[] {
  const ids = t.app_ids && t.app_ids.length > 0 ? t.app_ids : (t.app_id ? [t.app_id] : []);
  return ids.map((id) => appLabel(apps, id) ?? id);
}

/** O que vai no corpo de `POST /runs` para confirmar a prévia: `targets` (com persona) ou `instance_ids` (sem). */
export interface Eco {
  targets: RunTarget[];
  instance_ids: string[];
}

/**
 * O eco da prévia: os alvos que a pessoa VIU, fixados. Com persona, vão em `targets` agrupados por persona e app,
 * cada um com os aparelhos explícitos — assim o backend não re-escolhe (o balanceamento podia mudar de ideia entre a
 * prévia e o clique) e nenhum alvo fica com origem `texto` (409 `alvos_nao_confirmados`). Sem persona (aparelho
 * vazio), vão como `instance_ids`: a seleção exata que o texto estreitou deixa de ser estreitamento.
 *
 * Misturar os dois não tem forma no contrato — `RunTarget.profile_id` é obrigatório, e com `targets` os
 * `instance_ids` viram filtro, não alvo —, então a mistura é recusada com o que fazer.
 */
export function ecoDosAlvos(alvos: readonly ResolvedTarget[]): { eco: Eco; erro: null } | { eco: null; erro: string } {
  if (alvos.length === 0) return { eco: null, erro: 'A prévia não tem alvo para confirmar.' };
  const comPersona = alvos.filter((a) => a.profile_id);
  if (comPersona.length === 0) {
    return { eco: { targets: [], instance_ids: [...new Set(alvos.map((a) => a.instance_id))] }, erro: null };
  }
  if (comPersona.length < alvos.length) {
    return { eco: null, erro: 'Parte destes aparelhos tem persona e parte não: uma execução não mistura os dois quando '
      + 'o comando cita destinos. Faça duas execuções, ou marque só os aparelhos de um tipo.' };
  }
  const grupos = new Map<string, { profile_id: string; instance_ids: string[]; app_id: string | null }>();
  for (const a of comPersona) {
    const chave = `${a.profile_id}|${a.app_id ?? ''}`;
    const g = grupos.get(chave) ?? { profile_id: a.profile_id as string, instance_ids: [], app_id: a.app_id };
    if (!g.instance_ids.includes(a.instance_id)) g.instance_ids.push(a.instance_id);
    grupos.set(chave, g);
  }
  return { eco: { targets: [...grupos.values()], instance_ids: [] }, erro: null };
}

/** Os códigos de recusa da resolução de alvos: ficam NA tela, com o que fazer, e não num toast que some. */
export const RECUSAS_DE_ALVO: ReadonlySet<string> = new Set([
  'sem_intersecao', 'sem_vinculo', 'no_binding', 'aparelho_repetido_na_execucao', 'sem_alvo',
]);

export interface RecusaDeAlvo {
  code: string;
  titulo: string;
  /** O próximo passo, em português. */
  passo: string;
  /** A mensagem do backend, como veio (com os nomes e os aparelhos que ele viu). */
  mensagem: string;
}

export function recusaDosAlvos(e: ApiError): RecusaDeAlvo {
  const base = { code: e.code, mensagem: e.message };
  switch (e.code) {
    case 'sem_intersecao':
      return { ...base, titulo: 'Nenhum aparelho em comum',
               passo: 'Os aparelhos escolhidos não são desta persona. Tire o filtro de aparelhos, ou escolha aparelhos '
                 + 'dela (a guia Aparelhos da persona mostra quais são).' };
    case 'sem_vinculo':
      return { ...base, titulo: 'Aparelho sem vínculo com a persona',
               passo: 'Escolha um aparelho dela, ou vincule a persona a este aparelho em Personas → a pessoa → Aparelhos.' };
    case 'no_binding':
      return { ...base, titulo: 'Persona sem aparelho',
               passo: 'Vincule um aparelho em Personas → a pessoa → Aparelhos antes de mandar tarefa para ela.' };
    case 'aparelho_repetido_na_execucao':
      return { ...base, titulo: 'O mesmo aparelho duas vezes',
               passo: 'Uma execução usa cada aparelho uma vez só: duas personas no mesmo aparelho pedem duas '
                 + 'execuções. Tire uma persona, ou estreite para aparelhos diferentes.' };
    case 'sem_alvo':
      return { ...base, titulo: 'Sem destino',
               passo: 'Escolha ao menos uma persona (ou aparelhos), ou cite no comando: “no android-03”, “peça para '
                 + 'o André”.' };
    default:
      return { ...base, titulo: 'Não foi possível resolver os alvos', passo: hintForError(e) };
  }
}

/** A seleção que uma resposta produz. `semDestinos` = o comando perde os trechos de destino (fica a seleção). */
export interface Resposta {
  personas: string[] | null;
  aparelhos: string[] | null;
  semDestinos: boolean;
}

/**
 * O que clicar numa opção de uma pergunta faz com a seleção: a SELEÇÃO passa a dizer a resposta — a persona
 * escolhida no lugar das outras candidatas da pergunta, ou o aparelho escolhido. Na contradição (o texto fala de
 * quem não está na seleção), escolher o que JÁ estava selecionado quer dizer "fica a seleção": quem perde é o
 * texto, e o comando passa a ser o `command_sem_destinos`. Com a pergunta presa a um aparelho ("qual persona NESTE
 * aparelho?"), a persona vai junto com o aparelho.
 */
export function responder(q: TargetQuestion, opcao: string, personas: readonly string[],
                          aparelhos: readonly string[]): Resposta {
  const contradicao = q.code === 'destino_contraditorio';
  if (q.field === 'instance_id') {
    if (contradicao && aparelhos.includes(opcao)) return { personas: null, aparelhos: null, semDestinos: true };
    return { personas: null, aparelhos: [opcao], semDestinos: false };
  }
  if (contradicao && personas.includes(opcao)) {
    return { personas: [opcao], aparelhos: null, semDestinos: true };
  }
  const outras = personas.filter((p) => !q.options.includes(p));
  return {
    personas: [...outras, opcao],
    aparelhos: q.instance_id ? [q.instance_id] : null,
    semDestinos: false,
  };
}
