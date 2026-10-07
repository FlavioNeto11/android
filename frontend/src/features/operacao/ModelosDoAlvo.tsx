import { TrendingUp } from 'lucide-react';
import { Badge } from '../../components/Badge';
import { Skeleton } from '../../components/Skeleton';
import { formatInt, formatPercent } from '../../lib/format';
import { formatUsd } from '../usage/usage';
import { useUsage } from '../usage/useUsage';
import styles from './Operacao.module.css';
import type { CustoPorPasso } from './custoPorPasso';
import { LinhaDoTempoDoAlvo } from './LinhaDoTempoDoAlvo';
import { modelosDoAlvo } from './modelosLidos';

/**
 * 31.228: "Modelos e custo" no detalhe de um agente: por função e modelo, quantas chamadas e quanto custaram, e a divisão das decisões
 * entre os modelos (para medir quem decide a navegação e quem decide o que tem efeito, 31.223). Lido só ao abrir o detalhe.
 */
export function ModelosDoAlvo({ runId, custoPorPasso }: { runId: string; custoPorPasso?: CustoPorPasso | null }) {
  // v1.124: o central que manda o custo por passo dispensa a segunda leitura; sem ele (central anterior), o uso por função e modelo.
  if (custoPorPasso) return <LinhaDoTempoDoAlvo dado={custoPorPasso} />;
  return <ModelosPorUso runId={runId} />;
}

function ModelosPorUso({ runId }: { runId: string }) {
  const { report, error, loading } = useUsage({ run_id: runId });
  if (!report) {
    if (loading) return <Skeleton height={56} radius={8} />;
    return <p className={styles.mudo} role="status" data-modelos-erro>Não foi possível ler os modelos desta execução{error ? `: ${error.message}` : ''}.</p>;
  }
  const m = modelosDoAlvo(report);
  if (m.linhas.length === 0) return <p className={styles.mudo} data-modelos-vazio>Nenhuma chamada de IA registrada nesta execução.</p>;
  return (
    <div data-modelos-do-alvo>
      <table className={styles.tabelaDeModelos}>
        <caption className="sr-only">Chamadas de IA desta execução, por função e modelo</caption>
        <thead><tr><th scope="col">Função</th><th scope="col">Modelo</th><th scope="col">Chamadas</th><th scope="col">US$</th></tr></thead>
        <tbody>
          {m.linhas.map((l) => (
            <tr key={l.chave} data-funcao={l.funcao}>
              <th scope="row">{l.rotulo}</th>
              <td className="mono">{l.modelo}{l.escalonada ? <> <Badge tone="info" icon={TrendingUp} size="sm" title="Chamadas escalonadas para o modelo mais forte">escalonado</Badge></> : null}</td>
              <td>{formatInt(l.chamadas)}{l.erros > 0 ? <span className={styles.mudo}> ({l.erros} com erro)</span> : null}</td>
              <td>{formatUsd(l.usd)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          {m.somas.map((s) => (
            <tr key={s.funcao} data-soma-da-funcao={s.funcao}>
              <th scope="row" colSpan={2}>Soma de {s.rotulo}</th><td>{formatInt(s.chamadas)}</td><td>{formatUsd(s.usd)}{s.parcial ? ' (parcial)' : ''}</td>
            </tr>
          ))}
          <tr data-soma-total>
            <th scope="row" colSpan={2}>Total</th><td>{formatInt(m.chamadas)}</td><td>{formatUsd(m.usd)}{m.parcial ? ' (parcial)' : ''}</td>
          </tr>
        </tfoot>
      </table>
      {m.decisoes.length > 1 ? (
        <p className={styles.mudo} data-divisao-das-decisoes>
          Quem decidiu: {m.decisoes.map((d) => `${d.modelo} ${formatInt(d.chamadas)} (${formatPercent(d.parte * 100)})`).join(' · ')}.
        </p>
      ) : null}
    </div>
  );
}
