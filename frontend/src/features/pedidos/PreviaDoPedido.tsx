import { CalendarClock, CircleAlert, Coins, ShieldCheck, TriangleAlert, Users } from 'lucide-react';
import type { ReactNode } from 'react';
import type { PedidoPrevia, ProximaData } from '../../api/pedidos';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { dataCurta, fusoParaMostrar } from './formato';
import { duracaoCurta, formatUsd, ROTULO_DA_AUTONOMIA } from './modelo';
import styles from './Pedidos.module.css';

/**
 * As próximas datas como a pessoa lê ("sex, 02/10 às 19:00"), na hora do fuso do pedido, com a marca da hora que não
 * existe ou que se repete (horário de verão). O fuso aparece UMA vez, no título, e só quando difere do navegador.
 */
export function ProximasDatas({ datas, titulo = 'Próximas datas', fuso }: { datas: readonly ProximaData[]; titulo?: string; fuso?: string }) {
  const fusoMostrado = fusoParaMostrar(fuso);
  if (datas.length === 0) return <p className={styles.nota}>{titulo}: nenhuma data prevista.</p>;
  return (
    <div>
      <h4 className={styles.dim}>{titulo}{fusoMostrado ? ` (fuso ${fusoMostrado})` : ''}</h4>
      <ul className={styles.datas} aria-label={titulo}>
        {datas.map((d) => (
          <li key={`${d.gatilho}-${d.utc}`} title={d.local}>
            {dataCurta(d.utc, fuso)}
            {d.desviado ? <> <Badge size="sm" tone="warning" title="A hora local não existia (salto do horário de verão): roda no primeiro instante válido depois dele.">hora desviada</Badge></> : null}
            {d.repetido ? <> <Badge size="sm" tone="warning" title="A hora local aconteceu duas vezes: roda só na primeira.">hora repetida, roda só na primeira</Badge></> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Bloco({ icone: Icone, titulo, children }: { icone: typeof Users; titulo: string; children: ReactNode }) {
  return (
    <div className={styles.previaBloco}>
      <h4 className={styles.previaTitulo}><Icone size={14} aria-hidden /> {titulo}</h4>
      {children}
    </div>
  );
}

/**
 * A prévia do pedido (`POST /api/pedidos/previa`): o que a criação decidiria, sem efeito e sem custo, em blocos: quando,
 * quem faz e onde, as próximas datas, autonomia (com o que fica recusado) e custo. O custo nunca é inventado: sem
 * histórico nem teto por ocorrência, o backend diz `sem_base` e a tela diz "sem base de custo".
 */
export function PreviaDoPedido({ previa, resumoQuando, fuso }: { previa: PedidoPrevia; resumoQuando?: string; fuso?: string }) {
  const { custo, autonomia, alvos } = previa;
  return (
    <section className={styles.previa} aria-label="Prévia do pedido">
      <div className={styles.topo}>
        <h3 className={styles.previaCabeca}>Prévia</h3>
        <Badge size="sm" tone={previa.valido ? 'success' : 'danger'}>{previa.valido ? 'Pronta para criar' : 'Com bloqueios'}</Badge>
      </div>
      <p className={styles.nota}>{previa.objetivo_sem_destinos}</p>

      {previa.bloqueios.length > 0 ? (
        <Banner tone="danger" icon={TriangleAlert} compact title="Não dá para criar assim">
          <ul aria-label="Bloqueios" className={styles.listaSimples}>
            {previa.bloqueios.map((b, i) => <li key={`${b.codigo}-${i}`}><strong>{b.codigo}</strong>: {b.mensagem}</li>)}
          </ul>
        </Banner>
      ) : null}
      {previa.alertas.length > 0 ? (
        <Banner tone="warning" icon={CircleAlert} compact>
          <ul aria-label="Alertas" className={styles.listaSimples}>
            {previa.alertas.map((a, i) => <li key={`${a.codigo}-${i}`}>{a.mensagem}</li>)}
          </ul>
        </Banner>
      ) : null}

      <div className={styles.previaGrade}>
        <Bloco icone={CalendarClock} titulo="Quando">
          {resumoQuando ? <p className={styles.nota}>{resumoQuando}</p> : null}
          <ProximasDatas datas={previa.proximas} fuso={fuso} />
          {previa.intervalo_minimo_s !== null ? (
            <p className={styles.dim}>Menor intervalo entre as próximas datas: {duracaoCurta(previa.intervalo_minimo_s)}.</p>
          ) : null}
        </Bloco>

        <Bloco icone={Users} titulo="Quem faz e onde">
          {alvos.targets.length === 0 ? <p className={styles.nota}>Nenhum alvo resolvido.</p> : (
            <ul aria-label="Alvos" className={styles.listaSimples}>
              {alvos.targets.map((t, i) => (
                <li key={`${t.instance_id}-${t.profile_id ?? ''}-${i}`}>
                  {t.instance_id}{t.profile_id ? ` · persona ${t.profile_id}` : ''}{t.app_id ? ` · ${t.app_id}` : ''}
                </li>
              ))}
            </ul>
          )}
          {alvos.questions.length > 0 ? (
            <ul aria-label="Perguntas sobre os alvos" className={`${styles.listaSimples} ${styles.bloqueio}`}>
              {alvos.questions.map((q, i) => <li key={`${q.code}-${i}`}>{q.question}</li>)}
            </ul>
          ) : null}
          {alvos.warnings.map((w) => <p key={w} className={styles.nota}>{w}</p>)}
        </Bloco>

        <Bloco icone={ShieldCheck} titulo={`Autonomia: ${ROTULO_DA_AUTONOMIA[autonomia.teto]?.rotulo ?? autonomia.teto}`}>
          {autonomia.exige_aprovacao.length > 0 ? <p className={styles.nota}>Exige a sua aprovação: {autonomia.exige_aprovacao.join(', ')}.</p> : null}
          {autonomia.recusado.length > 0 ? <p className={styles.nota}>Fica recusado: {autonomia.recusado.join(', ')}.</p> : null}
          {autonomia.exige_aprovacao.length === 0 && autonomia.recusado.length === 0 ? <p className={styles.dim}>Nada fica recusado nem pede aprovação.</p> : null}
        </Bloco>

        <Bloco icone={Coins} titulo="Custo estimado">
          <p className={styles.nota}>
            {custo.base === 'sem_base' || custo.por_mes_usd === null
              ? 'Sem base de custo: ainda não há histórico nem teto por ocorrência.'
              : `${formatUsd(custo.por_mes_usd)} por mês (${formatUsd(custo.por_ocorrencia_usd)} por ocorrência${
                custo.base === 'mediana_das_ultimas_5' ? ', pela mediana das últimas 5' : ', pelo teto por ocorrência'})`}
          </p>
        </Bloco>
      </div>
    </section>
  );
}
