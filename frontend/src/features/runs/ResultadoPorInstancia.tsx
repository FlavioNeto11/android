import { CircleCheck, CircleDashed, Hand, Server, ShieldCheck, ShieldQuestion, Zap } from 'lucide-react';
import type { ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Disclosure } from '../../components/Disclosure';
import { JsonTree, KvList, KvRow } from '../../components/JsonTree';
import { StatusBadge } from '../../components/StatusBadge';
import { toneClass } from '../../components/tone';
import { cx, formatInt, humanizeKey, scalarToText } from '../../lib/format';
import { DELIVERY_LEVEL, OBJECTIVE_STATUS, type Tone, metaOf } from '../../lib/status';
import { formatClock } from '../../lib/time';
import { type ResultadoDaInstancia, lerResultados, seloDaProva } from './resultadoDaInstancia';
import styles from './Runs.module.css';

/** Acima disto, os cartões já comprovados começam recolhidos: sobra à vista o que deu errado (bateria de 8 aparelhos). */
const RECOLHER_COMPROVADOS_ACIMA_DE = 3;

const ROTULO_EXTRA: Record<string, string> = {
  summary: 'Resumo', status_detail: 'Detalhe', detail: 'Detalhe', error: 'Erro', attempts: 'Tentativas',
  account: 'Conta', evidence: 'Evidência', duration_s: 'Duração (s)', steps_done: 'Etapas concluídas', steps_total: 'Etapas',
};

function plural(n: number, um: string, varios: string): string {
  return `${formatInt(n)} ${n === 1 ? um : varios}`;
}

/**
 * "Resultado por instância" do relatório: um cartão por aparelho, no lugar da tabela de 15 colunas em que as listas de
 * etapas e efeitos viravam colunas estreitas e ilegíveis. O cabeçalho responde "deu certo, com prova, onde?"; o corpo
 * mostra as etapas (comprovadas com a prova, confirmadas à mão, em aberto no plano final) e os efeitos externos.
 */
export function ResultadoPorInstancia({ rows }: { rows: unknown[] }) {
  const resultados = lerResultados(rows);
  if (!resultados) return <JsonTree value={rows} />;
  const recolher = resultados.length > RECOLHER_COMPROVADOS_ACIMA_DE;
  return (
    <div className={styles.irList}>
      {resultados.map((r, i) => (
        <CartaoDaInstancia key={`${r.instanceId ?? 'sem-id'}-${i}`} r={r} n={i} recolhido={recolher && r.comprovado === true} />
      ))}
    </div>
  );
}

function CartaoDaInstancia({ r, n, recolhido }: { r: ResultadoDaInstancia; n: number; recolhido: boolean }) {
  const tituloId = `relatorio-instancia-${n}`;
  const selo = seloDaProva(r);
  // A borda diz a qualidade do resultado de relance: sucesso sem prova completa é aviso, não verde.
  const tom: Tone = selo === 'a_mao' || selo === 'sem_prova' ? 'warning' : r.status ? metaOf(OBJECTIVE_STATUS, r.status).tone : 'muted';
  const temEtapas = r.comprovadas !== null || r.aMao !== null || r.emAberto !== null;
  const corpo = temEtapas || r.efeitos !== null ? () => <CorpoDaInstancia r={r} /> : null;
  const rodape = [
    r.versaoDoPlano !== null ? plural(r.versaoDoPlano, 'versão do plano', 'versões do plano') : null,
    r.chamadasDeIa !== null ? plural(r.chamadasDeIa, 'chamada de IA', 'chamadas de IA') : null,
    r.tokensDeIa !== null ? plural(r.tokensDeIa, 'token', 'tokens') : null,
  ].filter((x): x is string => x !== null);

  return (
    <article className={cx(styles.irCard, toneClass(tom))} aria-labelledby={tituloId}>
      <header className={styles.irHead}>
        <h4 id={tituloId} className={styles.irId}>{r.instanceId ?? 'Instância sem id'}</h4>
        {r.status ? <StatusBadge meta={metaOf(OBJECTIVE_STATUS, r.status)} size="sm" /> : null}
        {selo === 'comprovado' ? (
          <Badge tone="success" icon={ShieldCheck} size="sm" title="Todas as etapas comprovadas pela tela, sem confirmação à mão.">Comprovado</Badge>
        ) : selo === 'a_mao' ? (
          <Badge tone="warning" icon={Hand} size="sm" title="Alguma etapa foi confirmada à mão: o sucesso não foi comprovado pela tela.">Com etapa confirmada à mão</Badge>
        ) : selo === 'sem_prova' ? (
          <Badge tone="warning" icon={ShieldQuestion} size="sm">Sucesso sem prova completa</Badge>
        ) : null}
        {r.entrega ? <StatusBadge meta={metaOf(DELIVERY_LEVEL, r.entrega)} size="sm" plain srPrefix="Entrega" /> : null}
        <span className={styles.irWhere}>
          <Server size={12} aria-hidden />
          {/* Onde rodou, como ficou gravado: sem fotografia, "não registrado" — nunca a máquina local por omissão. */}
          {r.servidor || r.serial ? (
            <span><span className="sr-only">Onde rodou: </span><span className="mono">{r.servidor ?? '—'}</span>{r.serial ? <> · <span className="mono">{r.serial}</span></> : null}</span>
          ) : (
            <span>onde rodou: não registrado</span>
          )}
        </span>
      </header>

      {r.texto ? <p className={styles.irText}>{r.texto}</p> : null}
      {r.motivo ? <p className={styles.irText}><span className={styles.irLabel}>Motivo: </span>{r.motivo}</p> : null}
      {r.falta ? <Banner tone="warning" icon={Hand} compact title="O que falta">{r.falta}</Banner> : null}

      {corpo ? (
        recolhido ? (
          <Disclosure bare summary={resumoDoCorpo(r)}>{corpo}</Disclosure>
        ) : corpo()
      ) : null}

      {r.extras.length > 0 ? (
        <KvList>
          {r.extras.map(([k, v]) => (
            <KvRow key={k} label={ROTULO_EXTRA[k] ?? humanizeKey(k)}>
              {v === null || typeof v !== 'object' ? scalarToText(v) : <JsonTree value={v} depth={1} />}
            </KvRow>
          ))}
        </KvList>
      ) : null}

      {rodape.length > 0 ? <p className={styles.irFoot}>{rodape.join(' · ')}</p> : null}
    </article>
  );
}

/** O resumo do cartão recolhido diz o que há dentro, para ninguém precisar abrir só para contar. */
function resumoDoCorpo(r: ResultadoDaInstancia): string {
  const partes = [
    r.comprovadas ? plural(r.comprovadas.length, 'etapa comprovada', 'etapas comprovadas') : null,
    r.efeitos ? plural(r.efeitos.length, 'efeito externo', 'efeitos externos') : null,
  ].filter((x): x is string => x !== null);
  return partes.length > 0 ? `Ver etapas e efeitos (${partes.join(', ')})` : 'Ver etapas e efeitos';
}

function CorpoDaInstancia({ r }: { r: ResultadoDaInstancia }) {
  const temEtapas = r.comprovadas !== null || r.aMao !== null || r.emAberto !== null;
  const semNenhuma = (r.comprovadas?.length ?? 0) + (r.aMao?.length ?? 0) + (r.emAberto?.length ?? 0) === 0;
  const variasVersoes = r.versaoDoPlano !== null && r.versaoDoPlano > 1;
  return (
    <>
      {temEtapas ? (
        <Bloco titulo="Etapas">
          {semNenhuma ? <p className={styles.muted}>Nenhuma etapa registrada.</p> : null}
          {r.comprovadas && r.comprovadas.length > 0 ? (
            <Grupo titulo="Comprovadas" nota={variasVersoes ? 'em qualquer versão do plano' : undefined}>
              {r.comprovadas.map((e, i) => (
                <Etapa key={i} icone={<CircleCheck size={14} aria-hidden className={styles.irOk} />} titulo={e.titulo}>
                  {e.prova ? <span className={styles.irProof}>{e.prova}</span> : null}
                </Etapa>
              ))}
            </Grupo>
          ) : null}
          {r.aMao && r.aMao.length > 0 ? (
            <Grupo titulo="Confirmadas à mão" nota="sem prova da tela">
              {r.aMao.map((t, i) => <Etapa key={i} icone={<Hand size={14} aria-hidden className={styles.irWarn} />} titulo={t} />)}
            </Grupo>
          ) : null}
          {r.emAberto && r.emAberto.length > 0 ? (
            <Grupo titulo={r.versaoDoPlano !== null ? `Em aberto no plano final (v${r.versaoDoPlano})` : 'Em aberto no plano final'}>
              {r.emAberto.map((t, i) => <Etapa key={i} icone={<CircleDashed size={14} aria-hidden className={styles.irOpen} />} titulo={t} />)}
            </Grupo>
          ) : null}
        </Bloco>
      ) : null}
      {r.efeitos !== null ? (
        <Bloco titulo="Efeitos externos">
          {r.efeitos.length === 0 ? (
            <p className={styles.muted}>Nenhum efeito fora do aparelho registrado.</p>
          ) : (
            <ul className={styles.irSteps}>
              {r.efeitos.map((e, i) => (
                <li key={i} className={styles.irStep}>
                  <Zap size={14} aria-hidden className={styles.irEffect} />
                  <div className={styles.irStepBody}>
                    {e.quando || e.etapa ? (
                      <span className={styles.irEffectHead}>
                        {e.quando ? <time className={styles.irTime} dateTime={e.quando}>{formatClock(e.quando)}</time> : null}
                        {e.etapa ? <span className={styles.irStepTitle}>{e.etapa}</span> : null}
                      </span>
                    ) : null}
                    <span className={styles.irProof}>{e.texto}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Bloco>
      ) : null}
    </>
  );
}

function Bloco({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <div className={styles.irBlock}>
      <h5 className={styles.irBlockTitle}>{titulo}</h5>
      {children}
    </div>
  );
}

function Grupo({ titulo, nota, children }: { titulo: string; nota?: string; children: ReactNode }) {
  return (
    <div className={styles.irGroup}>
      <p className={styles.irGroupTitle}>{titulo}{nota ? <span className={styles.irGroupNote}> — {nota}</span> : null}</p>
      <ol className={styles.irSteps}>{children}</ol>
    </div>
  );
}

function Etapa({ icone, titulo, children }: { icone: ReactNode; titulo: string; children?: ReactNode }) {
  return (
    <li className={styles.irStep}>
      {icone}
      <div className={styles.irStepBody}>
        <span className={styles.irStepTitle}>{titulo}</span>
        {children}
      </div>
    </li>
  );
}
