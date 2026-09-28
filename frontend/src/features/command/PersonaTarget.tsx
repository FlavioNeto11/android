import { Smartphone, Star, Users } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, profileAvatarUrl, toApiError } from '../../api/client';
import type { DevicePolicy, PersonaDTO, ResolveTargetsRequest, ResolveTargetsResponse } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import ui from '../../components/ui.module.css';
import { cx, plural } from '../../lib/format';
import { handleDe, idsDosAparelhos, nomeDe } from '../profiles/pessoa';
import { recusaDosAlvos, type RecusaDeAlvo } from './alvos';
import styles from './CommandPanel.module.css';

export const POLITICAS: { id: DevicePolicy; rotulo: string; dica: string }[] = [
  { id: 'one', rotulo: 'Um aparelho dela', dica: 'A pessoa faz uma vez: onde a sessão já está pronta, senão o principal.' },
  { id: 'primary', rotulo: 'O aparelho principal', dica: 'Sempre o aparelho principal de cada persona.' },
  { id: 'all', rotulo: 'Todos os aparelhos dela', dica: 'Um objetivo em CADA aparelho dela: curtir ou comentar acontece em todos.' },
];

export interface PreviaNaTela {
  previa: ResolveTargetsResponse | null;
  recusa: RecusaDeAlvo | null;
  carregando: boolean;
  /** O pedido a que `previa`/`recusa` respondem (JSON). Diferente do pedido atual = prévia velha: não executa. */
  chave: string | null;
}

/**
 * A prévia dos alvos, relida com atraso (400 ms) a cada mudança do texto ou da seleção. Pedido `null` não lê nada.
 * Guarda a chave do pedido respondido: quem executa confere que a prévia na tela é a do pedido atual — clicar
 * "Executar" logo depois de mudar o texto não pode ecoar alvos de outro texto.
 */
export function usePreviaDosAlvos(pedido: ResolveTargetsRequest | null): PreviaNaTela & { pedidoChave: string | null } {
  const pedidoChave = pedido ? JSON.stringify(pedido) : null;
  const [estado, setEstado] = useState<PreviaNaTela>({ previa: null, recusa: null, carregando: false, chave: null });
  useEffect(() => {
    if (!pedidoChave) {
      setEstado({ previa: null, recusa: null, carregando: false, chave: null });
      return;
    }
    const corpo = JSON.parse(pedidoChave) as ResolveTargetsRequest;
    const ctrl = new AbortController();
    setEstado((e) => ({ ...e, carregando: true }));
    const t = setTimeout(() => {
      api.resolveRunTargets(corpo, ctrl.signal)
        .then((previa) => { if (!ctrl.signal.aborted) setEstado({ previa, recusa: null, carregando: false, chave: pedidoChave }); })
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          setEstado({ previa: null, recusa: recusaDosAlvos(toApiError(e)), carregando: false, chave: pedidoChave });
        });
    }, 400);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [pedidoChave]);
  return { ...estado, pedidoChave };
}

/**
 * "Por persona": quem faz (uma ou mais personas, com foto e nome — com ou sem @), quantos aparelhos de cada uma
 * (`device_policy`) e, se quiser, em quais aparelhos dela (estreitar = interseção no backend).
 */
export function PersonaTarget({ pessoas, selecionadas, politica, estreitar, onPessoas, onPolitica, onEstreitar }: {
  pessoas: PersonaDTO[] | null;
  selecionadas: string[];
  politica: DevicePolicy;
  estreitar: string[];
  onPessoas: (ids: string[]) => void;
  onPolitica: (p: DevicePolicy) => void;
  onEstreitar: (ids: string[]) => void;
}) {
  // Os aparelhos que dá para escolher são os das personas marcadas: estreitar por um aparelho de fora seria sempre
  // `sem_intersecao`.
  const aparelhos = useMemo(() => {
    const ids = new Set<string>();
    for (const p of pessoas ?? []) if (selecionadas.includes(p.id)) for (const d of idsDosAparelhos(p)) ids.add(d);
    return [...ids].sort();
  }, [pessoas, selecionadas]);
  const alternar = (lista: string[], id: string) => (lista.includes(id) ? lista.filter((x) => x !== id) : [...lista, id]);

  return (
    <div className={styles.distribute}>
      <div className={styles.personaGrupo} role="group" aria-label="Personas">
        <span className={styles.distributeLabel}><Users size={13} aria-hidden /> Quem faz</span>
        {pessoas === null ? <span className={styles.previaNota}>Carregando as personas…</span>
          : pessoas.length === 0 ? <span className={styles.previaNota}>Nenhuma persona cadastrada (tela Personas).</span>
          : pessoas.map((p) => {
            const nome = nomeDe(p);
            const handle = handleDe(p);
            const n = idsDosAparelhos(p).length;
            return (
              <button key={p.id} type="button" className={cx(ui.chip, styles.personaChip)}
                      aria-pressed={selecionadas.includes(p.id)}
                      aria-label={`${nome}${handle ? ` (@${handle})` : ''}${n === 0 ? ', sem aparelho' : ''}`}
                      title={`${handle ? `@${handle} · ` : 'sem conta de cadastro · '}${n ? plural(n, 'aparelho', 'aparelhos') : 'sem aparelho vinculado'}`}
                      onClick={() => onPessoas(alternar(selecionadas, p.id))}>
                <Avatar src={profileAvatarUrl(p.id)} name={nome} size={20} />
                <span className={styles.personaChipNome}>{nome}</span>
                {n === 0 ? <span className={styles.previaNota}>sem aparelho</span> : null}
              </button>
            );
          })}
      </div>
      <div className={styles.personaGrupo} role="group" aria-label="Quantos aparelhos de cada persona">
        <span className={styles.distributeLabel}><Star size={13} aria-hidden /> Onde</span>
        {POLITICAS.map((p) => (
          <button key={p.id} type="button" className={ui.chip} aria-pressed={politica === p.id} title={p.dica}
                  onClick={() => onPolitica(p.id)}>
            {p.rotulo}{p.id === 'one' ? ' (padrão)' : ''}
          </button>
        ))}
      </div>
      {aparelhos.length > 1 ? (
        <div className={styles.personaGrupo} role="group" aria-label="Estreitar por aparelho">
          <span className={styles.distributeLabel}><Smartphone size={13} aria-hidden /> Só nestes (opcional)</span>
          {aparelhos.map((id) => (
            <button key={id} type="button" className={cx(ui.chip, 'mono')} aria-pressed={estreitar.includes(id)}
                    onClick={() => onEstreitar(alternar(estreitar, id))}>
              {id}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
