import { useEffect, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { formatInt, formatUsd4, plural } from '../../lib/format';
import { toLoadError, type LoadError } from '../../lib/loadError';
import type { Tone } from '../../lib/status';
import { formatQuando } from '../../lib/time';
import { hashDe } from '../../lib/rotas';
import { ROTULO_DA_VERIFICACAO, ROTULO_DO_ESTADO, rotuloDaAcao, type EstadoDoAlvo } from '../operacao/modelo';
import { lerHistoricoDaPersona, somaDoHistorico, type HistoricoDaPersona } from './historicoDeOperacoes';
import styles from './Profiles.module.css';

const TOM: Record<EstadoDoAlvo, Tone> = { pendente: 'muted', em_curso: 'info', concluido: 'success', bloqueado: 'warning', cancelado: 'muted' };

/**
 * 31.212: as operações de que esta persona foi alvo: o objetivo, o estágio final, a ação e se foi verificada, o custo de IA e um
 * link para a operação. Só leitura. O custo somado não conta como zero o alvo que não informa custo.
 */
export function OperacoesDaPersona({ profileId, versao = 0 }: { profileId: string; versao?: number }) {
  const [historico, setHistorico] = useState<HistoricoDaPersona | null | undefined>(undefined);   // undefined = lendo; null = sem o módulo
  const [erro, setErro] = useState<LoadError | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    lerHistoricoDaPersona(profileId, ctl.signal)
      .then((h) => { setHistorico(h); setErro(null); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); });
    return () => ctl.abort();
  }, [profileId, versao]);

  return (
    <Card>
      <CardHeader title="Operações desta persona" />
      <CardBody>
        {erro && !historico ? <p className={styles.muted} role="status">Não foi possível ler as operações: {erro.message}</p> : null}
        {historico === undefined && !erro ? <p className={styles.muted} role="status">Lendo as operações…</p> : null}
        {historico === null ? <p className={styles.muted} role="status">O central ainda não oferece o módulo de operações.</p> : null}
        {historico ? <Corpo h={historico} /> : null}
      </CardBody>
    </Card>
  );
}

function Corpo({ h }: { h: HistoricoDaPersona }) {
  const soma = somaDoHistorico(h.linhas);
  const olhou = h.lidas < h.totalDeOperacoes ? `Olhei as ${h.lidas} operações mais recentes, de ${formatInt(h.totalDeOperacoes)}.` : `Olhei as ${plural(h.lidas, 'operação', 'operações')}.`;
  return (
    <>
      {h.linhas.length === 0 ? (
        <p className={styles.muted} role="status" data-sem-operacoes>Esta persona não foi alvo de nenhuma operação. {olhou}</p>
      ) : (
        <>
          <p data-soma>
            {plural(soma.alvos, 'alvo', 'alvos')} · {formatInt(soma.concluidos)} {soma.concluidos === 1 ? 'concluído' : 'concluídos'} · {formatInt(soma.verificados)} {soma.verificados === 1 ? 'ação verificada' : 'ações verificadas'} ·{' '}
            custo de IA {soma.custoUsd === null ? 'não informado' : formatUsd4(soma.custoUsd)}
            {soma.custoUsd !== null && soma.semCusto > 0 ? ` (${plural(soma.semCusto, 'alvo sem custo informado fica fora', 'alvos sem custo informado ficam fora')} da soma)` : ''}.
          </p>
          <div className={styles.tabelaHost}>
          <table className={styles.tabela} aria-label="Operações desta persona">
            <thead>
              <tr><th scope="col">Operação</th><th scope="col">Quando</th><th scope="col">Estado</th><th scope="col">Estágio final</th><th scope="col">Ação</th><th scope="col">Verificação</th><th scope="col">Custo de IA</th></tr>
            </thead>
            <tbody>
              {h.linhas.map((l, i) => (
                <tr key={`${l.operacaoId}:${i}`} data-operacao={l.operacaoId}>
                  <th scope="row"><a href={hashDe('operacoes', { segmentos: [l.operacaoId] })}>{l.comando || l.operacaoId}</a></th>
                  <td>{l.criadaEm ? formatQuando(l.criadaEm) : '—'}</td>
                  <td>{l.estado ? <Badge tone={TOM[l.estado]} size="sm">{ROTULO_DO_ESTADO[l.estado]}</Badge> : 'não informado'}</td>
                  <td data-estagio-final>{l.estagioFinal}{l.motivo ? <><br /><span className={styles.muted}>{l.motivo}</span></> : null}</td>
                  <td>{l.acao ? rotuloDaAcao(l.acao) : '—'}</td>
                  <td>{l.verificacao === 'sem_acao' ? '—' : ROTULO_DA_VERIFICACAO[l.verificacao]}</td>
                  <td data-custo>{l.custoUsd === null ? '—' : formatUsd4(l.custoUsd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <p className={styles.muted}>{olhou}</p>
        </>
      )}
      {h.falhas > 0 ? (
        <p className={styles.muted} role="status" data-falhas>{plural(h.falhas, 'operação não pôde ser lida e ficou', 'operações não puderam ser lidas e ficaram')} fora desta lista.</p>
      ) : null}
    </>
  );
}
