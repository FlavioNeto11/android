import { CircleCheck, CircleX, Columns2, Copy, FlaskConical, Hourglass, MessageCircleQuestion, Play, Plus, ShieldCheck, ShieldQuestion, Workflow, type LucideIcon } from 'lucide-react';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox, Field, Select, TextInput } from '../../components/Field';
import { Page } from '../../components/Page';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx, formatInt, formatUsd4, plural } from '../../lib/format';
import { useIntervaloVisivel } from '../../lib/polling';
import { hashDe } from '../../lib/rotas';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import type { Tone } from '../../lib/status';
import { formatClock, formatQuando, formatSpan } from '../../lib/time';
import { toast, toastError } from '../../store/toasts';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { apiOperacoes, type ListaDeOperacoes } from './api';
import { AprendizadoDaOperacaoTab } from './AprendizadoDaOperacaoTab';
import { CriarOperacao } from './CriarOperacao';
import { guardarRascunho, rascunhoDaOperacao } from './criar';
import { filaEmPalavras, MOTIVO_DO_TETO } from './fila';
import { latenciaDaOperacao, latenciaDoAlvo } from './latencia';
import { CancelarAlvos } from './CancelarAlvos';
import { CompararOperacoes } from './CompararOperacoes';
import { estadoDoLaco, lacoEmPalavras, lacoExplica } from './laco';
import { LiberarAcoes } from './LiberarAcoes';
import { SomasDoCusto } from './LinhaDoTempoDoAlvo';
import { ModelosDoAlvo } from './ModelosDoAlvo';
import { PesquisaDaOperacaoSecao } from './PesquisaDaOperacaoSecao';
import { mascararTerceiros, usuariosConhecidosDaOperacao } from './terceiros';
import { RelatorioDaOperacao } from './RelatorioDaOperacao';
import { conhecimentoEmPalavras } from './pesquisaDaOperacao';
import styles from './Operacao.module.css';
import {
  acoesDaOperacao, acoesJaExecutadas, agregadoPorApp, alvosPreparados, adiadosDaOperacao, contarPorEstado, contarPorStatus, descricaoDaOperacao, filtrarOperacoes, isStatusDaOperacao, STATUS_DA_OPERACAO, ESTADOS_DO_ALVO, esperasDaOperacao, ESTAGIOS, estagiosAlcancados, estagioDeParada, fonteComoLink, isEstadoDoAlvo, isEstagio,
  ROTULO_DA_VERIFICACAO, ROTULO_DO_ESTADO, ROTULO_DO_STATUS, rotuloDaAcao, rotuloDoEstagio, verificacaoDoAlvo, type Alvo, type AlvoPreparado, type EstadoDoAlvo,
  type EstagioId, type Operacao, type ResumoDaOperacao, type StatusDaOperacao, type Verificacao,
} from './modelo';

/**
 * A tela "Operação" (prova de 07/10, FULL INSTAGRAM): objetivo → agentes → estado de cada um → conhecimento → ação → evidência →
 * falha → resultado. Uma linha por ALVO (persona + conta + aparelho), nunca por aparelho; o pipeline aparece em 14 estágios
 * na ordem do dono; no topo, a capacidade (solicitados, contas, sessões, disponíveis, concluídas, bloqueadas e os motivos): o
 * déficit aparece, não se esconde. Só leitura, exceto o cancelar (com confirmação). Contrato: rascunho do adendo v1.94; até a
 * rota existir no central, a tela lê um exemplo fixo e diz isso.
 */

/** O segmento da rota que abre o formulário de criação (`#/operacoes/nova`); os ids de operação nunca têm esta forma. */
const ROTA_NOVA = 'nova';
const ROTA_COMPARAR = 'comparar';
const MAX_COMPARADAS = 2;

const TOM_DO_ESTADO: Record<EstadoDoAlvo, Tone> = { pendente: 'muted', em_curso: 'info', concluido: 'success', bloqueado: 'warning', cancelado: 'muted' };
const ICONE_DO_ESTADO: Record<EstadoDoAlvo, LucideIcon> = { pendente: Hourglass, em_curso: Play, concluido: CircleCheck, bloqueado: ShieldQuestion, cancelado: CircleX };
const TOM_DO_STATUS: Record<StatusDaOperacao, Tone> = { em_curso: 'info', concluida: 'success', concluida_com_bloqueios: 'warning', cancelada: 'muted' };
const VERIFICACAO: Record<Verificacao, { icone: LucideIcon; tom: Tone }> = {
  verificada: { icone: ShieldCheck, tom: 'success' }, nao_verificada: { icone: ShieldQuestion, tom: 'warning' }, sem_acao: { icone: ShieldQuestion, tom: 'muted' },
};

type AbaDaOperacao = 'agentes' | 'aprendizado';
const ABAS: readonly TabDef<AbaDaOperacao>[] = [{ id: 'agentes', label: 'Agentes' }, { id: 'aprendizado', label: 'Aprendizado' }];

const numero = (n: number | null): string => (n === null ? 'não informado' : formatInt(n));

function FaixaDeCapacidade({ op }: { op: Operacao }) {
  const c = op.capacidade;
  const teto = c.motivos.find((m) => m.motivo === MOTIVO_DO_TETO && m.n > 0) ?? null;
  const celulas: { rotulo: string; valor: number | null; tom?: Tone }[] = [
    { rotulo: 'solicitados', valor: c.solicitados ?? op.alvos.length },
    { rotulo: 'contas existentes', valor: c.contas_existentes },
    { rotulo: 'sessões válidas', valor: c.sessoes_validas },
    { rotulo: 'disponíveis', valor: c.contas_disponiveis },
    { rotulo: 'em curso', valor: c.em_curso },
    { rotulo: 'concluídas', valor: c.concluidas, tom: 'success' },
    { rotulo: 'bloqueadas', valor: c.bloqueadas, tom: 'warning' },
  ];
  return (
    <section className={styles.faixa} aria-labelledby="operacao-capacidade">
      <h2 id="operacao-capacidade" className={styles.subtitulo}>Capacidade</h2>
      <dl className={styles.celulas}>
        {celulas.map((x) => (
          <div key={x.rotulo} className={cx(styles.celula, x.tom === 'success' && styles.celulaOk, x.tom === 'warning' && styles.celulaAtencao)}>
            <dd className={styles.celulaValor}>{numero(x.valor)}</dd>
            <dt className={styles.celulaRotulo}>{x.rotulo}</dt>
          </div>
        ))}
      </dl>
      {teto ? (
        <div data-teto-da-operacao>
          <Banner tone="warning" icon={ShieldQuestion} compact role="status" title={`${formatInt(teto.n)} ${teto.n === 1 ? 'agente cortado' : 'agentes cortados'} pelo teto da operação`}>
            {op.max_usd !== null && op.custo?.total_usd != null
              ? `O gasto de IA chegou a ${formatUsd4(op.custo.total_usd)} de um teto de ${formatUsd4(op.max_usd)}: o que passaria disso não foi planejado.`
              : 'O gasto de IA bateu no teto de gasto da operação: o que passaria disso não foi planejado.'}
          </Banner>
        </div>
      ) : null}
      {c.motivos.length ? (
        <div>
          <h3 className={styles.subtitulo}>Motivos dos bloqueios</h3>
          <ul className={styles.motivos}>
            {c.motivos.map((m) => <li key={m.motivo} data-motivo-do-teto={m.motivo === MOTIVO_DO_TETO ? '' : undefined}><strong>{formatInt(m.n)}</strong> {mascararTerceiros(m.motivo, usuariosConhecidosDaOperacao(op.parametros))}</li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

/**
 * 31.185: quanto tempo cada estágio levou e quanto cada agente levou no total, dos carimbos que o central já manda. Só mostra o que
 * há: o intervalo entre duas horas iguais (hoje, as da liberação) ou fora de ordem fica de fora da mediana, e a tela diz quantos.
 */
function Latencia({ op }: { op: Operacao }) {
  const l = latenciaDaOperacao(op.alvos, op.latencia_por_estagio);
  if (l.agentes === 0) return null;
  return (
    <section aria-labelledby="operacao-latencia" data-latencia>
      <h2 id="operacao-latencia" className={styles.subtitulo}>Latência</h2>
      {l.comTempo === 0 ? (
        <p className={styles.mudo}>Nenhum agente tem hora em pelo menos dois estágios: não há tempo a mostrar.</p>
      ) : (
        <>
          <p className={styles.objetivo}>
            Mediana por agente <strong>{l.medianaDoTotalMs === null ? '—' : formatSpan(l.medianaDoTotalMs)}</strong> ({formatInt(l.comTempo)} de {formatInt(l.agentes)} com tempo medido).
            {l.maisLento ? <> Estágio mais lento: <strong>{rotuloDoEstagio(l.maisLento.estagio)}</strong>, mediana {formatSpan(l.maisLento.medianaMs)}.</> : <> Nenhum intervalo entre estágios pôde ser medido.</>}
          </p>
          {l.porEstagio.length ? (
            <div className={styles.rolagem}>
              <table className={styles.tabela}>
                <caption className="sr-only">{l.fonte === 'central' ? 'Tempo de cada estágio desde o evento anterior, calculado pelo central: mediana, p95, maior e quantos agentes entram na conta.' : 'Tempo até cada estágio, desde o estágio anterior com hora: mediana, maior e quantos agentes entram na conta.'}</caption>
                <thead><tr><th scope="col">Estágio</th><th scope="col">Mediana</th>{l.fonte === 'central' ? <th scope="col">p95</th> : null}<th scope="col">Maior</th><th scope="col">Agentes</th></tr></thead>
                <tbody>
                  {l.porEstagio.map((e) => (
                    <tr key={e.estagio} data-estagio={e.estagio}>
                      <th scope="row" className={styles.persona}>{rotuloDoEstagio(e.estagio)}</th>
                      <td className={styles.numero}>{formatSpan(e.medianaMs)}</td>
                      {l.fonte === 'central' ? <td className={styles.numero} data-p95>{e.p95Ms == null ? '—' : formatSpan(e.p95Ms)}</td> : null}
                      <td className={styles.numero}>{formatSpan(e.maiorMs)}</td>
                      <td className={styles.numero}>{formatInt(e.agentes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
      {l.fonte === 'local' && l.mesmaHora + l.foraDeOrdem > 0 ? (
        <p className={styles.mudo} role="status" data-fora-da-conta>
          Ficaram de fora da conta {l.mesmaHora > 0 ? `${plural(l.mesmaHora, 'intervalo com a mesma hora do anterior', 'intervalos com a mesma hora do anterior')}` : ''}
          {l.mesmaHora > 0 && l.foraDeOrdem > 0 ? ' e ' : ''}
          {l.foraDeOrdem > 0 ? `${plural(l.foraDeOrdem, 'intervalo com hora fora de ordem', 'intervalos com hora fora de ordem')}` : ''}: o central carimbou esses estágios juntos ou fora da sequência.
        </p>
      ) : null}
    </section>
  );
}

function PorApp({ op }: { op: Operacao }) {
  const grupos = agregadoPorApp(op.alvos, op.app_id);
  return (
    <section aria-labelledby="operacao-por-app">
      <h2 id="operacao-por-app" className={styles.subtitulo}>Por aplicativo</h2>
      <ul className={styles.porApp}>
        {grupos.map((g) => (
          <li key={g.app}>
            <strong>{g.app}</strong>: {formatInt(g.alvos)} alvos, {formatInt(g.concluidos)} concluídos, {formatInt(g.verificados)} verificados,{' '}
            {formatInt(g.bloqueados)} bloqueados
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Os 14 estágios como pontos: cheios até onde chegou, o da parada marcado. A leitura de tela recebe a frase, não os pontos. */
function Pipeline({ alvo }: { alvo: Alvo }) {
  const feitos = estagiosAlcancados(alvo);
  const parada = alvo.estado === 'concluido' || alvo.estado === null ? null : estagioDeParada(alvo);
  const frase = parada === null
    ? `${feitos} de ${ESTAGIOS.length} estágios; último: “${rotuloDoEstagio(alvo.estagio)}”`
    : `${feitos} de ${ESTAGIOS.length} estágios; parou em “${rotuloDoEstagio(parada)}”`;
  return (
    <div className={styles.pipeline} role="img" aria-label={frase} title={frase}>
      {ESTAGIOS.map((e, i) => (
        <span key={e.id} className={cx(styles.ponto, i < feitos && styles.pontoFeito, e.id === parada && alvo.estado !== 'em_curso' && styles.pontoParada)} />
      ))}
    </div>
  );
}

/** O que o alvo mostra ao abrir: o texto gerado, o conhecimento, as evidências e os estágios com a hora. Só leitura. */
function DetalheDoAlvo({ alvo }: { alvo: Alvo }) {
  const r = alvo.resultado;
  const latencia = latenciaDoAlvo(alvo);
  const aba = (nome: string) => (alvo.run_id ? hashDe('execucoes', { segmentos: [alvo.run_id], query: { aba: nome } }) : null);
  const evidencia = (id: number | null, rotulo: string) => {
    const link = aba('evidencias');
    return id === null ? null : <li>{rotulo}: {link ? <a className={styles.link} href={link}>evidência {id}</a> : `evidência ${id}`}</li>;
  };
  return (
    <div className={styles.detalhe}>
      <div>
        <h4 className={styles.subtitulo}>Resposta gerada</h4>
        <p className={styles.resposta}>{r?.texto ?? 'Ainda não há texto para este agente.'}</p>
        {r?.acao_final ? (
          <p>Ação final: {rotuloDaAcao(r.acao_final.tipo)}, {r.acao_final.verificada === true ? 'verificada' : 'não verificada'}.</p>
        ) : <p className={styles.mudo}>Sem ação final neste agente.</p>}
        <ul className={styles.motivos}>
          {evidencia(r?.evidencia_id ?? null, 'Tela lida para escrever')}
          {evidencia(r?.acao_final?.evidencia_id ?? null, 'Prova da ação')}
        </ul>
        {alvo.run_id ? <p><a className={styles.link} href={hashDe('execucoes', { segmentos: [alvo.run_id] })}>Abrir a execução</a></p> : null}
      </div>
      <div>
        <h4 className={styles.subtitulo}>Conhecimento usado ({formatInt(r?.conhecimento_ids.length ?? 0)})</h4>
        {r?.conhecimento_ids.length ? (
          <ul className={styles.motivos}>
            {r.conhecimento_ids.map((k) => {
              const c = conhecimentoEmPalavras(k);
              return <li key={k} data-conhecimento={c.tipo ?? 'outro'}>{c.tipo ? <>{c.tipo}: </> : null}<span className="mono">{c.chave}</span></li>;
            })}
          </ul>
        ) : <p className={styles.mudo}>Nenhum item informado.</p>}
      </div>
      <div>
        <h4 className={styles.subtitulo}>Estágios alcançados</h4>
        {alvo.estagios.length ? (
          <ol className={styles.motivos}>
            {alvo.estagios.map((e) => {
              const passo = latencia.passos.find((p) => p.estagio === e.estagio);
              return (
                <li key={e.estagio}>{rotuloDoEstagio(e.estagio)}{e.em ? <span className={styles.mudo}> · {formatClock(e.em)}</span> : null}
                  {passo?.situacao === 'ok' ? <span className={styles.mudo} data-passo="ok"> · +{formatSpan(passo.ms!)}</span> : null}
                  {passo?.situacao === 'mesma_hora' ? <span className={styles.mudo} data-passo="mesma_hora"> · mesma hora {passo.deEstagio ? <>que “{rotuloDoEstagio(passo.deEstagio)}”</> : 'do evento anterior'}</span> : null}
                  {passo?.situacao === 'fora_de_ordem' ? <span className={styles.mudo} data-passo="fora_de_ordem"> · hora anterior à de “{rotuloDoEstagio(passo.deEstagio)}”: fora de ordem</span> : null}
                </li>
              );
            })}
          </ol>
        ) : <p className={styles.mudo}>O backend não informou a hora de cada estágio.</p>}
        {latencia.esperaDoLiberarMs !== null ? <p className={styles.mudo} data-espera-do-liberar>Esperou a aprovação {formatSpan(latencia.esperaDoLiberarMs)} (da ação preparada ao liberar; não entra no tempo da ação executada).</p> : null}
      </div>
      <div className={styles.blocoDeModelos}>
        <h4 className={styles.subtitulo}>Modelos e custo</h4>
        {alvo.run_id ? <ModelosDoAlvo runId={alvo.run_id} custoPorPasso={alvo.custo_por_passo} /> : <p className={styles.mudo}>Este agente não tem execução: não há chamada de IA a mostrar.</p>}
      </div>
    </div>
  );
}

function LinhaDoAlvo({ alvo, aberta, onAlternar, conhecidos, preparado, onLiberar, motivoSemVaga }: {
  alvo: Alvo; aberta: boolean; onAlternar: () => void; conhecidos: readonly string[];
  /** O texto preparado deste agente, quando ele PARA em "ação preparada" esperando a pessoa (31.257); `null` nos demais. */
  preparado: AlvoPreparado | null; onLiberar: () => void; motivoSemVaga: string | null;
}) {
  // Parado esperando liberação (e não já liberado e aguardando o espaçamento): mostra o texto preparado e o botão "Liberar" na linha.
  // Só quando o motivo é a espera (ou não há motivo): parado no teto de ações ("limite de ações executadas") continua dizendo o motivo.
  const espera = preparado !== null && alvo.estado === 'bloqueado' && !alvo.aguarda_resposta && (!alvo.motivo || /aguarda libera/i.test(alvo.motivo));
  const estado = alvo.estado;
  const Icone = estado ? ICONE_DO_ESTADO[estado] : null;
  const verificacao = verificacaoDoAlvo(alvo);
  const ver = VERIFICACAO[verificacao];
  const rotuloDaLinha = alvo.persona ?? 'Persona não informada';
  const total = latenciaDoAlvo(alvo).totalMs;
  const fila = alvo.fila ? filaEmPalavras(alvo.fila) : null;
  return (
    <Fragment>
      <tr data-alvo={alvo.id}>
        <th scope="row" className={styles.persona}>{rotuloDaLinha}</th>
        <td>{alvo.conta ?? <span className={styles.mudo}>sem conta</span>}</td>
        <td>{alvo.instance_id ?? <span className={styles.mudo}>sem aparelho</span>}</td>
        <td><Pipeline alvo={alvo} /></td>
        <td data-fila>{fila ? <><strong>{fila.posicao}</strong>{fila.aFrente ? <span className={styles.mudo}> · {fila.aFrente}</span> : null}<br /><span className={styles.mudo}>{fila.previsao}</span></> : <span className={styles.mudo}>—</span>}</td>
        <td>
          {/* Execução parada numa pergunta não está "na fila" nem "em andamento": nada avança sem a resposta (31.246). */}
          {alvo.aguarda_resposta ? <span data-aguardando-resposta><Badge tone="warning" icon={MessageCircleQuestion} size="sm">Aguardando resposta</Badge></span>
            : alvo.retomada_em ? <span data-adiado-pela-frota><Badge tone="info" icon={Hourglass} size="sm">Adiado pela frota</Badge></span>
            : estado && Icone ? <Badge tone={TOM_DO_ESTADO[estado]} icon={Icone} size="sm">{ROTULO_DO_ESTADO[estado]}</Badge> : <span className={styles.mudo}>não informado</span>}
        </td>
        <td className={styles.acao}>
          {alvo.aguarda_resposta ? (
            <span data-pergunta-do-alvo>
              <strong>A IA pergunta:</strong> {alvo.aguarda_resposta.pergunta ?? <span className={styles.mudo}>o central não mandou a pergunta</span>}
            </span>
          ) : null}
          {espera ? (
            <span data-texto-preparado>
              <strong>Aguarda liberação. Texto preparado:</strong> {preparado!.texto}
            </span>
          ) : null}
          {/* Ação barrada não "parou em Ação executada": ela falhou ao executar (31.254); e o motivo vem do backend, que pode citar terceiros. */}
          {/* Adiado pelo espaçamento entre contas (31.258): não parou, espera a vez; diz quando retoma, em hora local. */}
          {!alvo.aguarda_resposta && alvo.retomada_em ? (
            <span data-retomada-em={alvo.retomada_em}>
              <strong>Adiado pelo espaçamento entre contas da frota:</strong> retoma às {horaCurta(alvo.retomada_em)} (hora local).
            </span>
          ) : null}
          {!alvo.aguarda_resposta && !espera && !alvo.retomada_em && alvo.motivo ? (
            <span data-motivo-do-alvo={alvo.acao_barrada ? 'falha' : 'parada'}>
              <strong>{alvo.acao_barrada ? 'Falhou ao executar' : `Parou em ${rotuloDoEstagio(estagioDeParada(alvo))}`}:</strong> {mascararTerceiros(alvo.motivo, conhecidos)}
            </span>
          ) : null}
          {!alvo.aguarda_resposta && !espera && !alvo.retomada_em && !alvo.motivo && alvo.resultado?.texto ? <span>{alvo.resultado.texto}</span> : null}
          {!alvo.aguarda_resposta && !espera && !alvo.retomada_em && !alvo.motivo && !alvo.resultado?.texto ? <span className={styles.mudo}>—</span> : null}
          {alvo.resultado ? <span className={styles.mudo}> · {formatInt(alvo.resultado.conhecimento_ids.length)} itens de conhecimento</span> : null}
        </td>
        <td>
          {verificacao === 'sem_acao' ? <span className={styles.mudo}>—</span> : <Badge tone={ver.tom} icon={ver.icone} size="sm">{ROTULO_DA_VERIFICACAO[verificacao]}</Badge>}
        </td>
        <td className={styles.numero}>{alvo.custo_usd === null ? <span className={styles.mudo}>—</span> : formatUsd4(alvo.custo_usd)}</td>
        <td className={styles.numero} data-duracao>{total === null ? <span className={styles.mudo}>—</span> : formatSpan(total)}</td>
        <td>
          {espera ? (
            <Button size="sm" variant="primary" disabledReason={motivoSemVaga} onClick={onLiberar} label={`Liberar o texto de ${rotuloDaLinha}`}>Liberar</Button>
          ) : null}
          <Button size="sm" variant="ghost" aria-expanded={aberta} onClick={onAlternar} label={`${aberta ? 'Fechar' : 'Abrir'} o detalhe de ${rotuloDaLinha}`}>
            {aberta ? 'Fechar' : 'Detalhe'}
          </Button>
        </td>
      </tr>
      {aberta ? <tr className={styles.linhaDoDetalhe}><td colSpan={11}><DetalheDoAlvo alvo={alvo} /></td></tr> : null}
    </Fragment>
  );
}

/** De quanto em quanto tempo a operação em andamento (e a lista) se relê sozinha, com a aba à vista (31.247). */
const RELEITURA_DA_OPERACAO_MS = 5000;
const RELEITURA_DA_LISTA_MS = 10000;

function useCarga<T>(ler: (sinal: AbortSignal) => Promise<T>, chave: string): { dado: T | null; erro: LoadError | null; carregando: boolean; lidoEm: string | null; recarregar: () => void } {
  const [dado, setDado] = useState<T | null>(null);
  const [lidoEm, setLidoEm] = useState<string | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [vez, setVez] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setCarregando(true);
    ler(ctl.signal).then((d) => { setDado(d); setLidoEm(new Date().toISOString()); setErro(null); }).catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); })
      .finally(() => { if (!ctl.signal.aborted) setCarregando(false); });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chave, vez]);
  return { dado, erro, carregando, lidoEm, recarregar: useCallback(() => setVez((n) => n + 1), []) };
}

const AVISO_DE_EXEMPLO = (
  <Banner tone="info" icon={FlaskConical} title="Dados de exemplo" role="status">
    O módulo de operações (adendo v1.94) ainda não está no ar neste central: esta tela lê um exemplo fixo, com personas e contas inventadas.
    Nada aqui veio do parque. Quando a rota responder, a tela passa a ler o real.
  </Banner>
);

/** O custo (total, teto e a divisão pesquisa × agentes), o assunto e as fontes que o operador indicou. */
/** O selo do cabeçalho: quantos agentes esperam resposta e a(s) pergunta(s) literal(is); nada quando o central não diz. */
function EsperaDeResposta({ op }: { op: Operacao }) {
  const { quantos, perguntas } = esperasDaOperacao(op);
  if (quantos === 0) return null;
  return (
    <div data-espera-de-resposta>
      <Banner tone="warning" icon={MessageCircleQuestion} role="status" compact
              title={quantos === 1 ? '1 agente aguarda resposta' : `${formatInt(quantos)} agentes aguardam resposta`}>
        {perguntas.length ? <>A execução parou e pergunta: {perguntas.map((p, i) => <span key={p}>{i > 0 ? ' · ' : ''}“{p}”</span>)}. </> : null}
        Nada avança sem a resposta.
      </Banner>
    </div>
  );
}

/** HH:MM em hora local (o rótulo diz "hora local"; a rota está em UTC). */
const horaCurta = (iso: string): string => formatClock(iso).slice(0, 5);

/** O selo do cabeçalho: quantos alvos esperam a vez pelo espaçamento da frota e quando o primeiro retoma; nada quando o central não diz (31.264). */
function AdiadosPelaFrota({ op }: { op: Operacao }) {
  const { quantos, primeiraRetomada } = adiadosDaOperacao(op);
  if (quantos === 0) return null;
  return (
    <div data-adiados-pela-frota>
      <Banner tone="info" icon={Hourglass} role="status" compact
              title={quantos === 1 ? '1 agente adiado pelo espaçamento da frota' : `${formatInt(quantos)} agentes adiados pelo espaçamento da frota`}>
        Não é falha: contas diferentes não agem no mesmo alvo ao mesmo tempo.{primeiraRetomada ? <> O primeiro retoma às {horaCurta(primeiraRetomada)} (hora local).</> : null}
      </Banner>
    </div>
  );
}

function CustoEAssunto({ op }: { op: Operacao }) {
  const { custo, max_usd: teto, assunto, fontes, parametros } = op;
  const perfilAlvo = parametros?.username ? `@${parametros.username.replace(/^@/, '')}` : null;
  const somas = (op.custo_por_modelo?.length ?? 0) + (op.custo_por_estagio?.length ?? 0) > 0;
  if (!custo && teto === null && !assunto && fontes.length === 0 && !perfilAlvo && !somas) return null;
  return (
    <section aria-label="Custo e assunto" className={styles.faixa}>
      {custo || teto !== null ? (
        <p className={styles.objetivo}>
          {custo ? <>Custo de IA <strong>{custo.total_usd === null ? 'total não informado' : formatUsd4(custo.total_usd)}</strong>{teto !== null ? <> de um teto de {formatUsd4(teto)}</> : null}
            {' '}<span className={styles.mudo}>(pesquisa {custo.pesquisa_usd === null ? 'não informada' : formatUsd4(custo.pesquisa_usd)} · agentes {custo.alvos_usd === null ? 'não informado' : formatUsd4(custo.alvos_usd)})</span></>
            : <>Teto de custo de IA {formatUsd4(teto ?? 0)}</>}
        </p>
      ) : null}
      <SomasDoCusto porModelo={op.custo_por_modelo ?? []} porEstagio={op.custo_por_estagio ?? []} rotulo="Na operação" />
      {assunto ? <p className={styles.objetivo}><strong>Assunto:</strong> {assunto}</p> : null}
      {perfilAlvo ? <p className={styles.objetivo} data-perfil-alvo><strong>Perfil alvo:</strong> {perfilAlvo}</p> : null}
      {fontes.length ? (
        <p className={styles.objetivo}>
          <strong>Fontes indicadas:</strong>{' '}
          {fontes.map((f, i) => {
            const link = fonteComoLink(f);
            return (
              <span key={`${f}-${i}`}>
                {i > 0 ? ' · ' : ''}
                {link ? <a className={styles.link} href={link} target="_blank" rel="noopener noreferrer">{f}</a> : f}
              </span>
            );
          })}
        </p>
      ) : null}
    </section>
  );
}

function DetalheDaOperacao({ id }: { id: string }) {
  const { dado: op, erro, carregando, lidoEm, recarregar } = useCarga((s) => apiOperacoes.detalhe(id, s), id);
  const [estado, setEstado] = useState<EstadoDoAlvo | ''>('');
  const [parou, setParou] = useState<EstagioId | ''>('');
  const [abertas, setAbertas] = useState<ReadonlySet<string>>(new Set());
  const [cancelando, setCancelando] = useState(false);
  // `null` = fechado; `so` = o agente cuja linha foi clicada (vem marcado), ou `null` para o botão do cabeçalho (nada marcado).
  const [abrirLiberar, setAbrirLiberar] = useState<{ so: string | null } | null>(null);
  const [abrirCancelarAlvos, setAbrirCancelarAlvos] = useState(false);
  const [abrirRelatorio, setAbrirRelatorio] = useState(false);
  const [aba, setAba] = useState<AbaDaOperacao>('agentes');
  const limiteDeAcoes = useAppStore((s) => s.settings?.operacao_max_acoes_executadas);
  const contagem = useMemo(() => contarPorEstado(op?.alvos ?? []), [op]);
  // Operação em andamento se relê sozinha (a tela ficava no que viu ao abrir, sem dizer que era velho); parada a aba oculta, e ao voltar
  // relê na hora. Encerrada ou exemplo: sem releitura. A releitura não apaga o que está na tela (só `op === null` mostra o esqueleto).
  useIntervaloVisivel(recarregar, RELEITURA_DA_OPERACAO_MS, op?.status === 'em_curso' && !op.exemplo);

  if (carregando && !op) return <LoadingRegion label="Lendo a operação"><Skeleton height={160} /></LoadingRegion>;
  if (erro && !op) return <LoadErrorState what="a operação" error={erro} onRetry={recarregar} />;
  if (!op) return null;

  const alvos = op.alvos.filter((a) => (!estado || a.estado === estado) && (!parou || estagioDeParada(a) === parou));
  const encerrada = op.status !== null && op.status !== 'em_curso';
  const preparados = alvosPreparados(op.alvos);
  const feitas = acoesDaOperacao(op.alvos);
  // Quantas contas ainda cabem no limite configurado (as que já executaram contam); sem o limite à mão, a lista inteira.
  const vagas = typeof limiteDeAcoes === 'number' ? Math.max(0, limiteDeAcoes - acoesJaExecutadas(op.alvos)) : preparados.length;
  const motivoSemLiberar = op.exemplo ? 'É um exemplo: não há o que liberar.'
    : preparados.length === 0 ? 'Nenhum agente espera a liberação da ação final.'
      : vagas === 0 ? 'O limite de contas que executam a ação final já foi atingido.' : null;
  const motivoSemCancelar = op.exemplo ? 'É um exemplo: não há o que cancelar.' : encerrada ? 'A operação já terminou.' : null;

  async function cancelar() {
    if (!op) return;
    const r = await confirm({
      title: 'Cancelar a operação?', confirmLabel: 'Cancelar a operação', cancelLabel: 'Voltar', danger: true,
      body: 'As execuções ainda abertas são canceladas, pelo mesmo caminho do cancelamento de uma execução. O agente que já terminou não muda.',
    });
    if (!r.confirmed) return;
    setCancelando(true);
    try {
      await apiOperacoes.cancelar(op.id);
      toast({ tone: 'success', title: 'Operação cancelada' });
      recarregar();
    } catch (e) {
      toastError('Não foi possível cancelar a operação', e);
    } finally {
      setCancelando(false);
    }
  }

  return (
    <Page title="Operação" lead={op.command || 'Sem objetivo informado.'}
          actions={(
            <>
              <Button size="sm" variant="outline" icon={Copy} disabledReason={op.exemplo ? 'É um exemplo: não há o que repetir.' : null}
                      onClick={() => { guardarRascunho(rascunhoDaOperacao(op)); useUiStore.getState().navegar({ tela: 'operacoes', segmentos: [ROTA_NOVA] }); }}>Repetir como nova</Button>
              <Button size="sm" variant="outline" disabledReason={op.exemplo ? 'É um exemplo: não há o que relatar.' : null} onClick={() => setAbrirRelatorio(true)}>Relatório</Button>
              <Button size="sm" variant="primary" disabledReason={motivoSemLiberar} onClick={() => setAbrirLiberar({ so: null })}>Liberar</Button>
              <Button size="sm" variant="outline" disabledReason={motivoSemCancelar ?? (op.alvos.length === 0 ? 'A operação não tem alvos.' : null)} onClick={() => setAbrirCancelarAlvos(true)}>Cancelar alvos</Button>
              <Button size="sm" variant="danger" loading={cancelando} disabledReason={motivoSemCancelar} onClick={() => void cancelar()}>Cancelar a operação</Button>
            </>
          )}>
      {erro ? <LoadErrorBanner error={erro} onRetry={recarregar} /> : null}
      {op.exemplo ? AVISO_DE_EXEMPLO : null}
      {abrirRelatorio ? <RelatorioDaOperacao op={op} onFechar={() => setAbrirRelatorio(false)} /> : null}
      {abrirCancelarAlvos ? (
        <CancelarAlvos operacaoId={op.id} alvos={op.alvos} onFechar={() => setAbrirCancelarAlvos(false)}
                       onFeito={() => { setAbrirCancelarAlvos(false); recarregar(); }} />
      ) : null}
      {abrirLiberar ? (
        <LiberarAcoes operacaoId={op.id} preparados={abrirLiberar.so ? preparados.filter((p) => p.profile_id === abrirLiberar.so) : preparados} vagas={vagas}
                      iniciais={abrirLiberar.so ? [abrirLiberar.so] : []} onFechar={() => setAbrirLiberar(null)}
                      onLiberado={() => { setAbrirLiberar(null); recarregar(); }} />
      ) : null}
      <p className={styles.cabecalho}>
        <a className={styles.link} href={hashDe('operacoes')}>← Todas as operações</a>
        {op.status ? <Badge tone={TOM_DO_STATUS[op.status]} size="sm">{ROTULO_DO_STATUS[op.status]}</Badge> : null}
        <span className={styles.mudo}>Ação final: {rotuloDaAcao(op.acao_final)}</span>
        {lidoEm && !op.exemplo ? (
          <span className={styles.mudo} data-lido-as>
            · Lido às {formatClock(lidoEm)}{op.status === 'em_curso' ? ' (relê sozinha enquanto a aba está à vista)' : ''}
          </span>
        ) : null}
        {feitas.executadas > 0 ? (
          <span className={styles.mudo} data-acoes-feitas>
            · {plural(feitas.executadas, 'ação executada', 'ações executadas')}, {plural(feitas.verificadas, 'verificada', 'verificadas')}
          </span>
        ) : null}
      </p>
      <EsperaDeResposta op={op} />
      <AdiadosPelaFrota op={op} />
      <CustoEAssunto op={op} />
      <PesquisaDaOperacaoSecao op={op} />
      <FaixaDeCapacidade op={op} />
      <PorApp op={op} />
      <Latencia op={op} />
      <Tabs tabs={ABAS} active={aba} onChange={setAba} idBase="operacao" label="Detalhe da operação" />
      <TabPanel idBase="operacao" id={aba}>
      {aba === 'aprendizado' ? <AprendizadoDaOperacaoTab op={op} /> : (
      <section aria-labelledby="operacao-agentes">
        <h2 id="operacao-agentes" className={styles.subtitulo}>Agentes ({formatInt(alvos.length)} de {formatInt(op.alvos.length)})</h2>
        <div className={styles.filtros}>
          <Field label="Estado">
            {({ id: campo }) => (
              <Select id={campo} small value={estado} onChange={(e) => setEstado(isEstadoDoAlvo(e.target.value) ? e.target.value : '')}>
                <option value="">Todos</option>
                {ESTADOS_DO_ALVO.map((s) => <option key={s} value={s}>{ROTULO_DO_ESTADO[s]} ({contagem[s]})</option>)}
              </Select>
            )}
          </Field>
          <Field label="Parou em">
            {({ id: campo }) => (
              <Select id={campo} small value={parou} onChange={(e) => setParou(isEstagio(e.target.value) ? e.target.value : '')}>
                <option value="">Qualquer estágio</option>
                {ESTAGIOS.map((e) => <option key={e.id} value={e.id}>{e.rotulo}</option>)}
              </Select>
            )}
          </Field>
        </div>
        {alvos.length === 0 ? (
          <EmptyState icon={Workflow} compact title="Nenhum agente com este filtro" hint="Limpe o filtro para ver todos." />
        ) : (
          <div className={styles.rolagem}>
            <table className={styles.tabela}>
              <caption className="sr-only">Um agente por linha: persona, conta, aparelho, pipeline, estado, ação ou motivo e verificação.</caption>
              <thead>
                <tr>
                  <th scope="col">Persona</th><th scope="col">Conta</th><th scope="col">Aparelho</th><th scope="col">Pipeline</th><th scope="col">Fila do aparelho</th>
                  <th scope="col">Estado</th><th scope="col">Ação final ou motivo</th><th scope="col">Resultado</th><th scope="col">Custo de IA</th><th scope="col">Duração</th><th scope="col"><span className="sr-only">Detalhe</span></th>
                </tr>
              </thead>
              <tbody>
                {alvos.map((a) => (
                  <LinhaDoAlvo key={a.id} alvo={a} aberta={abertas.has(a.id)} conhecidos={usuariosConhecidosDaOperacao(op.parametros)}
                               preparado={preparados.find((p) => p.profile_id === a.profile_id) ?? null} onLiberar={() => setAbrirLiberar({ so: a.profile_id })}
                               motivoSemVaga={vagas === 0 ? 'O limite de contas que executam a ação final já foi atingido.' : null}
                               onAlternar={() => setAbertas((s) => { const n = new Set(s); if (n.has(a.id)) n.delete(a.id); else n.add(a.id); return n; })} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}
      </TabPanel>
    </Page>
  );
}

function ListaDeOperacoes() {
  const lacoConfigurado = useAppStore((s) => s.settings?.operacao_laco_s);
  const { dado, erro, carregando, recarregar } = useCarga<ListaDeOperacoes>((s) => apiOperacoes.lista(s), 'lista');
  useIntervaloVisivel(recarregar, RELEITURA_DA_LISTA_MS);
  const [estado, setEstado] = useState<StatusDaOperacao | ''>('');
  const [busca, setBusca] = useState('');
  const [marcadas, setMarcadas] = useState<string[]>([]);
  if (carregando && !dado) return <Page title="Operação"><LoadingRegion label="Lendo as operações"><Skeleton height={120} /></LoadingRegion></Page>;
  if (erro && !dado) return <Page title="Operação"><LoadErrorState what="as operações" error={erro} onRetry={recarregar} /></Page>;
  const todas: ResumoDaOperacao[] = dado?.itens ?? [];
  const itens = filtrarOperacoes(todas, estado, busca);
  const porStatus = contarPorStatus(todas);
  const laco = estadoDoLaco(lacoConfigurado);
  return (
    <Page title="Operação" lead="Um objetivo entregue a vários agentes: cada um com persona, conta e aparelho, acompanhado do início ao fim."
          actions={(
            <>
              <Button size="sm" icon={Columns2}
                      disabledReason={marcadas.length === MAX_COMPARADAS ? null : `Marque ${MAX_COMPARADAS} operações na lista para compará-las (${marcadas.length} marcada${marcadas.length === 1 ? '' : 's'}).`}
                      onClick={() => useUiStore.getState().navegar({ tela: 'operacoes', segmentos: [ROTA_COMPARAR], query: { a: marcadas[0] ?? '', b: marcadas[1] ?? '' } })}>Comparar as marcadas</Button>
              <Button size="sm" variant="primary" icon={Plus} disabledReason={dado?.exemplo ? 'O central ainda não oferece o módulo de operações.' : null}
                      onClick={() => useUiStore.getState().navegar({ tela: 'operacoes', segmentos: [ROTA_NOVA] })}>Nova operação</Button>
            </>
          )}>
      {erro && dado ? <LoadErrorBanner error={erro} onRetry={recarregar} /> : null}
      {dado?.exemplo ? AVISO_DE_EXEMPLO : null}
      {laco ? (
        <p className={styles.mudo} data-laco-do-sistema={laco.ligado ? 'ligado' : 'desligado'}>
          <strong>Laço do sistema: {lacoEmPalavras(laco)}.</strong> {lacoExplica(laco)}
        </p>
      ) : null}
      {todas.length === 0 ? (
        <EmptyState icon={Workflow} title="Nenhuma operação ainda" hint="Quando uma operação for criada, ela aparece aqui, da mais nova para a mais antiga." />
      ) : (
        <>
          <div className={styles.filtros}>
            <Field label="Estado">
              {({ id }) => (
                <Select id={id} small value={estado} onChange={(e) => setEstado(isStatusDaOperacao(e.target.value) ? e.target.value : '')}>
                  <option value="">Todos ({todas.length})</option>
                  {STATUS_DA_OPERACAO.filter((s) => porStatus[s] > 0).map((s) => <option key={s} value={s}>{ROTULO_DO_STATUS[s]} ({porStatus[s]})</option>)}
                </Select>
              )}
            </Field>
            <Field label="Buscar no objetivo">
              {({ id }) => <TextInput id={id} small value={busca} onChange={(e) => setBusca(e.target.value)} />}
            </Field>
          </div>
          <p className={styles.mudo} role="status">{formatInt(itens.length)} de {formatInt(todas.length)} operações, da mais nova para a mais antiga.</p>
        </>
      )}
      {todas.length > 0 && itens.length === 0 ? (
        <EmptyState icon={Workflow} compact title="Nenhuma operação com este filtro" hint="Limpe o estado ou a busca para ver todas." />
      ) : todas.length === 0 ? null : (
        <ul className={styles.lista} aria-label="Operações">
          {itens.map((o) => (
            <li key={o.id} className={styles.itemDaLista}>
              <Checkbox label="Comparar" aria-label={`Comparar: ${o.command || o.id}`} checked={marcadas.includes(o.id)}
                        disabled={!marcadas.includes(o.id) && marcadas.length >= MAX_COMPARADAS}
                        onChange={() => setMarcadas((m) => (m.includes(o.id) ? m.filter((x) => x !== o.id) : m.length < MAX_COMPARADAS ? [...m, o.id] : m))} />
              <a className={styles.link} href={hashDe('operacoes', { segmentos: [o.id] })}>{o.command || o.id}</a>
              <span className={styles.mudo} data-meta>{descricaoDaOperacao(o, formatQuando)}</span>
              <span className={styles.mudo}>
                {o.status ? ROTULO_DO_STATUS[o.status] : 'estado não informado'} · {numero(o.capacidade.solicitados)} solicitados,{' '}
                {numero(o.capacidade.concluidas)} concluídas, {numero(o.capacidade.bloqueadas)} bloqueadas
              </span>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}

export function OperacaoPage() {
  useEffect(() => { document.title = 'Operação · Central de Aparelhos'; }, []);
  const id = useUiStore((s) => s.rota.segmentos[0]);
  if (id === ROTA_NOVA) return <CriarOperacao />;
  if (id === ROTA_COMPARAR) return <CompararOperacoes />;
  return id ? <DetalheDaOperacao key={id} id={id} /> : <ListaDeOperacoes />;
}
