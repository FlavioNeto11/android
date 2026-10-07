import { CircleAlert, ListChecks, Play, Server, Shuffle, Smartphone, Sparkles, UserRound, Wand2 } from 'lucide-react';
import type { RunMode, RunTargetsSuggestion } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { plural } from '../../lib/format';
import { instanceShort } from '../../lib/ids';
import { useUiStore } from '../../store/ui';
import styles from './SugestaoDeAlvos.module.css';

const ADERENCIA = { alta: { tone: 'success', rotulo: 'aderência alta' }, media: { tone: 'info', rotulo: 'aderência média' },
                    baixa: { tone: 'warning', rotulo: 'aderência baixa' } } as const;

/**
 * "Quem faz e onde" do modo Automático (ADR-050): a escolha do sistema, com o MOTIVO de cada persona, o aparelho e o
 * servidor, as descartadas (dobradas) e as que não deu para avaliar sem enriquecer. Nada é criado até confirmar —
 * confirmar ecoa estes alvos, como a prévia por persona. Sem formulário novo: o que falta numa persona se completa
 * na própria persona ("Completar com IA"), a um clique daqui.
 */
export function SugestaoDeAlvos({ sugestao, carregando, erro, mode, enviando, impede, onConfirmar, onManual, onFechar }: {
  sugestao: RunTargetsSuggestion | null;
  carregando: boolean;
  erro: string | null;
  mode: RunMode;
  enviando: boolean;
  /** Por que não dá para confirmar (sem alvo, pergunta aberta, alerta de conduta), ou `null`. */
  impede: string | null;
  onConfirmar: () => void;
  onManual: () => void;
  onFechar: () => void;
}) {
  const openPersona = useUiStore((s) => s.openPersona);
  const s = sugestao;
  return (
    <section className={styles.box} aria-label="Quem faz e onde" aria-busy={carregando}>
      <header className={styles.head}>
        <span className={styles.titulo}><Wand2 size={15} aria-hidden /> Quem faz e onde</span>
        {s?.modo === 'distribuir' ? <Badge tone="neutral" icon={Shuffle}>pela carga dos servidores</Badge> : null}
        {s?.modo === 'texto' ? <Badge tone="warning">o comando cita destinos</Badge> : null}
        <Button size="sm" variant="ghost" className={styles.fechar} onClick={onFechar}>Fechar</Button>
      </header>

      {carregando ? <p className={styles.resumo}>Lendo o pedido, as personas e a fila de cada aparelho…</p> : null}
      {erro ? <p className={styles.erro} role="alert">{erro}</p> : null}

      {s && !carregando ? (
        <>
          {s.resumo ? <p className={styles.resumo}>{s.resumo}</p> : null}

          {s.escolhidas.length > 0 ? (
            <ul className={styles.lista} aria-label="Personas escolhidas">
              {s.escolhidas.map((e) => (
                <li key={e.profile_id} className={styles.item}>
                  <div className={styles.linha}>
                    <UserRound size={14} aria-hidden />
                    <strong>{e.nome}</strong>
                    <Badge tone={ADERENCIA[e.aderencia].tone} size="sm">{ADERENCIA[e.aderencia].rotulo}</Badge>
                    {e.instance_id ? (
                      <span className={styles.onde}>
                        <Smartphone size={13} aria-hidden /> {instanceShort(e.instance_id)}
                        {e.servidor ? <><Server size={13} aria-hidden /> {e.servidor}</> : null}
                      </span>
                    ) : null}
                  </div>
                  <p className={styles.motivo}>{e.motivo}</p>
                </li>
              ))}
            </ul>
          ) : null}

          {s.modo === 'distribuir' && s.targets.length > 0 ? (
            <p className={styles.resumo}>
              <Smartphone size={13} aria-hidden /> {s.targets.map((t) => instanceShort(t.instance_id)).join(' · ')}
            </p>
          ) : null}

          {[...s.perguntas, ...s.questions.map((q) => q.question)].map((p) => (
            <p key={p} className={styles.aviso}><CircleAlert size={13} aria-hidden /> {p}</p>
          ))}
          {s.warnings.map((w) => <p key={w} className={styles.aviso}>{w}</p>)}

          {s.nao_avaliaveis.length > 0 ? (
            <div className={styles.faltam}>
              <span>
                <Sparkles size={13} aria-hidden className={styles.faltamIcone} />{' '}
                {plural(s.nao_avaliaveis.length, 'persona ficou', 'personas ficaram')} de fora por falta de dados — complete na
                persona (“Completar com IA”) e peça de novo:
              </span>
              {s.nao_avaliaveis.map((n) => (
                <button key={n.profile_id} type="button" className={styles.link} title={n.falta}
                        onClick={() => openPersona(n.profile_id, 'persona')}>
                  {n.nome} <span className={styles.falta}>({n.falta})</span>
                </button>
              ))}
            </div>
          ) : null}

          {s.descartadas.length > 0 ? (
            <details className={styles.descartadas}>
              <summary>{plural(s.descartadas.length, 'persona descartada', 'personas descartadas')}</summary>
              <ul>
                {s.descartadas.map((d) => <li key={d.profile_id}><strong>{d.nome}</strong>: {d.motivo}</li>)}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}

      <footer className={styles.rodape}>
        <Button size="sm" variant="primary" icon={mode === 'plan' ? ListChecks : Play} loading={enviando}
                disabledReason={carregando ? 'Aguardando a sugestão…' : impede} onClick={onConfirmar}>
          {mode === 'plan' ? 'Confirmar e planejar' : 'Confirmar e executar'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onManual}>Escolher manualmente</Button>
      </footer>
    </section>
  );
}
