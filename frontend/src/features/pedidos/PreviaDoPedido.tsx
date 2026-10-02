import type { PedidoPrevia, ProximaData } from '../../api/pedidos';
import { Badge } from '../../components/Badge';
import { duracaoCurta, formatUsd, ROTULO_DA_AUTONOMIA } from './modelo';
import styles from './Pedidos.module.css';

/** As próximas datas, no fuso do pedido, com a marca da hora que não existe ou que se repete (horário de verão). */
export function ProximasDatas({ datas, titulo = 'Próximas datas' }: { datas: readonly ProximaData[]; titulo?: string }) {
  if (datas.length === 0) return <p className={styles.nota}>{titulo}: nenhuma data prevista.</p>;
  return (
    <div>
      <h4 className={styles.dim}>{titulo}</h4>
      <ul aria-label={titulo}>
        {datas.map((d) => (
          <li key={`${d.gatilho}-${d.utc}`}>
            {d.local}
            {d.desviado ? <> <Badge size="sm" tone="warning" title="A hora local não existia (salto do horário de verão): roda no primeiro instante válido depois dele.">hora desviada</Badge></> : null}
            {d.repetido ? <> <Badge size="sm" tone="warning" title="A hora local aconteceu duas vezes: roda só na primeira.">hora repetida, roda só na primeira</Badge></> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A prévia do pedido (`POST /api/pedidos/previa`): o que a criação decidiria, sem efeito e sem custo. O custo nunca
 * é inventado: sem histórico nem teto por ocorrência, o backend diz `sem_base` e a tela diz "sem base de custo".
 */
export function PreviaDoPedido({ previa, resumoQuando }: { previa: PedidoPrevia; resumoQuando?: string }) {
  const { custo, autonomia, alvos } = previa;
  return (
    <section className={styles.previa} aria-label="Prévia do pedido">
      <h3>Prévia {previa.valido ? '' : '(com bloqueios)'}</h3>
      {resumoQuando ? <span className={styles.nota}>Quando: {resumoQuando}</span> : null}
      <span className={styles.nota}>Objetivo: {previa.objetivo_sem_destinos}</span>

      {previa.bloqueios.length > 0 ? (
        <ul aria-label="Bloqueios">
          {previa.bloqueios.map((b, i) => <li key={`${b.codigo}-${i}`} className={styles.bloqueio}><strong>{b.codigo}</strong>: {b.mensagem}</li>)}
        </ul>
      ) : null}
      {previa.alertas.length > 0 ? (
        <ul aria-label="Alertas">
          {previa.alertas.map((a, i) => <li key={`${a.codigo}-${i}`}>{a.mensagem}</li>)}
        </ul>
      ) : null}

      <div>
        <h4 className={styles.dim}>Quem faz e onde</h4>
        {alvos.targets.length === 0 ? <p className={styles.nota}>Nenhum alvo resolvido.</p> : (
          <ul aria-label="Alvos">
            {alvos.targets.map((t, i) => (
              <li key={`${t.instance_id}-${t.profile_id ?? ''}-${i}`}>
                {t.instance_id}{t.profile_id ? ` · persona ${t.profile_id}` : ''}{t.app_id ? ` · ${t.app_id}` : ''}
                {' '}<span className={styles.dim}>(origem: {t.origem})</span>
              </li>
            ))}
          </ul>
        )}
        {alvos.questions.length > 0 ? (
          <ul aria-label="Perguntas sobre os alvos">
            {alvos.questions.map((q, i) => <li key={`${q.code}-${i}`} className={styles.bloqueio}>{q.question}</li>)}
          </ul>
        ) : null}
        {alvos.warnings.map((w) => <p key={w} className={styles.nota}>{w}</p>)}
      </div>

      <ProximasDatas datas={previa.proximas} />
      {previa.intervalo_minimo_s !== null ? (
        <span className={styles.nota}>Menor intervalo entre as próximas datas: {duracaoCurta(previa.intervalo_minimo_s)}.</span>
      ) : null}

      <div>
        <h4 className={styles.dim}>Autonomia: {ROTULO_DA_AUTONOMIA[autonomia.teto]?.rotulo ?? autonomia.teto}</h4>
        {autonomia.exige_aprovacao.length > 0 ? <p className={styles.nota}>Exige a sua aprovação: {autonomia.exige_aprovacao.join(', ')}.</p> : null}
        {autonomia.recusado.length > 0 ? <p className={styles.nota}>Fica recusado: {autonomia.recusado.join(', ')}.</p> : null}
      </div>

      <span className={styles.nota}>
        Custo estimado:{' '}
        {custo.base === 'sem_base' || custo.por_mes_usd === null
          ? 'sem base de custo (ainda não há histórico nem teto por ocorrência)'
          : `${formatUsd(custo.por_mes_usd)} por mês (${formatUsd(custo.por_ocorrencia_usd)} por ocorrência${
            custo.base === 'mediana_das_ultimas_5' ? ', pela mediana das últimas 5' : ', pelo teto por ocorrência'})`}
      </span>
    </section>
  );
}
