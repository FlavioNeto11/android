import {
  ArrowRightLeft, Bot, ChartColumn, Gauge, HeartPulse, History, PiggyBank, RefreshCw, Scale, Timer, TriangleAlert,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx, formatInt } from '../../lib/format';
import { LoadErrorBanner, LoadErrorState } from '../../lib/loadError';
import { formatDateTime } from '../../lib/time';
import { apiAprendizado } from './api';
import { metaDeSaude } from './detalhe';
import {
  atravessaAQuebra, formatHoras, formatTaxa, formatUsd, rotuloDaEconomia, rotuloDoModo, rotuloDoSemItem,
  type LadoDoProxy, type MetricasDoAprendizado, type Tempo,
} from './metricas';
import { ORIGEM_LABEL, rotuloDoEstado, rotuloDoKind, type EstadoDoLivro, type Origem } from './model';
import { textoDaDecisao } from './parecer';
import { RevisoesDoCurador } from './RevisoesDoCurador';
import { useCarga } from './useCarga';
import styles from './Aprendizado.module.css';

const JANELAS = [7, 14, 30] as const;
const NAO_PRONTO = 'nao_pronto' as const;

function Numero({ valor, rotulo, dica }: { valor: ReactNode; rotulo: string; dica?: string }) {
  return (
    <div className={styles.numero} title={dica}>
      <span className={styles.numeroValor}>{valor}</span>
      <span className={styles.numeroRotulo}>{rotulo}</span>
    </div>
  );
}

/** Um bloco da tabela do §10: título, a frase que diz o que se mede e os números. */
function Bloco({ icone: Icone, titulo, sobre, children, tom }: {
  icone: typeof Gauge; titulo: string; sobre: ReactNode; children: ReactNode; tom?: 'aviso';
}) {
  return (
    <section className={cx(styles.cartao, tom === 'aviso' && styles.cartaoAviso)} aria-label={titulo}>
      <h3 className={cx(styles.cartaoHead, styles.blocoTitulo)}><Icone size={16} aria-hidden /> <span className={styles.cartaoNome}>{titulo}</span></h3>
      <p className={styles.cartaoUso}>{sobre}</p>
      {children}
    </section>
  );
}

/** O bloco sem dado diz o porquê numa frase, em vez de uma grade de zeros. */
function Vazio({ children }: { children: ReactNode }) {
  return <p className={styles.cartaoLinha}>{children}</p>;
}

const soma = (c: Record<string, number>) => Object.values(c).reduce((a, b) => a + b, 0);
const estado = (k: string) => rotuloDoEstado(k as EstadoDoLivro);

/** "Receita 76 · Fluxo 16": o rótulo é a categoria e o número vem depois (sem concordância de plural para errar). */
function linhaDeContagem(c: Record<string, number>, rotulo: (k: string) => string): string {
  const partes = Object.entries(c).filter(([, n]) => n > 0).sort(([, a], [, b]) => b - a).map(([k, n]) => `${rotulo(k)} ${formatInt(n)}`);
  return partes.length > 0 ? partes.join(' · ') : 'nenhuma';
}

function TempoDoPar({ t, rotulo }: { t: Tempo; rotulo: string }) {
  return (
    <div className={styles.numero}>
      <span className={styles.numeroValor}>{t.n > 0 ? formatHoras(t.mediana_h) : 'sem amostra'}</span>
      <span className={styles.numeroRotulo}>
        {rotulo} · {t.n > 0 ? `mediana; 90% em até ${formatHoras(t.p90_h)}; ` : ''}n = {formatInt(t.n)}
      </span>
    </div>
  );
}

function LadoDoComparativo({ lado, rotulo }: { lado: LadoDoProxy | null; rotulo: string }) {
  if (!lado) return <Numero valor="—" rotulo={rotulo} />;
  return (
    <Numero valor={formatTaxa(lado.taxa_de_falha)}
            rotulo={`${rotulo}: ${formatInt(lado.falhas)} falhas em ${formatInt(lado.etapas)} etapas`} />
  );
}

function Painel({ m }: { m: MetricasDoAprendizado }) {
  const totalDeItens = Object.values(m.itens).reduce((a, c) => a + soma(c), 0);
  const porEstado: Record<string, number> = {};
  for (const c of Object.values(m.itens)) for (const [e, n] of Object.entries(c)) porEstado[e] = (porEstado[e] ?? 0) + n;
  const s = m.sucesso_depois_de_promovido;
  const r = m.refutados_depois_de_promovidos;
  const cur = m.curador;
  const orc = m.orcamento_do_curador;
  const proxy = m.falhas_evitadas_proxy;
  const saude = Object.entries(m.saude).filter(([, n]) => n > 0);
  const semItem = Object.entries(m.sem_item).filter(([, n]) => n > 0);
  const eco = m.economia;
  const ECONOMIA_PRINCIPAL = ['etapas', 'por_receita', 'receita_mais_ia', 'so_ia'] as const;

  return (
    <>
      <div className={styles.cartoes}>
        <Bloco icone={ChartColumn} titulo="O livro agora"
               sobre="O que o sistema sabe hoje neste recorte, pelo estado do ciclo e por quem ensinou.">
          {totalDeItens === 0 ? <Vazio>O livro está vazio neste recorte.</Vazio> : (<>
          <div className={styles.numeros}>
            <Numero valor={formatInt(totalDeItens)} rotulo="itens no livro" />
            <Numero valor={formatInt(m.pendentes)} rotulo="esperando você" />
            {(['published', 'validated', 'candidate'] as const).map((e) => (
              <Numero key={e} valor={formatInt(porEstado[e] ?? 0)} rotulo={rotuloDoEstado(e as EstadoDoLivro).toLowerCase()} />
            ))}
          </div>
          <p className={styles.cartaoLinha}>
            <span className={styles.cartaoRotulo}>Por tipo</span>
            {linhaDeContagem(Object.fromEntries(Object.entries(m.itens).map(([k, c]) => [k, soma(c)])), rotuloDoKind)}
          </p>
          <p className={styles.cartaoLinha}>
            <span className={styles.cartaoRotulo}>Por origem</span>
            {linhaDeContagem(m.por_origem, (k) => ORIGEM_LABEL[k as Origem] ?? k)}
          </p>
          </>)}
        </Bloco>

        <Bloco icone={ArrowRightLeft} titulo="Movimento na janela"
               sobre="As mudanças de estado do período: quem decidiu (o sistema ou uma pessoa) e quanto o livro mexeu.">
          {!m.churn.transicoes && !m.churn.criados ? <Vazio>Nenhum item nasceu nem mudou de estado nesta janela.</Vazio> : (<>
          <div className={styles.numeros}>
            <Numero valor={formatInt(m.churn.transicoes)} rotulo="mudanças de estado" />
            <Numero valor={formatInt(m.churn.itens_com_transicao)} rotulo="itens que mudaram" />
            <Numero valor={formatInt(m.churn.criados)} rotulo="itens criados" />
            <Numero valor={formatInt(m.churn.desligados)} rotulo="desligados ou aposentados" />
          </div>
          <p className={styles.cartaoLinha}>
            <span className={styles.cartaoRotulo}>Pelo sistema, pelo estado de chegada</span>{linhaDeContagem(m.aprovacoes.sistema, estado)}
          </p>
          <p className={styles.cartaoLinha}>
            <span className={styles.cartaoRotulo}>Por pessoas, pelo estado de chegada</span>{linhaDeContagem(m.aprovacoes.pessoa, estado)}
          </p>
          </>)}
        </Bloco>

        <Bloco icone={Timer} titulo="Tempo até subir"
               sobre="Da chegada a candidato até validado, e de validado até publicado. Item sem esse par na trilha fica fora; o n diz quantos entraram.">
          {m.tempos.candidate_validated.n + m.tempos.validated_published.n === 0 ? <Vazio>Nenhum item subiu de estado nesta janela.</Vazio> : (
            <div className={styles.numeros}>
              <TempoDoPar t={m.tempos.candidate_validated} rotulo="candidato → validado" />
              <TempoDoPar t={m.tempos.validated_published} rotulo="validado → publicado" />
            </div>
          )}
        </Bloco>

        <Bloco icone={Scale} titulo="Depois de publicado"
               sobre="Só a evidência real datada depois da publicação. A receita não tem evidência datada e aparece na economia.">
          {s.a_favor + s.contra === 0 && !r.desligados_pelo_sistema && !r.desligados_por_pessoa && !r.publicados_com_evidencia_contra
            ? <Vazio>Nenhuma evidência real nem desligamento de item publicado nesta janela.</Vazio> : (
          <div className={styles.numeros}>
            <Numero valor={formatTaxa(s.taxa)} rotulo={`de acerto · ${formatInt(s.a_favor)} a favor, ${formatInt(s.contra)} contra`}
                    dica="Evidência a favor ÷ (a favor + contra). Sem nenhuma, não há taxa." />
            <Numero valor={formatInt(r.desligados_pelo_sistema)} rotulo="desligados pelo sistema" />
            <Numero valor={formatInt(r.desligados_por_pessoa)} rotulo="desligados por pessoas" />
            <Numero valor={formatInt(r.publicados_com_evidencia_contra)} rotulo="publicados com evidência contra" />
          </div>
          )}
        </Bloco>

        <Bloco icone={HeartPulse} titulo="Saúde dos publicados"
               sobre="A mesma saúde da lista e do detalhe do item, contada neste recorte. Não depende da janela.">
          {saude.length === 0 ? <Vazio>Nenhum item publicado.</Vazio> : (
            <div className={styles.chipsDeSaude}>
              {saude.map(([k, n]) => {
                const meta = metaDeSaude(k);
                return (
                  <Badge key={k} tone={meta?.tone ?? 'neutral'} icon={meta?.icon} size="sm" title={meta?.description}>
                    {formatInt(n)} {meta?.label.toLowerCase() ?? k}
                  </Badge>
                );
              })}
            </div>
          )}
        </Bloco>

        <Bloco icone={PiggyBank} titulo="Economia de IA"
               sobre="Etapas feitas por receita em vez da IA. As chamadas evitadas são uma estimativa (a mediana que a mesma etapa custava à IA).">
          {eco === null ? <Vazio>A medida da economia não está ligada neste servidor.</Vazio> : !eco.etapas ? (
            <Vazio>Nenhuma etapa executada nesta janela.</Vazio>
          ) : (
            <>
              <div className={styles.numeros}>
                <Numero valor={formatInt(eco.chamadas_evitadas_estimadas)} rotulo={rotuloDaEconomia('chamadas_evitadas_estimadas')} />
                {ECONOMIA_PRINCIPAL.map((k) => <Numero key={k} valor={formatInt(eco[k])} rotulo={rotuloDaEconomia(k)} />)}
              </div>
              {(eco.etapas_por_receita_sem_base ?? 0) > 0 ? (
                <p className={styles.cartaoLinha}>
                  {formatInt(eco.etapas_por_receita_sem_base)} etapas por receita sem base para estimar: não somam nada.
                </p>
              ) : null}
            </>
          )}
        </Bloco>

        <Bloco icone={Bot} titulo="Receita × só IA (comparação, não prova)"
               sobre={<>Taxa de falha nas etapas que, na janela, rodaram dos dois jeitos. <strong>Não mede falha evitada</strong>: não há como saber o que teria acontecido.</>}>
          {proxy.etapas_comparadas === 0 ? (
            <Vazio>Nenhuma etapa rodou dos dois jeitos nesta janela.</Vazio>
          ) : (
            <div className={styles.numeros}>
              <LadoDoComparativo lado={proxy.com_receita} rotulo="com receita" />
              <LadoDoComparativo lado={proxy.so_ia} rotulo="só IA" />
              <Numero valor={formatInt(proxy.etapas_comparadas)} rotulo="etapas comparadas" />
            </div>
          )}
        </Bloco>

        <Bloco icone={History} titulo="Curador na janela"
               sobre="Os pareceres da IA sobre itens do livro. O parecer simulado é contado à parte e não entra nas decisões nem no custo.">
          {cur === null || (cur.revisoes === 0 && cur.simuladas === 0) ? (
            <Vazio>Nenhuma revisão do curador nesta janela.</Vazio>
          ) : (
            <>
              <div className={styles.numeros}>
                <Numero valor={formatInt(cur.revisoes)} rotulo="revisões reais" />
                <Numero valor={formatUsd(cur.usd)} rotulo="custo medido" />
                <Numero valor={formatInt(cur.aplicadas)} rotulo="decididas por alguém" />
                <Numero valor={formatInt(cur.overrides)} rotulo="decididas contra o parecer" />
              </div>
              <p className={styles.cartaoLinha}>
                <span className={styles.cartaoRotulo}>O que a IA sugeriu</span>
                {linhaDeContagem(cur.decisoes, textoDaDecisao)}
              </p>
              {cur.validade.invalida + cur.validade.recusada + cur.simuladas > 0 ? (
                <p className={styles.cartaoLinha}>
                  <span className={styles.cartaoRotulo}>Sem parecer</span>
                  {formatInt(cur.validade.invalida)} fora do contrato · {formatInt(cur.validade.recusada)} recusadas · {formatInt(cur.simuladas)} simuladas
                </p>
              ) : null}
            </>
          )}
        </Bloco>

        <Bloco icone={Gauge} titulo="Orçamento do curador" tom={orc?.aviso ? 'aviso' : undefined}
               sobre={orc ? `Vale para todos os apps e usa a janela do curador (${formatInt(orc.janela_dias)} dias), não a escolhida acima.` : 'O curador não está composto neste servidor.'}>
          {orc ? (
            <>
              {orc.uso !== null ? (
                <ProgressBar value={orc.uso} label="Uso do orçamento do curador" thick tone={orc.aviso ? 'warning' : 'accent'}
                             text={`${formatUsd(orc.gasto_da_curadoria)} de ${formatUsd(orc.orcamento)}`} />
              ) : <Vazio>Sem orçamento calculado: não houve gasto de IA na janela do curador.</Vazio>}
              {orc.uso === null && !orc.gasto_da_operacao && !orc.gasto_da_curadoria ? null : (
              <div className={styles.numeros}>
                <Numero valor={formatUsd(orc.gasto_da_curadoria)} rotulo="gasto da curadoria" />
                <Numero valor={formatUsd(orc.orcamento)} rotulo="orçamento (piso)" dica="Calculado com as revisões já gravadas: a próxima volta só pode aumentá-lo." />
                <Numero valor={formatUsd(orc.gasto_da_operacao)} rotulo="gasto da operação" />
                <Numero valor={formatUsd(orc.teto_alfa)} rotulo="teto (fração da operação)" />
              </div>
              )}
              <p className={styles.cartaoUso}>
                Curador {rotuloDoModo(orc.modo)} · {formatInt(orc.revisoes_na_janela)} revisões na janela
                {orc.aviso ? ' · passou de 80% do orçamento' : ''}
              </p>
            </>
          ) : null}
        </Bloco>
      </div>

      {semItem.length > 0 ? (
        <p className={styles.secaoLead}>
          Fora da conta, de itens que já saíram do livro: {semItem.map(([k, n]) => `${formatInt(n)} ${rotuloDoSemItem(k)}`).join(' e ')}.
        </p>
      ) : null}
    </>
  );
}

/**
 * Métricas (30.33; rotas do 30.8, §10 do desenho): o aprendizado medido, na janela escolhida e por app. Ausente
 * aparece como "sem amostra", nunca como zero; o proxy de falhas diz que é comparação; o orçamento do curador é global
 * e usa a janela dele. Embaixo, os pareceres do curador, do mais novo ao mais velho.
 */
export function MetricasTab() {
  const [dias, setDias] = useState<number>(14);
  const [app, setApp] = useState('');
  const [apps, setApps] = useState<{ pacote: string; nome: string }[]>([]);

  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.apps(ctl.signal)
      .then((v) => setApps(v.apps.map((a) => ({ pacote: a.pacote, nome: a.nome }))))
      .catch(() => setApps([]));  // sem a lista, o filtro fica só com "Todos"
    return () => ctl.abort();
  }, []);

  const { dado, erro, carregando, carregar } = useCarga(async (signal) => {
    try {
      return await apiAprendizado.metricas({ dias, app }, signal);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503 && e.code === 'not_ready') return NAO_PRONTO;
      throw e;
    }
  }, `${dias}|${app}`);

  const m = dado !== NAO_PRONTO ? dado : null;

  return (
    <section className={styles.secao} aria-label="Métricas do aprendizado">
      <div className={styles.toolbar}>
        <Field label="Janela" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={String(dias)} onChange={(e) => setDias(Number(e.target.value))}>
              {JANELAS.map((d) => <option key={d} value={d}>{d} dias</option>)}
            </Select>
          )}
        </Field>
        <Field label="Aplicativo" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={app} onChange={(e) => setApp(e.target.value)}>
              <option value="">Todos</option>
              {apps.map((a) => <option key={a.pacote} value={a.pacote}>{a.nome}</option>)}
            </Select>
          )}
        </Field>
        <div className={styles.toolbarFim}>
          {m?.desde && m.ate ? <span className={styles.cartaoUso}>{formatDateTime(m.desde)} a {formatDateTime(m.ate)}</span> : null}
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Atualizar</Button>
        </div>
      </div>

      {m && atravessaAQuebra(m.desde, m.ate) ? (
        <Banner tone="info" icon={TriangleAlert} compact role="note" title="Esta janela atravessa uma mudança de medida (03/10, 09:06 UTC)">
          Desde então, abrir o app sem IA conta como etapa sem ator. "Só IA", a economia e a comparação com receita mudam
          de regime nessa data: compare só janelas do mesmo lado.
        </Banner>
      ) : null}
      {m?.orcamento_do_curador?.aviso ? (
        <Banner tone="warning" icon={Gauge} compact role="status" title="O curador passou de 80% do orçamento da janela dele">
          O valor já é um teto: as próximas revisões só entram se couberem no orçamento.
        </Banner>
      ) : null}
      {erro && m ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}

      {dado === NAO_PRONTO ? (
        <EmptyState icon={ChartColumn} compact title="As métricas não estão ligadas neste servidor">
          O serviço do aprendizado ainda não foi composto. Depois de reiniciar o servidor, tente de novo.
        </EmptyState>
      ) : !m ? (
        erro ? <LoadErrorState what="as métricas do aprendizado" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Medindo o aprendizado…" className={styles.cartoes}>
            <Skeleton height={140} radius={8} />
            <Skeleton height={140} radius={8} />
            <Skeleton height={140} radius={8} />
          </LoadingRegion>
        )
      ) : <Painel m={m} />}

      {dado !== NAO_PRONTO ? <RevisoesDoCurador app={app} /> : null}
    </section>
  );
}
