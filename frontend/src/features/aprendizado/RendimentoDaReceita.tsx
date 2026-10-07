import { FlaskConical } from 'lucide-react';
import { Banner } from '../../components/Banner';
import { formatInt, plural } from '../../lib/format';
import { LoadErrorState } from '../../lib/loadError';
import { formatQuando } from '../../lib/time';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { SEM_DADO } from './detalhe';
import { formatUsd } from './metricas';
import { apiRendimento, type PorUso, type RendimentoDaReceita } from './rendimento';
import styles from './Aprendizado.module.css';
import { useCarga } from './useCarga';

/**
 * 31.196: o que esta receita rendeu, por uso (`GET /api/aprendizado/receitas/{id}/rendimento`, adendo v1.110). Só leitura, sem IA.
 * Uso real, prova e execução simulada ficam em colunas separadas e nunca se somam: a prova e a simulação não são uso de verdade.
 */

const n = (v: number | null): string => (v === null ? SEM_DADO : formatInt(v));

function Linha({ rotulo, u, nome }: { rotulo: string; u: PorUso; nome: string }) {
  return (
    <tr data-linha={nome}>
      <th scope="row">{rotulo}</th>
      <td data-uso="real">{n(u.real)}</td>
      <td data-uso="prova">{n(u.prova)}</td>
      <td data-uso="simulada">{n(u.simulada)}</td>
    </tr>
  );
}

function Resumo({ r }: { r: RendimentoDaReceita }) {
  const real = r.semIa.real === null && r.caiuNaIa.real === null && r.outras.real === null ? null : (r.semIa.real ?? 0) + (r.caiuNaIa.real ?? 0) + (r.outras.real ?? 0);
  if (real === null) return <p className={styles.secaoLead}>O central não informou o uso real desta receita.</p>;
  if (real === 0) return <p className={styles.secaoLead} data-resumo>Ainda sem uso real{r.ultimoUsoEm ? `: o último uso foi ${formatQuando(r.ultimoUsoEm)}` : ''}.</p>;
  return (
    <p data-resumo>
      Usada em <strong>{plural(real, 'etapa de verdade', 'etapas de verdade')}</strong>
      {r.semIa.real !== null ? <>: <strong>{formatInt(r.semIa.real)}</strong> sem IA{r.caiuNaIa.real !== null ? `, ${formatInt(r.caiuNaIa.real)} em que a IA assumiu` : ''}</> : null}.
      {r.ultimoUsoEm ? ` Último uso ${formatQuando(r.ultimoUsoEm)}.` : ''}
    </p>
  );
}

export function RendimentoDaReceitaView({ r }: { r: RendimentoDaReceita }) {
  return (
    <>
      {r.exemplo ? (
        <Banner tone="info" icon={FlaskConical} compact role="status" title="Dados de exemplo">
          O central ainda não oferece o rendimento da receita: estes números são um exemplo inventado. Nada aqui veio do parque.
        </Banner>
      ) : null}
      <Resumo r={r} />
      <table className={styles.tabelaDetalhe}>
        <caption>Etapas por tipo de uso (prova e simulada não contam como uso real)</caption>
        <thead><tr><th scope="col">O que a receita fez</th><th scope="col">Uso real</th><th scope="col">Prova</th><th scope="col">Simulada</th></tr></thead>
        <tbody>
          <Linha nome="sem_ia" rotulo="Conduziu e comprovou sem IA" u={r.semIa} />
          <Linha nome="caiu_na_ia" rotulo="Divergiu e a IA assumiu" u={r.caiuNaIa} />
          <Linha nome="outras" rotulo="Outras" u={r.outras} />
        </tbody>
      </table>
      <dl className={styles.fatos} data-custo>
        <dt>Custo evitado</dt>
        <dd data-custo-evitado>{r.custoEvitadoUsd === null ? <span className={styles.semDado}>sem referência de custo</span> : formatUsd(r.custoEvitadoUsd)}</dd>
        <dt>Custo médio de IA por etapa</dt>
        <dd>{r.custoMedioDaIaPorEtapaUsd === null ? <span className={styles.semDado}>{SEM_DADO}</span> : formatUsd(r.custoMedioDaIaPorEtapaUsd)}</dd>
        <dt>IA gasta nas tentativas dela</dt>
        <dd>{r.usdDaIaNaRetencao === null ? <span className={styles.semDado}>{SEM_DADO}</span> : formatUsd(r.usdDaIaNaRetencao)}</dd>
      </dl>
      <p className={styles.secaoLead}>
        O custo evitado é o uso real sem IA vezes o custo médio de IA de uma etapa com a mesma chave no mesmo app, nas chamadas que o central ainda guarda.
        {r.reproducoes.ok !== null || r.reproducoes.falha !== null ? ` Reproduções: ${n(r.reproducoes.ok)} certas, ${n(r.reproducoes.falha)} com falha.` : ''}
      </p>
    </>
  );
}

export function RendimentoDaReceitaSecao({ receitaRef }: { receitaRef: string }) {
  const { dado, erro, carregando, carregar } = useCarga((signal) => apiRendimento.daReceita(receitaRef, signal), receitaRef);
  if (erro) return <LoadErrorState what="o rendimento da receita" error={erro} onRetry={() => void carregar()} compact />;
  if (carregando || !dado) return <LoadingRegion label="Lendo o rendimento"><Skeleton height={48} /></LoadingRegion>;
  return <RendimentoDaReceitaView r={dado} />;
}
